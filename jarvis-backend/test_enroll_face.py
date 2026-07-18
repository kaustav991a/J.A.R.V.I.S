r"""
test_enroll_face.py — G4 enrol-UX pure-helper harness (no camera, no cv2)

Run: venv\Scripts\python.exe test_enroll_face.py

Covers the quality/diversity/report logic that gates what lands in
owner_embeddings.npz. The cv2 capture loop itself is exercised live on the
camera; these are the decision helpers that decide accept/reject/warn.
"""
from enroll_face import enroll_report, face_box_ok, too_similar

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def row(x, y, w, h, score):
    """A YuNet-shaped detection: [x, y, w, h, 10 landmark vals, score]."""
    return [x, y, w, h] + [0.0] * 10 + [score]


# --- face_box_ok ----------------------------------------------------------- #
def test_face_box_good():
    ok, reason = face_box_ok(row(270, 190, 100, 120, 0.95), 640, 480)
    check(ok, "well-framed, confident face accepted")
    check(reason == "ok", "reason ok")


def test_face_box_low_score():
    ok, reason = face_box_ok(row(270, 190, 100, 120, 0.50), 640, 480)
    check(not ok and reason == "HOLD STILL", "low detector confidence rejected")


def test_face_box_too_far():
    ok, reason = face_box_ok(row(300, 220, 40, 45, 0.95), 640, 480)
    check(not ok and reason == "MOVE CLOSER", "tiny (far) face rejected")


def test_face_box_clipped():
    ok, reason = face_box_ok(row(-5, 190, 120, 120, 0.95), 640, 480)
    check(not ok and reason == "CENTER YOUR FACE", "edge-clipped face rejected")


def test_face_box_score_checked_first():
    # low score AND too far -> score reason wins (checked first)
    ok, reason = face_box_ok(row(300, 220, 40, 45, 0.40), 640, 480)
    check(not ok and reason == "HOLD STILL", "score gate precedes size gate")


# --- too_similar ----------------------------------------------------------- #
def test_similar_empty_accepted():
    check(too_similar([1, 0, 0, 0], []) is False, "nothing kept yet -> not too similar")


def test_similar_identical():
    check(too_similar([1, 0, 0, 0], [[1, 0, 0, 0]]) is True, "identical embedding rejected")


def test_similar_orthogonal():
    check(too_similar([1, 0, 0, 0], [[0, 1, 0, 0]]) is False, "orthogonal embedding accepted")


def test_similar_new_angle_passes():
    # cos([1,1,0,0],[1,0,1,0]) = 0.5 -> a genuinely different pose
    check(too_similar([1, 1, 0, 0], [[1, 0, 1, 0]]) is False, "different pose accepted")


def test_similar_near_dup_rejected():
    # nearly the same vector -> cos ~0.9995 -> too similar
    check(too_similar([1.0, 0.02, 0, 0], [[1.0, 0.0, 0, 0]]) is True, "near-duplicate rejected")


# --- enroll_report --------------------------------------------------------- #
def test_report_diverse_clean():
    feats = [[1, 1, 0, 0], [1, 0, 1, 0], [0, 1, 1, 0]]  # pairwise cos = 0.5
    r = enroll_report(feats)
    check(r["n"] == 3, "report counts samples")
    check(abs(r["min"] - 0.5) < 1e-6, "min pairwise computed")
    check(r["warnings"] == [], "diverse same-person set has no warnings")


def test_report_outlier():
    feats = [[1, 0, 0, 0], [0.98, 0.02, 0, 0], [0, 0, 1, 0]]  # v3 orthogonal -> min ~0
    r = enroll_report(feats)
    check(any("outlier" in w for w in r["warnings"]), "orthogonal outlier flagged")


def test_report_no_diversity():
    feats = [[1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]]  # all identical -> min 1.0
    r = enroll_report(feats)
    check(any("identical" in w for w in r["warnings"]), "zero-diversity set flagged")


def test_report_empty_pairs():
    r = enroll_report([[1, 0, 0, 0]])  # 1 sample -> no pairs
    check(r["warnings"] == [] and r["n"] == 1, "single-sample report is warning-free")


TESTS = [
    test_face_box_good,
    test_face_box_low_score,
    test_face_box_too_far,
    test_face_box_clipped,
    test_face_box_score_checked_first,
    test_similar_empty_accepted,
    test_similar_identical,
    test_similar_orthogonal,
    test_similar_new_angle_passes,
    test_similar_near_dup_rejected,
    test_report_diverse_clean,
    test_report_outlier,
    test_report_no_diversity,
    test_report_empty_pairs,
]


def main():
    print("=" * 60)
    print("enroll_face pure-helper harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
