"""Plugin enable/disable gate for agents.

Reads api/data/plugins.json (written by the Plugin Marketplace toggle endpoint)
and returns whether a named plugin is currently enabled.

Design choices:
- Case-insensitive partial match so scraper code doesn't need to know the exact
  UI label ("AltNews Scraper" matches "altnews").
- Fail open: if the file is missing, unreadable, or the plugin isn't found,
  returns True so a JSON parse error never silently kills an agent pipeline.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("plugin_check")

_PLUGINS_FILE = Path(__file__).resolve().parents[2] / "api" / "data" / "plugins.json"


def is_plugin_enabled(plugin_name: str) -> bool:
    """Return True if the named plugin is enabled (or unknown — fail open)."""
    try:
        plugins: list[dict] = json.loads(_PLUGINS_FILE.read_text())
    except Exception as exc:
        logger.warning("plugin_check: could not read plugins.json (%s) — defaulting to enabled", exc)
        return True

    needle = plugin_name.lower()
    for p in plugins:
        pname = p.get("name", "").lower()
        if needle in pname or pname in needle:
            enabled = bool(p.get("enabled", True))
            if not enabled:
                logger.info("Plugin '%s' is disabled (matched '%s')", p["name"], plugin_name)
            return enabled

    # Plugin not listed — fail open so new scrapers work before the admin adds them.
    logger.debug("Plugin '%s' not found in plugins.json — defaulting to enabled", plugin_name)
    return True
