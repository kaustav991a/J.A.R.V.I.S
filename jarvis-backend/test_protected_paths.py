"""
test_protected_paths.py — the keys must outlive a mistake
=========================================================

Pre-Electron review, 2026-08-15. `ActionEngine.restricted_folders` guarded
`C:/Windows` and the two Program Files directories — the operating system,
which can be reinstalled — and nothing else. The files it did NOT guard are the
only ones on the machine that cannot be:

    jarvis_key.dpapi        DPAPI wrap of the data-encryption key
    jarvis_key.recovery     the scrypt wrap; the last resort
    jarvis_x25519.enc       the cloud->desk unseal private key
    jarvis_key.canary
    jarvis_longterm.db      the encrypted memories themselves
    .env                    every API key and token

Delete the two wraps and every row of `jarvis_longterm.db` is unreadable
forever — including with the recovery CODE, because the recovery WRAP is one of
the files that just went.

Two routes reached them and neither consulted anything: `delete_file` unlinked,
and `_workspace_write` truncated, which destroys a key file just as completely.
Governance approves both by action TYPE and never inspects the argument, so a
model steered by an injected web page or document could have ended the entire
C#11a encryption arc in one action — an action a tier check would have waved
through, because `delete_file` is a perfectly ordinary thing to be allowed.

The guard is deliberately a short list of FILES rather than a sandbox over the
backend directory: JARVIS writes code and notes beside these all day, and a
sandbox would cost that for no extra safety.
"""

import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

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


# The guard is module-level precisely so it can be driven without building an
# ActionEngine (which would open a TV connection, a Gmail agent and more).
from action_engine import (  # noqa: E402
    PROTECTED_FILES, PROTECTED_FOLDERS, protected_path_problem,
)

BACKEND = HERE


def test_every_key_file_is_refused():
    for name in ("jarvis_key.dpapi", "jarvis_key.recovery", "jarvis_x25519.enc",
                 "jarvis_key.canary", "jarvis_longterm.db",
                 "jarvis_fact_ledger.db", ".env"):
        problem = protected_path_problem(str(BACKEND / name))
        check(problem is not None, f"refused: {name}")
        check("won't touch" in (problem or ""), f"...with a reason a person reads: {name}")


def test_the_two_wraps_are_both_on_the_list():
    # Either one alone is enough to lose everything, so this is not redundancy.
    names = {p.name.lower() for p in PROTECTED_FILES}
    check("jarvis_key.dpapi" in names, "the DPAPI wrap is protected")
    check("jarvis_key.recovery" in names,
          "the RECOVERY wrap is protected — a recovery code cannot open a wrap that is gone")


def test_a_traversal_cannot_walk_around_the_check():
    # Resolved BEFORE comparison, so these all land on the same real file.
    for sneaky in (
        str(BACKEND / "subdir" / ".." / "jarvis_key.dpapi"),
        str(BACKEND / "." / "jarvis_key.dpapi"),
        str(BACKEND).replace("\\", "/") + "/jarvis_key.dpapi",
    ):
        check(protected_path_problem(sneaky) is not None,
              f"refused via a walked path: ...{sneaky[-38:]}")


def test_the_check_is_case_insensitive_on_windows():
    # Windows opens JARVIS_KEY.DPAPI and jarvis_key.dpapi as the same file, so a
    # case-sensitive comparison would be a bypass rather than a nicety.
    if sys.platform.startswith("win"):
        check(protected_path_problem(str(BACKEND / "JARVIS_KEY.DPAPI").upper()) is not None
              or protected_path_problem(str(BACKEND / "JARVIS_KEY.DPAPI")) is not None,
              "an upper-cased key filename is still refused")
    else:
        check(True, "case-insensitivity check is Windows-only (skipped)")


def test_the_backups_folder_is_refused_including_its_contents():
    root = PROTECTED_FOLDERS[0]
    check(protected_path_problem(str(root)) is not None,
          "the backups root itself is refused")
    check(protected_path_problem(str(root / "pre-encryption-20260808" / "x.db")) is not None,
          "...and anything inside it")


def test_ordinary_files_are_still_writable_and_deletable():
    # The guard is worthless if it also stops JARVIS doing its job. These sit
    # right beside the protected files on purpose.
    for ordinary in ("brain.py", "notes.md", "RESUME.md", "some_output.txt",
                     "jarvis_key_notes.txt", "jarvis_longterm.db.bak"):
        check(protected_path_problem(str(BACKEND / ordinary)) is None,
              f"still allowed: {ordinary}")
    check(protected_path_problem(r"C:\Users\KINGSHUK\Desktop\poem.txt") is None,
          "still allowed: an ordinary desktop file")


def test_an_unresolvable_path_is_refused_rather_than_raising():
    # Fail closed: a path that cannot even be resolved must not fall through to
    # unlink() and raise there.
    check(protected_path_problem("\x00bad") is not None,
          "a path that cannot be resolved is refused, not raised")


# ── wiring: both destructive routes must consult it ──────────────────────────

_SRC = (HERE / "action_engine.py").read_text(encoding="utf-8", errors="replace")
_TREE = ast.parse(_SRC)


def _calls_guard(method_name: str) -> bool:
    fn = next((n for n in ast.walk(_TREE)
               if isinstance(n, ast.FunctionDef) and n.name == method_name), None)
    if fn is None:
        return False
    return any(
        isinstance(n, ast.Call)
        and (getattr(n.func, "attr", None) in ("_protected_path_problem",
                                               "protected_path_problem")
             or getattr(n.func, "id", None) == "protected_path_problem")
        for n in ast.walk(fn)
    )


def test_delete_file_consults_the_guard():
    check(_calls_guard("_delete_file"),
          "_delete_file checks the protected list before unlinking")


def test_workspace_write_consults_the_guard():
    # This one had NO check of any kind, and truncating a key file destroys it
    # exactly as thoroughly as deleting it.
    check(_calls_guard("_workspace_write"),
          "_workspace_write checks the protected list before writing")


def test_the_guard_runs_before_the_destructive_call():
    """Order matters: a check after the unlink is not a check."""
    fn = next((n for n in ast.walk(_TREE)
               if isinstance(n, ast.FunctionDef) and n.name == "_delete_file"), None)
    check(fn is not None, "_delete_file found")
    guard_line = min(
        (n.lineno for n in ast.walk(fn)
         if isinstance(n, ast.Call)
         and getattr(n.func, "attr", None) == "_protected_path_problem"),
        default=None)
    unlink_line = min(
        (n.lineno for n in ast.walk(fn)
         if isinstance(n, ast.Call)
         and getattr(n.func, "attr", None) in ("unlink", "rmtree")),
        default=None)
    check(guard_line is not None and unlink_line is not None,
          "both the guard and the deletion are present")
    check((guard_line or 0) < (unlink_line or 0),
          f"the guard runs first (guard@{guard_line} < unlink@{unlink_line})")


TESTS = [
    test_every_key_file_is_refused,
    test_the_two_wraps_are_both_on_the_list,
    test_a_traversal_cannot_walk_around_the_check,
    test_the_check_is_case_insensitive_on_windows,
    test_the_backups_folder_is_refused_including_its_contents,
    test_ordinary_files_are_still_writable_and_deletable,
    test_an_unresolvable_path_is_refused_rather_than_raising,
    test_delete_file_consults_the_guard,
    test_workspace_write_consults_the_guard,
    test_the_guard_runs_before_the_destructive_call,
]


def main():
    print("=" * 60)
    print("protected-paths harness (pre-Electron review)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
