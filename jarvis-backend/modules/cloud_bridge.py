"""
cloud_bridge.py — Level-3 Desk↔Cloud Bridge (desk side)
=======================================================

Makes the always-on cloud gateway the SINGLE front door to J.A.R.V.I.S. while
still routing every remote command through the *real* desk brain when the PC is
online.

THE PROBLEM IT SOLVES
---------------------
The cloud gateway (cloud_gateway.py) is reachable 24/7 but is a deliberately
"lite" brain: it can chat and look things up, but it has NO access to the PC,
files, terminal, Gmail, or your real memory — those live only on the desk. And
because Telegram delivers updates to exactly one consumer, the cloud's webhook
already supersedes the desk's own long-poller. So today the desk's Telegram
gateway is effectively dark whenever the cloud is live.

THE BRIDGE
----------
Instead of the desk trying to consume Telegram directly, it dials OUT to the
cloud over an authenticated WebSocket (`/desk-link`). The cloud stays the sole
Telegram consumer and, whenever a desk is connected, FORWARDS each recognised
message down the socket:

    cloud  ──{cmd: chat_id,user,tier,text}──▶  desk
    desk   ──{notify}/{reply: chat_id,text}──▶  cloud ──▶ Telegram

On the desk, each forwarded command is run through the exact same
`run_remote_command(text, channel)` pipeline as the voice/HUD/Telegram paths —
full ActionEngine, ReAct planner, real memory — with a `BridgeChannel` whose
replies travel back up the socket to the right Telegram chat. When the desk is
NOT connected, the cloud silently falls back to its own local `think()`.

Net effect: PC on → full remote PC control + real memory. PC off → graceful lite
chat. One memory, one front door, no token conflict.

CONFIG (env)
------------
    JARVIS_CLOUD_BRIDGE     "1" to enable the bridge on this desk (default off)
    JARVIS_BRIDGE_URL       wss URL of the cloud desk-link endpoint, e.g.
                            wss://jarvis-cloud-gateway.onrender.com/desk-link
    BRIDGE_SECRET           shared secret; MUST match the cloud's BRIDGE_SECRET

When the bridge is enabled, main.py starts it INSTEAD OF telegram_bot.start_bot()
so the desk never contends with the cloud for the bot token.
"""

from __future__ import annotations

import os
import json
import asyncio
import hmac
import traceback
from typing import Awaitable, Callable, Optional

from modules.session_manager import OutputChannel
from modules import fact_drain, fact_sink

# ── Permission tiers (mirror action_engine / telegram_bot literals) ──────────
_ADMIN_TIER = "admin"
_VIP_GUEST_TIER = "vip_guest"

# Telegram hard-limits a message to 4096 chars; chunk anything longer.
_CHUNK = 4000

# Injected at start; keeps this module transport-only like telegram_bot.
_process_fn: Optional[Callable[[str, OutputChannel], Awaitable[None]]] = None

# Runtime state.
_task: Optional[asyncio.Task] = None
_stop = False

# Live-session state so out-of-band alerts (owner_notify) can ride the same
# socket. Set while a session is connected, cleared on disconnect.
_active_ws = None
_active_lock: Optional[asyncio.Lock] = None


# ════════════════════════════════════════════════════════════════════════════
# Output channel — replies ride back UP the socket to a specific Telegram chat
# ════════════════════════════════════════════════════════════════════════════
class BridgeChannel(OutputChannel):
    """Delivers J.A.R.V.I.S.'s replies back through the cloud bridge socket.

    One channel per inbound command. `send()` is a coroutine that writes a JSON
    frame to the shared WebSocket; a lock serialises concurrent writers so two
    simultaneous chats never interleave a frame.
    """

    kind = "bridge"

    def __init__(self, ws, send_lock: asyncio.Lock, chat_id: int, *,
                 user: str, permission_tier: str, honorific: str,
                 req_id=None) -> None:
        # Scope the session id to the remote chat, mirroring TelegramChannel so a
        # remote caller's working memory stays isolated from the desk HUD's.
        super().__init__(channel_id=f"bridge:{chat_id}", user=user)
        self._ws = ws
        self._send_lock = send_lock
        self.chat_id = chat_id
        self.permission_tier = permission_tier
        self.honorific = honorific
        # Phase 4 item 7: echoed on every frame so the cloud can correlate
        # replies/heartbeats to the forwarded request (wedged-desk fallback).
        self.req_id = req_id

    async def _emit(self, frame: dict) -> None:
        if self.req_id is not None:
            frame.setdefault("req_id", self.req_id)
        async with self._send_lock:
            try:
                await self._ws.send(json.dumps(frame))
            except Exception as e:  # noqa: BLE001
                print(f"[BRIDGE] frame send failed: {e}", flush=True)

    async def reply(self, text: str) -> None:
        if not text or not text.strip():
            return
        clean = text.strip()
        for i in range(0, len(clean), _CHUNK):
            await self._emit({"type": "reply", "chat_id": self.chat_id,
                              "text": clean[i:i + _CHUNK]})

    async def notify(self, status: str, message: str = "") -> None:
        # Only a typing indicator crosses the wire — no status spam in chat.
        await self._emit({"type": "notify", "chat_id": self.chat_id})

    async def send_document(self, path: str, caption: str = "") -> bool:
        # v1: files are not streamed over the bridge. Tell the caller plainly
        # rather than silently dropping it.
        await self._emit({
            "type": "reply", "chat_id": self.chat_id,
            "text": f"(The file is ready on the desk, {self.honorific}, but I can't "
                    f"send it over the remote link yet.)"
        })
        return False


