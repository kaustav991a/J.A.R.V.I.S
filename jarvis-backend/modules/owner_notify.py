"""
owner_notify.py — "notify the owner wherever he is" fan-out (Phase 4.1)
=======================================================================

THE PROBLEM IT SOLVES
---------------------
Proactive detections (intruder, health alarms, worker reports) were wired only
to the desk HUD + desk TTS — an alert spoken to an empty room. This module is
the single place that fans a message out to every reachable surface:

    desk HUD  (broadcast callback, injected from main.py)
    desk TTS  (speak callback, injected from main.py)
    phone     (direct Telegram poller OR the level-3 cloud bridge — whichever
               transport is actually live; main.py starts exactly one of them)

DESIGN
------
* Transport-agnostic: the phone leg tries `telegram_bot.send_text_to_owner()`
  first (direct poller), then `cloud_bridge.send_alert_to_owner()` (bridge).
  Both are cheap no-ops when their transport isn't running, so callers never
  need to know which front door is active.
* Honest delivery report: `notify_owner()` returns per-leg booleans so callers
  can log (not narrate) what actually got through. No leg raises.
* Callbacks are registered once from main.py's lifespan via `configure()`;
  before that, desk legs are skipped (phone leg still works — it only needs
  the transport modules).
"""

from __future__ import annotations

import traceback
from typing import Awaitable, Callable, Optional

# Injected from main.py at startup (same callbacks the daemons use).
_broadcast_fn: Optional[Callable[[dict], Awaitable[None]]] = None
_speak_fn: Optional[Callable[[str], Awaitable[None]]] = None


def configure(broadcast_fn: Callable[[dict], Awaitable[None]],
              speak_fn: Callable[[str], Awaitable[None]]) -> None:
    """Register the desk-side delivery callbacks (call from the FastAPI lifespan)."""
    global _broadcast_fn, _speak_fn
    _broadcast_fn = broadcast_fn
    _speak_fn = speak_fn


async def send_to_phone(text: str) -> bool:
    """Deliver one message to the owner's phone via whichever remote transport
    is live. Returns True only when a transport accepted the message."""
    if not text or not text.strip():
        return False
    clean = text.strip()

    # Leg 1: direct Telegram poller (desk owns the bot token).
    try:
        from modules import telegram_bot
        if await telegram_bot.send_text_to_owner(clean):
            return True
    except Exception as e:  # noqa: BLE001
        print(f"[NOTIFY] telegram leg failed: {e}", flush=True)

    # Leg 2: level-3 cloud bridge (cloud owns the token; we push an alert frame
    # up the authenticated socket and the cloud relays it to the admin chat).
    try:
        from modules import cloud_bridge
        if await cloud_bridge.send_alert_to_owner(clean):
            return True
    except Exception as e:  # noqa: BLE001
        print(f"[NOTIFY] bridge leg failed: {e}", flush=True)

    return False


async def notify_owner(message: str, *, speak: bool = True,
                       phone: bool = True,
                       hud_payload: Optional[dict] = None) -> dict:
    """Fan one message out to desk HUD + desk TTS + phone.

    speak=False suppresses TTS (standby mode / owner known absent).
    phone=False keeps it desk-only (ambient chatter should never buzz a phone).
    hud_payload overrides the default HUD frame (e.g. security_override).
    Returns {"hud": bool, "tts": bool, "phone": bool} — what actually delivered.
    """
    report = {"hud": False, "tts": False, "phone": False}
    if not message or not message.strip():
        return report
    clean = message.strip()

    if _broadcast_fn is not None:
        try:
            await _broadcast_fn(hud_payload or {
                "status": "speaking", "message": clean, "is_proactive": True})
            report["hud"] = True
        except Exception as e:  # noqa: BLE001
            print(f"[NOTIFY] HUD leg failed: {e}", flush=True)

    if speak and _speak_fn is not None:
        try:
            await _speak_fn(clean)
            report["tts"] = True
        except Exception:  # noqa: BLE001
            print(f"[NOTIFY] TTS leg failed:\n{traceback.format_exc()}", flush=True)

    if phone:
        report["phone"] = await send_to_phone(clean)

    delivered = [k for k, v in report.items() if v]
    print(f"[NOTIFY] '{clean[:60]}…' → delivered via {delivered or 'NOTHING'}", flush=True)
    return report
