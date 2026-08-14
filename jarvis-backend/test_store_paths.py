r"""test_store_paths.py — persistent stores must not follow the working directory.

Run: venv\Scripts\python.exe test_store_paths.py

The defect this pins, found 2026-08-15: `memory.py` held

    CHROMA_PATH = "jarvis_chroma_db"

a bare relative path, resolved against the *process's* CWD. Every other Chroma
call site in the tree anchors on `__file__`, so launching from the repo root
instead of jarvis-backend/ split Tier 3 in half: `memory.py` opened a new, empty
`jarvis_chroma_db` beside the repo while `modules/episodic_memory.py` — naming the
same folder, but anchored — kept using the real one. Neither raised. The symptom
is semantic memory that has "forgotten", with nothing in the log to explain it.

It was reproduced, not theorised: the stray store held collection `jarvis_memory`
with **0** embeddings while the real one held `jarvis_episodes` + `jarvis_memory`
with **119**. It also arrived UNIGNORED — every memory-store rule in .gitignore
was anchored to `jarvis-backend/`, and that store is the plaintext vector mirror
of facts that are ciphertext in `jarvis_longterm.db`.

Two tests, deliberately of different kinds:

  * The BEHAVIOURAL one imports `memory` in a subprocess whose CWD is a scratch
    directory, then asserts that nothing was created there. That is the actual
    failure, executed — not a claim about the source.
  * The STATIC one sweeps the tree for relative store paths. Here a source check
    is the right instrument rather than a proxy for one: the defect *is* a literal
    in source, and this is what catches the next file to do it, which the
    behavioural test cannot because it only knows about `memory`.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


# Folder names that are persistent stores, not incidental directories.
STORE_DIRS = (
    "jarvis_chroma_db",
    "personal_chroma_db",
    "action_chroma_db",
    "rag_chroma_db",
    "vector_db",
)


# ── 1. Behavioural: import from elsewhere, and nothing must be created there ──

def test_importing_memory_from_another_cwd_creates_nothing_there():
    """The regression, run rather than described.

    A subprocess imports `memory` with its CWD set to an empty scratch directory.
    Before the fix this produced `<scratch>/jarvis_chroma_db/chroma.sqlite3`; now
    the scratch directory must still be empty and CHROMA_PATH must sit under
    jarvis-backend/.
    """
    with tempfile.TemporaryDirectory(prefix="jarvis_cwd_") as scratch:
        code = (
            "import memory, json, sys;"
            "sys.stdout.write(json.dumps({'path': memory.CHROMA_PATH}))"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(HERE) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=scratch, env=env, capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            check(False, f"importing memory from another cwd failed: "
                         f"{(proc.stderr or '').strip()[-400:]}")
            return

        # Nothing at all may be created in the scratch directory.
        leaked = sorted(p.name for p in pathlib.Path(scratch).iterdir())
        check(not leaked, f"importing memory created {leaked} in an unrelated cwd")

        # And the path it chose must live under this backend folder.
        tail = (proc.stdout or "").strip()
        start = tail.rfind("{")
        try:
            import json
            path = pathlib.Path(json.loads(tail[start:])["path"]).resolve()
        except Exception as e:  # noqa: BLE001
            check(False, f"could not read CHROMA_PATH back: {e} (stdout tail={tail[-200:]!r})")
            return
        check(path.is_absolute(), f"CHROMA_PATH must be absolute, got {path}")
        check(HERE in path.parents,
              f"CHROMA_PATH must sit under {HERE}, got {path}")


# ── 2. Static: no store path anywhere may be CWD-relative ────────────────────

# A store name in quotes with no path construction around it. `os.path.join(...)`
# and `pathlib` forms are anchored and pass; a bare literal is the defect.
_BARE = re.compile(
    r"""^[^#\n]*?=\s*(['"])(?P<name>%s)/?\1""" % "|".join(map(re.escape, STORE_DIRS)),
    re.MULTILINE,
)


def _python_files():
    for p in HERE.rglob("*.py"):
        parts = set(p.parts)
        if "venv" in parts or "__pycache__" in parts or "node_modules" in parts:
            continue
        if p.name == pathlib.Path(__file__).name:
            continue          # this harness names them all, on purpose
        yield p


def test_no_store_path_is_a_bare_relative_literal():
    offenders = []
    for p in _python_files():
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _BARE.finditer(src):
            line = src[:m.start()].count("\n") + 1
            offenders.append(f"{p.relative_to(HERE)}:{line} -> {m.group('name')!r}")
    check(not offenders,
          "store paths must be anchored on __file__, not the CWD: " + "; ".join(offenders))


def test_the_guard_would_actually_catch_the_old_defect():
    """A guard nobody has seen fail is a guard nobody should trust."""
    check(bool(_BARE.search('CHROMA_PATH = "jarvis_chroma_db"\n')),
          "the sweep must flag the exact line that caused this")
    check(_BARE.search(
        'CHROMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),'
        ' "jarvis_chroma_db")\n') is None,
        "the sweep must NOT flag a correctly anchored path")


# ── 3. The stores must be unignorable by git, wherever they land ──────────────

def test_gitignore_covers_the_stores_unanchored():
    """The stray store arrived unignored because every rule was anchored to
    `jarvis-backend/`. A store is secret wherever it is, so the rules must match
    at any depth."""
    gi = HERE.parent / ".gitignore"
    if not gi.is_file():
        check(False, f"no .gitignore at {gi}")
        return
    lines = {ln.strip() for ln in gi.read_text(encoding="utf-8").splitlines()}
    for name in STORE_DIRS:
        check(f"{name}/" in lines,
              f".gitignore needs an unanchored '{name}/' rule")


TESTS = [
    test_importing_memory_from_another_cwd_creates_nothing_there,
    test_no_store_path_is_a_bare_relative_literal,
    test_the_guard_would_actually_catch_the_old_defect,
    test_gitignore_covers_the_stores_unanchored,
]


def main():
    print("=" * 60)
    print("store path anchoring harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
