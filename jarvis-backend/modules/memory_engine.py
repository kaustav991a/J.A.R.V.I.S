import chromadb
from chromadb.config import Settings
import os
import time

# [MEMORY] Initializing Semantic Memory Infrastructure...
print("[MEMORY] Initializing Semantic Memory Infrastructure...")

class MemoryEngine:
    def __init__(self, db_path="memory/vector_db"):
        self.db_path = db_path
        # Ensure absolute path for persistence
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.abs_db_path = os.path.join(base_dir, db_path)
        
        if not os.path.exists(self.abs_db_path):
            os.makedirs(self.abs_db_path, exist_ok=True)
            
        print("[MEMORY] Downloading embedding models... this may take a moment")
        self.client = chromadb.PersistentClient(
            path=self.abs_db_path,
            settings=Settings(allow_reset=True)
        )
        
        # Initialize embedding function
        from chromadb.utils import embedding_functions
        self.embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        
        self.collection = self.client.get_or_create_collection(
            name="jarvis_memory",
            embedding_function=self.embedding_function
        )
        print(f"[MEMORY] Semantic Memory Engine Online. Storage: {self.abs_db_path}")

    def store_memory(self, text, category="general"):
        """Stores a new memory snippet."""
        timestamp = str(time.time())
        self.collection.add(
            documents=[text],
            metadatas=[{"timestamp": timestamp, "category": category}],
            ids=[f"id_{timestamp}_{os.urandom(4).hex()}"]
        )
        print(f"[MEMORY] Stored semantic fact ({category}): {text[:50]}...")

    def query_memory(self, query, top_k=3):
        """Retrieves relevant memories."""
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k
        )
        memories = []
        if results['documents'] and len(results['documents']) > 0:
            for doc in results['documents'][0]:
                memories.append(doc)
        return memories

# Singleton instance
try:
    memory_engine = MemoryEngine()
except Exception as e:
    print(f"[MEMORY] Initialization Error: {e}")
    memory_engine = None
