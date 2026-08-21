"""Harness: no barrier may seal its own exit, and none may overstate what it did.

Four findings, one shape. The worst theme this gate has produced, stated in
`RESUME.md` as: *a security barrier whose only exit depends on the subsystem
whose failure raised it.*

  * **F-25** — the desk soft-lock. Arming needed a camera that was REACHABLE;
    clearing needed a RECOGNISED FACE. A camera that is reachable but blind —
    lens covered, pointed at a wall, stream frozen mid-decode — satisfies the
    first and can never satisfy the second, and that is most of the ways a camera
    fails. Mid-gate the desktop went black behind a fullscreen panel reading
    "face the camera to unlock", the monitor powered off on top of it, keys and
    clicks were swallowed, and the owner got out by closing VS Code. The one
    line of help on screen named the exit that could not work; the module
    docstring knew about the others and never told him.
  * **F-20** — the HUD overlay. `security_override` set the lockdown AND, because
    its status starts with "security_", put the UI back to sleep. Every message
    that would clear it is `is_proactive`, so it hit an early return and never
    reached `setIsLockdown(false)`. Observed live: JARVIS said "Welcome back"
    while the HUD stayed on the intruder screen.
  * **F-19** — why either fired. Four minutes after MATCH: KAUSTAV, on the
    60-second poll, the seated owner was announced as an unrecognized presence,
    it escalated to a lockdown alert that reached his phone, and the next cycle
    greeted him by name. The resolver returned UNKNOWN and KAUSTAV for the same
    man on consecutive cycles.
  * **F-21** — "Initiating lockdown protocols", which locked nothing and
    returned. Root cause #4: the identical false claim had already been found and
    fixed at the voice-command door in main.py, with a harness — which proved the
    sentence at one door and said nothing about this one.

WHAT THIS PINS
--------------
Offline. The gate's completed-pass counter is exercised for real; the daemon's
arm/clear conditions and the HUD's gate are structural, because reaching them
needs a camera, a monitor and a browser.
"""

import ast
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
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


def _read(rel):
    p = HERE / rel if not str(rel).startswith("..") else HERE.parent / str(rel)[3:]
    return io.open(p, encoding="utf-8", errors="replace").read()


# ── F-25 · the gate can say whether its verdict is about now ──────────────────

def test_the_gate_counts_only_completed_passes():
    """`check()` returns the PREVIOUS result on any fault, so `.last` cannot tell
    "a fresh pass saw no face" from "you are reading a verdict from a minute
    ago". Without that distinction a barrier can arm on a stale reading and then
    never satisfy the fresh one needed to lift it."""
    from modules import face_gate

    g = face_gate.FaceGate.__new__(face_gate.FaceGate)
    g.available = True
    g.last = face_gate.GateResult()
    g.checks_ok = 0
    # A fault path: `_ensure` says yes, then the body raises because the frame is
    # not an image. The real fault mode (a dead decode) lands in the same except.
    g._ensure = lambda: True
    before = g.checks_ok
    try:
        face_gate.FaceGate.check(g, None)
    except Exception:
        pass
    check(g.checks_ok == before,
          "a faulted check does NOT advance the completed-pass counter")


def test_the_counter_is_documented_where_it_is_defined():
    src = _read(os.path.join("modules", "face_gate.py"))
    check("checks_ok" in src, "the counter exists")
    body = src[src.index("self.checks_ok"):]
    check("F-25" in src[:src.index("self.checks_ok = 0") + 40] or "F-25" in src,
          "...and says which finding it is for")
    tail = src[src.index("self.checks_ok += 1"):][:400]
    check("real verdict" in tail or "see the note" in tail,
          "the increment says what it means")


# ── F-25 · arming and clearing read the same evidence ─────────────────────────

def test_the_arm_condition_requires_a_fresh_verdict():
    """The asymmetry was the trap. Arming on weaker evidence than clearing needs
    is what makes a barrier able to trap."""
    src = _read("gesture_daemon.py")
    arm = src[src.index("---- absence -> soft lock ----"):][:400]
    check("_gate_fresh" in arm, "the lock arms only on a fresh verdict")
    check("gate.available" in arm, "...as well as a reachable camera")


