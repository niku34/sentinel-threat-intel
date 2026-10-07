"""WebSocket connection manager + broadcast helpers for Live Operations.

A single process-wide `ConnectionManager` holds every open `/ws/live-feed`
socket. `broadcast_*` are async and run on the event loop; `emit_*` are the
sync-safe entry points used by ordinary (sync) request handlers and background
tasks — they schedule the coroutine onto the captured event loop via
`run_coroutine_threadsafe` and never raise, so a broadcast failure can never
break the business logic that triggered it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import WebSocket

logger = logging.getLogger("websocket_manager")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        # The main event loop, captured at app startup so sync callers can
        # schedule broadcasts from worker threads.
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        logger.info("WS connected — %d active", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("WS disconnected — %d active", len(self.active_connections))

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to every live connection; drop dead ones."""
        if not self.active_connections:
            return
        dead: list[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001 — closed/broken socket
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)


# Singleton — imported everywhere broadcasts are emitted.
manager = ConnectionManager()


# ── async broadcast helpers ───────────────────────────────
async def broadcast_threat(threat_data: dict) -> None:
    await manager.broadcast({
        "type": "threat",
        **threat_data,
        "timestamp": _now_iso(),
    })


async def broadcast_scan_event(event: str, data: dict | None = None) -> None:
    await manager.broadcast({
        "type": "scan_event",
        "event": event,
        "data": data or {},
        "timestamp": _now_iso(),
    })


# ── sync-safe entry points (for sync handlers / background tasks) ──
def _schedule(coro) -> None:
    """Run an async broadcast from sync code without ever raising."""
    loop = manager.loop
    if loop is None or not manager.active_connections:
        # No loop bound yet, or nobody listening — nothing to do.
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Broadcast scheduling failed: %s", exc)


def emit_threat(threat_data: dict) -> None:
    _schedule(broadcast_threat(threat_data))


def emit_scan_event(event: str, data: dict | None = None) -> None:
    _schedule(broadcast_scan_event(event, data))


def emit_security_alert(event: str, user: str, reasons: list[str], risk_level: str) -> None:
    severity = risk_level  # CRITICAL / HIGH / MEDIUM
    title = (
        f"Login anomaly — Kill Switch triggered for {user}"
        if risk_level == "CRITICAL"
        else f"Suspicious login detected for {user}"
    )
    _schedule(manager.broadcast({
        "type": "security_alert",
        "event": event,
        "severity": severity,
        "title": title,
        "user": user,
        "reasons": reasons,
        "timestamp": _now_iso(),
    }))
