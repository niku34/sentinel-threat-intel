"""Anchor voice fingerprinting engine.

Extracts a compact acoustic fingerprint (MFCC + prosody stats) from an audio or
video file and compares fingerprints to decide same-speaker vs. clone-risk.

Import note
-----------
librosa (→ numba/llvmlite) only installs on the agents venv (Python 3.11); the
API venv is Python 3.14 where numba has no wheel. So librosa is imported *lazily*
inside the extract methods — the module itself imports fine under both venvs, and
the API process can still use `compare()` / (de)serialisation, which need only
numpy. Actual feature extraction is delegated to the agents venv via the CLI at
the bottom of this file (`python -m agents.voice.fingerprinter extract <path>`).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Optional

try:  # numpy is present in BOTH venvs.
    import numpy as np
except Exception:  # noqa: BLE001
    np = None  # type: ignore

SAMPLE_RATE = 22050
SAME_SPEAKER_THRESHOLD = 0.85
CLONE_RISK_LOW = 0.60

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".opus", ".webm"}
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def _ffmpeg_exe() -> str:
    """Return an ffmpeg executable, falling back to the pip static binary."""
    from shutil import which

    exe = which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


class VoiceFingerprinter:
    # ── extraction ─────────────────────────────────────────
    def extract_fingerprint(
        self,
        audio_bytes: Optional[bytes] = None,
        audio_path: Optional[str] = None,
    ) -> dict:
        """Extract an acoustic fingerprint from audio bytes or a file path."""
        import librosa  # lazy — heavy dep, agents venv only

        tmp_path = None
        try:
            if audio_path is None:
                if audio_bytes is None:
                    raise ValueError("Provide audio_bytes or audio_path")
                tmp = tempfile.NamedTemporaryFile(suffix=".audio", delete=False)
                tmp.write(audio_bytes)
                tmp.flush()
                tmp.close()
                audio_path = tmp_path = tmp.name

            y, sr = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)
            if y.size == 0:
                raise ValueError("Decoded audio is empty")

            duration = float(len(y) / sr)

            # MFCC — 13 coefficients, mean + std per coefficient (26 values).
            mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
            mfcc_mean = mfcc.mean(axis=1)
            mfcc_std = mfcc.std(axis=1)

            # Pitch via YIN (fmin/fmax bracket the human vocal range).
            try:
                f0 = librosa.yin(y, fmin=65, fmax=400, sr=sr)
                f0 = f0[np.isfinite(f0)] if np is not None else f0
            except Exception:  # noqa: BLE001
                f0 = np.array([]) if np is not None else []
            if len(f0) > 0:
                pitch = {
                    "mean": float(np.mean(f0)),
                    "std": float(np.std(f0)),
                    "min": float(np.min(f0)),
                    "max": float(np.max(f0)),
                }
            else:
                pitch = {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}

            zcr = librosa.feature.zero_crossing_rate(y)[0]
            centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
            rms = librosa.feature.rms(y=y)[0]

            return {
                "version": 1,
                "sample_rate": int(sr),
                "duration_seconds": round(duration, 3),
                "mfcc_mean": [round(float(x), 6) for x in mfcc_mean],
                "mfcc_std": [round(float(x), 6) for x in mfcc_std],
                "pitch": {k: round(v, 4) for k, v in pitch.items()},
                "zcr": {
                    "mean": round(float(zcr.mean()), 6),
                    "std": round(float(zcr.std()), 6),
                },
                "spectral_centroid_mean": round(float(centroid.mean()), 4),
                "rms_mean": round(float(rms.mean()), 6),
            }
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def extract_from_video(
        self,
        video_bytes: Optional[bytes] = None,
        video_path: Optional[str] = None,
    ) -> dict:
        """Extract audio from a video with ffmpeg, then fingerprint it."""
        vid_tmp = None
        wav_tmp = None
        try:
            if video_path is None:
                if video_bytes is None:
                    raise ValueError("Provide video_bytes or video_path")
                vt = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                vt.write(video_bytes)
                vt.flush()
                vt.close()
                video_path = vid_tmp = vt.name

            wav_fd, wav_tmp = tempfile.mkstemp(suffix=".wav")
            os.close(wav_fd)

            cmd = [
                _ffmpeg_exe(), "-y", "-i", video_path,
                "-vn", "-acodec", "pcm_s16le",
                "-ar", str(SAMPLE_RATE), "-ac", "1",
                wav_tmp,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            if proc.returncode != 0 or not os.path.getsize(wav_tmp):
                raise RuntimeError(
                    f"ffmpeg audio extraction failed: {proc.stderr[-400:]}"
                )
            fp = self.extract_fingerprint(audio_path=wav_tmp)
            fp["source_kind"] = "video"
            return fp
        finally:
            for p in (vid_tmp, wav_tmp):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    # ── comparison (numpy only) ────────────────────────────
    def _mfcc_vector(self, fp: dict):
        vec = list(fp.get("mfcc_mean", [])) + list(fp.get("mfcc_std", []))
        if np is None:
            return vec
        return np.asarray(vec, dtype="float64")

    def compare(self, fp1: dict, fp2: dict) -> float:
        """Cosine similarity over the 26-value MFCC vector, mapped to 0.0–1.0."""
        v1 = self._mfcc_vector(fp1)
        v2 = self._mfcc_vector(fp2)
        if np is None:
            # Pure-python fallback.
            if len(v1) != len(v2) or not v1:
                return 0.0
            dot = sum(a * b for a, b in zip(v1, v2))
            n1 = sum(a * a for a in v1) ** 0.5
            n2 = sum(b * b for b in v2) ** 0.5
            if n1 == 0 or n2 == 0:
                return 0.0
            cos = dot / (n1 * n2)
        else:
            if v1.shape != v2.shape or v1.size == 0:
                return 0.0
            n1 = np.linalg.norm(v1)
            n2 = np.linalg.norm(v2)
            if n1 == 0 or n2 == 0:
                return 0.0
            cos = float(np.dot(v1, v2) / (n1 * n2))
        # Clamp negative correlation to 0 and cap at 1.
        return round(max(0.0, min(1.0, cos)), 4)

    def is_same_speaker(self, fp1: dict, fp2: dict) -> bool:
        return self.compare(fp1, fp2) >= SAME_SPEAKER_THRESHOLD

    def is_clone_risk(self, fp1: dict, fp2: dict) -> bool:
        sim = self.compare(fp1, fp2)
        return CLONE_RISK_LOW <= sim < SAME_SPEAKER_THRESHOLD

    # ── serialisation ──────────────────────────────────────
    def fingerprint_to_string(self, fp: dict) -> str:
        return json.dumps(fp, ensure_ascii=False)

    def string_to_fingerprint(self, s: str) -> dict:
        return json.loads(s)


# ── CLI: extraction bridge for the API venv ────────────────
# Usage:  python -m agents.voice.fingerprinter extract       <audio_path>
#         python -m agents.voice.fingerprinter extract-video <video_path>
# Prints the fingerprint dict as JSON on stdout.
if __name__ == "__main__":
    import sys

    fp = VoiceFingerprinter()
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: <extract|extract-video> <path>"}))
        sys.exit(2)
    mode, path = sys.argv[1], sys.argv[2]
    try:
        if mode == "extract-video":
            result = fp.extract_from_video(video_path=path)
        else:
            result = fp.extract_fingerprint(audio_path=path)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
