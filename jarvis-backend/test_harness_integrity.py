"""Harness: the suite runs every test it contains.

Found 2026-08-16, while adding three tests to `test_url_precondition.py`. The
harness reported **"43 passed, 0 failed"** afterwards — the identical number as
before — because it drove a hand-written `TESTS = [...]` list and the three new
functions were not in it. A green count that does not move is the most
convincing possible way to not notice that nothing changed.

`run_harnesses.py` had the same bug one level up: it kept a hand-written list of
harness FILES and was quietly not running one of them (§6.8.2). That was fixed by
switching to discovery. This is the same fix applied one level down, except that
rewriting twenty-odd harnesses is churn — so instead the property is enforced
from outside: **a test function that exists must be reachable.**

Nothing here executes another harness. It reads their source, which is the only
way to see a function that is never called.
"""

import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _listed_tests(tree: ast.Module):
    """What a module's `TESTS` assignment names, or None if it has no such list.

    Returns the sentinel "computed" for a TESTS built by discovery (a
    comprehension over `globals()`), which is correct by construction and has
    nothing to compare.
    """
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if "TESTS" not in [t.id for t in node.targets if isinstance(t, ast.Name)]:
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            return {e.id for e in node.value.elts if isinstance(e, ast.Name)}
        return "computed"
    return None


def test_no_harness_defines_a_test_it_never_runs():
    """Every `test_*` function is either in the module's TESTS list, or the
    module discovers its tests and cannot omit one.

    A harness with NO `TESTS` list at all is skipped here: those use the
    `globals()` loop in `__main__`, which is discovery already.
    """
    orphans = []
    scanned = 0
    for path in sorted(HERE.glob("test_*.py")):
        if path.name == pathlib.Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        defined = [n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name.startswith("test_")]
        if not defined:
            continue
        listed = _listed_tests(tree)
        if listed is None or listed == "computed":
            continue
        scanned += 1
        for name in defined:
            if name not in listed:
                orphans.append(f"{path.name}::{name}")

    check(scanned > 0,
          f"scanned {scanned} harnesses that keep an explicit TESTS list")
    check(not orphans,
          "no harness defines a test that its TESTS list omits"
          + (f" — ORPHANS: {orphans}" if orphans else ""))


def _has_main_guard(tree: ast.Module) -> bool:
    """Does the module have a top-level `if __name__ == "__main__":`?"""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            if isinstance(sub, ast.Name) and sub.id == "__name__":
                return True
    return False


def test_every_harness_can_actually_be_run():
    """A harness with test functions must have some way of running them.

    The check above skips a module with no `TESTS` list, on the assumption that
    it discovers its tests in a `__main__` loop. Two harnesses arrived on
    2026-08-20 with NEITHER — written in pytest style, and this suite retired
    pytest deliberately. `run_harnesses.py` exec'd them, they defined 23 test
    functions between them, ran none of them, printed nothing and exited 0. Both
    were reported green at "0 checks" for two days.

    That assumption is what this closes. `run_harnesses.py` now also refuses a
    harness that reports zero checks, which catches the same thing from outside;
    this catches it by shape, and names the file.
    """
    unrunnable = []
    for path in sorted(HERE.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        defined = [n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name.startswith("test_")]
        if not defined:
            continue
        if _listed_tests(tree) is None and not _has_main_guard(tree):
            unrunnable.append(f"{path.name} ({len(defined)} tests)")

    check(not unrunnable,
          "every harness holding tests can run them"
          + (f" — UNRUNNABLE: {unrunnable}" if unrunnable else ""))


def test_every_harness_file_is_discovered_rather_than_listed():
    """`run_harnesses.py` must not go back to a hand-kept list of files.

    This is the bug one level up, and it hid a never-run harness once already.
    Asserted structurally: the runner has to reach for the filesystem.
    """
    src = (HERE / "run_harnesses.py").read_text(encoding="utf-8", errors="replace")
    check("glob(" in src, "run_harnesses.py discovers harness files by glob")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 60)
    print("Harness integrity — does the suite run everything it holds?")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
