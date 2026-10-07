"""Digital Kavasam — YouTube channel health client.

Two data paths feed the same channel_snapshots table:

  • Primary  — YouTube Data API v3 (when an API key is configured)
  • Fallback — public YouTube page scraping (always available, no key needed)

`get_channel_stats` prefers the API and falls back to scraping on any failure,
tagging each result with `data_source`. `scan_all_channels` compares each channel
against its previous snapshot, persists a new snapshot, and — on a CRITICAL
anomaly (e.g. mass video deletion) — triggers a CERT-In *draft* (never auto-filed;
a human reviews and files it manually).

Runs in agents/.venv (Python 3.11): requests + beautifulsoup4 + googleapiclient
+ sqlalchemy. Every network call degrades gracefully so a scan never hard-fails.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("youtube_client")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}

# Public channel URLs for the scrape fallback, keyed by normalised channel name.
# Only covers channels that stay on a real channel_id (API-first) — their
# scrape fallback (used only if the API call fails) hits the same channel by
# ID, so name-keyed entries here must not collide with a *different* channel
# that happens to share the display name (e.g. the Spiritual sub-channel is
# named "Example Broadcaster" too — it has no entry here and falls through to its own
# /channel/{id} URL below).
PUBLIC_URLS = {
    "broadcaster plus": "https://www.youtube.com/@ExampleNews",
    "broadcaster plus news": "https://www.youtube.com/@ExampleNews",
}

# Channels with no reliable YouTube Data API channel_id — tracked via public
# handle instead, scraped directly (verified reachable @ time of writing).
CHANNEL_PUBLIC_HANDLES = {
    "brand_main": "@ExampleBroadcaster",
    "brand_entertainment": "@ExampleEntertainment",
    "brand_movies": "@ExampleMovies",
}

# Placeholder values that mean "no real channel_id configured yet".
_INVALID_CHANNEL_IDS = {"", "PASTE_ID_HERE"}

_SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

# ── Video-count anomaly thresholds ─────────────────────────
# Relative to library size with an absolute floor, so the same rule works for a
# 400-video channel and a 213,000-video one. Tunable via env for site-specific
# upload cadence.
#   mass delete   — >=2% of the library or >=25 videos, whichever is larger
#   partial delete— >=0.5% or >=10 videos  (unlisting / targeted takedown)
#   bulk upload   — >=2% or >=100 videos   (possible compromised-account spam)
# Calibrated against observed real deltas: Example News +373 on a 212,632 library
# and Example Broadcaster +91 on 19,969 are routine and must not fire; a 5,000-video dump
# on the same large channel must.
VIDEO_DROP_PCT = float(os.getenv("KAVASAM_VIDEO_DROP_PCT", "0.02"))
VIDEO_DROP_ABS_FLOOR = int(os.getenv("KAVASAM_VIDEO_DROP_ABS", "25"))
VIDEO_DROP_PARTIAL_PCT = float(os.getenv("KAVASAM_VIDEO_DROP_PARTIAL_PCT", "0.005"))
VIDEO_DROP_PARTIAL_ABS_FLOOR = int(os.getenv("KAVASAM_VIDEO_DROP_PARTIAL_ABS", "10"))
VIDEO_SPIKE_PCT = float(os.getenv("KAVASAM_VIDEO_SPIKE_PCT", "0.02"))
VIDEO_SPIKE_ABS_FLOOR = int(os.getenv("KAVASAM_VIDEO_SPIKE_ABS", "100"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_channel_id(channel_id: str | None) -> bool:
    return (channel_id or "").strip() not in _INVALID_CHANNEL_IDS


def is_configured_channel(channel_id: str | None) -> bool:
    """Public alias — a channel row is only scannable once a real ID is set.

    Rows still holding a setup placeholder (``PASTE_ID_HERE``, empty) must be
    skipped entirely rather than scanned. Scanning them falls through to the
    public-URL scrape, which resolves to whatever page the channel *name*
    happens to match and stores the result as if it were that channel's real
    metrics — producing fictitious subscriber/video counts that then feed
    anomaly detection and CERT-In drafting.
    """
    return _is_valid_channel_id(channel_id)


def public_url_for(name: str, channel_id: str) -> str:
    return PUBLIC_URLS.get((name or "").strip().lower()) or (
        f"https://www.youtube.com/channel/{channel_id}"
    )


def handle_urls_for(handle: str) -> list[str]:
    """Try the canonical handle page, then /about and /videos as fallbacks —
    YouTube occasionally serves a thin/empty shell on one path but not another.
    """
    base = f"https://www.youtube.com/{handle}"
    return [base, f"{base}/about", f"{base}/videos"]


def _parse_count(text: str) -> int | None:
    """Turn '4.21M subscribers' / '1,240' / '890K' / '440 thousand' into an int."""
    if not text:
        return None
    m = re.search(r"([\d,.]+)\s*(thousand|million|billion|[KMB])?", text.strip(), re.IGNORECASE)
    if not m:
        return None
    num = m.group(1).replace(",", "")
    try:
        value = float(num)
    except ValueError:
        return None
    mult = {
        "k": 1_000, "thousand": 1_000,
        "m": 1_000_000, "million": 1_000_000,
        "b": 1_000_000_000, "billion": 1_000_000_000,
    }.get((m.group(2) or "").lower(), 1)
    return int(value * mult)


class YouTubeClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or None
        self.use_api = bool(self.api_key)

    # ── API path ───────────────────────────────────────────
    def get_channel_stats_api(self, channel_id: str) -> dict:
        from googleapiclient.discovery import build

        youtube = build("youtube", "v3", developerKey=self.api_key, cache_discovery=False)
        resp = (
            youtube.channels()
            .list(part="snippet,statistics,status", id=channel_id)
            .execute()
        )
        items = resp.get("items", [])
        if not items:
            return {"channel_id": channel_id, "error": "Channel not found"}

        item = items[0]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        status = item.get("status", {})
        thumbs = snippet.get("thumbnails", {})
        thumb = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}

        return {
            "channel_id": item.get("id", channel_id),
            "name": snippet.get("title"),
            "subscriber_count": int(stats["subscriberCount"]) if "subscriberCount" in stats else None,
            "video_count": int(stats["videoCount"]) if "videoCount" in stats else None,
            "view_count": int(stats["viewCount"]) if "viewCount" in stats else None,
            "status": status.get("privacyStatus", "public"),
            "thumbnail_url": thumb.get("url"),
            "last_updated": _now_iso(),
            "source": "api",
        }

    # ── scrape path ────────────────────────────────────────
    def get_channel_stats_scrape(self, channel_urls: str | list[str], channel_name: str) -> dict:
        urls = [channel_urls] if isinstance(channel_urls, str) else list(channel_urls)
        best: dict | None = None
        last_error: str | None = None

        for url in urls:
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=8)
                resp.raise_for_status()
                html = resp.text
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("Scrape failed for %s: %s", url, exc)
                continue

            name = self._extract_name(html) or channel_name
            subs = self._extract_subscribers(html)
            videos = self._extract_video_count(html)
            result = {
                "channel_name": name,
                "name": name,
                "subscriber_count": subs,
                "video_count": videos,
                "status": "OK",
                "source": "scrape",
                "last_updated": _now_iso(),
            }
            if subs is not None:
                return result
            best = best or result

        if best is not None:
            return best
        return {
            "channel_name": channel_name,
            "subscriber_count": None,
            "video_count": None,
            "status": "OK",
            "source": "scrape",
            "last_updated": _now_iso(),
            "error": last_error,
        }

    @staticmethod
    def _extract_name(html: str) -> str | None:
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
        if m:
            return m.group(1)
        m = re.search(r"<title>([^<]+)</title>", html)
        return m.group(1).replace(" - YouTube", "").strip() if m else None

    @staticmethod
    def _extract_subscribers(html: str) -> int | None:
        # Method 1: ytInitialData subscriberCountText (legacy simpleText renderer).
        m = re.search(r'"subscriberCountText":\{[^}]*"simpleText":"([^"]+)"', html)
        if not m:
            # Method 2: newer renderer exposes only an accessibility label,
            # e.g. {"subscriberCountText":{"accessibility":{"accessibilityData":
            #        {"label":"440 thousand subscribers"}}}}
            m = re.search(
                r'"subscriberCountText":\{"accessibility":\{"accessibilityData":'
                r'\{"label":"([^"]+)"',
                html,
            )
        if not m:
            m = re.search(r'"metadataParts":.*?"([\d.,]+[KMB]?)\s+subscribers?"', html)
        # Deliberately no unscoped "N subscribers anywhere on the page" fallback:
        # channel pages embed related-channel shelves with their own subscriber
        # counts, and a blind match can pick up the wrong channel's number.
        return _parse_count(m.group(1)) if m else None

    @staticmethod
    def _extract_video_count(html: str) -> int | None:
        m = re.search(r'"videosCountText":\{"runs":\[\{"text":"([\d.,]+)"', html)
        if not m:
            m = re.search(r'([\d.,]+)\s+videos', html, re.IGNORECASE)
        return _parse_count(m.group(1)) if m else None

    # ── unified fetch ──────────────────────────────────────
    def get_channel_stats(
        self, channel_id: str, channel_urls: str | list[str], channel_name: str
    ) -> dict:
        stats: dict | None = None
        if self.use_api and _is_valid_channel_id(channel_id):
            try:
                api_stats = self.get_channel_stats_api(channel_id)
                if "error" not in api_stats:
                    stats = api_stats
                    stats["data_source"] = "youtube_api"
                else:
                    logger.warning("API returned error for %s: %s", channel_id, api_stats["error"])
            except Exception as exc:  # noqa: BLE001 — auth/quota/network
                logger.warning("YouTube API call failed for %s: %s", channel_id, exc)

        if stats is None:
            stats = self.get_channel_stats_scrape(channel_urls, channel_name)
            stats["data_source"] = "public_scrape"

        stats.setdefault("channel_id", channel_id)
        stats.setdefault("name", channel_name)
        return stats

    # ── anomaly detection ──────────────────────────────────
    @staticmethod
    def _normalize_status(status: str) -> str:
        s = (status or "").lower().strip()
        if s in ("public", "ok", "active", "published", ""):
            return "active"
        if s in ("private", "closed", "terminated"):
            return "inactive"
        return s

    def detect_anomalies(
        self,
        current: dict,
        previous: dict | None,
        previous_age_seconds: float | None = None,
    ) -> list[dict]:
        anomalies: list[dict] = []
        if not previous:
            return anomalies

        # Fix 4: skip if the previous snapshot is less than 1 hour old —
        # back-to-back scans produce noise, not signal.
        if previous_age_seconds is not None and previous_age_seconds < 3600:
            return anomalies

        # Fix 2: subscriber drop threshold raised from 5 % → 10 %
        cur_subs = current.get("subscriber_count")
        prev_subs = previous.get("subscriber_count")
        if cur_subs is not None and prev_subs:
            if cur_subs < prev_subs * 0.90:
                anomalies.append({
                    "anomaly_type": "SUBSCRIBER_DROP",
                    "severity": "HIGH",
                    "detail": f"Subscribers dropped {prev_subs:,} → {cur_subs:,} "
                              f"({(prev_subs - cur_subs) / prev_subs * 100:.1f}% loss)",
                })
            elif cur_subs > prev_subs * 1.5:
                anomalies.append({
                    "anomaly_type": "SUBSCRIBER_SPIKE",
                    "severity": "MEDIUM",
                    "detail": f"Subscribers spiked {prev_subs:,} → {cur_subs:,} (>50% gain)",
                })

        # Video-count thresholds are RELATIVE to library size, with an absolute
        # floor. Purely absolute limits made normal broadcast operation look
        # like a security event: on a 213,005-video channel, +10 videos fired a
        # spike and -3 fired a CRITICAL "possible takedown", when a few videos
        # being set private/unlisted is routine churn.
        cur_vid = current.get("video_count")
        prev_vid = previous.get("video_count")
        if cur_vid is not None and prev_vid is not None and prev_vid > 0:
            removed = prev_vid - cur_vid
            added = cur_vid - prev_vid

            mass_delete_floor = max(VIDEO_DROP_ABS_FLOOR, int(prev_vid * VIDEO_DROP_PCT))
            partial_delete_floor = max(
                VIDEO_DROP_PARTIAL_ABS_FLOOR, int(prev_vid * VIDEO_DROP_PARTIAL_PCT)
            )
            spike_floor = max(VIDEO_SPIKE_ABS_FLOOR, int(prev_vid * VIDEO_SPIKE_PCT))

            if removed >= mass_delete_floor:
                pct = removed / prev_vid * 100
                anomalies.append({
                    "anomaly_type": "VIDEO_COUNT_DROP",
                    "severity": "CRITICAL",
                    "detail": f"Mass deletion: video count fell {prev_vid:,} → {cur_vid:,} "
                              f"({removed:,} removed, {pct:.1f}% of library) — "
                              f"possible deletion/takedown",
                })
            elif removed >= partial_delete_floor:
                pct = removed / prev_vid * 100
                anomalies.append({
                    "anomaly_type": "VIDEO_COUNT_DROP",
                    "severity": "MEDIUM",
                    "detail": f"Video count fell {prev_vid:,} → {cur_vid:,} "
                              f"({removed:,} removed, {pct:.1f}% of library) — "
                              f"may be unlisting or takedown; review",
                })
            elif added >= spike_floor:
                pct = added / prev_vid * 100
                anomalies.append({
                    "anomaly_type": "VIDEO_COUNT_SPIKE",
                    "severity": "LOW",
                    "detail": f"Unusual bulk upload: video count rose {prev_vid:,} → "
                              f"{cur_vid:,} ({added:,} added, {pct:.1f}% of library)",
                })

        # Fix 1: normalise status before comparing so "public" == "OK"
        cur_status = self._normalize_status(current.get("status", ""))
        prev_status = self._normalize_status(previous.get("status", ""))
        if cur_status and prev_status and cur_status != prev_status:
            anomalies.append({
                "anomaly_type": "CHANNEL_STATUS_CHANGE",
                "severity": "HIGH",
                "detail": f"Channel status changed '{previous.get('status')}' → '{current.get('status')}'",
            })

        return anomalies

    # ── full scan ──────────────────────────────────────────
    def scan_all_channels(self, channel_id_overrides: dict | None = None) -> dict:
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from agents.kavasam.channel_health_agent import ChannelSnapshot
        from api.models.channel import Channel, engine

        overrides = channel_id_overrides or {}
        channels_out: list[dict] = []
        anomalies_out: list[dict] = []
        cert_in_triggered = False

        with Session(engine) as session:
            channels = session.execute(select(Channel)).scalars().all()

            for ch in channels:
                slug = _name_slug(ch.name)
                channel_id = overrides.get(slug) or ch.channel_id
                if not is_configured_channel(channel_id):
                    logger.warning(
                        "Skipping channel '%s' — no real channel_id configured (%r). "
                        "Set a real ID in Admin → Kavasam before it can be monitored.",
                        ch.name, channel_id,
                    )
                    continue
                handle = CHANNEL_PUBLIC_HANDLES.get(slug)
                urls = handle_urls_for(handle) if handle else public_url_for(ch.name, channel_id)

                stats = self.get_channel_stats(channel_id, urls, ch.name)

                previous = session.execute(
                    select(ChannelSnapshot)
                    .where(ChannelSnapshot.channel_id == ch.id)
                    .order_by(ChannelSnapshot.scanned_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                prev_dict = None
                prev_age_secs: float | None = None
                if previous:
                    prev_dict = {
                        "subscriber_count": previous.subscriber_count,
                        "video_count": previous.video_count,
                        "status": previous.status,
                    }
                    if previous.scanned_at:
                        scanned = previous.scanned_at
                        if scanned.tzinfo is None:
                            scanned = scanned.replace(tzinfo=timezone.utc)
                        prev_age_secs = (datetime.now(timezone.utc) - scanned).total_seconds()

                anomalies = self.detect_anomalies(stats, prev_dict, prev_age_secs)
                top = _most_severe(anomalies)
                anomaly_detected = bool(anomalies)

                # CRITICAL anomaly → CERT-In draft (human-in-the-loop, never filed).
                cert_in_draft_id = None
                if top and top["severity"] == "CRITICAL":
                    cert_in_draft_id = _trigger_certin(
                        session, display_channel_name(ch.name, channel_id), top["detail"]
                    )
                    if cert_in_draft_id:
                        cert_in_triggered = True

                judge_blob = {"anomalies": anomalies} if anomalies else None
                if cert_in_draft_id:
                    judge_blob = {**(judge_blob or {}), "cert_in_draft_id": cert_in_draft_id}

                session.add(ChannelSnapshot(
                    channel_id=ch.id,
                    subscriber_count=stats.get("subscriber_count"),
                    video_count=stats.get("video_count"),
                    view_count=stats.get("view_count"),
                    status=stats.get("status"),
                    anomaly_detected=anomaly_detected,
                    anomaly_type=top["anomaly_type"] if top else None,
                    anomaly_detail=top["detail"] if top else None,
                    scanned_at=datetime.now(timezone.utc),
                    judge=judge_blob,
                ))

                channel_status = (
                    "CRITICAL" if top and top["severity"] == "CRITICAL"
                    else "ANOMALY" if anomaly_detected
                    else "SECURE"
                )
                channels_out.append({
                    "channel_name": display_channel_name(ch.name, channel_id),
                    "channel_id": channel_id,
                    "subscriber_count": stats.get("subscriber_count"),
                    "video_count": stats.get("video_count"),
                    "view_count": stats.get("view_count"),
                    "status": channel_status,
                    "data_source": stats.get("data_source", "public_scrape"),
                    "last_updated": stats.get("last_updated", _now_iso()),
                    "anomalies": anomalies,
                })
                for a in anomalies:
                    anomalies_out.append({
                        **a,
                        "channel_name": display_channel_name(ch.name, channel_id),
                        "cert_in_draft_id": cert_in_draft_id,
                    })

            session.commit()

        return {
            "channels": channels_out,
            "anomalies": anomalies_out,
            "cert_in_triggered": cert_in_triggered,
        }


# Example Broadcaster's Spiritual (spiritual programming) sub-channel shares the "Example Broadcaster"
# display name with the main channel — disambiguate by channel_id.
SPIRITUAL_CHANNEL_ID = "UC_EXAMPLE_CHANNEL_ID"


def display_channel_name(name: str, channel_id: str) -> str:
    if channel_id == SPIRITUAL_CHANNEL_ID and not (name or "").endswith("(Spiritual)"):
        return f"{name} (Spiritual)"
    return name


def _name_slug(name: str) -> str:
    """'Example Broadcaster' → 'brand_main', 'J Movie Official' → 'brand_movies'."""
    low = (name or "").strip().lower()
    if "movie" in low:
        return "brand_movies"
    if "max" in low:
        return "brand_entertainment"
    if "plus" in low:
        return "brand_news"
    if "main" in low:
        return "brand_main"
    if "broadcaster" in low:
        return "brand_main"
    return re.sub(r"[^a-z0-9]+", "_", low).strip("_")


def _most_severe(anomalies: list[dict]) -> dict | None:
    if not anomalies:
        return None
    return max(anomalies, key=lambda a: _SEVERITY_RANK.get(a["severity"], 0))


def _trigger_certin(session, channel_name: str, detail: str) -> str | None:
    """Delegate to the CERT-In trigger module (defensive)."""
    try:
        from agents.kavasam.cert_in_trigger import maybe_create_certin_draft

        return maybe_create_certin_draft(session, channel_name, detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("CERT-In trigger failed for %s: %s", channel_name, exc)
        return None


def run_scan() -> dict:
    """Module entry point (used by the API subprocess bridge).

    Resolves the API key from platform_config → environment, applies any channel
    ID overrides, and runs a full scan.
    """
    from dotenv import load_dotenv

    from agents.core.config import get_youtube_api_key, load_platform_config

    load_dotenv()
    cfg = load_platform_config("kavasam")
    api_key = get_youtube_api_key()
    overrides = {
        slug: cfg.get(f"channel_id_{slug}")
        for slug in ("brand_main", "brand_main", "brand_news", "brand_entertainment", "brand_movies")
        if cfg.get(f"channel_id_{slug}")
    }
    client = YouTubeClient(api_key=api_key)
    result = client.scan_all_channels(overrides)
    result["api_configured"] = bool(api_key)
    result["data_source"] = "youtube_api" if api_key else "public_scrape"
    return result


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(asctime)s [%(levelname)s] %(message)s")
    print(json.dumps(run_scan(), ensure_ascii=False))