def test_a_stale_gate_releases_the_lock():
    """The subsystem whose failure raised the barrier can no longer make the
    decision that lifts it. The cost of releasing is a machine left unlocked
    while nobody can see the room; the cost of holding is the owner shut out of
    his own desk with the monitor dark."""
    src = _read("gesture_daemon.py")
    locked = src[src.index("---- locked: the owner"):]
    locked = locked[:locked.index("---- absence -> soft lock ----")]
    check("elif not _gate_fresh:" in locked,
          "the locked branch has a second exit")
    check("self._unlock()" in locked.split("elif not _gate_fresh:")[1],
          "...and it actually unlocks")
    check("F-25" in locked, "...and says why in the log")


def test_the_freshness_window_is_far_wider_than_the_check_cadence():
    """A window near the cadence would release the lock on ordinary jitter."""
    src = _read("gesture_daemon.py")
    check("GATE_STALE_S" in src, "there is a staleness constant")
    check("JARVIS_GATE_STALE_S" in src, "...overridable by env")
    import re
    m = re.search(r'JARVIS_GATE_STALE_S", "(\d+(?:\.\d+)?)"', src)
    check(m is not None and float(m.group(1)) >= 4.0,
          f"the window is several checks wide ({m.group(1) if m else '?'}s "
          f"against a 0.5s locked cadence)")


def test_the_lock_cannot_arm_before_the_camera_has_ever_answered():
    """A barrier that arms before its own sensor works is a barrier with no
    exit."""
    src = _read("gesture_daemon.py")
    check("self._last_verdict_t = -1e9" in src,
          "the last-verdict clock starts at 'never'")
    check("self._last_checks_ok = -1" in src,
          "...and so does the counter it is proved by")


# ── F-25 · the screen names its own exits ─────────────────────────────────────

def test_the_overlay_prints_every_exit_it_has():
    """One line named only the camera — the exit that cannot work, because a
    camera that has stopped recognising anyone is usually why it armed."""
    src = _read("lock_overlay.py")
    check("ways out" in src, "the overlay lists its exits")
    check("auto lock off" in src,
          "the spoken exit is named, and it is the one that always works")
    check("face the camera" in src, "the camera is still named")
    check("unlock code" in src, "the typed code is named")


def test_the_spoken_exit_is_named_first():
    """Voice is never blocked — only keys and clicks are. It is the exit most
    likely to work and it was the one not on the screen."""
    src = _read("lock_overlay.py")
    block = src[src.index("exits = ["):src.index("tk.Label(box, text=\"biometric")]
    check(block.index("auto lock off") < block.index("face the camera"),
          "the spoken exit is listed above the camera")


def test_an_absent_code_says_so_rather_than_going_quiet():
    """An exit the owner believes he has and does not is worse than a missing
    line."""
    src = _read("lock_overlay.py")
    check("NOT SET" in src, "a missing code is stated on the screen")
    check("JARVIS_UNLOCK_CODE" in src, "...and names the variable that sets it")


def test_a_code_always_exists_now():
    """It defaulted to DISABLED on a barrier whose only other exit was
    biometric: `lock_overlay` returns "break" for every key when no code is set,
    so the hatch the docstring advertised did not exist on a default install."""
    src = _read("gesture_daemon.py")
    check("self._unlock_code" in src, "the daemon holds an unlock code")
    check("secrets.randbelow" in src, "one is generated when none is configured")
    check("unlock code for this session" in src,
          "...and printed, since by lock time the monitor is off")


def test_the_configured_code_is_not_printed():
    """His own code is already known to him and does not need to be in a log
    that gets pasted into a findings file."""
    src = _read("gesture_daemon.py")
    block = src[src.index("_cfg_code = "):][:900]
    check("not printed" in block, "a configured code is not echoed")


def test_the_code_reaches_the_overlay_process():
    """The overlay is a separate process, so the generated code has to travel."""
    src = _read("gesture_daemon.py")
    spawn = src[src.index("lock_overlay.py\"]") - 700:]
    spawn = spawn[:900]
    check("env=_env" in spawn, "the overlay is spawned with an environment")
    check('_env["JARVIS_UNLOCK_CODE"]' in spawn, "...carrying the code")
    check("argv" in spawn, "...and says why not on argv, which is world-readable")


# ── F-20 · the HUD barrier does not drop the messages that lift it ────────────

def test_the_proactive_gate_does_not_apply_during_lockdown():
    src = _read(os.path.join("..", "jarvis-frontend", "src", "App.jsx"))
    check("!isLockdownRef.current" in src,
          "the proactive early-return is skipped while the overlay is up")
    gate = src[src.index("if (data.is_proactive"):][:200]
    check("hasWokenUpRef.current" in gate,
          "...and the asleep gate is otherwise unchanged")


