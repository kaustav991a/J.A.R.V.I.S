"""Phase G5.2 — harness for the calibration-wizard derivation helpers.

Pure math, no camera: reuses the synthetic 21-landmark builder from the gesture
harness. Covers palm_sign auto-detection, pinch-threshold derivation, reach ->
sensitivity/gain, and the end-to-end derive_calibration fold. The live capture
loop (calibrate_gesture.main) is hardware and is live-gated, not tested here.
"""

from calibrate_gesture import (
    derive_calibration,
    derive_gain_from_reach,
    derive_pinch_thresholds,
    derive_sensitivity_from_reach,
    detect_palm_sign,
    hand_size,
    palm_centroid,
    thumb_index_norm,
)
from modules.gesture_calibration import SCHEMA, _coerce
from modules.gesture_engine import GestureConfig, GestureEngine
from test_gesture_engine import PALM, PALM_BACK, make_hand, palm_at, pinched


# ---- palm_sign auto-detection ------------------------------------------ #

def test_palm_sign_facing_matches_engine_default():
    # an open palm facing the camera must yield the engine's default sign (1),
    # so _palm_facing() reads True for it.
    assert detect_palm_sign(PALM, "Right") == 1


def test_palm_sign_flips_for_back_of_hand():
    assert detect_palm_sign(PALM_BACK, "Right") == -1


def test_palm_sign_left_hand_inverts():
    # same physical open palm, left-handed convention -> opposite sign.
    assert detect_palm_sign(PALM, "Left") == -detect_palm_sign(PALM, "Right")


def test_detected_sign_makes_engine_face_true():
    sign = detect_palm_sign(PALM, "Right")
    e = GestureEngine(GestureConfig(palm_sign=sign))
    assert e._palm_facing([(p[0], p[1]) for p in PALM], "Right") is True


# ---- pinch thresholds -------------------------------------------------- #

def test_pinch_thresholds_ordered_with_hysteresis():
    stream = [0.2] * 20 + [1.2] * 20 + [0.6] * 10   # pinched + open + mid
    down, up = derive_pinch_thresholds(stream)
    assert down is not None and up is not None
    assert 0.2 <= down < up <= 1.4
    assert up - down >= 0.1                          # real hysteresis gap


def test_pinch_thresholds_reject_no_separation():
    # hand never actually pinched (all "open") -> not trustworthy -> keep defaults
    assert derive_pinch_thresholds([1.1, 1.15, 1.2, 1.18, 1.12]) == (None, None)


def test_pinch_thresholds_reject_too_few_samples():
    assert derive_pinch_thresholds([0.2, 1.2]) == (None, None)


def test_derived_thresholds_actually_click():
    # feed a real synthetic open<->pinch stream, derive, then prove the engine
    # with those thresholds fires exactly one click on a pinch tap.
    stream = [thumb_index_norm([(p[0], p[1], 0.0) for p in PALM])] * 30 \
        + [thumb_index_norm([(p[0], p[1], 0.0) for p in pinched()])] * 30
    down, up = derive_pinch_thresholds(stream)
    assert down is not None
    cfg = GestureConfig(pinch_down=down, pinch_up=up)
    e = GestureEngine(cfg)
    t = 0.0
    for _ in range(35):                      # engage
        e.process(INDEX_UP_LM, t); t += 1 / 30
    out = []
    for _ in range(4):
        out += e.process(PINCH_LM, t); t += 1 / 30
    for _ in range(8):
        out += e.process(PALM_LM, t); t += 1 / 30
    assert [i for i in out if i[0] == "click"], "derived thresholds must click"


INDEX_UP_LM = make_hand(ext=("index",))
PINCH_LM = pinched()
PALM_LM = PALM


# ---- reach -> sensitivity / gain --------------------------------------- #

def test_sensitivity_inverse_to_reach_and_clamped():
    assert derive_sensitivity_from_reach(0.35) > derive_sensitivity_from_reach(0.7)
    assert derive_sensitivity_from_reach(10.0) == 0.5     # clamp low
    assert derive_sensitivity_from_reach(0.0001) == 4.0   # clamp high


def test_gain_inverse_to_reach_and_clamped():
    assert derive_gain_from_reach(0.3) > derive_gain_from_reach(0.9)
    assert derive_gain_from_reach(100.0) == 0.2           # clamp low
    assert derive_gain_from_reach(0.0001) == 5.0          # clamp high


# ---- end-to-end fold --------------------------------------------------- #

def _reach_track():
    return [palm_centroid([(p[0], p[1]) for p in palm_at(0.3 * i)]) for i in range(4)]


def test_derive_calibration_absolute():
    cal = derive_calibration(
        [PALM] * 5,
        [0.2] * 20 + [1.2] * 20,
        _reach_track(),
        relative=False)
    assert cal["palm_sign"] == 1
    assert cal["pinch_down"] < cal["pinch_up"]
    assert cal["mapping_mode"] == "absolute" and "sensitivity" in cal
    assert cal["require_palm_facing"] is True


def test_derive_calibration_relative():
    cal = derive_calibration(
        [PALM] * 5, [0.2] * 20 + [1.2] * 20, _reach_track(), relative=True)
    assert cal["mapping_mode"] == "relative" and "base_gain" in cal
    assert "sensitivity" not in cal


def test_derive_calibration_roundtrips_through_schema():
    # everything the wizard emits must survive gesture_calibration's coercers,
    # or a save would silently drop it.
    cal = derive_calibration([PALM] * 5, [0.2] * 20 + [1.2] * 20,
                             _reach_track(), relative=True, mirror=True)
    for k, v in cal.items():
        assert k in SCHEMA, f"{k} not persistable"
        assert _coerce(k, v) is not None, f"{k}={v!r} won't coerce"


def test_hand_size_positive():
    assert hand_size([(p[0], p[1]) for p in PALM]) > 0.0


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
