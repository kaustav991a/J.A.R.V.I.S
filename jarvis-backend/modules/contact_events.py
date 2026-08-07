r"""contact_events.py — THAT a partner made contact, and when. Never what she said.

The butler's store (roadmap §6.7). A good butler says *"Madam rang, around three
— nothing pressing."* This table is the only thing he consults to say it, and it
is built so that saying more is not possible: **there is no content column.**

DISCRETION BY CONSTRUCTION, NOT BY CARE
---------------------------------------
`partner_log` holds her words (opt-in, sealed, and read only by the explicit
`summarize_partner_chat` override). This store holds three facts — who, when,
and one urgency bit — and nothing else. The separation is the security property:

  * `record()` takes no message parameter. A future edit cannot accidentally
    persist content here, because there is no argument through which content
    could arrive. The urgency scan happens in `partner_contact`, upstream, and
    only its boolean crosses the boundary.
  * A caller that wants her words has to go somewhere else entirely, and that
    somewhere else looks different, is flagged, and discloses itself.

So "the butler cannot leak the message" is not a claim about how carefully the
formatting code was written. It is a claim about which columns exist.

WHAT IS ENCRYPTED, AND WHY THIS DIFFERS FROM partner_log
---------------------------------------------------------
Everything that carries meaning: `partner_slot`, `timestamp`, `urgency`. All
three are AES-256-GCM through the existing C#11a field encryption — same DEK,
same DPAPI wrap, same recovery code, no new key mechanism and no new dependency.

`partner_log` deliberately leaves its `timestamp` readable because its secret is
the message body and every query filters on time. **Here the timestamp IS the
secret.** This table's entire content is a contact pattern; left in the clear,
a stolen copy plus the `partner_key` grouping would reveal exactly when and how
often she reaches him — which is most of what the store knows. So it is sealed,
and ordering is taken from the autoincrement `id` instead, which is insertion
order and therefore already chronological. Nothing needs to ORDER BY a
timestamp, which is what makes encrypting it affordable.

Two columns stay queryable, and both are deliberate:

  * `id` — the ordering. Leaks the total number of events, nothing about whom.
  * `partner_key` — a keyed blind index of the slot (`memory_crypto.blind_index`,
    the same primitive behind `memories.content_hash`). Randomised encryption
    cannot satisfy `WHERE partner_slot = ?`, so lookup moves here. It is an HMAC
    under a DEK-derived subkey: useless without the key, and it does not permit
    confirming a guess. It does let a thief see which rows belong to the same
    unnamed person, which is the accepted residual — the alternative is loading
    and decrypting every row in the table on every question.

Degrades exactly like the rest of the system: with no key set, values are stored
in the clear and `partner_key` holds the plain slot. A store written before the
ceremony keeps reading afterwards, because lookups try both predicates.

A locked keystore RAISES on read. "I cannot open the record" must never be
rendered as "she did not call" — the C#11a silent-empty-read rule, applied to a
person.

Reuses `jarvis_longterm.db`, the database `memory_manager` already owns, per the
"reuse an existing db, don't invent a store" constraint. Standard library only.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from modules import memory_crypto as _crypto
from modules.memory_crypto import MemoryLockedError  # re-exported for callers

__all__ = [
    "TABLE", "ENV_FLAG", "MemoryLockedError",
    "enabled", "ensure_table", "record", "recent", "count",
]

#: Reuses memory_manager's database file — no new store.
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "jarvis_longterm.db")

TABLE = "contact_events"

#: Opt-in switch, default **OFF** — same shape as `JARVIS_LOG_PARTNER_CHATS`.
#: It shipped default-ON (2026-08-02) on the reasoning that this store keeps only
#: the fact that a message arrived, roughly what a housemate in the hallway would
#: observe. That reasoning still holds, and it is still not enough: **anything
#: that records a third party's behaviour is opt-in here**, and the discipline is
#: worth more than the convenience of one fewer line in `.env`. Flipped by
#: Kaustav's ruling 2026-08-08 (RESUME item 2), so a fresh clone of this repo
#: records nothing about anyone until its owner says otherwise.
#:
#: Set to 1/true/yes/on to record. While it is off `partner_contact_status` says
#: it cannot tell either way and names this flag — never "no, she didn't
#: message", which would be a confident answer manufactured by a switch.
ENV_FLAG = "JARVIS_LOG_CONTACT_EVENTS"
_TRUE = frozenset({"1", "true", "yes", "on"})

#: AAD column names. Distinct per column, so a sealed timestamp cannot be read
#: back as a slot, and neither can be pasted in from `partner_messages`.
_COL_SLOT = "partner_slot"
_COL_TIME = "timestamp"
_COL_URGENCY = "urgency"


def enabled(env=None) -> bool:
    """Is contact-event recording on? Default OFF — it must be switched on
    deliberately. Read per call so a change takes effect without a restart.

    Unset, empty and unrecognised all read as OFF: a typo in `.env` must fail
    towards not recording, never towards recording.
    """
    src = os.environ if env is None else env
    return str(src.get(ENV_FLAG, "")).strip().lower() in _TRUE


def _encryption_on() -> bool:
    """Same switch as every other store — the presence of a key set, not an env
    flag someone can forget."""
    return _crypto.keys_ready()


def _encrypt(value, column: str):
    if value is None or not _encryption_on():
        return value
    return _crypto.encrypt_field(value, TABLE, column)


def _decrypt(value, column: str):
    """Plaintext passes through, so rows written before the ceremony still read."""
    return _crypto.decrypt_field(value, TABLE, column)


def _partner_key(partner_slot: str) -> str:
    """The lookup handle for one partner.

    Blind index when a key set exists; the plain slot otherwise. Both shapes can
    coexist in one table — see the two-predicate lookup in `recent()`.
    """
    if not _encryption_on():
        return partner_slot
    return _crypto.blind_index(partner_slot, TABLE, _COL_SLOT) or partner_slot


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    return sqlite3.connect(db_path or DB_PATH)


def ensure_table(db_path: str | None = None) -> None:
    """Create the table if missing. Called only on a write, so a system that has
    never received a partner message has no table at all."""
    conn = _connect(db_path)
    try:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_key  TEXT NOT NULL,
                partner_slot TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                urgency      TEXT NOT NULL
            )
        """)
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_key_id "
            f"ON {TABLE} (partner_key, id DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def record(partner_slot: str, *, urgent: bool = False, when: datetime | None = None,
           env=None, db_path: str | None = None) -> bool:
    """Record ONE contact event. Returns True only if a row was written.

    There is deliberately **no message parameter**. The caller assesses urgency
    (`partner_contact.assess_urgency`) and passes the resulting boolean; the text
    that produced it never reaches this module, so it cannot be stored here by
    accident, by a later refactor, or by a caller that means well.
    """
    if not enabled(env):
        return False
    slot = (partner_slot or "").strip()
    if not slot:
        return False

    stamp = (when or datetime.now(timezone.utc))
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()

    ensure_table(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            f"INSERT INTO {TABLE} (partner_key, partner_slot, timestamp, urgency) "
            f"VALUES (?, ?, ?, ?)",
            (_partner_key(slot),
             _encrypt(slot, _COL_SLOT),
             _encrypt(stamp.astimezone(timezone.utc).isoformat(), _COL_TIME),
             _encrypt("1" if urgent else "0", _COL_URGENCY)),
        )
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001 — a logging fault must never break her chat
        print(f"[CONTACT-EVENTS] write failed ({slot}): {e}", flush=True)
        return False
    finally:
        conn.close()