# ════════════════════════════════════════════════════════════════════════════
# Frame handling
# ════════════════════════════════════════════════════════════════════════════
async def _handle_cmd(ws, send_lock: asyncio.Lock, frame: dict) -> None:
    """Run one forwarded command through the shared desk pipeline."""
    text = (frame.get("text") or "").strip()
    chat_id = frame.get("chat_id")
    if not text or chat_id is None or _process_fn is None:
        return
    req_id = frame.get("req_id")
    channel = BridgeChannel(
        ws, send_lock, int(chat_id),
        user=frame.get("user") or "KAUSTAV",
        permission_tier=frame.get("tier") or _ADMIN_TIER,
        honorific=frame.get("honorific") or "Sir",
        req_id=req_id,
    )
    try:
        await _process_fn(text, channel)
    except Exception as e:  # noqa: BLE001
        print(f"[BRIDGE] process_fn fault: {e}\n{traceback.format_exc()}", flush=True)
        try:
            await channel.reply("I encountered a fault processing that.")
        except Exception:
            pass
    finally:
        # Signal completion so the cloud can clear any per-request state.
        if req_id is not None:
            try:
                async with send_lock:
                    await ws.send(json.dumps({"type": "done", "req_id": req_id}))
            except Exception:
                pass


async def _handle_hud_req(ws, send_lock: asyncio.Lock, frame: dict) -> None:
    """Answer the cloud's request for a vitals snapshot.

    Asked for only while a phone is attached to the cloud front door, and only
    while this desk is linked — the cloud has no CPU or disk figures of its own
    and must never invent any, so a phone on the cloud path sees real numbers or
    none at all. Read off the event loop: psutil samples CPU with a blocking
    interval.
    """
    req_id = frame.get("req_id")
    try:
        import sensors
        data = await asyncio.to_thread(sensors.get_system_telemetry)
    except Exception as e:  # noqa: BLE001
        print(f"[BRIDGE] telemetry snapshot failed: {e}", flush=True)
        return
    try:
        async with send_lock:
            await ws.send(json.dumps({"type": "hud", "req_id": req_id, "data": data}))
    except Exception as e:  # noqa: BLE001
        print(f"[BRIDGE] telemetry send failed: {e}", flush=True)


async def _handle_facts(ws, send_lock: asyncio.Lock, frame: dict) -> None:
    """Drain one sealed-fact batch and ack what was handled.

    Run off the event loop: the drain opens sqlite and walks DPAPI -> DEK -> the
    X25519 private half, all of it blocking. Anything not named in the ack stays
    in the cloud outbox and comes back on the next connect.
    """
    try:
        ack = await asyncio.to_thread(fact_drain.handle_cloud_frame, frame)
    except Exception as e:  # noqa: BLE001
        # A locked key store lands here. Nothing is acked, so the batch survives.
        print(f"[BRIDGE] fact drain aborted ({e}) — the batch stays queued in the "
              f"cloud.", flush=True)
        return
    if not ack:
        return
    try:
        async with send_lock:
            await ws.send(json.dumps(ack))
    except Exception as e:  # noqa: BLE001
        # The ack was lost, so the cloud will re-offer these. The ledger makes
        # that a no-op rather than a double-store.
        print(f"[BRIDGE] fact ack failed: {e}", flush=True)


