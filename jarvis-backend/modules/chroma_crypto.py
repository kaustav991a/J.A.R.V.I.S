r"""chroma_crypto.py — C#11a field encryption for a Chroma collection.

Chroma keeps document text in plaintext (twice — once in `embedding_metadata`
and again in the FTS5 shadow tables), its metadata in plaintext, and its vectors
in a binary index. The SQLite store was closed in C#11a; this closes the same
hole for the vector store, reusing that key story exactly: **one DEK, one DPAPI
wrap, one recovery code**. There is no second key mechanism and no new
dependency — every primitive here comes from `memory_crypto`.

WHAT THIS PROTECTS, AND WHAT IT DOES NOT
----------------------------------------
Protected at rest, always — running or stopped, clean exit or crash: the
document TEXT and the sensitive METADATA. A copied folder, a backup, or a sync
client yields ciphertext.

NOT protected: **the vectors.** They are computed from plaintext (that is what
makes semantic search work) and stored as ordinary floats. Dense embeddings leak
approximate content via inversion, and the encoder here (`all-MiniLM-L6-v2`) is
public, so this is a real residual channel, not a theoretical one. Accepted
deliberately — encrypting the vectors would destroy the search this store exists
for. Also not protected: code running as the owner on this machine, the same
limit DPAPI already carries.

THREE THINGS CHROMA FORCES, WHICH THE API HERE EXISTS TO HANDLE
---------------------------------------------------------------
1. **Vectors must be computed BEFORE encryption.** A collection with an
   `embedding_function` embeds whatever string you hand it, so passing
   ciphertext to `add()` would embed the ciphertext and silently destroy
   retrieval — the failure would look like "search got worse", not like a bug.
   Callers must embed the PLAINTEXT themselves and pass `embeddings=`
   explicitly, which overrides the collection's function.

2. **`where` filters cannot match ciphertext.** Field encryption is randomised,
   so the same path encrypts differently every time and `where={"path": ...}`
   would never match — silently, leaving stale chunks behind on re-ingest. Every
   filterable sensitive key therefore gets a companion `<key>_bi` holding a
   `blind_index` (keyed, deterministic, useless without the DEK).

3. **Chroma ids are plaintext, unavoidably.** An id like
   `Documents__diary__secret-affair.md__0` leaks the filename even when the
   document body is sealed. `doc_id()` derives the id from a blind index
   instead, so ids stay stable and idempotent while revealing nothing.

Locked keys must RAISE, never return nothing. An empty result set is
indistinguishable from "no relevant documents", which is the C#11a rule about
silent empty reads applied to retrieval.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from modules import memory_crypto as _crypto
from modules.memory_crypto import MemoryLockedError  # re-exported for callers

__all__ = [
    "MemoryLockedError", "encryption_on", "table_for", "blind",
    "encrypt_document", "decrypt_document",
    "encrypt_metadata", "decrypt_metadata",
    "sealed_add_kwargs", "open_documents",
    "doc_id", "BLIND_SUFFIX", "DOC_COLUMN",
]

#: The AAD column name for a document body. Metadata keys use their own name, so
#: a sealed `path` can never be read back as a `name` or as a document.
DOC_COLUMN = "document"

#: Companion key holding the deterministic fingerprint of a sensitive value.
BLIND_SUFFIX = "_bi"


def encryption_on() -> bool:
    """Is encryption active? Same switch as the SQLite store — the presence of a
    key set, not an env flag someone can forget to set."""
    return _crypto.keys_ready()


def table_for(collection: str) -> str:
    """AAD namespace for a collection.

    Prefixed so a Chroma ciphertext and a `memories` ciphertext can never be
    swapped for one another: the AAD is part of what GCM authenticates, so a
    blob moved between stores fails to open rather than decrypting to something
    plausible.
    """
    return f"chroma:{collection}"


def blind(value: str | None, collection: str, key: str) -> str | None:
    """Deterministic keyed fingerprint of a metadata value, for `where` filters."""
    return _crypto.blind_index(value, table_for(collection), key)


# ── documents ────────────────────────────────────────────────────────────────

def encrypt_document(text: str | None, collection: str) -> str | None:
    return _crypto.encrypt_field(text, table_for(collection), DOC_COLUMN)


def decrypt_document(value: str | None, collection: str) -> str | None:
    """Decrypt a stored document. Plaintext passes through, so a store written
    before the ceremony still reads correctly."""
    return _crypto.decrypt_field(value, table_for(collection), DOC_COLUMN)


# ── metadata ─────────────────────────────────────────────────────────────────

def encrypt_metadata(meta: Mapping[str, Any], collection: str,
                     sensitive: Iterable[str]) -> dict[str, Any]:
    """Seal the sensitive keys of one metadata dict, adding blind-index companions.

    Non-sensitive keys pass through untouched — `chunk` is an ordinal, not a
    secret, and Chroma needs some plain scalars to filter and sort on.

    A sensitive key holding a non-string raises rather than being silently left
    in the clear: encrypting an int into a string field would change its type on
    read-back, and quietly skipping it is exactly the kind of gap that survives
    review.
    """
    sensitive = set(sensitive)
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if k not in sensitive:
            out[k] = v
            continue
        if v is None:
            out[k] = v
            continue
        if not isinstance(v, str):
            raise TypeError(
                f"metadata key {k!r} is marked sensitive but holds "
                f"{type(v).__name__}; only strings can be encrypted")
        out[k] = encrypt_field_as(v, collection, k)
        bi = blind(v, collection, k)
        if bi is not None:
            out[k + BLIND_SUFFIX] = bi
    return out


def decrypt_metadata(meta: Mapping[str, Any] | None, collection: str,
                     sensitive: Iterable[str]) -> dict[str, Any]:
    """Inverse of `encrypt_metadata`. Blind-index companions are dropped — they
    are storage plumbing and no caller should ever see one."""
    if not meta:
        return {}
    sensitive = set(sensitive)
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if k.endswith(BLIND_SUFFIX) and k[: -len(BLIND_SUFFIX)] in sensitive:
            continue
        if k in sensitive and isinstance(v, str):
            out[k] = decrypt_field_as(v, collection, k)
        else:
            out[k] = v
    return out


def encrypt_field_as(value: str | None, collection: str, column: str) -> str | None:
    """Seal one value under a named column of this collection's AAD namespace."""
    return _crypto.encrypt_field(value, table_for(collection), column)


