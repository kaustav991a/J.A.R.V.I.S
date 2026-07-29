"""C#11a Step 2 — convert the existing plaintext rows to encrypted ones.

The whole design here is "never touch the original until the copy is proven":

    1. take a fresh backup (backup_memory.py, verified)
    2. copy the database
    3. encrypt the copy
    4. read EVERY row of the copy back and compare it to the original, byte
       for byte — via the same decrypt path the running system uses
    5. only then swap, and the original is MOVED ASIDE, never deleted

Any failure at any step leaves the live database exactly as it was.

    venv\\Scripts\\python.exe migrate_memory_encryption.py --report   # read-only
    venv\\Scripts\\python.exe migrate_memory_encryption.py --apply    # do it
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

from modules import memory_crypto as mc

BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = BACKEND_DIR / "jarvis_longterm.db"
BACKUP_ROOT = BACKEND_DIR.parent.parent / "JARVIS-BACKUPS"
ASIDE_DIR = BACKUP_ROOT / "plaintext-originals"

# (table, [encrypted columns], blind-index column or None)
TARGETS = [
    ("memories", ["content"], "content_hash"),
    ("partner_messages", ["content", "partner_name"], None),
]


def _connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(str(path))


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def report(db_path: Path) -> dict:
    """Read-only survey. Prints what WOULD happen and finds blockers."""
    conn = _connect(db_path, readonly=True)
    summary = {"tables": {}, "collisions": {}, "blocked": False}
    try:
        for table, columns, hash_col in TARGETS:
            if not _table_exists(conn, table):
                print(f"  {table:20s} absent — nothing to do")
                continue
            total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            per_column = {}
            for column in columns:
                rows = conn.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
                ).fetchall()
                plain = sum(1 for (v,) in rows if not mc.is_encrypted(v))
                enc = sum(1 for (v,) in rows if mc.is_encrypted(v))
                per_column[column] = {"plaintext": plain, "encrypted": enc}
            summary["tables"][table] = {"rows": total, "columns": per_column}
            desc = ", ".join(
                f"{c}: {d['plaintext']} plaintext / {d['encrypted']} already encrypted"
                for c, d in per_column.items()
            )
            print(f"  {table:20s} {total:>4} rows  ({desc})")

            # A collision would make the unique fingerprint index unbuildable.
            # Better to name the rows now than to discover it mid-swap.
            if hash_col and table == "memories":
                seen = {}
                for rid, user, content in conn.execute(
                    "SELECT id, user, content FROM memories"
                ):
                    if mc.is_encrypted(content):
                        continue
                    key = (user, " ".join((content or "").split()).casefold())
                    seen.setdefault(key, []).append(rid)
                dupes = {k: v for k, v in seen.items() if len(v) > 1}
                if dupes:
                    summary["collisions"][table] = {str(k): v for k, v in dupes.items()}
                    summary["blocked"] = True
                    print(f"\n  BLOCKER: {len(dupes)} duplicate fact(s) in {table}:")
                    for (user, text), ids in dupes.items():
                        print(f"    rows {ids} — [{user}] {text[:60]!r}")
                    print("    These differ only by case/spacing. Nothing was changed.")
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


def encrypt_copy(copy_path: Path, dek: bytes) -> dict:
    """Encrypt in place on the COPY. The live database is untouched here."""
    counts = {}
    conn = _connect(copy_path)
    try:
        for table, columns, hash_col in TARGETS:
            if not _table_exists(conn, table):
                continue
            done = 0
            id_rows = [r[0] for r in conn.execute(f"SELECT id FROM {table} ORDER BY id")]
            for rid in id_rows:
                row = conn.execute(
                    f"SELECT {', '.join(columns)} FROM {table} WHERE id = ?", (rid,)
                ).fetchone()
                updates, params = [], []
                for column, value in zip(columns, row):
                    if value is None or value == "" or mc.is_encrypted(value):
                        continue
                    updates.append(f"{column} = ?")
                    params.append(mc.encrypt_field(value, table, column, dek=dek))
                    if hash_col and column == "content":
                        updates.append(f"{hash_col} = ?")
                        params.append(mc.blind_index(value, table, column, dek=dek))
                if updates:
                    params.append(rid)
                    conn.execute(
                        f"UPDATE {table} SET {', '.join(updates)} WHERE id = ?", params
                    )
                    done += 1
            conn.commit()
            counts[table] = done
            print(f"  {table:20s} {done} row(s) encrypted on the copy")
    finally:
        conn.close()
    return counts


def verify_copy(original: Path, copy_path: Path, dek: bytes) -> list:
    """Decrypt every row of the copy and compare to the original. The proof."""
    problems = []
    src = _connect(original, readonly=True)
    dst = _connect(copy_path, readonly=True)
    try:
        for table, columns, _ in TARGETS:
            if not _table_exists(src, table):
                continue
            src_rows = dict(
                (r[0], r[1:]) for r in src.execute(
                    f"SELECT id, {', '.join(columns)} FROM {table} ORDER BY id"
                )
            )
            dst_rows = dict(
                (r[0], r[1:]) for r in dst.execute(
                    f"SELECT id, {', '.join(columns)} FROM {table} ORDER BY id"
                )
            )
            if set(src_rows) != set(dst_rows):
                missing = set(src_rows) - set(dst_rows)
                extra = set(dst_rows) - set(src_rows)
                problems.append(f"{table}: row ids differ (missing={missing}, extra={extra})")
                continue
            for rid, original_values in src_rows.items():
                for column, was, now in zip(columns, original_values, dst_rows[rid]):
                    try:
                        back = mc.decrypt_field(now, table, column, dek=dek)
                    except Exception as exc:
                        problems.append(f"{table}.{column} id={rid}: decrypt failed ({exc})")
                        continue
                    if back != was:
                        problems.append(
                            f"{table}.{column} id={rid}: round trip changed the value"
                        )
            print(f"  {table:20s} {len(src_rows)} row(s) verified byte-identical")
    finally:
        src.close()
        dst.close()
    return problems


def apply_migration() -> int:
    if not DB_PATH.exists():
        print(f"no database at {DB_PATH}")
        return 1
    if not mc.keys_ready():
        print("no key set. Run: manage_keys.py init")
        return 1

    print("── survey ──────────────────────────────────────────────")
    summary = report(DB_PATH)
    if summary["blocked"]:
        print("\nBLOCKED — resolve the duplicates above first. Nothing was changed.")
        return 2

    print("\n── backup ──────────────────────────────────────────────")
    if not take_backup():
        print("backup FAILED — refusing to migrate. Nothing was changed.")
        return 3

    dek = mc.load_dek()
    if not mc.verify_keys(dek):
        print("canary did not decrypt — refusing to migrate. Nothing was changed.")
        return 4

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    copy_path = DB_PATH.with_suffix(f".db.migrating-{stamp}")

    print("\n── encrypt a COPY ──────────────────────────────────────")
    shutil.copy2(DB_PATH, copy_path)
    try:
        encrypt_copy(copy_path, dek)

        print("\n── verify the copy against the original ────────────────")
        problems = verify_copy(DB_PATH, copy_path, dek)
        if problems:
            print("\nVERIFY FAILED — the live database was NOT touched:")
            for line in problems[:20]:
                print("  " + line)
            copy_path.unlink(missing_ok=True)
            return 5

        # Close this connection explicitly. On Windows an open sqlite handle
        # blocks the rename below with WinError 32, which strands the migration
        # halfway through the swap — exactly the moment it must not fail.
        check = _connect(copy_path, readonly=True)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if integrity != "ok":
            print(f"\ncopy integrity={integrity} — refusing to swap.")
            copy_path.unlink(missing_ok=True)
            return 6
    except Exception:
        copy_path.unlink(missing_ok=True)
        raise

    print("\n── swap ────────────────────────────────────────────────")
    ASIDE_DIR.mkdir(parents=True, exist_ok=True)
    aside = ASIDE_DIR / f"jarvis_longterm.db.plaintext-{stamp}"
    shutil.move(str(DB_PATH), str(aside))     # moved aside, never deleted
    try:
        # os.replace, not shutil.move: atomic on one volume, and it will not
        # silently fall back to copy-then-unlink and leave a stray file behind.
        os.replace(copy_path, DB_PATH)
    except OSError as exc:
        print(f"\nSWAP FAILED after the original was moved aside: {exc}")
        print("Your data is intact. Put it back with:")
        print(f'    copy "{aside}" "{DB_PATH}"')
        print(f'The encrypted candidate is at:\n    {copy_path}')
        return 8
    print(f"  original plaintext database kept at:\n    {aside}")

    print("\n── read back through the live code path ────────────────")
    import importlib
    import memory_manager
    importlib.reload(memory_manager)
    profile = memory_manager.get_full_profile("KAUSTAV")
    readable = sum(1 for m in profile if m["content"] and not mc.is_encrypted(m["content"]))
    print(f"  memory_manager.get_full_profile -> {len(profile)} rows, {readable} readable")
    if profile:
        print(f"  sample: [{profile[0]['category']}] {profile[0]['content'][:60]}")
    if readable != len(profile):
        print("\nWARNING: some rows did not decrypt through the live path.")
        print(f"Restore with:  copy \"{aside}\" \"{DB_PATH}\"")
        return 7

    print("\nMIGRATION COMPLETE — memory encrypted at rest, and readable.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
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
