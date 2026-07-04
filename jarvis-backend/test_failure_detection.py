"""Reliability harness for ActionEngine._is_failure.

Phase 2 of the reliability hardening: before this, control-action failures
(launch/type/save) were not prefixed "error:"/"failed:", so _is_failure scored
them COMPLETE and JARVIS narrated "Done, Sir" while nothing happened. This locks
in the fix: the specific phrases agents actually emit are detected, while
data/content results (which legitimately contain scary words) are never flagged.

Run:  python test_failure_detection.py     (imports ActionEngine; no live command)
Exit 0 = all passed, 1 = a failure.
"""
import sys

from action_engine import ActionEngine

# _is_failure only reads class-level attrs → skip the heavy __init__.
_engine = ActionEngine.__new__(ActionEngine)

# (result, action_type, expected_is_failure)
CASES = [
    ("Smart Open failed: app not found", "native_app_launcher", True),
    ("I couldn't locate 'Blender'", "native_app_launcher", True),
    ("I found 'x' but couldn't open it", "native_app_launcher", True),
    ("GUI Execution Error: focus lost", "ghost_type", True),
    ("SAVE_DIALOG_NOT_FOUND", "ghost_save_file", True),
    ("unable to reach the television", "tv_control", True),
    ("Saved, Sir.", "ghost_save_file", False),
    ("Launched Notepad (PID 1234)", "native_app_launcher", False),
    ("Created: test.py", "workspace_write", False),
    ("Closed 2 window(s) matching 'chrome'", "close_app", False),
    # Content actions must NEVER be flagged, even with scary words:
    ("SCREEN CONTENTS: fatal error: build failed", "read_screen", False),
    ("- Error handling in Python: use try/except", "web_search", False),
    ("commit abc failed to merge (log line)", "run_terminal_command", False),
    ("Error navigating to https://x", "web_browse", False),
    # Dict contract:
    ({"success": False}, "anything", True),
    ({"success": True}, "anything", False),
    ({"success": True}, None, False),
]


def _run() -> int:
    failures = 0
    print("=== _is_failure reliability harness ===\n")
    for result, atype, expected in CASES:
        got = _engine._is_failure(result, atype)
        ok = got == expected
        if not ok:
            failures += 1
        label = repr(result)[:48]
        print(f"[{'PASS' if ok else 'FAIL'}] {label:<50} at={atype} got={got} exp={expected}")
    total = len(CASES)
    print(f"\n{total - failures}/{total} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run())