async def _session(url: str, secret: str) -> None:
    """One connected lifetime: authenticate, then pump command frames."""
    global _active_ws, _active_lock
    import websockets

    # Secret travels in a header and is checked BEFORE the socket is accepted,
    # so an unauthenticated peer never reaches the command loop.
    headers = {"X-Bridge-Secret": secret}
    async with websockets.connect(
        url, additional_headers=headers, ping_interval=20, ping_timeout=20,
        max_size=2 ** 20, open_timeout=20,
    ) as ws:
        print(f"[BRIDGE] ✅ Linked to cloud front door → {url}", flush=True)
        send_lock = asyncio.Lock()
        _active_ws, _active_lock = ws, send_lock
        # C#11a Step 4 Phase 3: install the GOVERNED sink before the handshake
        # below, because accepting the key is what triggers the cloud's flush. A
        # drain with no sink holds the batch — safe, but holding a backlog we
        # could have drained is a bug, not a safety net. This is the only place
        # a sink is ever installed, which is what makes "no ungoverned path into
        # memory" a property of the wiring rather than a promise.
        fact_sink.install()
        # C#11a Step 4: hand over the public half on EVERY connect. The cloud
        # cannot seal a PC-off turn without it, and re-sending is what makes a
        # rotated keypair need no coordination. Accepting it triggers the flush of
        # whatever backlog built up while this desk was down.
        try:
            async with send_lock:
                await ws.send(json.dumps(fact_drain.handshake_frame()))
        except Exception as e:  # noqa: BLE001
            print(f"[BRIDGE] fact-key handshake failed ({e}) — the cloud cannot queue "
                  f"PC-off facts until the next connect.", flush=True)
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                ftype = frame.get("type")
                if ftype == "cmd":
                    # Fire-and-forget so long-running commands don't block the reader
                    # and multiple chats can be served concurrently.
                    asyncio.create_task(_handle_cmd(ws, send_lock, frame))
                elif ftype == "hud_req":
                    # Off the reader too: a blocking psutil sample must not stall
                    # a command frame queued behind it.
                    asyncio.create_task(_handle_hud_req(ws, send_lock, frame))
                elif ftype == "facts":
                    # Off the reader so a long drain cannot stall command frames.
                    asyncio.create_task(_handle_facts(ws, send_lock, frame))
                elif ftype == "welcome":
                    roster = frame.get("identities", "")
                    print(f"[BRIDGE] Cloud accepted the link. Identities: {roster}", flush=True)
                # ignore anything else (future frame types)
        finally:
            if _active_ws is ws:
                _active_ws, _active_lock = None, None


async def send_alert_to_owner(text: str) -> bool:
    """Push an unsolicited alert up the bridge for relay to the owner's
    Telegram chat (owner-notify fan-out). Includes the admin chat id when the
    desk knows it; otherwise the cloud resolves its own admin identity.
    Returns True only when the frame was written to a live socket."""
    ws, lock = _active_ws, _active_lock
    if ws is None or lock is None or not text or not text.strip():
        return False
    frame: dict = {"type": "alert", "text": text.strip()}
    owner_raw = os.getenv("TELEGRAM_USER_ID", "").strip()
    if owner_raw.lstrip("-").isdigit():
        frame["chat_id"] = int(owner_raw)
    try:
        async with lock:
            await ws.send(json.dumps(frame))
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[BRIDGE] alert send failed: {e}", flush=True)
        return False


async def _run_forever(url: str, secret: str) -> None:
    """Connect with exponential backoff; reconnect for the life of the process."""
    backoff = 2
    while not _stop:
        try:
            await _session(url, secret)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            print(f"[BRIDGE] link down ({e}); retrying in {backoff}s.", flush=True)
        if _stop:
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)  # cap at 1 min between attempts


# ════════════════════════════════════════════════════════════════════════════
# Lifecycle
# ════════════════════════════════════════════════════════════════════════════
def is_enabled() -> bool:
    return (os.getenv("JARVIS_CLOUD_BRIDGE", "").strip() == "1"
            and bool(os.getenv("JARVIS_BRIDGE_URL", "").strip())
            and bool(os.getenv("BRIDGE_SECRET", "").strip()))


def start_bridge(process_fn: Callable[[str, OutputChannel], Awaitable[None]]) -> bool:
    """Launch the cloud-bridge client as a background task.

    Returns True if started, False if not configured. Call from the FastAPI
    lifespan (needs a live event loop). When this starts, main.py should NOT also
    start telegram_bot.start_bot() — the cloud owns the Telegram token.
    """
    global _process_fn, _task, _stop

    if not is_enabled():
        print("[BRIDGE] Not enabled (need JARVIS_CLOUD_BRIDGE=1 + JARVIS_BRIDGE_URL "
              "+ BRIDGE_SECRET) — desk↔cloud bridge disabled.", flush=True)
        return False

    url = os.getenv("JARVIS_BRIDGE_URL", "").strip()
    secret = os.getenv("BRIDGE_SECRET", "").strip()
    _process_fn = process_fn
    _stop = False
    _task = asyncio.create_task(_run_forever(url, secret))
    print(f"[BRIDGE] ✅ Desk↔cloud bridge starting → {url}", flush=True)
    return True


async def stop_bridge() -> None:
    """Stop the bridge task (call on shutdown)."""
    global _stop, _task
    _stop = True
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
    print("[BRIDGE] Desk↔cloud bridge stopped.", flush=True)
