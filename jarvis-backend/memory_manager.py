"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  J.A.R.V.I.S.  —  PHASE 5: MEMORY OS                                       ║
║  memory_manager.py                                                           ║
║                                                                              ║
║  Dedicated MemoryManager for cross-session persistence.                      ║
║  This module sits BELOW memory.py (Tier 1 RAM / Tier 3 ChromaDB) as         ║
║  a clean Tier 2 replacement / enhancement for the SQLite long-term store.   ║
║                                                                              ║
║  Responsibilities:                                                           ║
║    1. add_memory()          — persist a categorized memory to SQLite         ║
║    2. get_relevant_memories() — retrieve the N freshest memories (opt.        ║
║                                 filtered by category) for prompt injection   ║
║    3. delete_memory()       — surgical removal by row ID                     ║
║    4. extract_memories_from_input() — fire a fast LLM call to decide        ║
║                                 whether the user utterance contains a        ║
║                                 permanent Preference, Fact, or Correction,   ║
║                                 and return a structured list if so.          ║
║                                                                              ║
║  Design constraints:                                                         ║
║    • Pure sqlite3 — no ORM dependency.                                       ║
║    • Thread-safe: every method opens + closes its own connection.            ║
║    • All LLM calls use JSON mode so parsing never fails silently.            ║
║    • The extraction call is deliberately lightweight (8b model, 256 tokens). ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import re
import json
import sqlite3
import datetime
from typing import Literal, Optional

from dotenv import load_dotenv
from modules.groq_key_manager import groq_model, has_groq_keys, run_with_key_rotation
from modules import memory_crypto as _crypto
from modules.memory_crypto import MemoryLockedError

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv(override=True)

# Visible extraction failures (ANSI red — readable on Windows Terminal / modern PowerShell).
def _mem_err(reason: str, detail: object | None = None) -> None:
    extra = f" | {detail!r}" if detail is not None and str(detail).strip() else ""
    print(f"\033[1;91m[MEMORY_MANAGER] ERROR:\033[0m {reason}{extra}", flush=True)


# Fast, cheap model for background extraction (does not compete with main LLM latency).
# Session 4: was a hardcoded `llama-3.1-8b-instant`, which Groq had
# decommissioned — so EVERY extraction 404'd, silently, because this pipeline
# swallows its own errors by design. One id, one place: groq_key_manager.
_EXTRACTION_MODEL: str = groq_model()

# ── Database path ─────────────────────────────────────────────────────────────
# Stored next to the existing jarvis_memory.db so both live in the same dir.
_DB_PATH: str = os.path.join(os.path.dirname(__file__), "jarvis_longterm.db")

# ── Category type alias ───────────────────────────────────────────────────────
MemoryCategory = Literal["Preference", "Correction", "Fact"]

# ── At-rest encryption (C#11a) ────────────────────────────────────────────────
# `content` is encrypted whenever a key set exists on this machine; there is no
# flag to forget. Reads decrypt unconditionally — plaintext passes straight
# through, which is what makes a half-finished migration still readable.
#
# A locked key raises MemoryLockedError out of these functions ON PURPOSE. The
# generic `except Exception: return []` below would otherwise turn "I cannot
# open your memory" into "you have no memories", which is indistinguishable
# from having forgotten him.
_TABLE = "memories"
_ENC_COLUMN = "content"


# ── "it failed" is not "there was nothing" (review finding M1) ───────────────
#
# Every function below reports a fault by returning the same value it uses for
# "nothing to do": `[]`, or `False`. For a live turn that is right — a memory
# extraction must never cost him his reply, and the next sentence he says gets
# another go.
#
# For a DRAINED CLOUD FACT it is fatal. `fact_sink.governed_write` returns
# `saved > 0`, and `fact_drain` reads False as a verdict — "the sink saw it and
# declined it" — so it ledgers the record STORED and acks it. The cloud then
# drops the sealed envelope permanently and the ledger guarantees the redelivery
# is skipped. **A Groq rate limit therefore destroyed the fact and logged
# `0 new, N already known`.** That path is unattended by definition: there is no
# next sentence, and nobody sees it happen.
#
# So the callers that cannot afford the ambiguity pass `strict=True` and get an
# exception instead. `fact_drain` treats any non-FactSealError as a FAULT and
# HOLDS the record for the next connect — "a locked key store must cost a retry,
# not a fact", now true of a rate-limited extractor too. Every live caller keeps
# the old contract exactly, because the default is unchanged.
class MemoryOperationError(RuntimeError):
    """A memory operation FAILED — as distinct from finding or storing nothing."""


