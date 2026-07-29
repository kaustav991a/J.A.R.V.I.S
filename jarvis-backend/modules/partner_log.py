r"""partner_log.py — the opt-in record of what a partner told JARVIS.

Why this exists: channel isolation (session_manager) means a partner's Telegram
conversation lives in her own session. Nothing the owner's session reads ever
saw it, so "what did my girlfriend tell you" honestly failed. This module is the
explicit store that makes the PULL in `summarize_partner_chat` possible.

It logs a third party's messages so someone else can read them. That is a
decision, not a feature, so:

  * **`JARVIS_LOG_PARTNER_CHATS` is OFF by default and there is no other way to
    turn it on.** Off means `log_inbound()` writes nothing at all — the rows do
    not exist, so no later change of mind can retroactively read them. Same
    discipline as the backdoor flag: a capability the system does not offer is
    safer than one it merely declines.
  * Every summary built from these rows DISCLOSES that it is logged data
    (`DISCLOSURE`). The owner should never forget where the knowledge came from.
  * **Inbound only.** JARVIS's own replies are not stored — the minimum data
    that answers the question.

Scope note, deliberately narrow: this flag governs THIS raw store only. JARVIS's
existing per-user memory extraction (`brain.extract_and_store_memory` →
`memory_manager`) has always run for every recognised caller and keeps running
with the flag off — that is how the brain knows who she is and answers her
naturally. Turning this flag off therefore means "no verbatim transcript is
kept", NOT "nothing about her is retained". Owner's explicit ruling 2026-07-26.

At-rest encryption (C#11a, landed 2026-07-30): `content` and `partner_name` are
AES-256-GCM encrypted whenever a key set exists on this machine. They live in
`jarvis_longterm.db` — the SAME database `memory_manager` already owns, per the
"reuse an existing db, don't invent a store" constraint.

`partner_slot`, `direction` and `timestamp` stay readable because every query
filters on them; `partner_id` stays readable because it is a Telegram id the
`.env` already holds in the clear. So the honest claim is: a stolen copy of this
file reveals THAT a slot was active and when, but not a word of what she said.

Standard library only (sqlite3), path- and env-injectable so the harness proves
the OFF default against a temp file and never touches the real database.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from modules import memory_crypto as _crypto
from modules.memory_crypto import MemoryLockedError

ENV_FLAG = "JARVIS_LOG_PARTNER_CHATS"
_TRUE = frozenset({"1", "true", "yes", "on"})

#: Reuses memory_manager's database file — no new store.
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "jarvis_longterm.db")

TABLE = "partner_messages"

DISCLOSURE = (
    "Note: this comes from logged partner messages, recorded because "
    "JARVIS_LOG_PARTNER_CHATS is switched on."
)

DIRECTION_INBOUND = "inbound"

#: Columns encrypted at rest. Everything else is needed by a WHERE or ORDER BY.
_ENC_COLUMNS = ("content", "partner_name")


def _encrypt(value, column: str):
    if not _crypto.keys_ready():
        return value
    return _crypto.encrypt_field(value, TABLE, column)


def _decrypt(value, column: str):
    """Plaintext passes through, so rows written before C#11a still read."""
    return _crypto.decrypt_field(value, TABLE, column)


def logging_enabled(env=None) -> bool:
    """Is partner-chat logging switched on? Default **off**, read per call."""
    src = os.environ if env is None else env
    return str(src.get(ENV_FLAG, "0")).strip().lower() in _TRUE


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    return sqlite3.connect(db_path or DB_PATH)


def ensure_table(db_path: str | None = None) -> None:
    """Create the table if missing. Only ever called on a write, so a system
    running with the flag off never even creates the table."""
    conn = _connect(db_path)
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_slot TEXT NOT NULL,
                partner_id   INTEGER,
                partner_name TEXT,
                direction    TEXT NOT NULL,
                content      TEXT NOT NULL,
                timestamp    TEXT NOT NULL
            )
        """)
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_slot_time "
            f"ON {TABLE} (partner_slot, timestamp DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def log_inbound(partner_slot: str, content: str, *, partner_id: int | None = None,
                partner_name: str | None = None, env=None,
                db_path: str | None = None) -> bool:
    """Record ONE inbound partner message. Returns True only if a row was written.

    Writes nothing — not even an empty table — when the flag is off, when the
    slot is unknown, or when the message is blank.
    """
    if not logging_enabled(env):
        return False
    slot = (partner_slot or "").strip()
    text = (content or "").strip()
    if not slot or not text:
        return False

    ensure_table(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            f"INSERT INTO {TABLE} (partner_slot, partner_id, partner_name, "
            f"direction, content, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (slot, partner_id, _encrypt(partner_name, "partner_name"),
             DIRECTION_INBOUND, _encrypt(text, "content"),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001 — a logging fault must never break her chat
        print(f"[PARTNER-LOG] write failed ({slot}): {e}", flush=True)
        return False
    finally:
        conn.close()


def recent(partner_slot: str, limit: int = 20,
           db_path: str | None = None) -> list[dict]:
    """The partner's most recent messages, oldest-first for readability.

    Reads only the requested slot — one partner's rows can never appear in
    another partner's history, whatever the caller asks for.
    """
    slot = (partner_slot or "").strip()
    if not slot:
        return []
    limit = max(1, min(int(limit or 20), 200))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT content, timestamp, partner_name FROM {TABLE} "
            f"WHERE partner_slot = ? AND direction = ? "
            f"ORDER BY id DESC LIMIT ?",
            (slot, DIRECTION_INBOUND, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # No table yet — nothing was ever logged (the flag has never been on).
        return []
    finally:
        conn.close()
    # A locked key propagates: "I cannot open her messages" must never be
    # rendered as "she never said anything".
    return [{"content": _decrypt(r[0], "content"), "timestamp": r[1],
             "partner_name": _decrypt(r[2], "partner_name")}
            for r in reversed(rows)]


def count(partner_slot: str, db_path: str | None = None) -> int:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE partner_slot = ?",
            ((partner_slot or "").strip(),),
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
