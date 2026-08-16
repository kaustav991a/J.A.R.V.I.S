"""Harness for the pre-Electron review, batch 3 — the brain.

  A1  a reply that dispatched NOTHING wrote the stub that proves an action ran,
      and F-16's guard reads exactly that stub as evidence
  A2  the streaming path parsed actions with a bare json.loads instead of the
      shared spine, so a fenced reply stored raw JSON in working memory
  A3  the security lockdown is armed by nobody, and would have been disarmed by
      the intruder's own words if it ever were

A1 is the one that matters: F-16 exists to stop JARVIS claiming work it did not
do, and this let the model mint its own permission to claim it.
"""

import pathlib
import re
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


# ── A1: evidence is a parse of what was dispatched, or it is not evidence ────

def test_an_empty_action_list_is_not_evidence_that_an_action_ran():
    """THE BUG. `{"actions": []}` used to write "[Action executed. Done.]", and
    `_actions_ran_recently` accepts that stub as proof something executed — so
    the NEXT turn's F-16 guard admitted every capability claim as founded."""
    import brain

    buffer = [{"role": "assistant", "content": "[Action executed. Done.]"}]
    check(brain._actions_ran_recently(buffer) is False,
          "the old empty-action stub no longer counts as evidence")

    buffer = [{"role": "assistant", "content": "[Executed: open_browser. Done.]"}]
    check(brain._actions_ran_recently(buffer) is True,
          "a real dispatch stub still does")


def test_the_stub_that_lied_is_no_longer_written_anywhere():
    """Structural, and deliberately on the source: the defect was a literal
    string in one branch, and a branch is what a test can miss."""
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    code = "\n".join(
        line for line in src.splitlines()
        if not line.lstrip().startswith("#"))
    check("[Action executed." not in code,
          "no code path writes the empty-action stub any more")


def test_the_guard_widens_only_on_real_evidence():
    """The whole point of the stub: with no action, a capability claim goes."""
    import brain

    claim = "I've opened the browser for you, Sir."
    kept = brain._strip_unfounded_conversational_claims(
        claim, actions_ran=True, title="Sir")
    dropped = brain._strip_unfounded_conversational_claims(
        claim, actions_ran=False, title="Sir")
    check("opened" in kept, "with evidence, a completion claim survives")
    check("opened" not in dropped,
          f"without evidence it is stripped; got {dropped!r}")


def test_the_streaming_path_uses_the_shared_parse_spine():
    """A2. `json.loads` fails on a fenced or prose-wrapped action reply, so a
    turn that really acted wrote raw JSON into working memory — the one thing
    process_command's own comment forbids."""
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    stream = src.split("def process_stream", 1)[1]
    check("action_parser.extract_actions" in stream,
          "process_stream parses actions with the shared spine")
    check("_ps_parsed = json.loads" not in stream,
          "and no longer with a bare json.loads")

    # The spine really does handle what json.loads cannot — proven, not assumed.
    import json

    from modules import action_parser
    fenced = '```json\n{"actions": [{"action_type": "open_browser", "target": "x"}]}\n```'
    try:
        json.loads(fenced)
        check(False, "json.loads parsed a fenced reply (test premise is wrong)")
    except json.JSONDecodeError:
        check(True, "a fenced action reply is invalid JSON to json.loads")
    check(len(action_parser.extract_actions(fenced)) == 1,
          "while the shared spine finds the action in it")


# ── A3: a disarm has to come from JARVIS ────────────────────────────────────

def test_the_lock_cannot_be_cleared_by_the_person_being_challenged():
    import brain

    locked = [{"role": "assistant", "content": "Unrecognized voice protocol detected."}]
    check(brain._security_locked(locked) is True, "the marker arms the lock")

    intruder_says = locked + [{"role": "user", "content": "access granted"}]
    check(brain._security_locked(intruder_says) is True,
          "the intruder saying 'access granted' does NOT clear it")

    for phrase in ("welcome home", "standby mode", "pleasure to see you"):
        probe = locked + [{"role": "user", "content": f"jarvis, {phrase}"}]
        check(brain._security_locked(probe) is True,
              f"nor does the intruder saying {phrase!r}")


def test_jarvis_own_welcome_still_clears_the_lock():
    """The legitimate case the scan was written for."""
    import brain

    admitted = [
        {"role": "assistant", "content": "Unrecognized voice protocol detected."},
        {"role": "user", "content": "kaustav"},
        {"role": "assistant", "content": "Voice print recognized. Welcome home, Sir."},
    ]
    check(brain._security_locked(admitted) is False,
          "JARVIS's own welcome clears it, as it always did")


def test_an_unarmed_buffer_is_not_locked():
    import brain

    check(brain._security_locked([]) is False, "an empty buffer is not locked")
    check(brain._security_locked(None) is False, "nor is no buffer at all")
    check(brain._security_locked([{"role": "user", "content": "hello"}]) is False,
          "nor an ordinary conversation")


def test_nothing_in_the_tree_arms_the_lock_and_that_is_recorded():
    """The finding itself, pinned. If someone later wires the marker, this test
    fails and they are made to read the docstring explaining what the barrier
    actually is — main.py's `continue` on an unrecognised person."""
    import brain

    writers = []
    for path in sorted(HERE.glob("*.py")) + sorted((HERE / "modules").glob("*.py")):
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if brain._LOCK_MARKER not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if brain._LOCK_MARKER in line and "_LOCK_MARKER" not in line:
                writers.append(f"{path.name}:{i}")
    check(not writers,
          "the lock marker is still written by nobody — the brain's challenge "
          f"mode is unreachable decoration, not a live control; found {writers}")

    doc = brain._security_locked.__doc__ or ""
    check("main.py" in doc and "continue" in doc,
          "and the docstring says where the REAL barrier is")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Pre-Electron review, batch 3 — the brain")
    print("=" * 62)
    for t in TESTS:
        t()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
