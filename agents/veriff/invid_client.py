"""InVID-style video verification client for Veriff.

⚠ REVERSE-VIDEO SEARCH IS NON-FUNCTIONAL — DO NOT ADVERTISE IT.

``BASE_URL`` points at ``api.invid-project.eu``, which **does not resolve**
(NXDOMAIN). The InVID project's own site is up, but it exposes no such public
API host. Every call to ``reverse_video_search`` has therefore always returned
an empty list, and because the method deliberately makes "no matches" and
"unreachable" indistinguishable to the verdict engine, a permanently dead
integration reads downstream as a clean result.

That fail-closed behaviour is correct and is kept — it is the *advertising*
that was wrong. The "InVID Connector" plugin has been removed from the
marketplace and the Veriff page no longer lists InVID or TinEye (there has
never been any TinEye code at all). Do not re-list either until a working
endpoint exists and is verified.

``check_video_url`` and ``get_keyframes`` use yt-dlp and DO work; they are the
only usable parts of this module.

InVID (invid-project.eu) is a journalists' toolkit for video verification:
keyframe extraction + reverse image search of those frames. Every method fails
*gracefully* (returns an empty list / a well-shaped default) and never raises
into the pipeline.

Keyframe extraction uses yt-dlp (already a project dependency) to pull a
video's thumbnail set — those thumbnail URLs are what we'd reverse-search
frame by frame.

Public surface:
    reverse_video_search(video_url) -> list[dict]
    check_video_url(url)            -> dict
    get_keyframes(video_url)        -> list[str]
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import List, Optional

logger = logging.getLogger("veriff.invid_client")

TIMEOUT = 10.0
USER_AGENT = "Sentinel/1.0 Veriff-Tamil InVID-Client"


class InVIDClient:
    BASE_URL = "https://api.invid-project.eu/api"

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = base_url or self.BASE_URL
        self.api_key = api_key

    # ── reverse video search ──────────────────────────────────
    def reverse_video_search(self, video_url: str) -> List[dict]:
        """Search each keyframe of a video for earlier appearances online.

        Attempts the InVID API first; on any failure returns []. This lets the
        verdict engine treat "no matches" and "InVID unreachable" identically.
        """
        if not video_url:
            return []

        api_results = self._call_invid(video_url)
        if api_results is not None:
            return api_results

        # InVID API unavailable — fail closed with an empty match set.
        # (Keyframes are still extractable via get_keyframes for callers that
        #  want to run their own reverse-image search.)
        return []

    def _call_invid(self, video_url: str) -> Optional[List[dict]]:
        endpoint = f"{self.base_url}/reverse-video-search"
        payload = json.dumps({"video_url": video_url}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
                data = json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            logger.info("InVID API unreachable (%s) — returning no matches", exc)
            return None

        matches: List[dict] = []
        for item in (data.get("results") or data.get("matches") or []):
            matches.append(
                {
                    "source_url": item.get("url") or item.get("source_url", ""),
                    "title": item.get("title", ""),
                    "match_type": item.get("match_type", "keyframe"),
                    "date": item.get("date") or item.get("published", ""),
                }
            )
        return matches

    # ── URL metadata ──────────────────────────────────────────
    def check_video_url(self, url: str) -> dict:
        """Return existence + basic metadata for a video URL via yt-dlp."""
        default = {"exists": False, "title": "", "upload_date": "", "view_count": 0}
        if not url:
            return default
        try:
            import yt_dlp
        except Exception as exc:  # noqa: BLE001
            logger.info("yt-dlp unavailable (%s) — cannot check video url", exc)
            return default

        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": TIMEOUT}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            logger.info("check_video_url failed for %s: %s", url, exc)
            return default
        if not info:
            return default
        return {
            "exists": True,
            "title": info.get("title", "") or "",
            "upload_date": info.get("upload_date", "") or "",
            "view_count": int(info.get("view_count") or 0),
        }

    # ── keyframes ─────────────────────────────────────────────
    def get_keyframes(self, video_url: str) -> List[str]:
        """Return candidate keyframe/thumbnail image URLs for reverse search."""
        if not video_url:
            return []
        try:
            import yt_dlp
        except Exception as exc:  # noqa: BLE001
            logger.info("yt-dlp unavailable (%s) — no keyframes", exc)
            return []

        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": TIMEOUT}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
        except Exception as exc:  # noqa: BLE001
            logger.info("get_keyframes failed for %s: %s", video_url, exc)
            return []
        if not info:
            return []

        urls: List[str] = []
        for thumb in info.get("thumbnails") or []:
            u = thumb.get("url")
            if u:
                urls.append(u)
        # Dedupe, preserve order.
        seen: set = set()
        out: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = InVIDClient()
    print("reverse:", client.reverse_video_search("https://example.com/fake.mp4"))
    print("check:", client.check_video_url("https://example.com/fake.mp4"))
