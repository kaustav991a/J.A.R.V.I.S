"""Snapshot every at-rest memory store before anything mutates it.

Written for C#11a (memory-at-rest encryption), but deliberately generic: this is
the "I can get my data back" insurance policy, so it must work even if the
encryption work is abandoned entirely.

Design rules, in order of importance:

1.  **Copies land OUTSIDE the repo.** A backup inside the working tree is one
    `git add -A` away from committing his private memory to GitHub.
2.  **sqlite is copied through the online-backup API**, never `shutil.copy`. A
    file copy of a live database can catch a half-written page; the backup API
    takes a consistent snapshot even if JARVIS is running.
3.  **Every file is hashed, and the copy is verified against the source.** A
    backup nobody checked is a rumour, not a backup.
4.  **Row counts are recorded per table**, so a later restore can be proven
    complete rather than merely present.

Usage:
    venv\\Scripts\\python.exe backup_memory.py            # take a snapshot
    venv\\Scripts\\python.exe backup_memory.py --verify DIR  # re-check an old one
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent

# Backups live beside the project, never inside it — see rule 1 above.
DEFAULT_BACKUP_ROOT = BACKEND_DIR.parent.parent / "JARVIS-BACKUPS"

# Every store that holds data we cannot regenerate. Ordered by how much it would
# hurt to lose. `secret` marks payloads that must never be copied anywhere but
# this machine.
TARGETS = [
    # (relative path, kind, why it matters, secret?)
    ("jarvis_longterm.db", "sqlite", "4-tier Memory OS facts — the system of record", False),
    # Retired 2026-07-30 into jarvis_longterm.db; listed so a machine that has
    # not run the retirement yet still gets it backed up.
    ("jarvis_memory.db", "sqlite", "retired second store (absent after retirement)", False),
    ("jarvis_tasks.db", "sqlite", "task queue / routines state", False),
    ("jarvis_chroma_db", "tree", "semantic memory + episodes (119 docs)", False),
    ("personal_chroma_db", "tree", "RAG chunks of his own documents", False),
    ("action_chroma_db", "tree", "action-routing catalogue", False),
    ("memory/vector_db", "tree", "legacy memory_engine store", False),
    ("models/owner_embeddings.npz", "file", "face biometrics — losing it means re-enrolling", False),
    (".env", "file", "every API key and chat id — irreplaceable credentials", True),
]

CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def sqlite_tables(path: Path) -> dict:
    """Row count per table, plus the integrity verdict. Read-only, no contents."""
    uri = f"file:{path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        names = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {}
        for name in names:
            counts[name] = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        return {"integrity": integrity, "rows": counts}
    finally:
        con.close()


def copy_sqlite(src: Path, dst: Path) -> None:
    """Consistent snapshot via the online backup API — safe on a live database."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    dst_con = sqlite3.connect(str(dst))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()


def copy_tree(src: Path, dst: Path) -> None:
    # Chroma keeps its own sqlite plus .bin segments; the directory must move as
    # one unit or the vectors stop matching the index.
    shutil.copytree(src, dst, dirs_exist_ok=False)


