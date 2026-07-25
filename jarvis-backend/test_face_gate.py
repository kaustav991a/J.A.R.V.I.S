"""G3 — harness for the pure-logic parts of the face gate (no camera, no cv2
model load): absence timing with a fake clock, cosine matching, stranger
confirmation, and the fast-path gesture-toggle regexes."""

from modules.face_gate import AbsenceTracker, StrangerConfirmer, cosine_best


def test_absence_needs_no_face_AND_no_motion():
    a = AbsenceTracker(absent_after_s=6.0)
    assert a.update(True, True, True, 0.0) is False      # owner there
    assert a.update(False, False, True, 3.0) is False    # head turned, typing
    assert a.update(False, False, True, 20.0) is False   # motion keeps presence
    assert a.update(False, False, False, 24.0) is False  # quiet, 4s < 6s
    assert a.update(False, False, False, 26.1) is True   # 6.1s quiet -> away


def test_absence_face_resets_timer():
    a = AbsenceTracker(absent_after_s=6.0)
    a.update(True, True, False, 0.0)
    assert a.update(False, False, False, 5.9) is False
    assert a.update(False, True, False, 6.5) is False    # any face resets
    assert a.update(False, False, False, 12.4) is False
    assert a.update(False, False, False, 12.6) is True


def test_absence_never_saw_anyone_never_locks():
    a = AbsenceTracker(absent_after_s=6.0)
    assert a.update(False, False, False, 100.0) is False
    assert a.update(False, False, False, 200.0) is False


def test_cosine_best_matches_owner():
    owner = [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]]
    assert cosine_best([1.0, 0.0, 0.0], owner) > 0.99          # same face
    assert cosine_best([0.0, 1.0, 0.0], owner) < 0.363         # stranger
    assert cosine_best([1.0, 0.0, 0.0], []) == -1.0            # empty db


# ---- StrangerConfirmer (the owner-flicker fix) ------------------------- #
# Live symptom being killed here: SFace flipped the owner to `stranger` on
# isolated checks (t+7.1/25.1/28.2/40.0 in one 60s run) while he was the only
# person in frame — once the desk locks, each of those was a Telegram snapshot
# of the owner himself.


def test_single_stranger_check_is_not_confirmed():
    s = StrangerConfirmer(needed=3.0)
    assert s.update(True, 0.0) is False          # one off-axis misread
    assert s.confirmed is False


def test_three_clear_stranger_checks_confirm():
    s = StrangerConfirmer(needed=3.0)
    assert s.update(True, 0.0) is False
    assert s.update(True, 0.5) is False
    assert s.update(True, 1.0) is True           # someone really is standing there
    assert s.update(True, 1.5) is True           # stays confirmed


def test_uncertain_checks_need_double_the_evidence():
    """A near-threshold face (probably the owner off-axis) counts half, so a
    2-second head turn at the locked 0.5s cadence still raises nothing."""
    s = StrangerConfirmer(needed=3.0)
    for i in range(5):                           # 5 checks = 2.5 evidence
        assert s.update(True, i * 0.5, uncertain=True) is False
    assert s.update(True, 2.5, uncertain=True) is True   # 6th = 3.0


def test_mixed_evidence_adds_up():
    s = StrangerConfirmer(needed=3.0)
    assert s.update(True, 0.0, uncertain=True) is False  # 0.5
    assert s.update(True, 0.5) is False                  # 1.5
    assert s.update(True, 1.0) is False                  # 2.5
    assert s.update(True, 1.5, uncertain=True) is True   # 3.0


def test_owner_or_empty_check_clears_the_streak():
    s = StrangerConfirmer(needed=3.0)
    s.update(True, 0.0)
    s.update(True, 0.5)
    assert s.update(False, 1.0) is False         # owner matched / nobody in frame
    assert s.evidence == 0.0
    assert s.update(True, 1.5) is False          # counting starts over
    assert s.update(True, 2.0) is False


def test_stale_sightings_do_not_accumulate():
    s = StrangerConfirmer(needed=3.0, window_s=3.0)
    assert s.update(True, 0.0) is False
    assert s.update(True, 60.0) is False         # a minute later = not one walk-up
    assert s.evidence == 1.0
    assert s.update(True, 60.5) is False
    assert s.update(True, 61.0) is True


def test_reset_clears_confirmation():
    s = StrangerConfirmer(needed=2.0)
    s.update(True, 0.0)
    assert s.update(True, 0.5) is True
    s.reset()
    assert s.confirmed is False
    assert s.update(True, 1.0) is False          # needs the full streak again


def test_fast_path_gesture_toggles_match():
    from modules import fast_path
    for phrase in ("hand control on", "jarvis, gesture control off",
                   "gestures on", "auto lock off", "enable autolock"):
        norm = fast_path._normalise(phrase)
        assert any(p.fullmatch(norm) for p, _ in fast_path._RULES), phrase


if __name__ == "__main__":
    import sys
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
