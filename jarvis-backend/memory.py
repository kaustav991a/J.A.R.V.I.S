import sqlite3
import datetime
import chromadb
import uuid
import os
import threading
from dotenv import load_dotenv

load_dotenv(override=True)

DB_PATH = "jarvis_memory.db"

# ==========================================
# TIER 1: SHORT-TERM WORKING MEMORY
# ==========================================
# Holds the last 30 conversational turns with automatic compression.
# When memory exceeds 30, the oldest 15 messages are summarized by the LLM
# into a single context message to preserve information without flooding the prompt.
working_memory = []
# G5.7: working_memory is touched from several threads (main turn loop, background
# monitors, worker loop, streaming daemon). A bare list whose head is slice-assigned
# by _compress racing an append corrupts it or raises mid-iteration. One re-entrant
# lock guards every mutate/read; getters return COPIES so callers iterate a snapshot.
# The LLM summarize call is made OUTSIDE the lock so it never stalls other threads.
_wm_lock = threading.RLock()

def add_to_working_memory(role, content):
    """Adds a message to the short-term memory queue (keeps the last 30 messages)."""
    with _wm_lock:
        working_memory.append({"role": role, "content": content})
        over = len(working_memory) > 30
    if over:
        _compress_oldest_memories()   # self-locking; the LLM call runs unlocked

def _compress_oldest_memories():
    """Summarizes the oldest 15 messages into a single context message using the LLM.
    Snapshots the head under the lock, summarizes UNLOCKED (network), then applies the
    replacement under the lock — appends from other threads only touch the tail, so the
    leading 15 we're replacing stay put."""
    global working_memory

    with _wm_lock:
        old_messages = list(working_memory[:15])
    if not old_messages:
        return

    # Build a transcript for summarization (outside the lock)
    transcript_lines = []
    for msg in old_messages:
        role_label = "User" if msg["role"] == "user" else "JARVIS"
        transcript_lines.append(f"{role_label}: {str(msg['content'])[:200]}")
    transcript = "\n".join(transcript_lines)

    try:
        from modules.groq_key_manager import run_with_key_rotation

        completion = run_with_key_rotation(
            lambda c: c.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{
                    "role": "system",
                    "content": (
                        "Summarize this conversation excerpt in 2-3 concise sentences. "
                        "Preserve key facts, names, and decisions. Do not add commentary.\n\n"
                        f"{transcript}"
                    ),
                }],
                temperature=0.2,
                max_tokens=100,
            )
        )
        summary = completion.choices[0].message.content.strip()

        # Replace the oldest 15 messages with a single summary
        with _wm_lock:
            working_memory[:15] = [{
                "role": "system",
                "content": f"[CONTEXT SUMMARY] {summary}"
            }]
        print(f"[MEMORY] Compressed 15 messages into context summary")
    except Exception as e:
        # Fallback: just trim if LLM fails
        print(f"[MEMORY] Compression failed ({e}), falling back to simple trim")
        with _wm_lock:
            working_memory[:15] = []

def get_working_memory():
    """Returns a COPY of the FULL short-term buffer (snapshot, safe to iterate)."""
    with _wm_lock:
        return list(working_memory)


# Token-trim: how many recent messages the LLM actually sees per turn. The full
# 30-message buffer above is kept for compression/consolidation, but shipping all
# of it to the model every turn is the single biggest INPUT-token cost. The
# semantic + episodic recall re-injects older *relevant* context anyway, so a
# short raw window loses nothing important while cutting the payload hard.
# Override with JARVIS_HISTORY_TURNS (set to 30 to restore the old behaviour).
import os as _os
_HISTORY_TURNS = int(_os.getenv("JARVIS_HISTORY_TURNS", "12"))


def get_context_window(limit: int | None = None):
    """Return the last `limit` messages for the LLM call, ALWAYS keeping a
    leading [CONTEXT SUMMARY] system message if the buffer has one (so the
    compressed older history isn't dropped). Falls back to the full buffer when
    limit is None or the buffer is already small."""
    n = _HISTORY_TURNS if limit is None else limit
    with _wm_lock:
        if n <= 0 or len(working_memory) <= n:
            return list(working_memory)
        tail = working_memory[-n:]
        head = working_memory[0]
        if (head not in tail
                and head.get("role") == "system"
                and str(head.get("content", "")).startswith("[CONTEXT SUMMARY]")):
            return [head] + list(tail)
        return list(tail)

def clear_working_memory():
    """Wipes the short-term memory (useful for a reset command)."""
    global working_memory
    with _wm_lock:
        working_memory = []

# ==========================================
# TIER 1.5: SESSION DIGEST (Sleep/Wake Continuity)
# ==========================================
# Bridges the gap between volatile working memory (wiped on sleep) and the
# permanent long-term store. On sleep we condense the just-finished conversation
# into a short digest; on wake we re-seed working memory with it so J.A.R.V.I.S.
# remembers what you were *just* doing instead of starting from zero.
SESSION_RECAP_PREFIX = "[PREVIOUS SESSION RECAP]"

