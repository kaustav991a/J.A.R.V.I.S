import sqlite3
import datetime
import chromadb
import uuid
import os
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

def add_to_working_memory(role, content):
    """Adds a message to the short-term memory queue (keeps the last 30 messages)."""
    working_memory.append({"role": role, "content": content})
    if len(working_memory) > 30:
        _compress_oldest_memories()

def _compress_oldest_memories():
    """Summarizes the oldest 15 messages into a single context message using the LLM."""
    global working_memory
    
    # Extract the oldest 15 messages to compress
    old_messages = working_memory[:15]
    
    # Build a transcript for summarization
    transcript_lines = []
    for msg in old_messages:
        role_label = "User" if msg["role"] == "user" else "JARVIS"
        transcript_lines.append(f"{role_label}: {msg['content'][:200]}")
    transcript = "\n".join(transcript_lines)
    
    try:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{
                "role": "system", 
                "content": f"Summarize this conversation excerpt in 2-3 concise sentences. Preserve key facts, names, and decisions. Do not add commentary.\n\n{transcript}"
            }],
            temperature=0.2,
            max_tokens=100
        )
        summary = completion.choices[0].message.content.strip()
        
        # Replace the oldest 15 messages with a single summary
        working_memory[:15] = [{
            "role": "system", 
            "content": f"[CONTEXT SUMMARY] {summary}"
        }]
        print(f"[MEMORY] Compressed 15 messages into context summary")
    except Exception as e:
        # Fallback: just trim if LLM fails
        print(f"[MEMORY] Compression failed ({e}), falling back to simple trim")
        working_memory[:15] = []

def get_working_memory():
    """Returns the current conversational context."""
    return working_memory

def clear_working_memory():
    """Wipes the short-term memory (useful for a reset command)."""
    global working_memory
    working_memory = []

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