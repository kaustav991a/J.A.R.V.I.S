r"""Review finding M5 — seal the documents already sitting in the vector store.

`memory.py` and `modules/episodic_memory.py` now encrypt on write. That does
nothing for what is ALREADY on disk, which on this machine was 118 plaintext
memory documents in `jarvis_memory` plus the episode summaries in
`jarvis_episodes` — the same facts that `jarvis_longterm.db`, in the same
folder, holds as ciphertext.

    venv\Scripts\python.exe migrate_chroma_encryption.py --report   # read-only
    venv\Scripts\python.exe migrate_chroma_encryption.py --apply    # do it

WHY THIS IS SAFE TO RUN, AND SAFE TO INTERRUPT
----------------------------------------------
* A full copy of the store is taken BEFORE any client opens it, so the backup
  can never contain a half-written row.
* The vectors are never recomputed. Each row is updated with the embedding it
  already had, read back out of Chroma and handed straight in again — so the
  index is bit-identical afterwards and retrieval quality cannot move. It also
  means no embedding model is loaded and the run costs seconds.
* `chroma_crypto.decrypt_document` passes plaintext straight through, so a run
  interrupted halfway leaves a store that still reads correctly — a mix of
  sealed and unsealed rows is a valid state, not a corrupt one.
* Idempotent: a row that already carries the `enc:v1:` prefix is skipped, so
  re-running is free and never double-encrypts.
* Every row is read back and compared to the plaintext it came from before the
  run is called a success.

WHAT IS NOT SEALED, DELIBERATELY
--------------------------------
The metadata (`user`, `timestamp`, `date`, `session_id`) and the vectors. The
metadata matches the SQLite half, which keeps its `user` and `timestamp`
columns plain because both stores filter on them; the vectors are the residual
channel `chroma_crypto`'s own docstring records as accepted — encrypting them
would destroy the search the store exists for.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

CHROMA_PATH = BACKEND_DIR / "jarvis_chroma_db"

#: Collections and the module that owns each. Both are sealed under their own
#: name as the AAD namespace, so a blob cannot be moved between them.
COLLECTIONS = ("jarvis_memory", "jarvis_episodes")

_ENC_PREFIX = "enc:v1:"


def _is_sealed(value) -> bool:
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)


def _open_store(path: Path):
    import chromadb
    return chromadb.PersistentClient(path=str(path))


def _rows(collection):
    """Every row, with the embeddings, so nothing has to be recomputed."""
    return collection.get(include=["documents", "metadatas", "embeddings"])


def report() -> int:
    if not CHROMA_PATH.exists():
        print(f"no vector store at {CHROMA_PATH} — nothing to do.")
        return 0
    client = _open_store(CHROMA_PATH)
    print(f"store: {CHROMA_PATH}\n")
    total_plain = 0
    for name in COLLECTIONS:
        try:
            col = client.get_collection(name)
        except Exception as e:  # noqa: BLE001
            print(f"  {name:<16} — absent ({type(e).__name__})")
            continue
        got = col.get(include=["documents"])
        docs = got.get("documents") or []
        sealed = sum(1 for d in docs if _is_sealed(d))
        plain = len(docs) - sealed
        total_plain += plain
        print(f"  {name:<16} {len(docs):>4} document(s): "
              f"{sealed} sealed, {plain} PLAINTEXT")
    print()
    if total_plain:
        print(f"{total_plain} document(s) are readable on disk. Run with --apply.")
    else:
        print("every document is sealed.")
    return 0


def backup() -> Path | None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = CHROMA_PATH.parent / f"{CHROMA_PATH.name}.plaintext-{stamp}"
    # The stamp is second-resolution, so two runs inside one second would
    # collide — and copytree raises rather than overwriting, which would abort
    # the migration for the most boring possible reason.
    suffix = 1
    while dest.exists():
        suffix += 1
        dest = CHROMA_PATH.parent / f"{CHROMA_PATH.name}.plaintext-{stamp}-{suffix}"
    print(f"copying the store aside first → {dest.name}")
    try:
        shutil.copytree(CHROMA_PATH, dest)
    except Exception as e:  # noqa: BLE001
        print(f"  backup FAILED: {e}")
        return None
    print("  backup taken.\n")
    return dest


def migrate(name: str, client, crypto) -> tuple[int, int, int]:
    """Seal one collection. Returns (sealed_now, already_sealed, verified)."""
    try:
        col = client.get_collection(name)
    except Exception:  # noqa: BLE001
        print(f"  {name}: absent, skipped.")
        return 0, 0, 0

    got = _rows(col)
    ids = got.get("ids") or []
    docs = got.get("documents") or []
    embs = got.get("embeddings")
    if embs is None:
        embs = [None] * len(ids)

    todo, plaintext_by_id, already = [], {}, 0
    for i, rid in enumerate(ids):
        doc = docs[i] if i < len(docs) else None
        if doc is None:
            continue
        if _is_sealed(doc):
            already += 1
            continue
        todo.append(i)
        plaintext_by_id[rid] = doc

    if not todo:
        print(f"  {name}: {already} already sealed, nothing to do.")
        return 0, already, 0

    print(f"  {name}: sealing {len(todo)} document(s) "
          f"({already} already sealed)…")
    for i in todo:
        rid = ids[i]
        sealed = crypto.encrypt_document(docs[i], name)
        kwargs = {"ids": [rid], "documents": [sealed]}
        emb = embs[i] if i < len(embs) else None
        if emb is not None:
            # Hand the ORIGINAL vector back. Updating a document without this
            # would re-embed — the ciphertext — and silently destroy retrieval.
            kwargs["embeddings"] = [list(emb)]
        col.update(**kwargs)

    # Read back through the same path the running system uses.
    check = col.get(ids=list(plaintext_by_id), include=["documents"])
    verified = 0
    for rid, doc in zip(check.get("ids") or [], check.get("documents") or []):
        if not _is_sealed(doc):
            print(f"    ⛔ {rid} did not seal")
            return len(todo), already, -1
        if crypto.decrypt_document(doc, name) != plaintext_by_id[rid]:
            print(f"    ⛔ {rid} does not read back as what it was")
            return len(todo), already, -1
        verified += 1
    print(f"    ✅ {verified} document(s) sealed and verified byte-for-byte.")
    return len(todo), already, verified


def apply() -> int:
    if not CHROMA_PATH.exists():
        print(f"no vector store at {CHROMA_PATH} — nothing to do.")
        return 0

    from modules import chroma_crypto as crypto

    if not crypto.encryption_on():
        print("⛔ no key set on this machine — there is nothing to encrypt WITH.\n"
              "   Provision the key store first; nothing was changed.")
        return 1

    kept = backup()
    if kept is None:
        print("refusing to migrate without a backup. Nothing was changed.")
        return 1

    client = _open_store(CHROMA_PATH)
    sealed_total = 0
    for name in COLLECTIONS:
        sealed, _already, verified = migrate(name, client, crypto)
        if verified < 0:
            print(f"\n⛔ VERIFICATION FAILED on {name}. The store is a mix of sealed "
                  f"and plaintext rows, which still READS correctly — but restore "
                  f"from {kept.name} if you want the original back.")
            return 1
        sealed_total += sealed

    print(f"\ndone — {sealed_total} document(s) newly sealed.")
    print(f"the plaintext copy is at {kept.name}. **Shred it** once you have "
          f"confirmed JARVIS still recalls correctly — it is the very thing this "
          f"migration exists to remove from the disk.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true", help="read-only survey")
    group.add_argument("--apply", action="store_true", help="seal the documents")
    args = parser.parse_args()
    return report() if args.report else apply()


if __name__ == "__main__":
    raise SystemExit(main())