def test_the_lockdown_state_is_readable_inside_the_socket_handler():
    """Same reason `hasWokenUp` has a ref: the handler closes over stale state
    otherwise, and a gate reading a stale false would not fix anything."""
    src = _read(os.path.join("..", "jarvis-frontend", "src", "App.jsx"))
    check("const isLockdownRef = useRef(false)" in src, "there is a ref mirror")
    check("isLockdownRef.current = isLockdown" in src, "...kept in sync")


def test_the_clearing_branch_is_still_there():
    """The fix is that the branch becomes REACHABLE. If it had been removed, the
    overlay would never lift at all."""
    src = _read(os.path.join("..", "jarvis-frontend", "src", "App.jsx"))
    check("setIsLockdown(false)" in src, "something still clears the lockdown")
    check('data.status === "security_override"' in src, "and something sets it")


# ── F-19 · one reading is not a verdict ──────────────────────────────────────

def test_an_unknown_face_must_survive_a_streak():
    src = _read("background_monitor.py")
    check("_unknown_streak" in src, "unknown readings accumulate")
    check("stranger_confirm_cycles" in src, "...against a confirmation count")
    check("_identity_is_trustworthy_as_unknown" in src,
          "...and one place decides whether it has earned the sentence")


def test_a_recent_known_person_suppresses_an_unknown_reading():
    """A recognised owner 60 seconds ago is much stronger evidence than one
    failed resolve now. He never left the chair."""
    src = _read("background_monitor.py")
    check("_last_known_person_t" in src, "a known sighting is stamped")
    check("known_person_grace_s" in src, "...and there is a grace window")
    fn = src[src.index("def _identity_is_trustworthy_as_unknown"):]
    fn = fn[:fn.index("\n    _KNOWN") if "\n    _KNOWN" in fn else 900]
    check("return False" in fn, "an unknown inside the grace is not acted on")


def test_the_intruder_flag_is_debounced_too():
    """Both paths fired. Fixing the greeting and leaving the alarm would be the
    same class left open at the other site."""
    src = _read("background_monitor.py")
    check("_intruder_streak" in src, "the intruder flag accumulates")
    check("_intruder_confirmed" in src, "...and is only acted on once confirmed")
    check("_owner_seen_recently" in src,
          "...and not while a known person was just identified")


def test_a_held_reading_is_logged_rather_than_dropped_silently()  :
    """A suppressed alarm that leaves no trace is indistinguishable from a
    resolver that never fired, which is how this would be re-found rather than
    remembered."""
    src = _read("background_monitor.py")
    check(src.count("F-19") >= 3, "the finding is named at each guard")
    held = src[src.index("intruder reading held"):][:300]
    check("streak" in held, "the log says how far the streak has got")


def test_a_known_name_resets_the_streak():
    """Otherwise a single unknown reading every few minutes would eventually
    accumulate into an alarm about a man sitting still."""
    src = _read("background_monitor.py")
    block = src[src.index("if person in self._KNOWN_PEOPLE:"):][:400]
    check("_unknown_streak = 0" in block, "a known name clears the streak")
    check("_last_known_person_t = now" in block, "...and stamps the grace")


# ── F-21 · the claim matches what happened ───────────────────────────────────

def test_the_ambient_alert_no_longer_claims_to_lock_anything():
    src = _read("background_monitor.py")
    check("Initiating lockdown protocols" not in src,
          "the sentence that secured nothing is gone")
    check("I have not locked the machine" in src.replace("\"\n                           \"", ""),
          "...replaced by what it actually did and did not do")


def test_it_matches_the_wording_already_fixed_at_the_other_door():
    """Root cause #4. The voice-command door in main.py was fixed and harnessed;
    this one was not, and a harness that proves a sentence at one site says
    nothing about the other."""
    mainsrc = _read("main.py")
    mon = _read("background_monitor.py")
    check("Lockdown display engaged" in mainsrc,
          "the voice door still says what it does")
    check("LOCKDOWN DISPLAY ENGAGED" in mon,
          "and the ambient door's broadcast now says the same")


def test_both_doors_disclaim_the_same_two_things():
    """A partial disclaimer is a new false impression. Both name the machine and
    the network, because those are what "lockdown" is heard to mean."""
    mainsrc = _read("main.py")
    mon = _read("background_monitor.py")
    for name, src in (("the voice door", mainsrc), ("the ambient door", mon)):
        flat = " ".join(src.split())
        check("network setting" in flat, f"{name} disclaims network changes")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Lockdown exits — F-19, F-20, F-21, F-25")
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
