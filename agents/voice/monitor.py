"""Anchor voice monitoring loop.

Downloads audio from public URLs / Telegram channels, fingerprints it, and
compares against enrolled anchor profiles. Matches (impersonation) and
clone-risk hits are logged as DEEPFAKE_AUDIO threats.

Runs in the agents venv (librosa + yt-dlp). Invoked by the API's
POST /api/voice/monitor/sweep via `python -m agents.voice.monitor sweep`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from shutil import which

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from agents.voice.fingerprinter import VoiceFingerprinter  # noqa: E402

logger = logging.getLogger("voice.monitor")

DEFAULT_CHANNELS = ["examplebroadcaster", "examplebroadcaster", "brand_news", "examplenews"]
SAME_SPEAKER_THRESHOLD = 0.85
CLONE_RISK_LOW = 0.60
_MEDIA_RE = re.compile(r"https?://[^\s\"'<>]+\.(?:mp3|mp4|ogg|oga|wav|m4a|webm)", re.IGNORECASE)
_YT_RE = re.compile(r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+")
_MAX_CLIPS_PER_CHANNEL = 5
# Hard cap on a single yt-dlp download attempt. yt-dlp has no timeout of its
# own, so an unresponsive/huge video previously blocked the sweep for up to
# the caller's 120s subprocess ceiling — one slow clip could consume the
# entire scan budget. Skip and move on instead.
_YTDLP_TIMEOUT_SECONDS = 18


def _engine():
    from api.models.channel import engine

    return engine


def _ffmpeg_ok() -> bool:
    return which("ffmpeg") is not None or _static_ffmpeg() is not None


def _static_ffmpeg():
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


class VoiceMonitor:
    def __init__(self) -> None:
        self.vf = VoiceFingerprinter()

    # ── profile loading ────────────────────────────────────
    def _load_profiles(self) -> list[dict]:
        try:
            with Session(_engine()) as db:
                rows = db.execute(
                    text(
                        "SELECT anchor_name, channel, fingerprint_data "
                        "FROM anchor_voice_profiles"
                    )
                ).fetchall()
            out = []
            for name, chan, fp_str in rows:
                try:
                    out.append({
                        "anchor_name": name,
                        "channel": chan,
                        "fp": self.vf.string_to_fingerprint(fp_str),
                    })
                except Exception:  # noqa: BLE001
                    continue
            return out
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load voice profiles: %s", exc)
            return []

    # ── download ───────────────────────────────────────────
    def _download_audio(self, url: str) -> tuple[bytes | None, str]:
        """Return (audio_bytes, kind) — kind in {audio, video}."""
        if _YT_RE.search(url) or "youtube.com" in url or "youtu.be" in url:
            return self._download_youtube(url), "audio"
        # Direct media URL.
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Sentinel/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                data = resp.read(20 * 1024 * 1024)  # cap 20MB
            kind = "video" if url.lower().rsplit(".", 1)[-1] in ("mp4", "webm", "mkv", "mov") else "audio"
            return data, kind
        except Exception as exc:  # noqa: BLE001
            logger.info("Direct download failed for %s: %s", url, exc)
            return None, "audio"

    def _download_youtube(self, url: str) -> bytes | None:
        tmp = tempfile.NamedTemporaryFile(suffix=".m4a", delete=False)
        tmp.close()
        env = dict(os.environ)
        env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"
        ytdlp = which("yt-dlp") or str(Path(sys.executable).parent / "yt-dlp")
        try:
            proc = subprocess.run(
                [ytdlp, "-f", "bestaudio", "--no-playlist",
                 "-o", tmp.name, "--force-overwrites", url],
                cwd=str(PROJECT_ROOT), env=env,
                capture_output=True, text=True, timeout=_YTDLP_TIMEOUT_SECONDS,
            )
            if proc.returncode != 0 or not os.path.getsize(tmp.name):
                logger.info("yt-dlp failed for %s: %s", url, proc.stderr[-200:])
                return None
            with open(tmp.name, "rb") as fh:
                return fh.read()
        except subprocess.TimeoutExpired:
            logger.info(
                "skipped clip %s: download exceeded %ds timeout",
                url, _YTDLP_TIMEOUT_SECONDS,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.info("yt-dlp error for %s: %s", url, exc)
            return None
        finally:
            if os.path.exists(tmp.name):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass

    # ── core check ─────────────────────────────────────────
    def check_audio_url(self, url: str, profiles: list[dict] | None = None) -> dict:
        if profiles is None:
            profiles = self._load_profiles()

        result = {
            "url": url,
            "match_found": False,
            "matched_anchor": None,
            "similarity": 0.0,
            "is_clone_risk": False,
            "recommendation": "CLEAR",
        }

        data, kind = self._download_audio(url)
        if not data:
            result["recommendation"] = "SKIPPED — could not download media"
            return result

        try:
            if kind == "video":
                query_fp = self.vf.extract_from_video(video_bytes=data)
            else:
                query_fp = self.vf.extract_fingerprint(audio_bytes=data)
        except Exception as exc:  # noqa: BLE001
            logger.info("Fingerprint failed for %s: %s", url, exc)
            result["recommendation"] = "SKIPPED — fingerprint failed"
            return result

        best = None
        for p in profiles:
            sim = self.vf.compare(query_fp, p["fp"])
            if best is None or sim > best[1]:
                best = (p["anchor_name"], sim)

        if best:
            name, sim = best
            result["similarity"] = sim
            if sim >= SAME_SPEAKER_THRESHOLD:
                result["match_found"] = True
                result["matched_anchor"] = name
                result["recommendation"] = "TAKEDOWN — anchor voice impersonation confirmed"
            elif sim >= CLONE_RISK_LOW:
                result["is_clone_risk"] = True
                result["matched_anchor"] = name
                result["recommendation"] = "REVIEW — possible AI voice clone"
        return result

    # ── telegram scrape ────────────────────────────────────
    def scan_telegram_channel(self, channel_slug: str, profiles: list[dict] | None = None) -> list[dict]:
        if profiles is None:
            profiles = self._load_profiles()
        url = f"https://t.me/s/{channel_slug}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Sentinel"})
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
                html = resp.read().decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            logger.info("Telegram scrape failed for %s: %s", channel_slug, exc)
            return []

        links = list(dict.fromkeys(_MEDIA_RE.findall(html) + _YT_RE.findall(html)))
        results = []
        for link in links[:_MAX_CLIPS_PER_CHANNEL]:
            clip_started = time.monotonic()
            results.append(self.check_audio_url(link, profiles))
            logger.info(
                "TIMING: clip %s took %.1fs", link, time.monotonic() - clip_started
            )
        return results

    # ── sweep ──────────────────────────────────────────────
    def run_monitoring_sweep(self, channels: list[str] | None = None) -> dict:
        channels = channels or DEFAULT_CHANNELS
        profiles = self._load_profiles()
        all_results: list[dict] = []
        for slug in channels:
            channel_started = time.monotonic()
            for r in self.scan_telegram_channel(slug, profiles):
                r["channel_scanned"] = slug
                all_results.append(r)
            logger.info(
                "TIMING: voice channel %s took %.1fs", slug, time.monotonic() - channel_started
            )

        threats_found = 0
        for r in all_results:
            if r.get("match_found") or r.get("is_clone_risk"):
                if self._create_threat(r):
                    threats_found += 1

        return {
            "channels_scanned": len(channels),
            "clips_checked": len(all_results),
            "threats_found": threats_found,
            "results": all_results,
        }

    def _create_threat(self, result: dict) -> bool:
        anchor = result.get("matched_anchor") or "an enrolled anchor"
        sim = result.get("similarity", 0.0)
        confirmed = result.get("match_found")
        severity = "CRITICAL" if confirmed else "HIGH"
        kind_label = "confirmed impersonation" if confirmed else "possible voice clone"
        description = (
            f"Voice monitoring flagged audio at {result.get('url')} as a {kind_label} of "
            f"{anchor} (voice similarity {sim:.2f}). Recommendation: "
            f"{result.get('recommendation')}."
        )
        title = f"Deepfake audio impersonating {anchor}"
        try:
            with Session(_engine()) as db:
                db.execute(
                    text(
                        "INSERT INTO threats "
                        "(threat_type, severity, title, description, source_platform, source_url, status, detected_at) "
                        "VALUES ('DEEPFAKE_AUDIO', :sev, :title, :descr, 'telegram', :url, 'OPEN', :ts)"
                    ),
                    {
                        "sev": severity,
                        "title": title,
                        "descr": description,
                        "url": result.get("url"),
                        "ts": datetime.now(timezone.utc),
                    },
                )
                db.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist DEEPFAKE_AUDIO threat: %s", exc)
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    mode = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    monitor = VoiceMonitor()
    if mode == "sweep":
        chans = sys.argv[2].split(",") if len(sys.argv) > 2 else None
        print(json.dumps(monitor.run_monitoring_sweep(chans), ensure_ascii=False))
    elif mode == "check" and len(sys.argv) > 2:
        print(json.dumps(monitor.check_audio_url(sys.argv[2]), ensure_ascii=False))
    else:
        print(json.dumps({"error": "usage: sweep [chan,chan] | check <url>"}))