def hash_tree(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = sha256_file(path)
    return out


def take_snapshot(backup_root: Path, stamp: str, skip_secrets: bool) -> Path:
    dest = backup_root / f"pre-encryption-{stamp}"
    if dest.exists():
        raise SystemExit(f"refusing to overwrite an existing snapshot: {dest}")
    dest.mkdir(parents=True)

    manifest = {
        "created": stamp,
        "backend_dir": str(BACKEND_DIR),
        "reason": "C#11a memory-at-rest encryption — pre-change snapshot",
        "entries": [],
        "skipped": [],
    }

    print(f"snapshot -> {dest}\n")

    for rel, kind, why, secret in TARGETS:
        src = BACKEND_DIR / rel
        if not src.exists():
            manifest["skipped"].append({"path": rel, "reason": "not present"})
            print(f"  --  {rel:32s} absent, skipped")
            continue
        if secret and skip_secrets:
            manifest["skipped"].append({"path": rel, "reason": "secret, --no-secrets"})
            print(f"  --  {rel:32s} secret, skipped by request")
            continue

        out = dest / rel.replace("/", os.sep)
        entry = {"path": rel, "kind": kind, "why": why, "secret": secret}

        if kind == "sqlite":
            before = sqlite_tables(src)
            copy_sqlite(src, out)
            after = sqlite_tables(out)
            entry["source"] = before
            entry["copy"] = after
            entry["sha256_copy"] = sha256_file(out)
            # The file hash legitimately differs (the backup API rewrites pages),
            # so equality is proven by integrity + row counts, not by bytes.
            entry["verified"] = (
                after["integrity"] == "ok" and after["rows"] == before["rows"]
            )
            total = sum(before["rows"].values())
            print(
                f"  OK  {rel:32s} {total:>5} rows across "
                f"{len(before['rows'])} tables, integrity={after['integrity']}"
            )

        elif kind == "tree":
            copy_tree(src, out)
            src_hashes = hash_tree(src)
            dst_hashes = hash_tree(out)
            entry["files"] = len(src_hashes)
            entry["sha256_tree"] = dst_hashes
            entry["verified"] = src_hashes == dst_hashes
            print(f"  OK  {rel:32s} {len(src_hashes):>5} files, hashes match={entry['verified']}")

        else:  # single file
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
            src_hash = sha256_file(src)
            entry["sha256"] = src_hash
            entry["verified"] = src_hash == sha256_file(out)
            print(f"  OK  {rel:32s} {src.stat().st_size:>5} bytes, hash match={entry['verified']}")

        manifest["entries"].append(entry)

    (dest / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (dest / "RESTORE.txt").write_text(RESTORE_TEXT.format(dest=dest), encoding="utf-8")

    failed = [e["path"] for e in manifest["entries"] if not e.get("verified")]
    print()
    if failed:
        print("VERIFY FAILED for: " + ", ".join(failed))
        raise SystemExit(2)
    print(f"all {len(manifest['entries'])} targets copied and verified")
    return dest


def verify_snapshot(dest: Path) -> None:
    """Re-check an existing snapshot against its own manifest, offline."""
    manifest = json.loads((dest / "MANIFEST.json").read_text(encoding="utf-8"))
    print(f"verifying {dest} (taken {manifest['created']})\n")
    bad = []
    for entry in manifest["entries"]:
        rel = entry["path"]
        out = dest / rel.replace("/", os.sep)
        if not out.exists():
            bad.append(f"{rel}: MISSING from snapshot")
            continue
        if entry["kind"] == "sqlite":
            now = sqlite_tables(out)
            if now["integrity"] != "ok":
                bad.append(f"{rel}: integrity={now['integrity']}")
            elif now["rows"] != entry["copy"]["rows"]:
                bad.append(f"{rel}: row counts drifted {entry['copy']['rows']} -> {now['rows']}")
            else:
                print(f"  OK  {rel:32s} {sum(now['rows'].values())} rows, integrity ok")
        elif entry["kind"] == "tree":
            now = hash_tree(out)
            if now != entry["sha256_tree"]:
                bad.append(f"{rel}: tree hashes changed")
            else:
                print(f"  OK  {rel:32s} {len(now)} files, hashes match")
        else:
            if sha256_file(out) != entry["sha256"]:
                bad.append(f"{rel}: hash mismatch")
            else:
                print(f"  OK  {rel:32s} hash matches")

    print()
    if bad:
        for line in bad:
            print("  FAIL " + line)
        raise SystemExit(2)
    print("snapshot intact")


RESTORE_TEXT = """HOW TO GET YOUR DATA BACK
=========================

This snapshot was taken before JARVIS's long-term memory was encrypted.
Restoring needs no tooling and no Claude — it is a file copy.

1. Stop JARVIS (close the backend, and the watchdog if it is running).

2. Copy the files back over the live ones, from this folder into
   F:\\work\\JARVIS-Project\\jarvis-backend\\ :

       jarvis_longterm.db          <- the 4-tier Memory OS, the important one
       jarvis_memory.db
       jarvis_tasks.db
       jarvis_chroma_db\\           <- whole folder, replace it entirely
       personal_chroma_db\\
       action_chroma_db\\
       memory\\vector_db\\
       models\\owner_embeddings.npz <- face login; without it, re-enrol
       .env                        <- API keys, if it was included

   Folders must be replaced whole. Copying single files out of a Chroma folder
   will desynchronise the vectors from the index.

3. Start JARVIS. The memory is exactly as it was when this snapshot was taken.

To check this snapshot is still good without restoring anything:

    venv\\Scripts\\python.exe backup_memory.py --verify "{dest}"

NOTE: if .env is present here, this folder contains every API key in plaintext.
Keep it on this machine. Do not sync it, zip it into a shared drive, or upload it.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", metavar="DIR", help="verify an existing snapshot instead")
    parser.add_argument("--root", default=str(DEFAULT_BACKUP_ROOT), help="where snapshots live")
    parser.add_argument(
        "--no-secrets", action="store_true", help="skip .env (keys stay uncopied)"
    )
    args = parser.parse_args()

    if args.verify:
        verify_snapshot(Path(args.verify))
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = take_snapshot(Path(args.root), stamp, args.no_secrets)
    print(f"\nrestore instructions: {dest / 'RESTORE.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
