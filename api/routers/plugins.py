"""
Plugin catalogue CRUD.

The production `plugins` table has UNIQUE(module) — one row per module —
which conflicts with the multi-plugin-per-module model. Plugins are stored in
api/data/plugins.json, consistent with the team_members.json pattern.
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

_DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "plugins.json"
_lock = threading.Lock()

MODULE_ORDER = {"kaaval": 0, "kavasam": 1, "veriff": 2}


def _load() -> list[dict]:
    if not _DATA_FILE.exists():
        return []
    return json.loads(_DATA_FILE.read_text())


def _save(plugins: list[dict]) -> None:
    _DATA_FILE.write_text(json.dumps(plugins, indent=2, default=str))


# ── GET /api/plugins ───────────────────────────────────────────────────────

@router.get("")
def list_plugins():
    with _lock:
        plugins = _load()
    plugins.sort(key=lambda p: (MODULE_ORDER.get(p["module"], 99), p["name"]))
    return {"plugins": plugins, "total": len(plugins)}


# ── PATCH /api/plugins/{id}/toggle ────────────────────────────────────────

@router.patch("/{plugin_id}/toggle")
def toggle_plugin(plugin_id: str):
    with _lock:
        plugins = _load()
        target = next((p for p in plugins if p["id"] == plugin_id), None)
        if not target:
            raise HTTPException(404, detail="Plugin not found")
        target["enabled"] = not target["enabled"]
        target["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(plugins)

    return {"id": target["id"], "name": target["name"], "enabled": target["enabled"]}
