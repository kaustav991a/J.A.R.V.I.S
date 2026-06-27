"""
Phase 8.4 E2E: WebSocket client + backdoor test:deep_work_ui.
Requires: uvicorn main:app on 127.0.0.1:8000 (launches VS Code + browser on Windows).
"""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.request

import websockets

BACKDOOR_URL = "http://127.0.0.1:8000/api/backdoor"
WS_URL = "ws://127.0.0.1:8000/ws"


def _post_backdoor() -> None:
    req = urllib.request.Request(
        BACKDOOR_URL,
        data=json.dumps({"command": "test:deep_work_ui"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        print("[POST /api/backdoor]", resp.read().decode()[:500])


async def _run() -> None:
    received: list[dict] = []

    async with websockets.connect(WS_URL) as ws:

        async def listen() -> None:
            while True:
                raw = await ws.recv()
                received.append(json.loads(raw))

        task = asyncio.create_task(listen())
        await asyncio.sleep(0.4)
        threading.Thread(target=_post_backdoor, daemon=True).start()
        await asyncio.sleep(10.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    print("\n--- WebSocket frames (chronological) ---")
    for i, p in enumerate(received):
        preview = json.dumps(p, default=str)
        if len(preview) > 220:
            preview = preview[:217] + "..."
        print(f"  [{i:02d}] {preview}")

    ui_state = [p for p in received if p.get("type") == "ui_state"]
    print(f"\nui_state frames: {len(ui_state)}")
    for p in ui_state:
        print("   ", p)


if __name__ == "__main__":
    asyncio.run(_run())
