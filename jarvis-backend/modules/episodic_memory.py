import os
import json
import datetime
import uuid
import chromadb

# ==========================================
# TIER 4: EPISODIC MEMORY (Conversation Log)
# ==========================================
# Every session is saved as a JSON log file and its summary
# is embedded into ChromaDB for semantic retrieval across sessions.

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
CHROMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jarvis_chroma_db")

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Initialize the episodic ChromaDB collection
EPISODES_COLLECTION = "jarvis_episodes"

try:
    _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    episodes_collection = _chroma_client.get_or_create_collection(name=EPISODES_COLLECTION)
except Exception as e:
    print(f"[EPISODIC] WARNING: Failed to initialize episodic ChromaDB. {e}")
    episodes_collection = None

# Review finding M5 named `jarvis_memory`; this collection is the same defect one
# door over, in the same folder, and the document here is a summary of a WHOLE
# CONVERSATION rather than one fact. Sealed on the same terms: content
# encrypted, `user`/`date` metadata left in the clear because both are filtered
# on and the SQLite half keeps its equivalents plain too.
from modules import chroma_crypto as _chroma_crypto  # noqa: E402

_embed_fn = None


def _embedder():
    """Chroma's DEFAULT embedding function, held explicitly — the same model
    this collection already uses, so old and new vectors share a space."""
    global _embed_fn
    if _embed_fn is None:
        from chromadb.utils import embedding_functions
        _embed_fn = embedding_functions.DefaultEmbeddingFunction()
    return _embed_fn

# The in-memory session buffer
_current_session = []
_session_user = "KAUSTAV"
_session_start = datetime.datetime.now()


def log_turn(role: str, content: str, user: str = "KAUSTAV"):
    """Called after every user/assistant exchange to record the conversation."""
    global _session_user
    _session_user = user
    
    _current_session.append({
        "timestamp": datetime.datetime.now().isoformat(),
        "role": role,
        "content": content,
        "user": user
    })


def save_session(groq_client=None):
    """
    Called when the user says 'go to sleep'.
    1. Saves the full session to a JSON file on disk.
    2. Generates a summary and embeds it into ChromaDB for semantic search.
    """
    global _current_session, _session_start
    
    if not _current_session or len(_current_session) < 2:
        print("[EPISODIC] Session too short to save.")
        _current_session = []
        return
    
    # --- 1. SAVE RAW SESSION TO DISK ---
    session_id = uuid.uuid4().hex[:8]
    date_str = datetime.date.today().isoformat()
    filename = f"{date_str}_session_{session_id}.json"
    filepath = os.path.join(LOG_DIR, filename)
    
    session_data = {
        "session_id": session_id,
        "user": _session_user,
        "started": _session_start.isoformat(),
        "ended": datetime.datetime.now().isoformat(),
        "turn_count": len(_current_session),
        "turns": _current_session
    }
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)
        print(f"[EPISODIC] Session saved to {filepath} ({len(_current_session)} turns)")
    except Exception as e:
        print(f"[EPISODIC] Failed to save session file: {e}")
    
    # --- 2. GENERATE SUMMARY & EMBED INTO CHROMADB ---
    if episodes_collection:
        try:
            from modules.groq_key_manager import groq_model, run_with_key_rotation
            # Build a condensed transcript for the LLM
            transcript_lines = []
            for turn in _current_session[-20:]:  # Last 20 turns max
                role_label = "User" if turn["role"] == "user" else "JARVIS"
                transcript_lines.append(f"{role_label}: {turn['content'][:150]}")
            transcript = "\n".join(transcript_lines)
            
            summary_prompt = f"""Summarize this conversation between a user and JARVIS in exactly 2-3 sentences. 
Focus on the KEY TOPICS discussed, any decisions made, and any personal facts revealed.
Do NOT mention that this is a conversation or a transcript. Just state the facts.

Transcript:
{transcript}"""
            
            completion = run_with_key_rotation(
                lambda c: c.chat.completions.create(
                    model=groq_model(),
                    messages=[{"role": "system", "content": summary_prompt}],
                    temperature=0.3,
                    max_tokens=100,
                )
            )
            summary = completion.choices[0].message.content.strip()
            
            # Embed the summary into ChromaDB. sealed_add_kwargs embeds the
            # PLAINTEXT and stores the ciphertext — handing the sealed string to
            # documents= alone would embed the ciphertext and silently destroy
            # retrieval.
            episodes_collection.add(
                **_chroma_crypto.sealed_add_kwargs(
                    [summary], EPISODES_COLLECTION, _embedder()),
                metadatas=[{
                    "user": _session_user,
                    "date": date_str,
                    "session_id": session_id,
                    "turn_count": len(_current_session),
                    "timestamp": datetime.datetime.now().isoformat()
                }],
                ids=[f"episode_{session_id}"]
            )
            print(f"[EPISODIC] Session summary embedded: {summary[:80]}...")
        except Exception as e:
            print(f"[EPISODIC] Failed to generate/embed summary: {e}")
    
    # --- 3. RESET FOR NEXT SESSION ---
    _current_session = []
    _session_start = datetime.datetime.now()


def recall_past_sessions(user: str, query: str, n_results: int = 3) -> str:
    """Searches past session summaries for relevant context."""
    if not episodes_collection:
        return "No past session data available."
    try:
        # Check if collection has any documents first
        count = episodes_collection.count()
        if count == 0:
            return "No past session data available."
        
        results = episodes_collection.query(
            query_texts=[query],
            n_results=min(n_results, count),
            where={"user": user}
        )
    except Exception as e:
        print(f"[EPISODIC] Past session recall failed: {e}")
        return "Past session retrieval offline."

    documents = results.get("documents")
    metadatas = results.get("metadatas")
    if not (documents and documents[0]):
        return "No relevant past sessions found."
    try:
        # A locked store must not read as "no past sessions" — see memory.py.
        opened = _chroma_crypto.open_documents(documents[0], EPISODES_COLLECTION)
    except _chroma_crypto.MemoryLockedError as e:
        print(f"[EPISODIC] Recall blocked — memory store locked: {e}", flush=True)
        return "Past session retrieval offline — the memory store is locked."
    memory_strings = []
    for i, doc in enumerate(opened):
        date = metadatas[0][i].get("date", "unknown") if metadatas else "unknown"
        memory_strings.append(f"- [{date}] {doc}")
    return "\n".join(memory_strings)


def get_session_turn_count() -> int:
    """Returns how many turns the current session has."""
    return len(_current_session)
