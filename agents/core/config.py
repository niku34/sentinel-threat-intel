"""Shared platform_config DB reader for agent code.

The Admin Console persists integration credentials (YouTube Data API key,
channel ID overrides, etc.) into the `platform_config` Postgres table — not
into process environment variables. Every agent that needs one of these
values must read it from here, not from `os.getenv` alone, or it silently
runs in a degraded/dormant mode whenever the calling process's environment
doesn't happen to also carry the value (e.g. an agents/ subprocess spawned
without the same env as the api/ process).
"""
from __future__ import annotations

import os


def load_platform_config(module: str) -> dict:
    """Return {key: value} for every platform_config row under `module`."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from api.models.channel import engine

    with Session(engine) as db:
        rows = db.execute(
            text("SELECT key, value FROM platform_config WHERE module = :module"),
            {"module": module},
        ).fetchall()
    return {k: v for k, v in rows}


def get_youtube_api_key() -> str | None:
    """YouTube Data API key: platform_config (module='kavasam') first, falling
    back to the YOUTUBE_API_KEY env var only if no DB value is configured.
    """
    cfg = load_platform_config("kavasam")
    return cfg.get("youtube_api_key") or os.getenv("YOUTUBE_API_KEY") or None
