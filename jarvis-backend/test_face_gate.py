"""G3 — harness for the pure-logic parts of the face gate (no camera, no cv2
model load): absence timing with a fake clock, cosine matching, and the
fast-path gesture-toggle regexes."""

from modules.face_gate import AbsenceTracker, cosine_best


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