def recent(partner_slot: str, limit: int = 100,
           db_path: str | None = None) -> list[dict]:
    """This partner's most recent contact events, newest first.

    Ordered by `id` because the timestamp is ciphertext and cannot be sorted on
    — insertion order is chronological, so this is the same ordering by another
    route.

    A locked keystore propagates `MemoryLockedError` rather than returning [].
    An empty list means "she did not call"; a raise means "I cannot open the
    record". The caller must be able to tell those apart.
    """
    slot = (partner_slot or "").strip()
    if not slot:
        return []
    limit = max(1, min(int(limit or 100), 1000))

    # Both handles are tried: rows written under a key use the blind index, rows
    # written before the ceremony hold the plain slot. Neither shape is stranded
    # when the key set arrives.
    keys = [_partner_key(slot)]
    if keys[0] != slot:
        keys.append(slot)

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT timestamp, urgency FROM {TABLE} "
            f"WHERE partner_key IN ({','.join('?' * len(keys))}) "
            f"ORDER BY id DESC LIMIT ?",
            (*keys, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # No table — nothing has ever been recorded. Distinct from a locked key,
        # which raises out of the decrypt below.
        return []
    finally:
        conn.close()

    return [{"timestamp": _decrypt(r[0], _COL_TIME),
             "urgent": _decrypt(r[1], _COL_URGENCY) == "1"}
            for r in rows]


def count(partner_slot: str, db_path: str | None = None) -> int:
    """How many events exist for one partner. Needs no key — `partner_key` is
    the blind index, so an audit can count without opening anything."""
    slot = (partner_slot or "").strip()
    if not slot:
        return 0
    keys = [_partner_key(slot)]
    if keys[0] != slot:
        keys.append(slot)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE partner_key IN "
            f"({','.join('?' * len(keys))})", tuple(keys)).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()
