"""Harness: no test in this suite may be silently skipped.

WHY THIS EXISTS
---------------
2026-09-05. Four tests were added to `test_claims_guard.py`, the file was run,
and it printed:

    91 passed, 0 failed

Exactly what it printed before they were written. The file collects its tests
into a module-level `TESTS = sorted(...)` list, and that line sat **above** the
new functions, so they were never called. Nothing said so. The suite stayed
green, the run took the same time, and the only reason it was caught is that the
number looked wrong.

**A skipped test is worse than a missing one.** A missing test is an absence
someone may notice; a skipped one reports confidence it has not earned, which is
the exact failure this whole suite exists to catch in the product. The class is
also familiar by now: a thing that fails quietly while the layer above it
reports success.

Every harness in this directory that collects at import time is one edit away
from the same silence, so the rule is checked for all of them rather than fixed
in the one that happened to be caught.

WHAT THIS PINS
--------------
That no harness defines a `test_` function AFTER the point where it decides
which tests to run.
"""

from __future__ import annotations

import ast
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent

_checks = 0
_fails: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print(f"FAIL  {label}")


def _collection_line(tree: ast.Module) -> int | None:
    """Line where a module-level assignment gathers the test functions.

    Only module-level: a collection inside `main()` or a helper runs when it is
    called, by which point every function in the file exists. That is the safe
    shape, and finding one means there is nothing to check here.
    """
    for node in tree.body:  # module level only, deliberately
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        src = ast.unparse(node)
        if "startswith" in src and "test_" in src and "globals()" in src:
            return node.lineno
    return None


def _harnesses() -> list[Path]:
    return sorted(p for p in HERE.glob("test_*.py") if p.name != Path(__file__).name)


def test_there_are_harnesses_to_check():
    files = _harnesses()
    check(len(files) > 50,
          f"the sweep sees the suite ({len(files)} harnesses) - an empty sweep "
          f"passes vacuously, which is this file's own failure mode")


def test_no_harness_defines_a_test_after_it_decides_what_to_run():
    offenders = []
    for path in _harnesses():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            offenders.append(f"{path.name}: will not parse ({e.lineno})")
            continue
        line = _collection_line(tree)
        if line is None:
            continue  # collects at call time, or lists its tests explicitly
        after = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test_") and n.lineno > line]
        if after:
            offenders.append(
                f"{path.name}: collects at line {line}, but defines "
                f"{len(after)} test(s) below it that will never run: "
                f"{', '.join(after[:3])}")
    check(not offenders,
          "no harness collects its tests before the last one is defined"
          + ("\n      " + "\n      ".join(offenders) if offenders else ""))


def test_the_file_that_was_caught_now_collects_at_call_time():
    """The specific fix, kept honest - it is easy to revert by tidying."""
    path = HERE / "test_claims_guard.py"
    if not path.exists():
        check(False, "test_claims_guard.py exists")
        return
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    check(_collection_line(tree) is None,
          "test_claims_guard.py no longer gathers its tests at import time")
    src = path.read_text(encoding="utf-8", errors="replace")
    check("def _collect()" in src, "it collects inside a function instead")
    check("TESTS = _collect()" in src, "and main() calls that function")


if __name__ == "__main__":
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)),
                   key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        try:
            fn()
        except Exception:
            _fails.append(name)
            print(f"FAIL  {name} raised")
            traceback.print_exc()
    print(f"\n{_checks - len(_fails)}/{_checks} passed.")
    sys.exit(1 if _fails else 0)
