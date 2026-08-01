"""C#11a Step 4 — the desk side: drain the sealed backlog on connect.

    desk connects  ->  hands over its public half  ->  cloud flushes
    each record: seen before?   ->  ack, do nothing
                 opens?         ->  hand to the sink, record it, ack
                 does not?      ->  quarantine, record it, ack (dead-lettered)
                 sink REFUSES?  ->  quarantine, record it, ack (dead-lettered)
                 sink FAULTS?   ->  ack nothing, hold it for the next connect
    key store locked            ->  raise, ack NOTHING, leave the batch alone

Two layers of idempotency, and they answer different questions:

  * the LEDGER here (record UUID) stops a redelivered record from being opened
    and re-extracted at all — it is what makes the cloud's "unacked records get
    re-offered" behaviour free rather than expensive;
  * the C#11a blind index (`memories.content_hash`) is the guarantee that
    actually protects the store, because it keys on the FACT, not the record.
    It holds even if the same fact arrives by a different route entirely.

Redelivery is normal here, not exceptional: the cloud re-offers anything it did
not see an ack for, which is how a bridge that drops mid-batch resumes.

Draining a fact into memory is a WRITE and has to go through the governed path,
so this module cannot reach memory by itself and does not try: it imports no
store, and `modules/fact_sink.py` — which runs the governance gate — is the only
thing ever handed to `set_sink()`. Until a sink is installed, records are HELD:
not acked, not ledgered, so nothing is lost and nothing is written. An un-acked
record is still sitting in the cloud outbox.

A sink has two ways to say no, and they are not the same event. A
`fact_seal.FactSealError` is a VERDICT about the record (malformed past the
seal, or governance refused it): dead-letter it, ledger it, ack it — keeping it
for inspection, and never offering it again. Any other exception is a FAULT on
our side (a locked key store, a busy database): ack nothing, so it comes back.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from modules import fact_seal

BACKEND_DIR = Path(__file__).resolve().parent.parent
LEDGER_DB = BACKEND_DIR / "jarvis_fact_ledger.db"

STORED = "stored"
QUARANTINED = "quarantined"

# Installed by Phase 3 with the governed write path. Signature:
#   sink(payload: dict) -> bool     (True = accepted; raise = store fault)
_sink: Optional[Callable[[dict], bool]] = None


def set_sink(fn: Optional[Callable[[dict], bool]]) -> None:
    global _sink
    _sink = fn


def has_sink() -> bool:
    return _sink is not None


# ── the ledger ──────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(LEDGER_DB), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Idempotent. Holds record IDs only — never a fact, sealed or otherwise."""
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drained (
                id       TEXT PRIMARY KEY,
                seen_at  TEXT NOT NULL,
                outcome  TEXT NOT NULL,
                who      TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def already_drained(record_id: str) -> bool:
    if not record_id:
        return False
    init_db()
    conn = _connect()
    try:
        return conn.execute("SELECT 1 FROM drained WHERE id = ? LIMIT 1",
                            (record_id,)).fetchone() is not None
    finally:
        conn.close()


def _record(record_id: str, outcome: str, who: Optional[str] = None) -> None:
    init_db()
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO drained (id, seen_at, outcome, who) VALUES (?, ?, ?, ?)",
            (record_id, datetime.datetime.now(datetime.timezone.utc).isoformat(),
             outcome, who),
        )
        conn.commit()
    finally:
        conn.close()


def ledger_count(outcome: Optional[str] = None) -> int:
    init_db()
    conn = _connect()
    try:
        if outcome is None:
            return conn.execute("SELECT COUNT(*) FROM drained").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM drained WHERE outcome = ?",
                            (outcome,)).fetchone()[0]
    finally:
        conn.close()


# ── draining ────────────────────────────────────────────────────────────────