class ExtractionFailedError(MemoryOperationError):
    """The extractor could not run: no key, a rate limit, a timeout, a reply
    that would not parse. Nothing is known about whether this turn held a fact."""


class MemoryWriteError(MemoryOperationError):
    """The row could not be written. NOT raised for a duplicate — that is a
    successful outcome with nothing new in it."""


def one_line(text) -> str:
    """One memory is ONE line. Review finding M3, 2026-08-16.

    `content.strip()` trims the ends only, SQL parameters preserve embedded
    newlines, and `format_memory_block` joins entries with a newline — which
    `brain.py` splices RAW into the system prompt with no delimiter and no
    escaping. So a single stored fact could become N prompt lines that are
    byte-identical to genuine `[Correction]` entries, and Corrections sort FIRST
    into every turn.

    `remember_fact` is AUTO in governance.json, so the argument is never
    inspected, and under the §6.8 tool layer that argument can be lifted
    verbatim from a web result or an indexed document. That is a persistent
    prompt injection with one AUTO action and no prompt to the owner.

    Collapsed on the WAY IN (add_memory) and on the WAY OUT
    (format_memory_block): rows written before today, or by any other path, must
    not be able to render as more than one line either. Same treatment
    `partner_messaging.normalise_body` already gives a message body.
    """
    return " ".join(str(text or "").split())


def _encryption_on() -> bool:
    return _crypto.keys_ready()


def _decrypt_row_content(value):
    return _crypto.decrypt_field(value, _TABLE, _ENC_COLUMN)


# ── Provenance (`source`) ─────────────────────────────────────────────────────
# Which route a memory arrived by. `desk` is something he said to JARVIS with the
# machine in front of him; `cloud` is a fact the Render gateway captured while the
# PC was off, sealed, and the desk later drained through the governed sink
# (modules/fact_sink.py). The second is a weaker guarantee — the cloud saw that
# turn in plaintext, and a sealed record's `who` is a claim, not a credential — so
# the two must be tellable apart in the store.
#
# DELIBERATELY PLAINTEXT, unlike `content` beside it. Ruled 2026-08-02. Encryption
# here is randomised per write, so a sealed `source` could never satisfy
# `WHERE source = ?`, which is the entire point of the column; the blind-index
# workaround that rescues `content_hash` would leak the same distribution anyway
# for a two-value vocabulary. Same reasoning that keeps `partner_messages.slot`
# in the clear: metadata you filter on stays queryable, payloads get sealed.
# Do NOT add this column to migrate_memory_encryption.TARGETS.
SOURCE_DESK = "desk"
SOURCE_CLOUD = "cloud"

#: Closed vocabulary. add_memory REFUSES anything else rather than coercing it —
#: a mislabelled write is worse than a rejected one, because the whole feature is
#: being able to trust the label.
KNOWN_SOURCES = frozenset({SOURCE_DESK, SOURCE_CLOUD})

#: Rows written before this column existed. The backfill sets them to `desk` and
#: the inference is airtight: cloud drain did not exist before the column did, so
#: every pre-existing row IS desk-origin. Reads apply the same default so a store
#: that has not been migrated yet still answers correctly.
_LEGACY_SOURCE = SOURCE_DESK


def _normalise_source(source: Optional[str]) -> Optional[str]:
    """The stored value for a caller-supplied source, or None if unacceptable."""
    value = (source or SOURCE_DESK).strip().lower()
    return value if value in KNOWN_SOURCES else None


def _row_source(value: Optional[str]) -> str:
    return value if value else _LEGACY_SOURCE


def _ensure_source_column(conn: sqlite3.Connection) -> bool:
    """Add `source` if it is missing. Idempotent; returns True if it was added.

    Shared by _init_db() and migrate_memory_source.py ON PURPOSE, so the live
    schema and the migrated copy cannot drift apart.

    `ALTER TABLE ... ADD COLUMN` with no DEFAULT is metadata-only in SQLite: it
    rewrites the schema header and does not touch a single row, which is why it
    is safe here while the row-by-row BACKFILL is not, and gets the full
    copy-verify-swap ceremony in the migration script instead.
    """
    columns = {r[1] for r in conn.execute(f"PRAGMA table_info({_TABLE})")}
    if "source" in columns:
        return False
    conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN source TEXT")
    return True


# =============================================================================
# DATABASE BOOTSTRAP
# =============================================================================

