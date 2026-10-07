import os
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
YOUTUBE_PARTS = "snippet,statistics,status"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_channel_stats(channel_id: str) -> dict:
    params = {
        "part": YOUTUBE_PARTS,
        "id": channel_id,
        "key": YOUTUBE_API_KEY,
    }

    try:
        response = httpx.get(YOUTUBE_CHANNELS_URL, params=params, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        return {
            "channel_id": channel_id,
            "error": f"HTTP {exc.response.status_code}: {exc.response.text}",
            "fetched_at": _utc_now_iso(),
        }
    except httpx.HTTPError as exc:
        return {
            "channel_id": channel_id,
            "error": f"Request failed: {exc}",
            "fetched_at": _utc_now_iso(),
        }
    except ValueError as exc:
        return {
            "channel_id": channel_id,
            "error": f"Invalid JSON response: {exc}",
            "fetched_at": _utc_now_iso(),
        }

    items = payload.get("items", [])
    if not items:
        return {
            "channel_id": channel_id,
            "error": "Channel not found",
            "fetched_at": _utc_now_iso(),
        }

    item = items[0]
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    status = item.get("status", {})
    thumbnails = snippet.get("thumbnails", {})
    thumbnail = (
        thumbnails.get("high")
        or thumbnails.get("medium")
        or thumbnails.get("default")
        or {}
    )

    return {
        "channel_id": item.get("id", channel_id),
        "name": snippet.get("title"),
        "subscriber_count": int(statistics["subscriberCount"])
        if "subscriberCount" in statistics
        else None,
        "video_count": int(statistics["videoCount"])
        if "videoCount" in statistics
        else None,
        "view_count": int(statistics["viewCount"])
        if "viewCount" in statistics
        else None,
        "status": status.get("privacyStatus"),
        "thumbnail_url": thumbnail.get("url"),
        "fetched_at": _utc_now_iso(),
    }


def get_all_channel_stats(channel_ids: list[str]) -> list[dict]:
    return [get_channel_stats(channel_id) for channel_id in channel_ids]
