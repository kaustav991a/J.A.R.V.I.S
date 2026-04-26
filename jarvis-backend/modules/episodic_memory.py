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
try:
    _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    episodes_collection = _chroma_client.get_or_create_collection(name="jarvis_episodes")
except Exception as e:
    print(f"[EPISODIC] WARNING: Failed to initialize episodic ChromaDB. {e}")
    episodes_collection = None

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
    if episodes_collection and groq_client:
        try:
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
            
            completion = groq_client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "system", "content": summary_prompt}],
                temperature=0.3,
                max_tokens=100
            )
            summary = completion.choices[0].message.content.strip()
            
            # Embed the summary into ChromaDB
            episodes_collection.add(
                documents=[summary],
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
        
        documents = results.get("documents")
        metadatas = results.get("metadatas")
        
        if documents and documents[0]:
            memory_strings = []
            for i, doc in enumerate(documents[0]):
                date = metadatas[0][i].get("date", "unknown") if metadatas else "unknown"
                memory_strings.append(f"- [{date}] {doc}")
            return "\n".join(memory_strings)
        return "No relevant past sessions found."
    except Exception as e:
        print(f"[EPISODIC] Past session recall failed: {e}")
        return "Past session retrieval offline."


def get_session_turn_count() -> int:
    """Returns how many turns the current session has."""
    return len(_current_session)