def _init_db() -> None:
    """
    Create the database and the `memories` table if they do not already exist.
    Called once at module import time — idempotent, safe to run on every boot.

    Schema:
      id        — auto-increment PK; used by delete_memory()
      category  — one of 'Preference', 'Correction', 'Fact'
      content   — the actual memory string (UNIQUE to prevent exact duplication)
      user      — the active user at extraction time (KAUSTAV / MOUSUMI / etc.)
      timestamp — ISO 8601 UTC string; used for ordering by freshness
    """
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                category  TEXT    NOT NULL CHECK(category IN ('Preference','Correction','Fact')),
                content   TEXT    NOT NULL,
                user      TEXT    NOT NULL DEFAULT 'KAUSTAV',
                timestamp TEXT    NOT NULL
            )
        """)
        # Prevent storing identical strings for the same user.
        # (Different users CAN share content — e.g. "prefers dark mode".)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_user_content
            ON memories (user, content)
        """)

        # C#11a: once `content` is ciphertext the index above can never fire —
        # every encryption of the same fact produces different bytes. Duplicate
        # detection moves to a keyed fingerprint of the plaintext.
        columns = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        if "content_hash" not in columns:
            conn.execute("ALTER TABLE memories ADD COLUMN content_hash TEXT")

        # Provenance. Same additive, metadata-only shape as content_hash above.
        # Existing rows are left NULL here and read as `desk`; the backfill that
        # actually writes to every row is migrate_memory_source.py, which does it
        # on a verified copy rather than in place.
        _ensure_source_column(conn)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user_source
            ON memories (user, source)
        """)

        # UNIQUE here would fail outright if two existing rows collide under
        # normalisation. That failure must not take the whole boot down, and it
        # must not be silent either: fall back to a plain index and say so, so
        # migrate_memory_encryption.py can report the colliding rows for a
        # decision rather than quietly dropping one of them.
        try:
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_user_content_hash
                ON memories (user, content_hash)
            """)
        except sqlite3.IntegrityError:
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_user_content_hash_dup
                ON memories (user, content_hash)
            """)
            print(
                "[MEMORY_MANAGER] WARNING: duplicate memories block the unique "
                "fingerprint index — run migrate_memory_encryption.py --report",
                flush=True,
            )
        conn.commit()
    finally:
        conn.close()

# Bootstrap on first import — no external call required.
_init_db()
print(f"[MEMORY_MANAGER] SQLite store initialised -> {_DB_PATH}", flush=True)


# =============================================================================
# CORE CRUD METHODS
# =============================================================================

def add_memory(
    content: str,
    category: MemoryCategory = "Fact",
    user: str = "KAUSTAV",
    source: str = SOURCE_DESK,
    strict: bool = False,
) -> bool:
    """
    Persist a single memory to the database.

    Args:
        content  : The human-readable memory string.
                   e.g. "Sir prefers dark-mode interfaces."
        category : 'Preference', 'Correction', or 'Fact'.
        user     : Active user identifier (matches brain.py naming).
        source   : How it arrived — SOURCE_DESK (default, and what every live
                   write is) or SOURCE_CLOUD (drained from the PC-off backlog).
                   Defaulted so every existing caller keeps its exact behaviour.
        strict   : raise `MemoryWriteError` on a WRITE FAULT instead of returning
                   False (finding M1). A duplicate still returns False under
                   strict — it is a successful outcome with nothing new in it,
                   and the caller must be free to ack the record.

    Returns:
        True  — memory was inserted successfully.
        False — memory was a duplicate (silently ignored), the source was not one
                this store issues, or an error occurred.

    Thread-safety:
        Opens a fresh connection per call — safe to call from any thread or
        asyncio.to_thread() without a shared lock.
    """
    if not content or not content.strip():
        return False

    stored_source = _normalise_source(source)
    if stored_source is None:
        # Refused rather than coerced to `desk`. Silently relabelling a write
        # this store cannot place would defeat the only thing the column is for.
        print(f"[MEMORY_MANAGER] REFUSED a write with source={source!r} — known "
              f"sources are {sorted(KNOWN_SOURCES)}.", flush=True)
        return False

    # M3: one memory, one line — before the hash, before the encryption, before
    # the row. A newline in here is a new line of the system prompt.
    content = one_line(content)
    timestamp = datetime.datetime.utcnow().isoformat()

    # Encrypt before the row leaves this function. A locked key raises rather
    # than falling back to plaintext — silently writing readable rows into a
    # store he believes is encrypted is worse than a loud failure.
    stored = content
    content_hash = None
    if _encryption_on():
        stored = _crypto.encrypt_field(content, _TABLE, _ENC_COLUMN)
        content_hash = _crypto.blind_index(content, _TABLE, _ENC_COLUMN)

    conn = sqlite3.connect(_DB_PATH)
    try:
        # Explicit duplicate check as well as the index: the fingerprint index
        # is only UNIQUE when no pre-existing rows collided (see _init_db).
        #
        # `source` is NOT part of this key, on purpose. The same fact arriving by
        # both routes is still one fact, and the existing row is left untouched —
        # so the FIRST writer's provenance is what stands. A later echo down the
        # weaker path cannot relabel a memory he gave in person, and a live
        # restatement does not launder a cloud-drained one into `desk`.
        if content_hash is not None:
            hit = conn.execute(
                "SELECT 1 FROM memories WHERE user = ? AND content_hash = ? LIMIT 1",
                (user.upper(), content_hash),
            ).fetchone()
            if hit:
                print(f"[MEMORY_MANAGER] Duplicate skipped: {content[:60]}", flush=True)
                return False

        conn.execute(
            "INSERT INTO memories (category, content, user, timestamp, content_hash, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (category, stored, user.upper(), timestamp, content_hash, stored_source),
        )
        conn.commit()
        print(
            f"[MEMORY_MANAGER] +{category} [{user}/{stored_source}]: {content[:80]}",
            flush=True,
        )
        return True
    except sqlite3.IntegrityError:
        # Duplicate — silently ignore.  This is the happy path for idempotency.
        print(f"[MEMORY_MANAGER] Duplicate skipped: {content[:60]}", flush=True)
        return False
    except Exception as exc:
        print(f"[MEMORY_MANAGER] add_memory error: {exc}", flush=True)
        if strict:
            # A drained cloud fact: returning False here would be read as
            # "already known" and the sealed original destroyed. M1.
            raise MemoryWriteError(f"add_memory failed: {exc}") from exc
        return False
    finally:
        conn.close()


def get_relevant_memories(
    user: str = "KAUSTAV",
    category: Optional[MemoryCategory] = None,
    limit: int = 5,
) -> list[dict]:
    """
    Retrieve the N most recently stored memories for injection into the prompt.

    Args:
        user     : Filter to a specific user.  Defaults to 'KAUSTAV'.
        category : Optional category filter ('Preference', 'Correction', 'Fact').
                   Pass None to retrieve across all categories.
        limit    : Maximum number of rows to return (default 5).

    Returns:
        A list of dicts, each with keys: id, category, content, timestamp, source.
        Ordered newest-first.

    Usage in brain.py:
        memories = get_relevant_memories(user=active_user, limit=5)
        block = format_memory_block(memories)   # → inject into prompt
    """
    conn = sqlite3.connect(_DB_PATH)
    try:
        if category:
            rows = conn.execute(
                """
                SELECT id, category, content, timestamp, source
                FROM memories
                WHERE user = ? AND category = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user.upper(), category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, category, content, timestamp, source
                FROM memories
                WHERE user = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user.upper(), limit),
            ).fetchall()
        return [
            {"id": r[0], "category": r[1], "content": _decrypt_row_content(r[2]),
             "timestamp": r[3], "source": _row_source(r[4])}
            for r in rows
        ]
    except MemoryLockedError:
        # Never degrade to []. An empty profile reads as "you never told me
        # that" — he would believe the facts were lost.
        raise
    except Exception as exc:
        print(f"[MEMORY_MANAGER] get_relevant_memories error: {exc}", flush=True)
        return []
    finally:
        conn.close()


