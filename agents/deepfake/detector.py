"""Deepfake detection engine — spectral/statistical feature extraction + an
Ollama Qwen 2.5 14B reasoning pass.

This replaces the old rule-based `_score_deepfake` RNG with a real pipeline:

  1. Extract cheap, explainable signal features from the media (PIL for images,
     the stdlib `wave` module — or librosa if installed — for audio).
  2. Hand those features to qwen2.5:14b via Ollama /api/chat and ask it to reason
     about whether the media is AI-generated / manipulated, returning strict JSON.
  3. Normalise + clamp the model output into a stable schema.

Everything degrades gracefully: if Ollama is down or returns junk, `_call_ollama`
falls back to an UNCERTAIN verdict so the caller always gets a usable dict.
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import struct
import time
import urllib.request
import wave
from typing import List, Optional

from PIL import Image, ImageFilter, ImageStat

try:  # numpy is available in both venvs but keep it optional.
    import numpy as np
except Exception:  # noqa: BLE001
    np = None  # type: ignore

logger = logging.getLogger("deepfake.detector")

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:14b"
# 70s, not 45s: an idle Ollama unloads the 14B model, and a cold reload alone
# measured ~37-39s in testing before inference even starts. 45s clipped a
# real cold-start call and produced a spurious "no score" fallback.
OLLAMA_TIMEOUT = 70

VOICE_VERIFY_URL = "http://localhost:8000/api/voice/verify"

_FAKE_THRESHOLD = 0.70
_VERDICTS = {"LIKELY_REAL", "LIKELY_FAKE", "UNCERTAIN"}


def _clamp01(value, default: float = 0.5) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return round(max(0.0, min(1.0, v)), 3)


class DeepfakeDetector:
    def __init__(
        self,
        ollama_url: str = OLLAMA_URL,
        model: str = OLLAMA_MODEL,
        timeout: int = OLLAMA_TIMEOUT,
    ) -> None:
        self.ollama_url = ollama_url
        self.model = model
        self.timeout = timeout

    # ── IMAGE ───────────────────────────────────────────────
    def analyze_image(self, image_bytes: bytes) -> dict:
        features = self._extract_image_features(image_bytes)
        prompt = (
            "You are a deepfake detection expert. Analyze these image features and "
            "determine if this could be AI-generated or manipulated:\n"
            f"Color uniformity: {features['color_uniformity']}\n"
            f"Edge sharpness: {features['edge_sharpness']}\n"
            f"Noise pattern: {features['noise_pattern']}\n"
            f"Facial symmetry: {features['facial_symmetry']} "
            f"(face_detected={features['face_detected']})\n"
            "Real photos show natural color variation, organic edge falloff, and "
            "sensor noise; AI images tend to be too uniform, too sharp or too smooth, "
            "and unnaturally symmetric.\n"
            "Based on these indicators, provide:\n"
            "1. deepfake_probability: 0.0-1.0\n"
            "2. confidence: 0.0-1.0\n"
            "3. indicators: list of suspicious patterns found\n"
            "4. verdict: LIKELY_REAL | LIKELY_FAKE | UNCERTAIN\n"
            "Respond in JSON only."
        )
        result = self._call_ollama(prompt)
        result["features"] = features
        result["analysis_method"] = (
            "ollama_spectral" if result.get("analysis_method") != "fallback" else "fallback"
        )
        return result

    def _extract_image_features(self, image_bytes: bytes) -> dict:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            gray = img.convert("L")

            # Color histogram uniformity — flat/peaky histograms score high.
            stat = ImageStat.Stat(img)
            channel_std = sum(stat.stddev) / len(stat.stddev)
            color_uniformity = round(1.0 - min(channel_std / 128.0, 1.0), 3)

            # Edge sharpness — mean magnitude of the edge map.
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_mean = ImageStat.Stat(edges).mean[0]
            edge_sharpness = round(min(edge_mean / 80.0, 1.0), 3)

            # Noise pattern — residual after Gaussian blur (high-freq content).
            blurred = gray.filter(ImageFilter.GaussianBlur(radius=2))
            noise = self._residual_std(gray, blurred)
            noise_pattern = round(min(noise / 40.0, 1.0), 3)

            # Facial symmetry proxy — left half vs mirrored right half.
            face_detected, facial_symmetry = self._symmetry(gray)

        return {
            "color_uniformity": color_uniformity,
            "edge_sharpness": edge_sharpness,
            "noise_pattern": noise_pattern,
            "facial_symmetry": facial_symmetry,
            "face_detected": face_detected,
        }

    def _residual_std(self, a: Image.Image, b: Image.Image) -> float:
        if np is not None:
            arr_a = np.asarray(a, dtype="float32")
            arr_b = np.asarray(b, dtype="float32")
            return float(np.std(arr_a - arr_b))
        # Pure-PIL fallback: use difference image stddev.
        from PIL import ImageChops

        diff = ImageChops.difference(a, b)
        return ImageStat.Stat(diff).stddev[0]

    def _symmetry(self, gray: Image.Image):
        small = gray.resize((128, 128))
        left = small.crop((0, 0, 64, 128))
        right = small.crop((64, 0, 128, 128)).transpose(Image.FLIP_LEFT_RIGHT)
        resid = self._residual_std(left, right)
        # Low residual → high symmetry. Face detection isn't available without
        # cv2/dlib, so we report the whole-frame proxy and flag face_detected=False.
        symmetry = round(1.0 - min(resid / 80.0, 1.0), 3)
        return False, symmetry

    # ── AUDIO ───────────────────────────────────────────────
    def analyze_audio(self, audio_bytes: bytes) -> dict:
        features = self._extract_audio_features(audio_bytes)
        dur = features.get("duration_seconds", "?")
        nc_std = features.get("noise_consistency_std", features.get("noise_consistency", "?"))
        prompt = (
            "You are a deepfake / voice-clone detection expert. Analyze these audio "
            "features extracted by librosa and determine if this could be an "
            "AI-generated or cloned voice:\n"
            f"Duration: {dur}s | Sample rate: {features['sample_rate']} Hz\n"
            f"Spectral flatness: {features['spectral_flatness']} "
            "(voiced speech naturally spans <0.001–0.50; suspicious ONLY if >0.80, "
            "which indicates unnatural noise-like spectrum; low values are NORMAL)\n"
            f"Silence ratio: {features['silence_ratio']} "
            "(real speech 0.10–0.30; <0.05 on clips >10s = no natural pauses, suspicious)\n"
            f"RMS noise std: {nc_std} "
            "(real audio varies; suspicious: std <0.01 = unnaturally flat background)\n"
            f"MFCC variance: {features.get('mfcc_variance', 'N/A')} "
            "(real speech >5.0; suspicious: <0.5 = phonemes too uniform, likely synthetic)\n"
            f"Pitch variance: {features.get('pitch_variance', 'N/A')} "
            "(real voices >100; suspicious: <10 = unnaturally flat prosody)\n"
            "DECISION RULES: "
            "Return LIKELY_FAKE only when 3+ of these are true: spectral_flatness>0.80, "
            "silence_ratio<0.05 on >10s clip, rms_noise_std<0.01, mfcc_variance<0.5, "
            "pitch_variance<10. "
            "Return LIKELY_REAL when mfcc_variance>5.0 AND pitch_variance>100 AND no "
            "other strong indicators. "
            "Otherwise return UNCERTAIN.\n"
            "Provide:\n"
            "1. deepfake_probability: 0.0-1.0\n"
            "2. confidence: 0.0-1.0\n"
            "3. indicators: list of suspicious patterns found\n"
            "4. verdict: LIKELY_REAL | LIKELY_FAKE | UNCERTAIN\n"
            "Respond in JSON only."
        )
        result = self._call_ollama(prompt)
        result["features"] = features
        result["analysis_method"] = (
            "ollama_spectral" if result.get("analysis_method") != "fallback" else "fallback"
        )
        return result

    # ── ANCHOR VOICE CROSS-CHECK ────────────────────────────
    def verify_voice(self, audio_bytes: bytes, media_type: str = "audio") -> dict:
        """Cross-check media against enrolled anchor voices via /api/voice/verify.

        For video, audio is extracted with ffmpeg first. Returns the verify
        payload plus a derived `indicators` list:
          • VOICE_CLONE_RISK   — similarity in the clone-risk band
          • ANCHOR_IMPERSONATION — confirmed same-speaker match
        Degrades to an empty (no-indicator) result on any failure.
        """
        try:
            data, filename, ctype = audio_bytes, "clip.mp3", "audio/mpeg"
            if str(media_type).lower().startswith("video"):
                data = self._extract_audio_wav(audio_bytes)
                filename, ctype = "clip.wav", "audio/wav"
            resp = self._post_voice_verify(data, filename, ctype)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verify_voice failed: %s", exc)
            return {
                "match_found": False,
                "is_clone_risk": False,
                "indicators": [],
                "error": str(exc),
            }

        indicators: List[str] = []
        if resp.get("is_clone_risk"):
            indicators.append("VOICE_CLONE_RISK")
        if resp.get("match_found"):
            indicators.append("ANCHOR_IMPERSONATION")
        resp["indicators"] = indicators
        return resp

    def _resolve_ffmpeg(self) -> str:
        from shutil import which

        ffmpeg = which("ffmpeg")
        if not ffmpeg:
            try:
                import imageio_ffmpeg

                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:  # noqa: BLE001
                ffmpeg = "ffmpeg"
        return ffmpeg

    # ── VIDEO ───────────────────────────────────────────────
    def analyze_video(self, video_bytes: bytes) -> dict:
        """Extract a representative frame via ffmpeg and run image analysis on it.

        There is no dedicated video-deepfake model here — this is a single-frame
        proxy, same tier of signal as the image path. If frame extraction fails
        (corrupt file, no ffmpeg), returns an UNCERTAIN/unavailable result rather
        than silently handing raw video bytes to PIL (which would just raise).
        """
        try:
            frame_bytes = self._extract_video_frame(video_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Video frame extraction failed: %s", exc)
            return {
                "deepfake_probability": None,
                "confidence": 0.0,
                "indicators": [],
                "verdict": "UNCERTAIN",
                "analysis_method": "unavailable",
                "error": str(exc),
            }
        result = self.analyze_image(frame_bytes)
        result["analysis_method"] = f"{result.get('analysis_method', 'ollama_spectral')}_video_frame"
        return result

    def _extract_video_frame(self, video_bytes: bytes) -> bytes:
        """Grab a single JPEG frame ~1s into the clip via ffmpeg."""
        import os
        import subprocess
        import tempfile

        ffmpeg = self._resolve_ffmpeg()
        vid = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        frame_fd, frame_path = tempfile.mkstemp(suffix=".jpg")
        os.close(frame_fd)
        try:
            vid.write(video_bytes)
            vid.flush()
            vid.close()
            proc = subprocess.run(
                [ffmpeg, "-y", "-ss", "1", "-i", vid.name, "-frames:v", "1",
                 "-q:v", "2", frame_path],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0 or not os.path.getsize(frame_path):
                # Retry at t=0 for very short clips where -ss 1 overruns.
                proc = subprocess.run(
                    [ffmpeg, "-y", "-i", vid.name, "-frames:v", "1",
                     "-q:v", "2", frame_path],
                    capture_output=True, text=True, timeout=30,
                )
            if proc.returncode != 0 or not os.path.getsize(frame_path):
                raise RuntimeError(f"ffmpeg failed: {proc.stderr[-300:]}")
            with open(frame_path, "rb") as fh:
                return fh.read()
        finally:
            for p in (vid.name, frame_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    def _extract_audio_wav(self, video_bytes: bytes) -> bytes:
        """Extract mono 22.05k WAV audio from video bytes via ffmpeg."""
        import os
        import subprocess
        import tempfile

        ffmpeg = self._resolve_ffmpeg()
        vid = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(wav_fd)
        try:
            vid.write(video_bytes)
            vid.flush()
            vid.close()
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", vid.name, "-vn", "-acodec", "pcm_s16le",
                 "-ar", "22050", "-ac", "1", wav_path],
                capture_output=True, text=True, timeout=120,
            )
            if proc.returncode != 0 or not os.path.getsize(wav_path):
                raise RuntimeError(f"ffmpeg failed: {proc.stderr[-300:]}")
            with open(wav_path, "rb") as fh:
                return fh.read()
        finally:
            for p in (vid.name, wav_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    def _post_voice_verify(self, data: bytes, filename: str, ctype: str) -> dict:
        import uuid

        boundary = f"----sentinel{uuid.uuid4().hex}"
        pre = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        post = f"\r\n--{boundary}--\r\n".encode()
        body = pre + data + post
        req = urllib.request.Request(
            VOICE_VERIFY_URL,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
            return json.loads(resp.read().decode())

    def _extract_audio_features(self, audio_bytes: bytes) -> dict:
        # ① Try librosa directly (available in agents/.venv / Python 3.11).
        try:
            import librosa  # type: ignore
            import numpy as _np

            y, sr = librosa.load(io.BytesIO(audio_bytes), sr=None, mono=True)
            flatness = float(librosa.feature.spectral_flatness(y=y).mean())
            rms = librosa.feature.rms(y=y)[0]
            silence_ratio = float((rms < 0.01).mean())
            mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
            mfcc_variance = float(_np.var(mfcc))
            try:
                f0 = librosa.yin(y, fmin=65, fmax=400, sr=sr)
                finite_f0 = f0[_np.isfinite(f0)]
                pitch_variance = float(_np.var(finite_f0)) if len(finite_f0) > 0 else 0.0
            except Exception:  # noqa: BLE001
                pitch_variance = 0.0
            return {
                "sample_rate": int(sr),
                "duration_seconds": round(float(len(y) / sr), 3),
                "spectral_flatness": round(flatness, 4),
                "silence_ratio": round(silence_ratio, 3),
                "noise_consistency_std": round(float(rms.std()), 6),
                "mfcc_variance": round(mfcc_variance, 4),
                "pitch_variance": round(pitch_variance, 4),
                "extractor": "librosa",
            }
        except Exception:  # noqa: BLE001
            pass

        # ② Stdlib wave (PCM WAV only).
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                sr = wf.getframerate()
                n = wf.getnframes()
                sampwidth = wf.getsampwidth()
                nchannels = wf.getnchannels()
                raw = wf.readframes(n)
            samples = self._decode_pcm(raw, sampwidth)
            if nchannels > 1:
                samples = samples[::nchannels]
            return self._audio_stats_from_samples(samples, sr)
        except Exception as exc:  # noqa: BLE001
            logger.info("stdlib wave fallback failed (%s) — trying agents venv", exc)

        # ③ Cross-venv subprocess: agents/.venv has librosa + Python 3.11.
        return self._extract_audio_features_subprocess(audio_bytes)

    def _extract_audio_features_subprocess(self, audio_bytes: bytes) -> dict:
        """Extract features via agents/.venv when the current venv lacks librosa."""
        import os
        import subprocess
        import tempfile
        from pathlib import Path as _Path

        agents_python = _Path(__file__).resolve().parents[2] / "agents" / ".venv" / "bin" / "python"
        script = _Path(__file__).resolve().parent / "audio_features.py"

        tmp = tempfile.NamedTemporaryFile(suffix=".audio", delete=False)
        try:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp.close()
            proc = subprocess.run(
                [str(agents_python), str(script), tmp.name],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(_Path(__file__).resolve().parents[2]),
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "audio_features script failed")
            stdout = proc.stdout.strip()
            last_line = stdout.splitlines()[-1] if stdout else ""
            data = json.loads(last_line)
            if "error" in data:
                raise RuntimeError(data["error"])
            return data
        finally:
            if os.path.exists(tmp.name):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

    def _decode_pcm(self, raw: bytes, sampwidth: int) -> List[int]:
        if sampwidth == 2:
            count = len(raw) // 2
            return list(struct.unpack("<" + "h" * count, raw[: count * 2]))
        if sampwidth == 1:
            return [b - 128 for b in raw]
        # 24/32-bit: sample every `sampwidth` bytes as a coarse proxy.
        return [raw[i] - 128 for i in range(0, len(raw), sampwidth)]

    def _audio_stats_from_samples(self, samples: List[int], sr: int) -> dict:
        if not samples:
            raise ValueError("no samples decoded")
        peak = max(1, max(abs(s) for s in samples))
        norm = [s / peak for s in samples]
        silence = sum(1 for s in norm if abs(s) < 0.02) / len(norm)

        # Windowed RMS energy for background-noise consistency.
        win = max(1, sr // 20) if sr else 1024
        energies = []
        for i in range(0, len(norm), win):
            chunk = norm[i : i + win]
            if chunk:
                energies.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
        if energies:
            mean_e = sum(energies) / len(energies)
            var_e = sum((e - mean_e) ** 2 for e in energies) / len(energies)
            noise_consistency = round(1.0 - min((var_e ** 0.5) / (mean_e + 1e-6), 1.0), 3)
        else:
            noise_consistency = 0.5

        flatness = self._spectral_flatness(norm)
        return {
            "sample_rate": int(sr),
            "sample_rate_consistent": True,
            "spectral_flatness": round(flatness, 4),
            "silence_ratio": round(silence, 3),
            "noise_consistency": noise_consistency,
            "extractor": "wave",
        }

    def _spectral_flatness(self, norm: List[float]) -> float:
        if np is None or len(norm) < 8:
            return 0.5
        arr = np.asarray(norm, dtype="float64")
        spectrum = np.abs(np.fft.rfft(arr)) + 1e-10
        geo_mean = np.exp(np.mean(np.log(spectrum)))
        arith_mean = np.mean(spectrum)
        return float(geo_mean / arith_mean)

    def _byte_entropy(self, data: bytes) -> float:
        if not data:
            return 0.0
        counts = [0] * 256
        for b in data[:100000]:
            counts[b] += 1
        total = sum(counts)
        ent = 0.0
        for c in counts:
            if c:
                p = c / total
                ent -= p * math.log2(p)
        return ent / 8.0  # normalize 0-1

    # ── TEXT CLAIM ──────────────────────────────────────────
    def analyze_text_claim(self, text: str, source: str = "") -> dict:
        prompt = (
            "You are a deepfake detection expert assessing a text claim that some "
            "media (video/audio/image) is AI-generated or manipulated. You cannot see "
            "the media — reason about the plausibility of the deepfake claim from the "
            "description, wording, and source.\n"
            f"Claim: {text}\n"
            f"Source: {source or 'unknown'}\n"
            "Consider: does the described content match known deepfake patterns "
            "(cloned anchor voice, fabricated announcements, unofficial distribution "
            "channels like anonymous Telegram)? Provide:\n"
            "1. deepfake_probability: 0.0-1.0 (likelihood the described media is a deepfake)\n"
            "2. confidence: 0.0-1.0\n"
            "3. indicators: list of suspicious patterns found\n"
            "4. verdict: LIKELY_REAL | LIKELY_FAKE | UNCERTAIN\n"
            "Respond in JSON only."
        )
        result = self._call_ollama(prompt)
        if result.get("analysis_method") != "fallback":
            result["analysis_method"] = "ollama_text"
        return result

    # ── OLLAMA ──────────────────────────────────────────────
    def _call_ollama(self, prompt: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        try:
            req = urllib.request.Request(
                self.ollama_url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                body = json.loads(resp.read().decode())
            content = (body.get("message") or {}).get("content", "")
            parsed = self._extract_json(content)
            if parsed is None:
                raise ValueError(f"no JSON in model response: {content[:200]!r}")
            return self._normalize(parsed)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Ollama deepfake call failed: %s", exc)
            return {
                "deepfake_probability": 0.5,
                "confidence": 0.3,
                "indicators": [],
                "verdict": "UNCERTAIN",
                "analysis_method": "fallback",
            }

    def _extract_json(self, content: str) -> Optional[dict]:
        content = (content or "").strip()
        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None

    def _normalize(self, parsed: dict) -> dict:
        prob = _clamp01(parsed.get("deepfake_probability"), 0.5)
        conf = _clamp01(parsed.get("confidence"), 0.3)

        indicators = parsed.get("indicators", [])
        if isinstance(indicators, str):
            indicators = [indicators]
        elif not isinstance(indicators, list):
            indicators = []
        indicators = [str(i) for i in indicators][:12]

        verdict = str(parsed.get("verdict", "")).upper().replace(" ", "_")
        if verdict not in _VERDICTS:
            verdict = (
                "LIKELY_FAKE" if prob >= _FAKE_THRESHOLD
                else "LIKELY_REAL" if prob <= 0.3
                else "UNCERTAIN"
            )
        return {
            "deepfake_probability": prob,
            "confidence": conf,
            "indicators": indicators,
            "verdict": verdict,
            "analysis_method": "ollama_spectral",
        }


if __name__ == "__main__":
    det = DeepfakeDetector()
    out = det.analyze_text_claim(
        "This audio clip of a Example News anchor announcing election results sounds AI generated",
        source="https://t.me/tamil_news_unofficial",
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