def decrypt_field_as(value: str | None, collection: str, column: str) -> str | None:
    return _crypto.decrypt_field(value, table_for(collection), column)


# ── the write/read pair, so rule 1 lives in ONE place ────────────────────────

def sealed_add_kwargs(texts, collection: str, embed_fn) -> dict:
    """The kwargs an `add()`/`upsert()` needs so the store ends up sealed.

    Rule 1 at the top of this file is the subtle one and the easy one to get
    wrong: the vector has to come from the PLAINTEXT. A collection with an
    embedding_function embeds whatever string it is handed, so passing
    ciphertext to `add(documents=…)` embeds the ciphertext and destroys
    retrieval **silently** — the symptom is "search got worse", not an error.

    So the ordering is encoded once, here, rather than restated at each call
    site. `embed_fn` is a callable taking a list of strings (Chroma's own
    embedding-function protocol), passed in so this module keeps its promise of
    depending on nothing but `memory_crypto`.

    Encryption off ⇒ just the documents, and the collection embeds them as it
    always did. Nothing about an unencrypted store changes.

    Review finding M5, 2026-08-16: `jarvis_memory` held 118 documents in
    plaintext while `jarvis_longterm.db` beside it was sealed 60/60. This module
    existed to close exactly that and was imported by `personal_rag` only.
    """
    texts = list(texts)
    if not encryption_on():
        return {"documents": texts}
    return {
        "documents": [encrypt_document(t, collection) for t in texts],
        "embeddings": embed_fn(texts),
    }


def open_documents(documents, collection: str) -> list:
    """Decrypt a row of query/get results. Plaintext passes straight through.

    That pass-through is what lets a half-finished migration still read, and it
    is why sealing a store is safe to do incrementally.
    """
    return [decrypt_document(d, collection) for d in (documents or [])]


# ── ids ──────────────────────────────────────────────────────────────────────

def doc_id(source: str, index: int, collection: str, key: str = "path") -> str:
    """A stable, non-revealing chunk id.

    Encryption-on ⇒ derived from the blind index of `source`, so re-ingesting
    the same file lands on the same ids (upsert stays idempotent) while the id
    itself carries no filename. Encryption-off ⇒ the caller's own scheme is used
    unchanged, so nothing about an unencrypted store changes.
    """
    bi = blind(source, collection, key)
    return f"{bi}__{index}"