def get_full_profile(user: str = "KAUSTAV") -> list[dict]:
    """
    Retrieve EVERY stored memory for a user — no arbitrary limit.
    Results are ordered Correction → Preference → Fact so the synthesis LLM
    sees behavioural corrections and preferences before raw facts.
    """
    conn = sqlite3.connect(_DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT id, category, content, timestamp, source
            FROM memories
            WHERE user = ?
            ORDER BY
                CASE category
                    WHEN 'Correction' THEN 1
                    WHEN 'Preference' THEN 2
                    ELSE 3
                END,
                id DESC
            """,
            (user.upper(),),
        ).fetchall()
        return [
            {"id": r[0], "category": r[1], "content": _decrypt_row_content(r[2]),
             "timestamp": r[3], "source": _row_source(r[4])}
            for r in rows
        ]
    except MemoryLockedError:
        raise
    except Exception as exc:
        print(f"[MEMORY_MANAGER] get_full_profile error: {exc}", flush=True)
        return []
    finally:
        conn.close()


# =============================================================================
# PROVENANCE: FILTER + AUDIT
# =============================================================================

def get_memories_by_source(
    source: str,
    user: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Every memory that arrived by one route. The audit path.

    This is what `source` being plaintext buys: an exact SQL filter, no key
    needed to select the rows (the content still needs one to read). Untagged
    legacy rows answer to SOURCE_DESK, matching how the read paths report them.
    """
    wanted = _normalise_source(source)
    if wanted is None:
        print(f"[MEMORY_MANAGER] unknown source {source!r} — known sources are "
              f"{sorted(KNOWN_SOURCES)}.", flush=True)
        return []

    clauses, params = [], []
    if wanted == _LEGACY_SOURCE:
        clauses.append("(source = ? OR source IS NULL)")
    else:
        clauses.append("source = ?")
    params.append(wanted)
    if user:
        clauses.append("user = ?")
        params.append(user.upper())

    sql = ("SELECT id, category, content, timestamp, source, user FROM memories "
           f"WHERE {' AND '.join(clauses)} ORDER BY id DESC")
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    conn = sqlite3.connect(_DB_PATH)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [
            {"id": r[0], "category": r[1], "content": _decrypt_row_content(r[2]),
             "timestamp": r[3], "source": _row_source(r[4]), "user": r[5]}
            for r in rows
        ]
    except MemoryLockedError:
        raise
    except Exception as exc:
        print(f"[MEMORY_MANAGER] get_memories_by_source error: {exc}", flush=True)
        return []
    finally:
        conn.close()


def source_counts(user: Optional[str] = None) -> dict:
    """How many rows arrived by each route. Needs no key at all.

    `untagged` is reported SEPARATELY from `desk` even though the read paths
    treat them identically — it is how you tell whether migrate_memory_source.py
    has run, and a number that quietly folded into `desk` would hide that.
    """
    sql = "SELECT source, COUNT(*) FROM memories"
    params: list = []
    if user:
        sql += " WHERE user = ?"
        params.append(user.upper())
    sql += " GROUP BY source"

    counts = {s: 0 for s in sorted(KNOWN_SOURCES)}
    counts["untagged"] = 0
    conn = sqlite3.connect(_DB_PATH)
    try:
        for value, n in conn.execute(sql, params).fetchall():
            key = value if value else "untagged"
            counts[key] = counts.get(key, 0) + n
        return counts
    except Exception as exc:
        print(f"[MEMORY_MANAGER] source_counts error: {exc}", flush=True)
        return counts
    finally:
        conn.close()


def get_balanced_memories_for_prompt(
    user: str = "KAUSTAV",
    total_limit: int = 14,
    *,
    correction_cap: int = 5,
    preference_cap: int = 5,
) -> list[dict]:
    """
    Retrieval for supervisor prompts: newest-first within each category, merged as
    Correction → Preference → Fact so behaviour-changing rows surface before facts.

    This is intentionally simple "scoring": priority bands + recency inside each band.
    """
    if total_limit <= 0:
        return []

    u = user.upper()
    corrections = get_relevant_memories(u, category="Correction", limit=min(correction_cap, total_limit))
    preferences = get_relevant_memories(u, category="Preference", limit=min(preference_cap, total_limit))
    used = len(corrections) + len(preferences)
    fact_limit = max(0, total_limit - used)
    facts = (
        get_relevant_memories(u, category="Fact", limit=fact_limit)
        if fact_limit
        else []
    )
    return corrections + preferences + facts


def delete_memory(memory_id: int) -> bool:
    """
    Surgically remove a single memory record by its primary-key ID.

    Args:
        memory_id : The `id` field from a get_relevant_memories() result.

    Returns:
        True if a row was deleted; False if the ID was not found or an error occurred.

    Use-case:
        J.A.R.V.I.S. can expose a "forget that" command that maps to this.
        Example action: {"action_type": "delete_memory", "target": "42"}
    """
    conn = sqlite3.connect(_DB_PATH)
    try:
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()
        if cursor.rowcount > 0:
            print(f"[MEMORY_MANAGER] Deleted memory id={memory_id}", flush=True)
            return True
        print(f"[MEMORY_MANAGER] No row found for id={memory_id}", flush=True)
        return False
    except Exception as exc:
        print(f"[MEMORY_MANAGER] delete_memory error: {exc}", flush=True)
        return False
    finally:
        conn.close()


# =============================================================================
# PROMPT FORMATTING HELPER
# =============================================================================

def format_memory_block(memories: list[dict]) -> str:
    """
    Convert a list of memory dicts into a clean, prompt-ready string block.

    Output format:
        [LONG-TERM MEMORY]
        [Preference] Sir prefers dark-mode interfaces.
        [Fact] Sir's dog is named Bruno, a 4-month-old Labrador.
        [Correction] Never say 'Certainly'. Use 'Right away' or just act.

    Returns an empty string if the list is empty — caller should guard for this.
    """
    if not memories:
        return ""

    lines = ["[LONG-TERM MEMORY]"]
    for m in memories:
        # M3: the renderer is the SINK into the system prompt, so it collapses
        # too. A row stored before this rule existed — or written by any path
        # that skips add_memory — still cannot become two prompt lines here.
        lines.append(f"  [{one_line(m['category'])}] {one_line(m['content'])}")
    return "\n".join(lines)


# =============================================================================
# BACKGROUND LLM EXTRACTION
# =============================================================================

# System prompt — MUST match Groq response_format json_object (top-level JSON **object**, not a bare array).
_EXTRACTION_SYSTEM_PROMPT: str = """You are a silent background memory-extraction agent for J.A.R.V.I.S.

Your ONLY job: read the user's message and decide whether it contains PERMANENT information to remember across sessions.

Categories (exact spelling):
  "Preference"  — recurring likes/dislikes or working-style choices.
  "Fact"        — stable facts about the user or their world (pets, age of pets, location, etc.).
  "Correction"  — explicit instructions to change how J.A.R.V.I.S. speaks or behaves.

OUTPUT FORMAT (mandatory — Groq JSON mode requires an object):
Return ONLY valid JSON with exactly this shape — no markdown, no prose:
{"memories": []}
when nothing should be stored, OR
{"memories": [{"category": "Fact", "content": "..."}, ...]}
Use category exactly one of: Preference | Correction | Fact.

Each "content" must be one concise third-person sentence suitable for storage, e.g.
  BAD:  "I like dark mode"
  GOOD: "Sir prefers dark-mode interfaces."

Hypothetical output-format examples (DO NOT copy these into real output — see ANTI-PLAGIARISM RULE below):
  {"category": "Fact",       "content": "User resides on Planet Xylophone."}
  {"category": "Preference", "content": "User prefers Zorblax-flavoured tea."}
  {"category": "Correction", "content": "Always address User as Supreme Overlord Blorptron."}

RULES:
1. Transient commands ("read file X", "open notepad"), chit-chat → {"memories": []}

2. ZERO INFERENCE RULE: You must ONLY extract memories if the user makes an EXPLICIT, DEFINITIVE statement about themselves, their life, or their preferences. The statement must directly describe the user — not something they are searching for, curious about, or requesting.
   VALID triggers: "I love X", "My brother is Y", "I live in Z", "Remember that I prefer...", "Always call me...", "Note that my dog's name is..."
   INVALID triggers: anything that doesn't directly assert a personal truth about the user.

3. CRITICAL — IGNORE QUERIES: Do NOT extract preferences, facts, or corrections from search requests, action commands, questions, or hypothetical scenarios. The following input patterns MUST ALWAYS return {"memories": []}:
   a) Web/data search commands — any input starting with or containing: "search for", "find", "look up", "google", "check", "show me", "get me", "fetch", "browse".
      Example: "Search for best React libraries" → {"memories": []} (do NOT store "Sir likes React libraries").
   b) Action-verb commands — inputs whose primary verb is an instruction to J.A.R.V.I.S.: "open", "close", "play", "pause", "set", "run", "write", "create", "delete", "list", "read", "send", "enable", "disable", "turn on/off", "give me", "snapshot", "diagnostic", "status".
      Example: "Play jazz music" → {"memories": []} (do NOT store "Sir prefers jazz").
      Example: "Give me a full system snapshot" → {"memories": []} (system query, no personal fact).
   c) Questions — any input ending with "?" or containing question words: "what", "who", "where", "when", "why", "how", "do you", "can you", "have you", "is there".
   d) Hypotheticals — any input containing: "if", "would", "could", "might", "suppose", "imagine", "what if", "let's say".

4. CRITICAL — QUESTION GUARD: If the user message is a QUESTION or recall request, ALWAYS return {"memories": []}. NEVER extract from a question. Examples that MUST return {"memories": []}: "Do you remember my IDE preference?" / "What do you remember about me?" / "What is my coffee order?"

5. Only extract from an explicit personal DECLARATION: "Remember that...", "I prefer...", "My X is Y", "Always do Z", "Note that...", "I love...", "I hate...", "I am a...".

6. Be conservative; when in any doubt return {"memories": []}

7. Do not add any keys other than "memories".

8. ANTI-PLAGIARISM RULE: You are strictly forbidden from outputting the hypothetical examples provided in these instructions. You may ONLY extract facts that literally appear in the current user prompt. If the user prompt does not contain new facts, return {"memories": []}."""


# ── Guard: the extractor echoing its own instructions back ───────────────────
#
# Rule 8 of the prompt forbids this. It happened anyway — "Always address User
# as Supreme Overlord Blorptron." was living in his real profile as a
# Correction, the category that sorts FIRST into every prompt injection. An LLM
# rule is a request, so this is the part that actually enforces it.
#
# The sentences are parsed OUT of the prompt rather than copied, so editing the
# examples above cannot silently leave this guard checking for stale text.

def _prompt_example_sentences() -> set:
    """Every `"content": "..."` literal in the system prompt, normalised."""
    found = re.findall(r'"content"\s*:\s*"([^"]+)"', _EXTRACTION_SYSTEM_PROMPT)
    return {_normalise_for_echo(s) for s in found if s and s != "..."}


#: Invented words that exist only in those examples. Kept explicit because
#: deriving "which words are nonsense" is guesswork, and a wrong guess would
#: silently discard a real memory. test_memory_extraction_guard.py asserts
#: these still appear in the prompt, so changing the examples fails loudly.
_PROMPT_NONSENSE_TOKENS = ("xylophone", "zorblax", "blorptron")


def _normalise_for_echo(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", " ".join((text or "").split()).casefold()).strip()


def is_prompt_echo(content: str) -> bool:
    """True if the model handed back one of its own worked examples.

    Deliberately narrow. A false negative just means one junk row to delete by
    hand; a false positive silently throws away something he actually said. So
    this matches whole sentences, not themes — "Sir prefers dark-mode
    interfaces" appears in the prompt as a style example and is ALSO a perfectly
    real preference, so it is not blocked.
    """
    normalised = _normalise_for_echo(content)
    if not normalised:
        return False
    if normalised in _prompt_example_sentences():
        return True
    return any(token in normalised for token in _PROMPT_NONSENSE_TOKENS)


def _extraction_failed(reason: str, detail: object, strict: bool) -> list:
    """One place where a failure is reported, so the two contracts cannot drift.

    Loud either way; the only difference is whether the caller is told in a form
    it can act on. See the M1 note at the top of this module.
    """
    _mem_err(reason, detail)
    if strict:
        raise ExtractionFailedError(reason)
    return []


def extract_memories_from_input(
    user_text: str,
    user: str = "KAUSTAV",
    strict: bool = False,
) -> list[dict]:
    """
    Fire a fast, lightweight LLM call to extract permanent memories from a
    single user utterance.  Returns a list of extracted memory dicts, or []
    if nothing permanent was detected.

    `strict=True` (finding M1) raises `ExtractionFailedError` when the CALL
    failed — no key, a rate limit, a timeout, a reply that would not parse —
    rather than reporting it as "this turn had no fact in it". A turn that
    genuinely holds nothing still returns `[]` under strict; the distinction is
    between *nothing found* and *nothing looked*.

    This function is designed to run in a background thread via asyncio.to_thread()
    so it never blocks the main event loop.

    Args:
        user_text : The raw user input from this conversational turn.
        user      : The active user identifier.

    Returns:
        A (possibly empty) list of dicts: [{"category": ..., "content": ...}, ...]

    LLM call spec:
        Model         : _EXTRACTION_MODEL — groq_model(), one id for the desk
        Temperature   : 0.0  (deterministic extraction)
        Max tokens    : 256  (JSON array never needs more)
        Response fmt  : json_object  (Groq JSON mode — structurally enforced)
        Timeout       : 15s  (extraction must not stall the pipeline)

    Integration:
        Called in main.py BEFORE process_command() so new memories are stored
        even if the main LLM call fails.
    """
    if not user_text or not user_text.strip():
        return []

    if not has_groq_keys():
        return _extraction_failed(
            "GROQ_API_KEY / GROQ_API_KEYS missing — cannot run Memory OS extraction.",
            None, strict)

    raw = ""
    try:
        # ── LLM call (429-aware key rotation via groq_key_manager) ─────────────
        completion = run_with_key_rotation(
            lambda c: c.chat.completions.create(
                model=_EXTRACTION_MODEL,
                messages=[
                    {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.0,
                max_tokens=512,
                timeout=20.0,
                response_format={"type": "json_object"},
            )
        )

        msg = completion.choices[0].message if completion.choices else None
        if msg is None or not msg.content:
            return _extraction_failed(
                "Groq returned empty message content for extraction", None, strict)

        raw = msg.content.strip()
        print(f"[MEMORY_MANAGER] Extraction raw response: {raw[:200]}", flush=True)

        # ── Parse the returned JSON ───────────────────────────────────────────
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            _mem_err(f"JSON decode failed on extraction response ({exc})", raw[:300])

            # Second-chance: strip accidental fences
            stripped = raw.strip()
            if stripped.startswith("```"):
                stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
                stripped = re.sub(r"\s*```\s*$", "", stripped)
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return _extraction_failed(
                        "extraction reply would not parse even de-fenced",
                        raw[:300], strict)
            else:
                return _extraction_failed(
                    "extraction reply would not parse", raw[:300], strict)

        # Canonical shape: {"memories": [...]}
        items_list: list | None = None
        if isinstance(parsed, dict):
            mem = parsed.get("memories")
            if isinstance(mem, list):
                items_list = mem
            else:
                for key in ("items", "data", "results", "memory"):
                    if key in parsed and isinstance(parsed[key], list):
                        items_list = parsed[key]
                        print(
                            f"[MEMORY_MANAGER] WARN: model used legacy key {key!r}; "
                            "prefer top-level 'memories'.",
                            flush=True,
                        )
                        break
                if items_list is None:
                    if "category" in parsed and "content" in parsed:
                        items_list = [parsed]
                    elif parsed == {} or all(k not in parsed for k in ("memories", "category")):
                        return []
                    else:
                        return _extraction_failed(
                            "Unexpected JSON object shape (expected 'memories' array)",
                            parsed, strict)
        elif isinstance(parsed, list):
            items_list = parsed
        else:
            return _extraction_failed(
                "Top-level JSON must be an object or list",
                type(parsed).__name__, strict)

        if items_list is None:
            return []

        # ── Validate each item ────────────────────────────────────────────────
        valid_categories = {"Preference", "Correction", "Fact"}
        results: list[dict] = []
        for row in items_list:
            if not isinstance(row, dict):
                continue
            cat = row.get("category", "").strip()
            content = row.get("content", "").strip()
            if cat not in valid_categories or not content:
                continue
            if is_prompt_echo(content):
                # Rule 8 already forbids this in the prompt. It happened anyway:
                # "Always address User as Supreme Overlord Blorptron." sat in his
                # real profile as a Correction — the category that sorts FIRST
                # into every prompt. A prompt rule is a request; this is the
                # enforcement.
                _mem_err("Discarded a prompt example echoed back as a memory", content)
                continue
            results.append({"category": cat, "content": content})

        return results

    except MemoryOperationError:
        # Already reported by _extraction_failed. Re-raised rather than caught
        # below, or strict mode would be swallowed by the very handler it exists
        # to correct.
        raise
    except Exception as exc:
        return _extraction_failed(
            "extract_memories_from_input failed (API timeout, rate limit, or "
            "unexpected error)", exc, strict)


# =============================================================================
# CONVENIENCE: EXTRACT + PERSIST IN ONE CALL
# =============================================================================

def extract_and_persist(user_text: str, user: str = "KAUSTAV",
                        source: str = SOURCE_DESK,
                        strict: bool = False) -> int:
    """
    Convenience wrapper: extract memories from user_text then persist each one.

    Returns the count of NEW memories committed to the database.

    This is the function called by brain.py's extract_and_store_memory()
    and is safe to run via asyncio.to_thread().

    `source` defaults to SOURCE_DESK, so every live path — brain.py, main.py,
    streaming_daemon.py — is untouched. modules/fact_sink.py is the one caller
    that passes SOURCE_CLOUD.

    `strict` (finding M1) makes a FAILURE raise rather than return 0. 0 under
    strict means what it says: the turn held no new fact — the extractor ran and
    found nothing, or everything it found was already known. Nothing else can
    produce it.
    """
    extracted = extract_memories_from_input(user_text, user, strict=strict)
    saved = 0
    for mem in extracted:
        ok = add_memory(
            content=mem["content"],
            category=mem["category"],   # type: ignore[arg-type]
            user=user,
            source=source,
            strict=strict,
        )
        if ok:
            saved += 1
    if extracted and saved == 0:
        print(
            f"[MEMORY_MANAGER] Extracted {len(extracted)} candidate(s) but 0 new SQLite rows "
            f"(duplicates or DB error) for {user}.",
            flush=True,
        )
    if saved:
        print(
            f"[MEMORY_MANAGER] Persisted {saved}/{len(extracted)} new memories for {user}.",
            flush=True,
        )
    return saved
