"""
rag_cortex.py — Local RAG Cortex (Phase 3)
==========================================

Privacy-first retrieval over the frontend coding standards, used by the Overnight
Autopilot so the code generator can ground itself in our BEM/SCSS rules before writing.

- Vector store: local ChromaDB (persisted on disk; nothing leaves the machine).
- Embeddings: local HuggingFace `all-MiniLM-L6-v2` via sentence-transformers — NO OpenAI.
- Lazy initialisation: the embedding model (and its ~90 MB download on first run) is only
  loaded when `_ensure()` is first called, so importing this module never blocks startup
  and never crashes the boot if sentence-transformers isn't present yet.

All blocking work is exposed through async wrappers (`aingest_standards`, `aquery_standards`)
that offload to a worker thread, keeping the J.A.R.V.I.S. event loop non-blocking.
"""

# A log character must not be able to abort an operation:
# rag_cortex has its own __main__. See modules/utf8_stdout.py.
try:                            # imported as part of the package
    from . import utf8_stdout   # noqa: F401
except ImportError:             # run as a bare script path
    import utf8_stdout          # noqa: F401,E402

import os
import asyncio

_PERSIST_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rag_chroma_db")
)
_STANDARDS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend_standards.md")
)
_COLLECTION_NAME = "frontend_standards"
_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "all-MiniLM-L6-v2")

_collection = None  # cached chromadb collection


def _ensure():
    """Lazily build the persistent collection with a local HF embedding function."""
    global _collection
    if _collection is not None:
        return _collection
    import chromadb
    from chromadb.utils import embedding_functions

    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=_EMBED_MODEL
    )
    client = chromadb.PersistentClient(path=_PERSIST_DIR)
    _collection = client.get_or_create_collection(
        name=_COLLECTION_NAME, embedding_function=embed_fn
    )
    return _collection


def _chunk(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    """Heading-aware chunking: split on markdown H2 sections, then size-cap each section."""
    import re

    sections = re.split(r"(?=^##\s)", text, flags=re.MULTILINE)
    chunks: list[str] = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(sec) <= size:
            chunks.append(sec)
            continue
        start = 0
        while start < len(sec):
            chunks.append(sec[start:start + size])
            start += size - overlap
    return chunks


def ingest_standards(path: str | None = None) -> int:
    """
    (Re)embed the standards file into ChromaDB. Idempotent — clears prior chunks first.
    Returns the number of chunks embedded (0 if the file is missing).
    """
    path = os.path.abspath(path or _STANDARDS_PATH)
    if not os.path.exists(path):
        print(f"[RAG] Standards file not found: {path}", flush=True)
        return 0
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    chunks = _chunk(text)
    if not chunks:
        return 0

    col = _ensure()
    # Wipe previous version of this source so re-ingest doesn't duplicate.
    try:
        col.delete(where={"source": "frontend_standards"})
    except Exception:
        pass

    col.upsert(
        documents=chunks,
        ids=[f"fs_{i}" for i in range(len(chunks))],
        metadatas=[{"source": "frontend_standards", "chunk": i} for i in range(len(chunks))],
    )
    print(f"[RAG] Embedded {len(chunks)} standards chunk(s) into ChromaDB.", flush=True)
    return len(chunks)


def query_standards(query: str, n_results: int = 4) -> list[str]:
    """Return the most relevant standards chunks for a query (auto-ingests if empty)."""
    col = _ensure()
    try:
        if col.count() == 0:
            ingest_standards()
    except Exception:
        pass
    res = col.query(query_texts=[query], n_results=n_results)
    docs = res.get("documents") or [[]]
    return docs[0] if docs else []


# --- Async wrappers (non-blocking) ------------------------------------------
async def aingest_standards(path: str | None = None) -> int:
    return await asyncio.to_thread(ingest_standards, path)


async def aquery_standards(query: str, n_results: int = 4) -> list[str]:
    return await asyncio.to_thread(query_standards, query, n_results)


if __name__ == "__main__":
    print(f"Ingesting standards from {_STANDARDS_PATH} …")
    count = ingest_standards()
    print(f"Embedded {count} chunks.")
    if count:
        hits = query_standards("How should I map Figma auto-layout to SCSS?")
        print(f"\nTop retrieval for auto-layout question ({len(hits)} hits):\n")
        for h in hits:
            print("-", h[:120].replace("\n", " "), "…")