def drain_records(records) -> dict:
    """Drain one batch. Returns counts plus the ids to ack.

    The private half is unwrapped ONCE, before the loop. A locked key store is
    not any record's fault, so MemoryLockedError propagates with an empty ack
    list and the whole batch stays in the cloud outbox for the next connect.
    """
    if not isinstance(records, list):
        return {"stored": 0, "duplicates": 0, "quarantined": 0, "held": 0, "ack": []}

    result = {"stored": 0, "duplicates": 0, "quarantined": 0, "held": 0, "ack": []}
    if not records:
        return result

    if not has_sink():
        # Phase 2 state. Nothing acked, so nothing is lost — the cloud keeps them.
        result["held"] = len(records)
        print(f"[DRAIN] {len(records)} sealed fact(s) held — no memory sink installed "
              f"yet (Phase 3 wires the governed write). They stay in the cloud outbox.",
              flush=True)
        return result

    raw = fact_seal.desk_private_raw()      # MemoryLockedError propagates on purpose

    for envelope in records:
        record_id = envelope.get("id") if isinstance(envelope, dict) else None

        if isinstance(record_id, str) and already_drained(record_id):
            # Redelivery. Ack it so the cloud stops offering it; do NOT re-open
            # and do NOT re-extract.
            result["duplicates"] += 1
            result["ack"].append(record_id)
            continue

        payload = fact_seal.open_or_quarantine(envelope, raw)
        if payload is None:
            # Dead-lettered: quarantined on disk, ledgered so a redelivery is a
            # cheap duplicate, and acked so one poison record cannot wedge the
            # queue behind it.
            result["quarantined"] += 1
            if isinstance(record_id, str) and record_id:
                _record(record_id, QUARANTINED)
                result["ack"].append(record_id)
            continue

        try:
            accepted = _sink(payload)
        except fact_seal.FactSealError as exc:
            # The sink REFUSED the record itself — governance said no, or the
            # payload was malformed past the seal. That is a verdict, not a
            # fault, so it takes the same road as a record that would not open:
            # dead-lettered, ledgered, acked. Kept, and never written.
            # No `who` on this row on purpose: a refused record's claimed
            # identity is exactly the field that failed to check out, and the
            # ledger is not encrypted. Accepted rows carry theirs; this one does
            # not get to write an unvetted string to disk.
            fact_seal.quarantine(envelope, f"sink refused: {exc}")
            result["quarantined"] += 1
            if isinstance(record_id, str) and record_id:
                _record(record_id, QUARANTINED)
                result["ack"].append(record_id)
            continue
        except Exception as exc:  # noqa: BLE001
            # A store fault is not a record fault. Leave it unacked and unledgered
            # so the next connect tries again — losing a fact to a transient
            # database error would be the worst outcome available here.
            print(f"[DRAIN] ⛔ sink faulted on {record_id}: {exc} — record HELD "
                  f"for the next connect.", flush=True)
            result["held"] += 1
            continue

        if accepted is False:
            # The sink saw it and declined it (a duplicate under content_hash, or
            # governance said no). It has been handled — ack and ledger it, or the
            # cloud would offer it forever.
            result["duplicates"] += 1
        else:
            result["stored"] += 1
        _record(record_id, STORED, payload.get("who"))
        result["ack"].append(record_id)

    print(f"[DRAIN] batch of {len(records)}: {result['stored']} stored, "
          f"{result['duplicates']} already known, {result['quarantined']} quarantined, "
          f"{result['held']} held.", flush=True)
    return result


# ── the two frames this side speaks ─────────────────────────────────────────

def handshake_frame() -> dict:
    """Sent on every connect. Idempotent on the cloud, and the reason a rotated
    keypair needs no coordination — the next connect simply carries the new half."""
    return {"type": "fact_key", "public": fact_seal.desk_public_b64()}


def handle_cloud_frame(frame: dict) -> Optional[dict]:
    """Handle a `facts` frame. Returns the ack frame to send, or None.

    Returns None when there is nothing to ack — including the held case, where
    silence is exactly right: the cloud keeps the records.
    """
    if not isinstance(frame, dict) or frame.get("type") != "facts":
        return None
    result = drain_records(frame.get("records") or [])
    if not result["ack"]:
        return None
    return {"type": "fact_ack", "ids": result["ack"]}
