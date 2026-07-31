"""C#11a — retire `jarvis_memory.db` into the encrypted Memory OS.

"Retire" is not "delete": both of its tables were still live.

  long_term_memory — written by the `remember_fact` action, read into the wake
                     briefing. Its facts move into `memory_manager`, which has
                     the per-user attribution and duplicate detection the old
                     store never had.
  session_digest   — sleep/wake continuity. Moves to `jarvis_longterm.db` and
                     becomes encrypted, which it never was.

The file itself is MOVED ASIDE, never deleted, exactly like the plaintext
database from the encryption migration.

    venv\\Scripts\\python.exe retire_jarvis_memory_db.py --report
    venv\\Scripts\\python.exe retire_jarvis_memory_db.py --apply [--keep-all]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Force UTF-8 on stdout/stderr, same hardening as main.py \ watchdog.py \
# run_harnesses.py. This CLI moves a live database aside; a cp1252
# UnicodeEncodeError on a status line must not be what stops it half-way.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BACKEND_DIR = Path(__file__).resolve().parent
OLD_DB = BACKEND_DIR / "jarvis_memory.db"
ASIDE_DIR = BACKEND_DIR.parent.parent / "JARVIS-BACKUPS" / "plaintext-originals"

#: Two facts count as restatements above this token overlap. The old store's
#: UNIQUE(fact) only caught byte-identical text, so repeated `remember_fact`
#: calls piled up six phrasings of one preference.
NEAR_DUPLICATE_RATIO = 0.6


def _tokens(text: str) -> set:
    return {w for w in "".join(
        c if c.isalnum() or c.isspace() else " " for c in (text or "").casefold()
    ).split() if len(w) > 2}


def _similar(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def cluster_restatements(facts: list) -> tuple:
    """Group restatements. Returns (keep, skipped) where skipped names its twin.

    The shortest phrasing wins — the longer ones in this store are artefacts of
    the action parser echoing its own template ("Preferred coding folder is
    Documents, Fact details: Documents").

    Single-linkage: a candidate joins a cluster if it resembles ANY member, not
    just the representative. "Coding folder preference, Fact details: Documents"
    scores only 0.43 against the shortest phrasing but 0.71 against a middle
    one, so comparing against the representative alone would let it through —
    and lowering the threshold to catch it would start merging unrelated facts.
    """
    keep, skipped, clusters = [], [], []
    for rid, category, fact in sorted(facts, key=lambda r: len(r[2] or "")):
        home = next(
            (c for c in clusters
             if any(_similar(member, fact) >= NEAR_DUPLICATE_RATIO for member in c["texts"])),
            None,
        )
        if home:
            home["texts"].append(fact)
            skipped.append((rid, category, fact, home["id"]))
        else:
            clusters.append({"id": rid, "texts": [fact]})
            keep.append((rid, category, fact))
    return keep, skipped


def read_old_store() -> dict:
    if not OLD_DB.exists():
        return {"facts": [], "digests": [], "missing": True}
    conn = sqlite3.connect(f"file:{OLD_DB.as_posix()}?mode=ro", uri=True)
    try:
        facts, digests = [], []
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "long_term_memory" in tables:
            facts = conn.execute(
                "SELECT id, category, fact FROM long_term_memory ORDER BY id"
            ).fetchall()
        if "session_digest" in tables:
            digests = conn.execute(
                "SELECT user, digest, timestamp FROM session_digest"
            ).fetchall()
        return {"facts": facts, "digests": digests, "missing": False}
    finally:
        conn.close()


def report(keep_all: bool = False) -> dict:
    import memory_manager as mm

    data = read_old_store()
    if data["missing"]:
        print(f"  {OLD_DB.name} is already gone — nothing to retire")
        return data

    existing = set()
    for user in ("KAUSTAV", "MOUSUMI"):
        existing |= {" ".join(m["content"].split()).casefold()
                     for m in mm.get_full_profile(user)}

    fresh = [(rid, c, f) for rid, c, f in data["facts"]
             if " ".join((f or "").split()).casefold() not in existing]
    already = len(data["facts"]) - len(fresh)

    keep, skipped = (fresh, []) if keep_all else cluster_restatements(fresh)

    print(f"  long_term_memory : {len(data['facts'])} rows")
    print(f"    already in the Memory OS : {already}")
    print(f"    to migrate               : {len(keep)}")
    for rid, category, fact in keep:
        print(f"      id={rid:<3} [{category}] -> [{_map(category)}] {fact}")
    if skipped:
        print(f"    restatements skipped     : {len(skipped)}  (--keep-all migrates them)")
        for rid, _, fact, twin in skipped:
            print(f"      id={rid:<3} {fact[:58]!r}\n           same as id={twin}")
    print(f"  session_digest   : {len(data['digests'])} row(s) -> encrypted in jarvis_longterm.db")

    data["keep"], data["skipped"] = keep, skipped
    return data


def _map(category: str) -> str:
    import memory
    return memory._map_category(category)


def apply(keep_all: bool = False) -> int:
    import memory
    import memory_manager as mm
    from modules import memory_crypto as mc

    if not OLD_DB.exists():
        print(f"{OLD_DB.name} is already retired.")
        return 0
    if not mc.keys_ready():
        print("no key set. Run: manage_keys.py init")
        return 1

    print("── survey ──────────────────────────────────────────────")
    data = report(keep_all)

    print("\n── backup ──────────────────────────────────────────────")
    proc = subprocess.run([sys.executable, str(BACKEND_DIR / "backup_memory.py")],
                          cwd=BACKEND_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        print("backup FAILED — refusing to retire. Nothing was changed.")
        print(proc.stdout[-800:])
        return 2
    print("  backup taken and verified")

    print("\n── migrate the facts ───────────────────────────────────")
    migrated, refused = [], []
    for rid, category, fact in data["keep"]:
        ok = mm.add_memory(content=fact.strip(), category=_map(category), user="KAUSTAV")
        (migrated if ok else refused).append((rid, fact))
    print(f"  migrated {len(migrated)}, already-present {len(refused)}")

    print("\n── migrate the session digest ──────────────────────────")
    for user, digest, _ts in data["digests"]:
        if not digest:
            continue
        memory.save_session_digest(user, digest)
        back = memory.get_last_session_digest(user)
        if back != digest:
            print(f"  FAILED for {user}: digest did not round-trip. Nothing moved aside.")
            return 3
        print(f"  {user}: {len(digest)} chars, encrypted and read back identical")

    print("\n── verify every migrated fact is readable ──────────────")
    now = {" ".join(m["content"].split()).casefold()
           for m in mm.get_full_profile("KAUSTAV")}
    missing = [f for _, f in migrated if " ".join(f.split()).casefold() not in now]
    if missing:
        print("  MISSING after migration — nothing moved aside:")
        for f in missing:
            print(f"    {f!r}")
        return 4
    print(f"  all {len(migrated)} migrated fact(s) read back from the encrypted store")

    print("\n── move the old file aside ─────────────────────────────")
    ASIDE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    aside = ASIDE_DIR / f"jarvis_memory.db.retired-{stamp}"
    shutil.move(str(OLD_DB), str(aside))
    print(f"  kept at:\n    {aside}")

    print("\nRETIRED. jarvis_memory.db is gone from the backend directory;")
    print("both of its tables now live encrypted in jarvis_longterm.db.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-all", action="store_true",
                        help="migrate restatements too, instead of collapsing them")
    args = parser.parse_args()

    if args.report:
        print("── survey (read-only, nothing is changed) ──────────────")
        report(args.keep_all)
        return 0
    return apply(args.keep_all)


if __name__ == "__main__":
    raise SystemExit(main())
