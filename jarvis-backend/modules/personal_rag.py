"""
personal_rag.py — Personal-Document RAG (Roadmap §4: "genuinely yours")
=======================================================================

Indexes the USER's own documents/notes so J.A.R.V.I.S. can answer
"what did I decide about X last month?" / "find my notes on Y" across their whole
life, not just session memory.

Mirrors rag_cortex.py's privacy-first design:
- Vector store: local ChromaDB (persisted on disk; nothing leaves the machine).
- Embeddings: local HuggingFace `all-MiniLM-L6-v2` — NO cloud embeddings.
- Lazy init: the embedding model only loads on first use, so import never blocks boot.

Roots are configured via JARVIS_DOCS_ROOTS (os.pathsep-separated). Default: the
user's Documents folder plus a `jarvis_docs/` folder beside the backend. Only plain
text/markdown files are indexed (no extra parser dependencies).
"""

from __future__ import annotations

import os
import re
import asyncio

_PERSIST_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "personal_chroma_db")
)
_COLLECTION_NAME = "personal_documents"
_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "all-MiniLM-L6-v2")
_TEXT_EXTS = {".md", ".markdown", ".txt", ".rst", ".text", ".log"}
_MAX_FILE_BYTES = 2_000_000  # skip anything larger than ~2 MB

_collection = None


def _doc_roots() -> list[str]:
    raw = os.getenv("JARVIS_DOCS_ROOTS", "").strip()
    if raw:
        return [os.path.abspath(p) for p in raw.split(os.pathsep) if p.strip()]
    return [
        os.path.join(os.path.expanduser("~"), "Documents"),
        os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "jarvis_docs")),
    ]


def _ensure():
    """Lazily build the persistent collection with a local HF embedding function."""
    global _collection
    if _collection is not None:
        return _collection
    import chromadb
    from chromadb.utils import embedding_functions

    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=_EMBED_MODEL)
    client = chromadb.PersistentClient(path=_PERSIST_DIR)
    _collection = client.get_or_create_collection(name=_COLLECTION_NAME, embedding_function=embed_fn)
    return _collection


def _chunk(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    """Heading-aware chunking: split on markdown H1/H2, then size-cap each section."""
    sections = re.split(r"(?=^#{1,2}\s)", text, flags=re.MULTILINE)
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


def ingest_documents(roots: list[str] | None = None) -> int:
    """
    (Re)embed all text/markdown documents under the configured roots into ChromaDB.
    Idempotent per-file (upserts by stable id). Returns the number of chunks embedded.
    """
    roots = roots or _doc_roots()
    col = _ensure()
    total_chunks = 0
    files_seen = 0

    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip hidden / dependency / vault-internal dirs.
            dirnames[:] = [d for d in dirnames if not d.startswith(".")
                           and d not in ("node_modules", "venv", "__pycache__")]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() not in _TEXT_EXTS:
                    continue
                fpath = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(fpath) > _MAX_FILE_BYTES:
                        continue
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except Exception:
                    continue
                chunks = _chunk(text)
                if not chunks:
                    continue
                files_seen += 1
                rel = os.path.relpath(fpath, root)
                base_id = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{os.path.basename(root)}__{rel}")
                try:
                    col.delete(where={"path": fpath})  # drop prior version of this file
                except Exception:
                    pass
                col.upsert(
                    documents=chunks,
                    ids=[f"{base_id}__{i}" for i in range(len(chunks))],
                    metadatas=[{"path": fpath, "name": fn, "chunk": i} for i in range(len(chunks))],
                )
                total_chunks += len(chunks)

    print(f"[PERSONAL_RAG] Indexed {files_seen} file(s), {total_chunks} chunk(s) from "
          f"{len([r for r in roots if os.path.isdir(r)])} root(s).", flush=True)
    return total_chunks


def query_documents(query: str, n_results: int = 4) -> list[str]:
    """Return the most relevant document chunks (with a source tag) for a query."""
    try:
        col = _ensure()
    except Exception as e:
        print(f"[PERSONAL_RAG] vector store unavailable: {e}", flush=True)
        return []
    try:
        if col.count() == 0:
            ingest_documents()
        if col.count() == 0:
            return []
        res = col.query(query_texts=[query], n_results=n_results)
    except Exception as e:
        print(f"[PERSONAL_RAG] query failed: {e}", flush=True)
        return []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    out = []
    for i, d in enumerate(docs):
        name = metas[i].get("name") if i < len(metas) and isinstance(metas[i], dict) else "document"
        out.append(f"[{name}] {d}")
    return out


# Patterns that indicate the user is asking about THEIR OWN notes/documents — used
# to gate auto-injection so ordinary turns pay no retrieval latency.
_PERSONAL_QUERY_RE = re.compile(
    r"\b(my notes?|my docs?|my documents?|did i (write|note|save|decide|mention)|"
    r"what did i|in my (notes?|docs?|documents?|files?)|from my (notes?|docs?)|"
    r"according to my|i wrote (about|down)|search my (notes?|docs?|documents?)|"
    r"remember when i|find my)\b",
    re.IGNORECASE,
)


def looks_like_personal_query(user_text: str) -> bool:
    return bool(user_text and _PERSONAL_QUERY_RE.search(user_text))


# --- Async wrappers (non-blocking) ------------------------------------------
async def aingest_documents(roots: list[str] | None = None) -> int:
    return await asyncio.to_thread(ingest_documents, roots)


async def aquery_documents(query: str, n_results: int = 4) -> list[str]:
    return await asyncio.to_thread(query_documents, query, n_results)


if __name__ == "__main__":
    print(f"Indexing personal documents from: {_doc_roots()}")
    n = ingest_documents()
    print(f"Embedded {n} chunks.")
    if n:
        for h in query_documents("what did I decide"):
            print("-", h[:120].replace("\n", " "), "…")
