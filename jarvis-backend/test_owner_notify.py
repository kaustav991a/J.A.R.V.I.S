"""
test_owner_notify.py — harness for the Phase 4.1 owner-notify fan-out.

Run: .\\venv\\Scripts\\python.exe test_owner_notify.py   (no hardware needed)

Covers the pure delivery logic: leg selection (telegram → bridge fallback),
honest per-leg reporting, empty-message rejection, and that no leg ever raises
out of notify_owner/send_to_phone.
"""

import asyncio
import sys

# Same hardening as main.py: piped stdout falls back to cp1252 on Windows and
# chokes on ✅/❌/emoji output.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from modules import owner_notify
from modules import telegram_bot
from modules import cloud_bridge

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def reset():
    """Restore module state between cases."""
    owner_notify._broadcast_fn = None
    owner_notify._speak_fn = None
    telegram_bot._bot = None
    telegram_bot._OWNER_ID = None
    cloud_bridge._active_ws = None
    cloud_bridge._active_lock = None


class FakeBot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((chat_id, text))


class FakeWS:
    def __init__(self, fail=False):
        self.frames = []
        self.fail = fail

    async def send(self, raw):
        if self.fail:
            raise RuntimeError("socket dead")
        self.frames.append(raw)


async def main():
    print("\n— send_to_phone: no transport live —")
    reset()
    check("returns False with nothing configured",
          await owner_notify.send_to_phone("alert") is False)
    check("rejects empty message", await owner_notify.send_to_phone("  ") is False)

    print("\n— send_to_phone: direct telegram poller —")
    reset()
    telegram_bot._bot = FakeBot()
    telegram_bot._OWNER_ID = 42
    ok = await owner_notify.send_to_phone("intruder!")
    check("delivers via telegram", ok is True)
    check("owner chat targeted", telegram_bot._bot.sent == [(42, "intruder!")])

    print("\n— send_to_phone: telegram down, bridge live → falls back —")
    reset()
    telegram_bot._bot = FakeBot(fail=True)
    telegram_bot._OWNER_ID = 42
    ws = FakeWS()
    cloud_bridge._active_ws = ws
    cloud_bridge._active_lock = asyncio.Lock()
    ok = await owner_notify.send_to_phone("cpu at 95%")
    check("falls back to bridge", ok is True)
    check("alert frame written", len(ws.frames) == 1 and '"type": "alert"' in ws.frames[0])

    print("\n— send_to_phone: both transports dead → honest False —")
    reset()
    telegram_bot._bot = FakeBot(fail=True)
    telegram_bot._OWNER_ID = 42
    cloud_bridge._active_ws = FakeWS(fail=True)
    cloud_bridge._active_lock = asyncio.Lock()
    check("returns False, no exception",
          await owner_notify.send_to_phone("alert") is False)

    print("\n— telegram_bot.send_text_to_owner: chunking —")
    reset()
    telegram_bot._bot = FakeBot()
    telegram_bot._OWNER_ID = 7
    long = "x" * 9000
    ok = await telegram_bot.send_text_to_owner(long)
    check("long message accepted", ok is True)
    check("chunked at 4000", [len(t) for _, t in telegram_bot._bot.sent] == [4000, 4000, 1000])
    check("unconfigured → False", await owner_notify.send_to_phone("") is False)

    print("\n— cloud_bridge.send_alert_to_owner: frame shape —")
    reset()
    ws = FakeWS()
    cloud_bridge._active_ws = ws
    cloud_bridge._active_lock = asyncio.Lock()
    import os
    os.environ["TELEGRAM_USER_ID"] = "1234"
    ok = await cloud_bridge.send_alert_to_owner("disk 91%")
    import json
    frame = json.loads(ws.frames[0])
    check("send returns True", ok is True)
    check("frame carries chat_id from env", frame.get("chat_id") == 1234)
    check("frame carries text", frame.get("text") == "disk 91%")
    os.environ.pop("TELEGRAM_USER_ID", None)
    check("no live socket → False",
          (cloud_bridge.__dict__.__setitem__("_active_ws", None) or
           await cloud_bridge.send_alert_to_owner("x")) is False)

    print("\n— notify_owner: full fan-out + honest report —")
    reset()
    hud_frames = []
    spoken = []

    async def fake_broadcast(payload):
        hud_frames.append(payload)

    async def fake_speak(text):
        spoken.append(text)

    owner_notify.configure(fake_broadcast, fake_speak)
    telegram_bot._bot = FakeBot()
    telegram_bot._OWNER_ID = 42
    report = await owner_notify.notify_owner("ram high")
    check("all three legs delivered",
          report == {"hud": True, "tts": True, "phone": True}, str(report))
    check("HUD got proactive frame", hud_frames[0].get("is_proactive") is True)
    check("TTS spoke it", spoken == ["ram high"])

    report = await owner_notify.notify_owner("quiet one", speak=False, phone=False)
    check("speak/phone suppressible",
          report == {"hud": True, "tts": False, "phone": False}, str(report))

    reset()
    report = await owner_notify.notify_owner("nothing wired")
    check("unconfigured + no transport → all False",
          report == {"hud": False, "tts": False, "phone": False}, str(report))

    print("\n— notify_owner: a failing desk leg never blocks the phone leg —")
    reset()

    async def broken_broadcast(payload):
        raise RuntimeError("HUD socket dead")

    async def broken_speak(text):
        raise RuntimeError("TTS engine dead")

    owner_notify.configure(broken_broadcast, broken_speak)
    telegram_bot._bot = FakeBot()
    telegram_bot._OWNER_ID = 42
    report = await owner_notify.notify_owner("critical alert")
    check("phone still delivered",
          report == {"hud": False, "tts": False, "phone": True}, str(report))

    print(f"\n{PASS}/{PASS + FAIL} passed.")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
