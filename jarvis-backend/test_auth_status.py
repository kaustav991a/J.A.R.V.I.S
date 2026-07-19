r"""
test_auth_status.py — G6.1 face-auth status contract (no I/O)

Run: venv\Scripts\python.exe test_auth_status.py

Asserts the additive frame shapes the FaceAuthOverlay consumes: each stage maps
to status "auth_face_<stage>", user rides only on success, reason only on fail,
and an unknown stage is rejected (so a typo can't ship a silent dead frame).
"""

from modules import auth_status as a

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def test_start_and_scanning_are_minimal():
    check(a.face_frame("start") == {"status": "auth_face_start"}, "start frame is minimal")
    check(a.face_frame("scanning") == {"status": "auth_face_scanning"}, "scanning frame is minimal")


def test_success_carries_user_only():
    f = a.face_frame("success", user="KAUSTAV")
    check(f == {"status": "auth_face_success", "user": "KAUSTAV"}, "success carries user")
    check("reason" not in f, "success has no reason")


def test_fail_carries_reason_only():
    f = a.face_frame("fail", reason="no_match")
    check(f == {"status": "auth_face_fail", "reason": "no_match"}, "fail carries reason")
    check("user" not in f, "fail has no user")


def test_none_fields_omitted():
    check(a.face_frame("scanning", user=None, reason=None) == {"status": "auth_face_scanning"},
          "None user/reason are omitted, frame stays minimal")


def test_unknown_stage_rejected():
    raised = False
    try:
        a.face_frame("matchng")   # typo
    except ValueError:
        raised = True
    check(raised, "an unknown stage raises rather than shipping a dead frame")


def test_all_valid_stages_build():
    ok = True
    for s in a.VALID_STAGES:
        try:
            fr = a.face_frame(s)
            ok = ok and fr["status"] == f"auth_face_{s}"
        except Exception:  # noqa: BLE001
            ok = False
    check(ok, "every VALID_STAGES entry builds a well-formed frame")


TESTS = [test_start_and_scanning_are_minimal, test_success_carries_user_only,
         test_fail_carries_reason_only, test_none_fields_omitted,
         test_unknown_stage_rejected, test_all_valid_stages_build]


def main():
    print("=" * 60)
    print("auth_status contract harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
