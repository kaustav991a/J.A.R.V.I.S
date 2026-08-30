"""Harness: the soft lock must always have a way out, and JARVIS must know it.

WHY THIS EXISTS
---------------
2026-08-30. He was away from home. The desk soft-locked, and he could not get it
back — he ended up powering the machine off at the case. Four separate defects
lined up, and each one alone would have been survivable.

**1. Motion armed a lock only a FACE can clear.** His phone-camera URLs were
unreachable (the phone was with him), so the daemon fell back to the built-in
webcam pointed at an empty room:

    [GESTURE] camera auto-select: chose 0 from ['http://10.171.25.26:8080/video',
              ..., 0]

No face was ever seen. But `AbsenceTracker.update` treated `moving` as presence,
and a frame-difference over a real sensor picks up light changes and noise. That
armed the tracker; stillness ran out the timer; the desk locked. The lock's exits
are *be recognised by the camera* or *type the code at the keyboard* — both need
a person there. **Never enter a state whose exit has never been demonstrated.**

**2. There was no unlock action at all.** Over the Telegram bridge he wrote
*"Turn off the soft lock. I'm not at the home .."*, and the log shows what
happened:

    [ACTION ENGINE] Processing payload:
        {'action_type': 'os_control', 'target': 'lock_screen'}

It locked the screen again. The daemon could always do this — `set_auto_lock(False)`
unlocks if locked — but nothing exposed it, so the model reached for the nearest
action in its vocabulary and got the exact opposite. **A capability the assistant
cannot reach is not a capability**, and a model with no right action will pick a
wrong one rather than say it has none.

**3. The admin override did not work on the lock screen.** He typed
`JARVIS_ADMIN_OVERRIDE_CODE` at the overlay. It compared only against
`JARVIS_UNLOCK_CODE`, and swallows every other key, so there was not even
feedback. An override that fails on the one screen you cannot walk away from is
not an override.

**4. He asked JARVIS to read the code out of `.env`** — that needs
`run_terminal_command`, permanently BLOCKED by governance. Correct, and it closed
the last door.

WHAT THIS PINS
--------------
Every one of the four, because they were independent and any one of them would
have let him back in.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_checks = 0
_fails: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print(f"FAIL  {label}")


# ── 1 · motion alone must not arm the lock ─────────────────────────────────

def test_motion_alone_never_arms_the_lock():
    from modules.face_gate import AbsenceTracker

    t = AbsenceTracker(absent_after_s=10.0)
    # an empty room with a live sensor: motion flickers, no face ever
    check(t.update(False, False, True, 0.0) is False, "motion is not absence")
    for n in range(1, 60):
        got = t.update(False, False, False, float(n))
        if got:
            break
    check(not t.update(False, False, False, 1000.0),
          "a lock is NEVER armed by motion alone — no face was ever seen, so "
          "nothing could clear the lock it would have created")


def test_a_face_still_arms_it_normally():
    """The feature must still work. He walks up, is seen, leaves."""
    from modules.face_gate import AbsenceTracker

    t = AbsenceTracker(absent_after_s=10.0)
    check(t.update(True, True, True, 0.0) is False, "owner present is not absent")
    check(t.update(False, False, False, 5.0) is False, "5s < 10s is not yet away")
    check(t.update(False, False, False, 11.0) is True,
          "and once he HAS been seen, leaving still locks the desk")


def test_motion_after_a_face_still_counts_as_presence():
    """The reason motion is there at all: a turned head loses the face while
    typing continues. That must keep the session alive."""
    from modules.face_gate import AbsenceTracker

    t = AbsenceTracker(absent_after_s=10.0)
    t.update(True, True, False, 0.0)             # seen once
    for n in range(1, 40):                        # then only motion
        check_at = t.update(False, False, True, float(n))
        if check_at:
            break
    check(not t.update(False, False, True, 100.0),
          "motion keeps a session alive once a face has been seen")


# ── 2 · an unlock action exists, is governed, and is ADVERTISED ─────────────

def test_the_unlock_action_exists():
    from action_engine import ActionEngine
    check(hasattr(ActionEngine, "_unlock_desk"),
          "there is an action that can clear the soft lock")


def test_governance_knows_it():
    from governance_manager import GovernanceManager
    g = GovernanceManager()
    check(g.is_known("unlock_desk"),
          "governance knows unlock_desk — an unknown action fails safe to BLOCK, "
          "which would have left him locked out just as effectively")
    ruleset = json.loads((HERE / "governance.json").read_text(encoding="utf-8"))
    tier = ruleset.get("rules", {}).get("unlock_desk")
    check(tier == "AUTO",
          f"and it is AUTO, not CONFIRM — a confirmation prompt he cannot see is "
          f"another closed door (got {tier!r})")


def test_the_model_is_told_the_action_exists():
    """The last mile, and the one that actually caused this. The handler existing
    changes nothing if the action list the brain reads does not mention it."""
    src = (HERE / "brain.py").read_text(encoding="utf-8")
    check(src.count('"unlock_desk"') >= 2,
          f"unlock_desk is in the action list(s) the model reads "
          f"({src.count(chr(34) + 'unlock_desk' + chr(34))} mentions)")
    check("unlock/soft lock off" in src,
          "and the routing hints send an unlock request to it")
    check("NEVER answer an unlock request with os_control lock_screen" in src,
          "and the wrong answer it actually gave is named, because that is the "
          "one the model already chose once")


# ── 3 · the admin override opens the lock screen ───────────────────────────

def test_the_lock_overlay_accepts_the_admin_override():
    src = (HERE / "lock_overlay.py").read_text(encoding="utf-8")
    check("JARVIS_ADMIN_OVERRIDE_CODE" in src,
          "the overlay accepts the admin override code")
    check("any(tail.endswith(c) for c in accepted)" in src,
          "...and checks BOTH codes, not just the first one set")
    check("if not accepted:" in src,
          "the swallow-all-keys guard follows the accepted list, so setting only "
          "the admin code still gives a way in")


def test_the_overlay_still_names_its_exits():
    """F-25's lesson, which this must not undo: every exit that exists is on the
    screen. The person standing there cannot grep the source."""
    src = (HERE / "lock_overlay.py").read_text(encoding="utf-8")
    check("auto lock off" in src, "the spoken exit is still offered")
    check("admin override code works here too" in src,
          "and the newly-accepted code is ANNOUNCED — an exit he does not know "
          "about is one he does not have")


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
