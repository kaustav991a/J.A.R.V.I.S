"""Connect the REAL desk bridge to the cloud for a few seconds, and nothing else.

WHY THIS EXISTS
---------------
`has_desk_key: false` on the live gateway is the ordinary state of this system,
not a broken desk: the PC being off is exactly why a fact is queued, and the
key used to live in memory and on a disk every deploy throws away. The fix is a
durable mirror (`fact_outbox.set_change_hook`), and the only way to prove it is
to hand the cloud a real key, then look at `/health` after the NEXT deploy.

That proof needs a desk. Starting the whole desk backend to get it costs several
minutes and a lot of the machine; this connects the bridge alone.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It answers no commands. While this is connected the cloud believes the desk is
up and will route his questions here, so `process_fn` says one honest sentence
and nothing else — a stub that invented answers would be worse than a desk that
is off, which is the whole reason this is measured in seconds.

It also installs no sink, so nothing drains into memory. Draining is the desk's
real job and belongs to the real desk.

    venv\\Scripts\\python.exe tools\\bridge_handshake_check.py [seconds]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(HERE / ".env", override=True)

HEALTH = (os.getenv("JARVIS_BRIDGE_URL") or "").replace("wss://", "https://") \
    .replace("ws://", "http://").replace("/desk-link", "/health")


def health() -> dict:
    with urllib.request.urlopen(HEALTH, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", "replace"))


async def _say_nothing_useful(text: str, channel) -> None:
    """The one honest thing a stub desk can say."""
    await channel.reply("The desk is running a maintenance check and is not "
                        "answering right now, sir. Ask again in a moment.")


async def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    from modules import cloud_bridge, fact_seal

    if not cloud_bridge.is_enabled():
        print("[CHECK] bridge not configured (JARVIS_CLOUD_BRIDGE / "
              "JARVIS_BRIDGE_URL / BRIDGE_SECRET)")
        return 2

    before = health()
    print(f"[CHECK] before: commit={before.get('commit') or '?'} "
          f"desk_linked={before.get('desk_linked')} "
          f"fact_outbox={before.get('fact_outbox')}")
    print(f"[CHECK] this desk's public half: {fact_seal.desk_public_b64()[:12]}...")

    cloud_bridge.start_bridge(_say_nothing_useful)
    await asyncio.sleep(seconds)
    await cloud_bridge.stop_bridge()
    # the cloud notices the socket close on its own; give it a moment to
    await asyncio.sleep(2.0)

    after = health()
    box = after.get("fact_outbox") or {}
    print(f"[CHECK] after:  desk_linked={after.get('desk_linked')} "
          f"fact_outbox={box}")
    ok = bool(box.get("has_desk_key"))
    print(f"[CHECK] {'PASS' if ok else 'FAIL'} — has_desk_key={box.get('has_desk_key')}, "
          f"durable={box.get('durable')}")
    if ok and not box.get("durable"):
        print("[CHECK] ⚠ the key is held but the mirror is NOT armed: it will "
              "die at the next spin-down, exactly as before.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
