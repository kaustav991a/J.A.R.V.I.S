"""
Phase 8.4 — WebSocket UI bridge for the React HUD.

Broadcasts JSON payloads to every connected client. Safe to call from synchronous
macro code via schedule_ui_update() once set_app_loop() has run (FastAPI lifespan).
"""

from __future__ import annotations

import asyncio
from typing import Any

_clients: set[Any] = set()
_app_loop: asyncio.AbstractEventLoop | None = None


def set_app_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _app_loop
    _app_loop = loop


def register_client(ws: Any) -> None:
    _clients.add(ws)


def unregister_client(ws: Any) -> None:
    _clients.discard(ws)


async def send_ui_update(payload: dict[str, Any]) -> None:
    """
    Broadcast a JSON payload to all connected WebSocket clients.
    Dead sockets are removed. Same contract as legacy main.py broadcast loops.
    """
    dead: set[Any] = set()
    for ws in list(_clients):
        try:
            state = getattr(ws, "client_state", None)
            if state is not None and getattr(state, "value", None) != 1:
                dead.add(ws)
                continue
            await ws.send_json(payload)
        except Exception:
            dead.add(ws)
    for ws in dead:
        _clients.discard(ws)


def schedule_ui_update(payload: dict[str, Any]) -> None:
    """Thread-safe: queue send_ui_update on the FastAPI event loop (sync callers e.g. MacroAgent)."""
    loop = _app_loop
    if loop is None:
        return
    if not loop.is_running():
        return
    try:
        asyncio.run_coroutine_threadsafe(send_ui_update(payload), loop)
    except Exception:
        pass
