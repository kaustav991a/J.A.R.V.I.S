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

At-rest encryption: these rows are personal third-party data and belong under
roadmap TIER C #11 (encrypted-at-rest store). Until that lands they are plain
SQLite in `jarvis_longterm.db` — the SAME database `memory_manager` already
owns, per the "reuse an existing db, don't invent a store" constraint.

Standard library only (sqlite3), path- and env-injectable so the harness proves
the OFF default against a temp file and never touches the real database.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

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
            (slot, partner_id, partner_name, DIRECTION_INBOUND, text,
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
    return [{"content": r[0], "timestamp": r[1], "partner_name": r[2]}
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
