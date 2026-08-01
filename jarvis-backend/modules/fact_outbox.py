"""C#11a Step 4 — the cloud-side outbox: seal now, deliver when the desk shows up.

The normal case is the bridge being DOWN. A fact only exists to be queued
because the desk was off, and the desk being off is exactly why the bridge is
not connected — so this is the primary path, not a fallback.

    turn answered by think()  ->  seal to the desk public half  ->  outbox
    desk connects, hands over its public half  ->  flush  ->  desk acks
    acked records leave the outbox; UNACKED ones stay at the front, in order

That last line is the whole reliability story. A bridge that drops mid-batch
loses nothing: the desk has already recorded what it drained in its own ledger,
and whatever it did not ack is still here, still first in line, for the next
connect. Redelivery is therefore normal and expected — the desk's ledger, not
this module, is what makes it harmless.

Runs on Render, so the import discipline from fact_seal applies here too:
stdlib + fact_seal (pynacl) only. `cloud_gateway.py` deliberately imports
nothing from `modules/`; this and `fact_seal` are the first two, and both are
safe because `modules/__init__.py` is a comment and neither touches
`cryptography`, pywin32, or a database.

Render's filesystem is ephemeral, so the disk spill below is a best-effort
bonus for a process restart inside a live container — not the durability story.
The durability story is that the desk drains fast and often. What the spill DOES
guarantee is that nothing readable is ever written: only sealed envelopes go to
disk, which is the reason sealing happens before queueing rather than after.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable, Optional

from modules import fact_seal

BACKEND_DIR = Path(__file__).resolve().parent.parent

OUTBOX_FILE = BACKEND_DIR / "fact_outbox.jsonl"
DESK_KEY_FILE = BACKEND_DIR / "fact_desk_key.json"

# A cap is required — an unbounded outbox on a 512MB free dyno is an outage
# waiting for a long desk holiday. Oldest goes first, and it is announced: a
# silent cap reads as "everything was delivered" when it was not.
MAX_OUTBOX = 500

# Records per frame. The bridge is capped at max_size=2**20 on the desk side, and
# a sealed turn is ~1KB, so 25 leaves an order of magnitude of headroom.
BATCH = 25

# Same discipline as task_queue.MAX_ATTEMPTS: a record the desk will never ack
# (corrupt beyond having a usable id) must not be redelivered forever and wedge
# the queue behind it.
MAX_ATTEMPTS = 3

_outbox: deque = deque()          # items: {"envelope": {...}, "attempts": int}
_desk_public: Optional[str] = None
_loaded = False

_dropped_no_key = 0
_dropped_overflow = 0
_dead_lettered = 0


# ── persistence (best effort; never fatal) ──────────────────────────────────

def _load() -> None:
    global _loaded, _desk_public
    if _loaded:
        return
    _loaded = True
    try:
        if DESK_KEY_FILE.exists():
            _desk_public = json.loads(DESK_KEY_FILE.read_text(encoding="utf-8")).get("public")
    except Exception as exc:  # noqa: BLE001
        print(f"[OUTBOX] could not read the cached desk key: {exc}", flush=True)
    try:
        if OUTBOX_FILE.exists():
            for line in OUTBOX_FILE.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue        # a torn last line is the only expected loss
                if isinstance(item, dict) and isinstance(item.get("envelope"), dict):
                    _outbox.append({"envelope": item["envelope"],
                                    "attempts": int(item.get("attempts") or 0)})
            if _outbox:
                print(f"[OUTBOX] recovered {len(_outbox)} sealed fact(s) from disk.", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[OUTBOX] could not read the spill file: {exc}", flush=True)


def _persist() -> None:
    try:
        OUTBOX_FILE.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in _outbox),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[OUTBOX] spill write failed (queue is still in memory): {exc}", flush=True)


# ── the desk's public half ──────────────────────────────────────────────────

def set_desk_public(public_b64: str) -> bool:
    """Accept the public half from the desk handshake. True if it changed.

    Validated here rather than at seal time so a bad handshake is loud at the
    handshake, not once per queued fact.
    """
    global _desk_public
    _load()
    try:
        if len(base64.b64decode(public_b64, validate=True)) != 32:
            raise ValueError("not a 32-byte X25519 public key")
    except (binascii.Error, ValueError, TypeError) as exc:
        print(f"[OUTBOX] ⛔ rejected desk public key: {exc}", flush=True)
        return False
    if public_b64 == _desk_public:
        return False
    _desk_public = public_b64
    try:
        DESK_KEY_FILE.write_text(json.dumps({"public": public_b64}), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[OUTBOX] desk key cache write failed: {exc}", flush=True)
    print("[OUTBOX] ✅ desk public half accepted — PC-off turns will be sealed to it.",
          flush=True)
    return True


def desk_public() -> Optional[str]:
    _load()
    return _desk_public


# ── queueing ────────────────────────────────────────────────────────────────

def queue_fact(user_text: str, who: str = "KAUSTAV", tier=None,
               reply: Optional[str] = None) -> Optional[dict]:
    """Seal one PC-off turn and queue it. Returns the envelope, or None.

    Never raises. This is called on the path that is about to answer him, and a
    lost fact must never cost him the reply.
    """
    _load()
    if _desk_public is None:
        global _dropped_no_key
        _dropped_no_key += 1
        # No public half means no way to seal, and plaintext-at-rest is not on
        # the table. Say so every time — this is real loss, not a nit.
        print(f"[OUTBOX] ⚠ no desk public key yet — fact NOT queued "
              f"(dropped {_dropped_no_key} so far this process). The desk hands "
              f"the key over on its next connect.", flush=True)
        return None
    try:
        payload = fact_seal.new_fact(user_text, who=who, tier=tier, reply=reply)
        envelope = fact_seal.seal_fact(payload, _desk_public)
    except Exception as exc:  # noqa: BLE001
        print(f"[OUTBOX] ⛔ could not seal a fact: {exc}", flush=True)
        return None

    if len(_outbox) >= MAX_OUTBOX:
        global _dropped_overflow
        dropped = _outbox.popleft()
        _dropped_overflow += 1
        print(f"[OUTBOX] ⚠ outbox full at {MAX_OUTBOX} — dropped the OLDEST record "
              f"{dropped['envelope'].get('id')} to make room "
              f"({_dropped_overflow} lost this way).", flush=True)

    _outbox.append({"envelope": envelope, "attempts": 0})
    _persist()
    return envelope


def pending(limit: Optional[int] = None) -> list:
    """Queued envelopes, oldest first."""
    _load()
    items = list(_outbox) if limit is None else list(_outbox)[:limit]
    return [item["envelope"] for item in items]


def depth() -> int:
    _load()
    return len(_outbox)


def ack(ids) -> int:
    """Drop acked records. Returns how many left the queue.

    Everything the desk did not name stays where it was, in order — that is what
    makes a mid-batch drop resumable.
    """
    _load()
    wanted = set(ids or ())
    if not wanted:
        return 0
    before = len(_outbox)
    kept = [item for item in _outbox if item["envelope"].get("id") not in wanted]
    _outbox.clear()
    _outbox.extend(kept)
    removed = before - len(_outbox)
    if removed:
        _persist()
    return removed


# ── delivery ────────────────────────────────────────────────────────────────

async def flush(send: Callable[[dict], Awaitable[None]], batch: int = BATCH) -> int:
    """Push the queue at a connected desk. Returns records handed to `send`.

    Records are NOT removed here. They leave on the ack, so a socket that dies
    between the send and the ack costs a redelivery rather than a fact.
    """
    _load()
    if not _outbox:
        return 0

    # Dead-letter first: a record that has been offered MAX_ATTEMPTS times and
    # never acked is not going to start working now.
    global _dead_lettered
    poison = [item for item in _outbox if item["attempts"] >= MAX_ATTEMPTS]
    if poison:
        for item in poison:
            print(f"[OUTBOX] ☠ dead-lettered {item['envelope'].get('id')} after "
                  f"{item['attempts']} unacked deliveries.", flush=True)
        _dead_lettered += len(poison)
        ack([item["envelope"].get("id") for item in poison])
        if not _outbox:
            return 0

    items = list(_outbox)
    sent = 0
    for start in range(0, len(items), max(1, batch)):
        chunk = items[start:start + max(1, batch)]
        try:
            await send({"type": "facts", "records": [i["envelope"] for i in chunk]})
        except Exception as exc:  # noqa: BLE001
            # The link died mid-flush. Everything unsent stays queued; what went
            # out is unacked and will be re-offered. Neither is a loss.
            print(f"[OUTBOX] flush interrupted after {sent} record(s): {exc}", flush=True)
            break
        for item in chunk:
            item["attempts"] += 1
        sent += len(chunk)
    if sent:
        _persist()
        print(f"[OUTBOX] handed {sent} sealed fact(s) to the desk; "
              f"{len(_outbox)} awaiting ack.", flush=True)
    return sent


async def handle_desk_frame(frame: dict,
                            send: Optional[Callable[[dict], Awaitable[None]]] = None) -> bool:
    """Handle the two fact frames the desk sends. True if this frame was ours.

    `fact_key` is the handshake: accepting it is what makes flushing possible,
    so a flush follows immediately when a sender is available.
    """
    if not isinstance(frame, dict):
        return False
    ftype = frame.get("type")
    if ftype == "fact_key":
        set_desk_public(frame.get("public") or "")
        if send is not None and desk_public() is not None:
            await flush(send)
        return True
    if ftype == "fact_ack":
        removed = ack(frame.get("ids") or [])
        if removed:
            print(f"[OUTBOX] desk acked {removed} record(s); {depth()} left.", flush=True)
        return True
    return False


def stats() -> dict:
    _load()
    return {
        "depth": len(_outbox),
        "has_desk_key": _desk_public is not None,
        "dropped_no_key": _dropped_no_key,
        "dropped_overflow": _dropped_overflow,
        "dead_lettered": _dead_lettered,
    }


def reset_state() -> None:
    """Test-only: forget everything, including the loaded flag."""
    global _loaded, _desk_public, _dropped_no_key, _dropped_overflow, _dead_lettered
    _outbox.clear()
    _desk_public = None
    _loaded = False
    _dropped_no_key = _dropped_overflow = _dead_lettered = 0
