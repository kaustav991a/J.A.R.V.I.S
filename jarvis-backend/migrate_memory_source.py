r"""Backfill `memories.source` on the existing rows — provenance, 2026-08-02.

Every row written before the column existed predates the cloud→desk drain
entirely, so all of them are desk-origin. That inference is airtight, which is
why they are backfilled to a real value rather than left as a permanent
`legacy` bucket nobody wants in their queries.

SAFETY, and it is the whole point of this file
----------------------------------------------
Two different operations get two different levels of ceremony, because they
carry two different risks:

  * `ALTER TABLE memories ADD COLUMN source TEXT` is **metadata-only** in SQLite
    when no DEFAULT is given: it rewrites the schema header and does not read or
    write a single row. That one lives in `memory_manager._init_db()` alongside
    the identical `content_hash` add, and runs on every boot.

  * The **backfill** touches every row, so it is done exactly the way the C#11a
    encryption migration was:

        1. survey (read-only) and refuse if anything looks wrong
        2. take a fresh, verified backup (backup_memory.py)
        3. copy the database
        4. add the column and backfill ON THE COPY
        5. verify the copy against the original -- identical row ids, and
           category / user / timestamp / content / content_hash byte-identical,
           and every content still DECRYPTS to the same plaintext it did before
        6. PRAGMA integrity_check on the copy
        7. only then swap, and the original is MOVED ASIDE, never deleted
        8. read back through the live code path

    Any failure at any step leaves the live database exactly as it was: the copy
    is deleted and nothing else has been touched.

Resumable and re-runnable: rows that already carry a source are skipped, so a
second `--apply` is a no-op rather than a rewrite.

    venv\Scripts\python.exe migrate_memory_source.py --report   # read-only
    venv\Scripts\python.exe migrate_memory_source.py --apply    # do it
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import memory_manager as mm
from modules import memory_crypto as mc

# Same cp1252 hardening as migrate_memory_encryption.py / run_harnesses.py. A
# migration is the worst possible place to die on a log line's encoding.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = BACKEND_DIR / "jarvis_longterm.db"
BACKUP_ROOT = BACKEND_DIR.parent.parent / "JARVIS-BACKUPS"
ASIDE_DIR = BACKUP_ROOT / "pre-source-originals"

BACKFILL_TO = mm.SOURCE_DESK

#: Everything that must come through the migration unchanged. `source` is
#: deliberately absent — it is the only column allowed to differ.
_UNTOUCHED = ("category", "user", "timestamp", "content", "content_hash")


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(str(path))


def _has_source_column(conn: sqlite3.Connection) -> bool:
    return "source" in {r[1] for r in conn.execute("PRAGMA table_info(memories)")}


def _table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchone() is not None


def report(db_path: Path) -> dict:
    """Read-only survey. Prints what WOULD happen and finds blockers."""
    summary = {"rows": 0, "untagged": 0, "tagged": {}, "encrypted_rows": 0,
               "has_column": False, "blocked": False}
    if not db_path.exists():
        print(f"  no database at {db_path}")
        return summary

    conn = _connect(db_path, readonly=True)
    try:
        if not _table_exists(conn):
            print("  memories table absent — nothing to do")
            return summary

        summary["rows"] = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        summary["has_column"] = _has_source_column(conn)
        summary["encrypted_rows"] = sum(
            1 for (v,) in conn.execute("SELECT content FROM memories")
            if mc.is_encrypted(v)
        )

        if summary["has_column"]:
            for value, n in conn.execute(
                    "SELECT source, COUNT(*) FROM memories GROUP BY source"):
                if value:
                    summary["tagged"][value] = n
                else:
                    summary["untagged"] += n
        else:
            summary["untagged"] = summary["rows"]

        print(f"  rows                {summary['rows']}")
        print(f"  source column       {'present' if summary['has_column'] else 'ABSENT (boot adds it)'}")
        print(f"  already tagged      {summary['tagged'] or '{}'}")
        print(f"  to backfill -> {BACKFILL_TO:<6} {summary['untagged']}")
        print(f"  encrypted content   {summary['encrypted_rows']}/{summary['rows']}")

        # An unknown value means somebody wrote outside the vocabulary. Naming it
        # now beats discovering it after a swap.
        unknown = {v: n for v, n in summary["tagged"].items()
                   if v not in mm.KNOWN_SOURCES}
        if unknown:
            summary["blocked"] = True
            print(f"\n  BLOCKER: rows carry sources this store does not issue: {unknown}")
            print(f"    known sources: {sorted(mm.KNOWN_SOURCES)}. Nothing was changed.")

        # Verification decrypts every row. Without the key it cannot prove the
        # content survived, and an unverifiable migration is not one.
        if summary["encrypted_rows"] and not mc.keys_ready():
            summary["blocked"] = True
            print("\n  BLOCKER: rows are encrypted but no key set is available, so the")
            print("    round-trip verification cannot run. Nothing was changed.")
    finally:
        conn.close()
    return summary


def take_backup() -> bool:
    print("taking a fresh backup first...\n")
    proc = subprocess.run(
        [sys.executable, str(BACKEND_DIR / "backup_memory.py")],
        cwd=BACKEND_DIR, text=True,
    )
    return proc.returncode == 0


def backfill_copy(copy_path: Path) -> int:
    """Add the column and set the untagged rows. Runs ONLY on the copy."""
    conn = _connect(copy_path)
    try:
        added = mm._ensure_source_column(conn)
        if added:
            print("  source column added to the copy")
        cursor = conn.execute(
            "UPDATE memories SET source = ? WHERE source IS NULL OR source = ''",
            (BACKFILL_TO,),
        )
        conn.commit()
        print(f"  {cursor.rowcount} row(s) backfilled to '{BACKFILL_TO}' on the copy")
        return cursor.rowcount
    finally:
        conn.close()


def verify_copy(original: Path, copy_path: Path) -> list:
    """The proof. Every row, every column that must not have moved.

    Reads through `mc.decrypt_field` as well as raw, so this answers both "are
    the bytes the same" and "does the content still come back as the same
    sentence it was before the column existed".
    """
    problems = []
    dek = mc.load_dek() if mc.keys_ready() else None
    src = _connect(original, readonly=True)
    dst = _connect(copy_path, readonly=True)
    try:
        cols = ", ".join(_UNTOUCHED)
        src_rows = {r[0]: r[1:] for r in
                    src.execute(f"SELECT id, {cols} FROM memories ORDER BY id")}
        dst_rows = {r[0]: r[1:] for r in
                    dst.execute(f"SELECT id, {cols} FROM memories ORDER BY id")}

        if set(src_rows) != set(dst_rows):
            missing = sorted(set(src_rows) - set(dst_rows))
            extra = sorted(set(dst_rows) - set(src_rows))
            problems.append(f"row ids differ (missing={missing}, extra={extra})")
            return problems
        if len(src_rows) != len(dst_rows):
            problems.append(f"row count changed: {len(src_rows)} -> {len(dst_rows)}")
            return problems

        for rid, was in src_rows.items():
            for column, before, after in zip(_UNTOUCHED, was, dst_rows[rid]):
                if before != after:
                    problems.append(f"id={rid}: {column} was modified by the backfill")

        # The content still has to READ the same, not merely look the same.
        for rid, (raw_before,) in {
            r[0]: (r[1],) for r in
            src.execute("SELECT id, content FROM memories ORDER BY id")
        }.items():
            raw_after = dst.execute(
                "SELECT content FROM memories WHERE id = ?", (rid,)).fetchone()[0]
            try:
                before = mc.decrypt_field(raw_before, "memories", "content", dek=dek)
                after = mc.decrypt_field(raw_after, "memories", "content", dek=dek)
            except Exception as exc:                      # noqa: BLE001
                problems.append(f"id={rid}: content no longer decrypts ({exc})")
                continue
            if before != after:
                problems.append(f"id={rid}: content round-trip changed the value")

        # And the column we DID write must be complete and in-vocabulary.
        for rid, value in dst.execute("SELECT id, source FROM memories ORDER BY id"):
            if not value:
                problems.append(f"id={rid}: source is still empty after the backfill")
            elif value not in mm.KNOWN_SOURCES:
                problems.append(f"id={rid}: source={value!r} is not a known source")

        print(f"  {len(src_rows)} row(s) verified — ids, content, content_hash, "
              f"category, user and timestamp all unchanged")
        print(f"  {len(src_rows)} row(s) verified — content still decrypts to the "
              f"same plaintext")
    finally:
        src.close()
        dst.close()
    return problems


def apply_migration() -> int:
    if not DB_PATH.exists():
        print(f"no database at {DB_PATH}")
        return 1

    print("── survey ──────────────────────────────────────────────")
    summary = report(DB_PATH)
    if summary["blocked"]:
        print("\nBLOCKED — see above. Nothing was changed.")
        return 2
    if summary["rows"] == 0:
        print("\nNothing to migrate (empty store). The column is added at boot.")
        return 0
    if summary["untagged"] == 0:
        print("\nAlready migrated — every row carries a source. Nothing to do.")
        return 0

    print("\n── backup ──────────────────────────────────────────────")
    if not take_backup():
        print("backup FAILED — refusing to migrate. Nothing was changed.")
        return 3

    if summary["encrypted_rows"]:
        if not mc.verify_keys():
            print("canary did not decrypt — refusing to migrate. Nothing was changed.")
            return 4

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    copy_path = DB_PATH.with_suffix(f".db.sourcing-{stamp}")

    print("\n── backfill a COPY ─────────────────────────────────────")
    shutil.copy2(DB_PATH, copy_path)
    try:
        backfill_copy(copy_path)

        print("\n── verify the copy against the original ────────────────")
        problems = verify_copy(DB_PATH, copy_path)
        if problems:
            print("\nVERIFY FAILED — the live database was NOT touched:")
            for line in problems[:20]:
                print("  " + line)
            copy_path.unlink(missing_ok=True)
            return 5

        # Explicit close before the rename: on Windows an open sqlite handle
        # blocks it with WinError 32, stranding the swap exactly where it must
        # not fail. Same lesson as the encryption migration.
        check = _connect(copy_path, readonly=True)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if integrity != "ok":
            print(f"\ncopy integrity={integrity} — refusing to swap.")
            copy_path.unlink(missing_ok=True)
            return 6
        print("  PRAGMA integrity_check: ok")
    except Exception:
        copy_path.unlink(missing_ok=True)
        raise

    print("\n── swap ────────────────────────────────────────────────")
    ASIDE_DIR.mkdir(parents=True, exist_ok=True)
    aside = ASIDE_DIR / f"jarvis_longterm.db.pre-source-{stamp}"
    shutil.move(str(DB_PATH), str(aside))     # moved aside, never deleted
    try:
        # os.replace, not shutil.move: atomic on one volume, and it cannot
        # silently degrade to copy-then-unlink and strand a stray file.
        os.replace(copy_path, DB_PATH)
    except OSError as exc:
        print(f"\nSWAP FAILED after the original was moved aside: {exc}")
        print("Your data is intact. Put it back with:")
        print(f'    copy "{aside}" "{DB_PATH}"')
        print(f"The backfilled candidate is at:\n    {copy_path}")
        return 8
    print(f"  original database kept at:\n    {aside}")

    print("\n── read back through the live code path ────────────────")
    # No importlib.reload here, unlike migrate_memory_encryption.py: every read
    # opens a fresh connection to _DB_PATH, so the swapped file is picked up on
    # its own. Reloading would re-run _init_db() and reset _DB_PATH, which is
    # both unnecessary and the kind of side effect a migration should not have.
    profile = mm.get_full_profile("KAUSTAV")
    readable = sum(1 for m in profile
                   if m["content"] and not mc.is_encrypted(m["content"]))
    print(f"  get_full_profile -> {len(profile)} rows, {readable} readable")
    print(f"  source_counts    -> {mm.source_counts()}")
    if profile:
        sample = profile[0]
        print(f"  sample: [{sample['category']}/{sample['source']}] "
              f"{sample['content'][:60]}")
    if readable != len(profile):
        print("\nWARNING: some rows did not decrypt through the live path.")
        print(f'Restore with:  copy "{aside}" "{DB_PATH}"')
        return 7

    print("\nMIGRATION COMPLETE — every memory now says how it arrived.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true", help="read-only survey")
    group.add_argument("--apply", action="store_true", help="run the migration")
    args = parser.parse_args()

    if args.report:
        print("── survey (read-only, nothing is changed) ──────────────")
        summary = report(DB_PATH)
        print("\nkeys ready:", mc.keys_ready())
        return 2 if summary["blocked"] else 0
    return apply_migration()


if __name__ == "__main__":
    raise SystemExit(main())
