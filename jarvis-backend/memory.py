import sqlite3
import datetime

DB_PATH = "jarvis_memory.db"

# ==========================================
# TIER 1: SHORT-TERM WORKING MEMORY
# ==========================================
# This holds the last few conversational turns so he understands context.
working_memory = []

def add_to_working_memory(role, content):
    """Adds a message to the short-term memory queue (keeps the last 10 messages)."""
    working_memory.append({"role": role, "content": content})
    if len(working_memory) > 10:
        working_memory.pop(0)

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

# --- OPTIONAL: Seed some initial data ---
# Remember we have the location widget in React? Let's make sure his brain knows it too.
remember_fact("Location", "The user is located in Ichhapur, West Bengal, India.")