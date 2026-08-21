"""Harness: a log character cannot abort an operation.

On Windows `sys.stdout.encoding` is `cp1252` whenever stdout is not a UTF-8
console — redirected to a file, piped, captured by the Electron shell, or run
under a service. One `print` containing an em dash, an arrow or an emoji then
raises `UnicodeEncodeError`, and it raises INSIDE whatever was logging. The log
line is not what dies; the operation is.

main.py has opened with a hardening block since the Electron work and its comment
says exactly this. What went unnoticed is that it hardens the process only when
main.py IS the process. Measured 2026-08-22: **48 backend files print non-ASCII
and had no guard.** Most are imported by main.py and inherit its hardening, which
is precisely why this stayed invisible — it is harmless for as long as main.py is
the only way in, and five of them are their own entry point.

Two of the exposed lines sit on paths the live gate is stuck on: `brain.py`
prints an em dash on the `close_app guard` path and an arrow on
`Code-file guard -> workspace_write`. Under `run_evals.py`, under the worker, or
under a harness, a log glyph sat between an instruction and a file write.

Found while fixing F-44, by writing a new log line with a `⚠` in it and watching
the harness die on it rather than the assertion.
"""

import glob
import io
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_passed = 0
_failed = 0

# A print whose arguments contain a byte no cp1252 codepage can encode.
_NON_ASCII_PRINT = re.compile(r"print\([^\n]*[^\x00-\x7f]")
_GUARD = "utf8_stdout"
_INLINE_GUARD = 'reconfigure(encoding="utf-8"'

# Files that are their own entry point AND print non-ASCII. Anything else in the
# backend is reached through one of these, and inherits its hardened stdout.
_ENTRY_POINTS = [
    "main.py",
    "cloud_gateway.py",
    "recorder.py",
    "brain.py",
    "run_phase1_regression.py",
    "cursor_overlay.py",
    "run_evals.py",
    os.path.join("modules", "web_agent.py"),
]


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _read(rel):
    return io.open(HERE / rel, encoding="utf-8", errors="replace").read()


def test_the_shared_guard_exists_and_hardens_on_import():
    """Importing it IS the call — there is nothing to invoke, because it has to
    have run before the first print and an import at the top of a file is the
    only thing that reliably has."""
    src = _read(os.path.join("modules", "utf8_stdout.py"))
    check(_INLINE_GUARD in src, "the shared module reconfigures stdout")
    check('errors="replace"' in src,
          "...with errors=replace, so an unconvertible stream degrades to '?' "
          "rather than to a dead command")
    check("import sys" in src and "def " not in src.split('"""')[-1],
          "...at import time, with no function to forget to call")


def test_it_actually_changes_the_encoding():
    import importlib
    import modules.utf8_stdout as g
    importlib.reload(g)
    check(sys.stdout.encoding.lower().replace("-", "") == "utf8",
          f"stdout is UTF-8 after the import (got {sys.stdout.encoding})")


def test_it_cannot_raise_on_a_stream_it_cannot_reconfigure():
    """A module that exists to stop a log line being fatal must not itself be
    fatal. A closed or wrapped stream is the normal case under a test runner."""
    src = _read(os.path.join("modules", "utf8_stdout.py"))
    body = src[src.index("for _stream"):]
    check("except Exception" in body, "a stream that refuses is swallowed")
    check("pass" in body, "...and does not propagate")


def test_every_entry_point_that_logs_unicode_is_guarded():
    """The property. An entry point is a process, and a process needs its own
    hardening — inheriting main.py's only works if main.py started it."""
    unguarded = []
    for rel in _ENTRY_POINTS:
        src = _read(rel)
        if not _NON_ASCII_PRINT.search(src):
            continue
        if _GUARD not in src and _INLINE_GUARD not in src:
            unguarded.append(rel)
    check(not unguarded,
          "every entry point that prints non-ASCII hardens its stdout"
          + (f" — UNGUARDED: {unguarded}" if unguarded else ""))


def test_the_guard_precedes_the_first_print_in_each_file():
    """Ordering is the whole property. A guard below the first print is a guard
    that runs too late in exactly the case that matters."""
    late = []
    for rel in _ENTRY_POINTS:
        src = _read(rel)
        if not _NON_ASCII_PRINT.search(src):
            continue
        g = min([i for i in (src.find(_GUARD), src.find(_INLINE_GUARD)) if i != -1],
                default=-1)
        m = _NON_ASCII_PRINT.search(src)
        if g == -1 or (m and m.start() < g):
            late.append(rel)
    check(not late,
          "the guard is above the first non-ASCII print"
          + (f" — TOO LATE IN: {late}" if late else ""))


def test_the_two_import_forms_of_the_module_agent_both_work():
    """`modules/web_agent.py` has its own `__main__`, so it is reached both as
    `modules.web_agent` and as a bare script path. A relative import breaks the
    second; a bare import breaks the first. It was written the wrong way round
    first and the package import raised ModuleNotFoundError."""
    src = _read(os.path.join("modules", "web_agent.py"))
    check("from . import utf8_stdout" in src, "the package form is present")
    check("import utf8_stdout" in src.split("except ImportError")[-1][:120],
          "...and the script-path form is the fallback")


def test_no_new_unguarded_entry_point_appears_unnoticed():
    """A file that grows a `__main__` and a Unicode print is a new instance of
    this, and nothing else in the suite would see it. Listed rather than
    discovered, because 'is this really an entry point' is a judgement — but the
    count is pinned so the judgement gets revisited."""
    candidates = []
    for f in sorted(glob.glob(str(HERE / "*.py"))
                    + glob.glob(str(HERE / "modules" / "*.py"))):
        rel = os.path.relpath(f, HERE)
        if os.path.basename(rel).startswith("test_"):
            continue
        src = io.open(f, encoding="utf-8", errors="replace").read()
        if "__main__" in src and _NON_ASCII_PRINT.search(src):
            if _GUARD not in src and _INLINE_GUARD not in src:
                candidates.append(rel)
    check(not candidates,
          "no file has both a __main__ and an unguarded Unicode print"
          + (f" — NEW: {candidates}" if candidates else ""))


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Log encoding — a print must not be able to kill a command")
    print("=" * 62)
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            global _failed
            _failed += 1
            print(f"FAIL  {t.__name__} raised {type(e).__name__}: {e}")
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