def save_session_digest(user: str, digest: str) -> None:
    """Persists (or replaces) the single rolling session digest for a user."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO session_digest (user, digest, timestamp) VALUES (?, ?, ?)",
            (user, digest, datetime.datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

def get_last_session_digest(user: str) -> str | None:
    """Returns the stored session digest for a user, or None if none exists."""
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT digest FROM session_digest WHERE user = ?", (user,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None

def consolidate_working_memory(user: str = "KAUSTAV") -> str | None:
    """
    Condenses the current in-RAM working memory into a 2-3 sentence digest and
    persists it. Called just BEFORE clear_working_memory() on sleep/standby.

    Runs synchronously (offload via asyncio.to_thread) and never raises — a
    failure here must never block the system going to sleep.
    Returns the digest string if one was produced, else None.
    """
    # Only consolidate genuine conversation turns; ignore system recaps/stubs.
    with _wm_lock:
        snapshot = list(working_memory)
    real_turns = [
        m for m in snapshot
        if m.get("role") in ("user", "assistant")
        and m.get("content")
        and not str(m.get("content", "")).startswith(SESSION_RECAP_PREFIX)
    ]
    if len(real_turns) < 2:
        return None

    transcript_lines = []
    for m in real_turns[-20:]:  # cap context
        role_label = "User" if m["role"] == "user" else "JARVIS"
        transcript_lines.append(f"{role_label}: {str(m['content'])[:200]}")
    transcript = "\n".join(transcript_lines)

    try:
        from modules.llm_router import universal_llm_call
        digest = universal_llm_call(
            messages=[{
                "role": "system",
                "content": (
                    "Summarize this conversation between the user and J.A.R.V.I.S. in 2-3 "
                    "concise sentences. Capture what the user was doing, any open tasks, "
                    "decisions, and key facts so the assistant can resume seamlessly later. "
                    "Write it as a factual recap addressed to J.A.R.V.I.S. Do NOT add commentary.\n\n"
                    f"{transcript}"
                ),
            }],
            temperature=0.2,
            max_tokens=120,
            stream=False,
            json_mode=False,
            timeout=20.0,
        )
        digest = (digest or "").strip()
        if digest:
            save_session_digest(user, digest)
            print(f"[MEMORY] Session digest stored for {user}: {digest[:80]}...", flush=True)
            return digest
    except Exception as e:
        print(f"[MEMORY] Session consolidation failed (non-fatal): {e}", flush=True)
    return None

def seed_from_last_digest(user: str = "KAUSTAV") -> str | None:
    """
    Re-seeds fresh working memory with the last session's digest so the assistant
    retains immediate context on wake. Should be called AFTER clear_working_memory().
    Returns the digest that was seeded, or None if there was nothing to seed.
    """
    digest = get_last_session_digest(user)
    if not digest:
        return None
    # Guard against double-seeding the same recap.
    with _wm_lock:
        snapshot = list(working_memory)
    for m in snapshot:
        if str(m.get("content", "")).startswith(SESSION_RECAP_PREFIX):
            return digest
    add_to_working_memory(
        "system",
        f"{SESSION_RECAP_PREFIX} Earlier, before standby: {digest} "
        f"If the user picks up where they left off, use this context naturally — do not announce it.",
    )
    print(f"[MEMORY] Working memory seeded with prior session recap for {user}.", flush=True)
    return digest

# ==========================================
# TIER 2: LONG-TERM SQLITE MEMORY
# ==========================================
def init_db():
    """Creates the SQLite database and tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create the table for persistent facts
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS long_term_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            fact TEXT UNIQUE,
            timestamp TEXT
        )
    ''')

    # Holds ONE rolling "session digest" per user — the LLM-condensed recap of the
    # last conversation. Written just before working memory is wiped on sleep, and
    # read back on wake to re-seed short-term context (fixes the sleep/wake amnesia).
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS session_digest (
            user TEXT PRIMARY KEY,
            digest TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

def remember_fact(category, fact):
    """Saves a permanent fact to the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    timestamp = datetime.datetime.now().isoformat()
    
    try:
        cursor.execute('''
            INSERT INTO long_term_memory (category, fact, timestamp)
            VALUES (?, ?, ?)
        ''', (category, fact, timestamp))
        conn.commit()
        print(f"[MEMORY] Logged to permanent storage: {fact}")
    except sqlite3.IntegrityError:
        # Ignore duplicate facts
        pass 
    finally:
        conn.close()

def recall_all_facts():
    """Retrieves all stored facts to inject into J.A.R.V.I.S.'s core system prompt."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT fact FROM long_term_memory')
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return "No specific user preferences saved yet."
        
    memory_strings = [f"- {row[0]}" for row in rows]
    return "\n".join(memory_strings)

# Initialize the database immediately when the backend boots
init_db()

# ==========================================
# TIER 3: CHROMA VECTOR MEMORY (SEMANTIC)
# ==========================================
CHROMA_PATH = "jarvis_chroma_db"
try:
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    semantic_collection = chroma_client.get_or_create_collection(name="jarvis_memory")
except Exception as e:
    print(f"[MEMORY] WARNING: Failed to initialize ChromaDB. {e}")
    semantic_collection = None

def save_semantic_memory(user: str, fact: str):
    """Embeds and saves a permanent fact into the Vector Database."""
    if not semantic_collection:
        return
    try:
        memory_id = str(uuid.uuid4())
        semantic_collection.add(
            documents=[fact],
            metadatas=[{"user": user, "timestamp": datetime.datetime.now().isoformat()}],
            ids=[memory_id]
        )
        print(f"[MEMORY] Logged semantic memory for {user}: {fact}")
    except Exception as e:
        print(f"[MEMORY] Failed to save semantic memory: {e}")

def recall_semantic_context(user: str, query: str, n_results: int = 3) -> str:
    """Searches the vector database for the most relevant past memories."""
    if not semantic_collection:
        return "No relevant past memories found."
    try:
        results = semantic_collection.query(
            query_texts=[query],
            n_results=n_results,
            where={"user": user} # Only recall facts belonging to the current user
        )
        
        documents = results.get("documents")
        if documents and documents[0]:
            memory_strings = [f"- {doc}" for doc in documents[0]]
            return "\n".join(memory_strings)
        return "No relevant past memories found."
    except Exception as e:
        print(f"[MEMORY] Semantic recall failed: {e}")
        return "Memory retrieval offline."