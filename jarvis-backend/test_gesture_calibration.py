r"""
test_gesture_calibration.py — G4 calibration-persistence harness (no hardware)

Run: venv\Scripts\python.exe test_gesture_calibration.py

Covers the JSON that lets live-tuned gesture knobs survive a restart:
load/save round-trip, unknown-key filtering, type coercion, corrupt-file
safety, apply_to/from_config, and the resolution order
GestureConfig defaults < calibration JSON < JARVIS_* env.
"""
import json
import os
import tempfile

from modules import gesture_calibration as gc
from modules.gesture_engine import GestureConfig

_passed = 0
_failed = 0
_ENV_KEYS = ("JARVIS_GESTURE_CALIBRATION", "JARVIS_GESTURE_SENSITIVITY",
             "JARVIS_GESTURE_SMOOTH", "JARVIS_PALM_SIGN", "JARVIS_PALM_FACING")


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _tmp():
    fd, p = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(p)  # start absent
    return p


def _clear_env():
    for k in _ENV_KEYS:
        os.environ.pop(k, None)


# --- load / save ----------------------------------------------------------- #
def test_load_missing():
    check(gc.load(_tmp()) == {}, "missing file loads as {}")


def test_save_load_roundtrip():
    p = _tmp()
    ok = gc.save({"sensitivity": 2.3, "palm_sign": -1, "mirror": True}, p)
    check(ok, "save reports success")
    got = gc.load(p)
    check(got["sensitivity"] == 2.3, "sensitivity round-trips")
    check(got["palm_sign"] == -1, "palm_sign round-trips")
    check(got["mirror"] is True, "mirror round-trips as bool")
    os.unlink(p)


def test_unknown_keys_dropped():
    p = _tmp()
    gc.save({"sensitivity": 1.2, "bogus": 99, "__proto__": "x"}, p)
    got = gc.load(p)
    check("bogus" not in got and "__proto__" not in got, "unknown keys never persisted")
    check(got == {"sensitivity": 1.2}, "only whitelisted keys survive")
    os.unlink(p)


def test_type_coercion():
    p = _tmp()
    # simulate a hand-edited file with stringy values
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"sensitivity": "1.5", "palm_sign": "-1",
                   "require_palm_facing": "false", "mirror": "on"}, fh)
    got = gc.load(p)
    check(got["sensitivity"] == 1.5 and isinstance(got["sensitivity"], float), "str->float")
    check(got["palm_sign"] == -1 and isinstance(got["palm_sign"], int), "str->int")
    check(got["require_palm_facing"] is False, "'false'->bool False")
    check(got["mirror"] is True, "'on'->bool True")
    os.unlink(p)


def test_corrupt_json_safe():
    p = _tmp()
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json ]")
    check(gc.load(p) == {}, "corrupt JSON loads as {} (no raise)")
    os.unlink(p)


def test_non_dict_json_safe():
    p = _tmp()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump([1, 2, 3], fh)
    check(gc.load(p) == {}, "non-dict JSON loads as {}")
    os.unlink(p)


def test_uncoercible_value_skipped():
    p = _tmp()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"sensitivity": "not-a-number", "palm_sign": 1}, fh)
    got = gc.load(p)
    check("sensitivity" not in got, "uncoercible value skipped, not fatal")
    check(got == {"palm_sign": 1}, "other keys still load")
    os.unlink(p)


# --- apply_to / from_config ------------------------------------------------ #
def test_apply_to():
    cfg = GestureConfig()
    gc.apply_to(cfg, {"sensitivity": 2.7, "palm_sign": -1, "mirror": True, "bogus": 1})
    check(cfg.sensitivity == 2.7, "apply_to sets sensitivity")
    check(cfg.palm_sign == -1, "apply_to sets palm_sign")
    check(not hasattr(cfg, "mirror"), "apply_to does not add 'mirror' to config")
    check(not hasattr(cfg, "bogus"), "apply_to ignores unknown key")


def test_from_config():
    cfg = GestureConfig()
    cfg.sensitivity = 3.1
    snap = gc.from_config(cfg, mirror=False)
    check(snap["sensitivity"] == 3.1, "from_config snapshots sensitivity")
    check(snap["mirror"] is False, "from_config records mirror")
    check("start_hold_s" in snap, "from_config includes all persistable knobs")
    # round-trips back onto a fresh config
    cfg2 = GestureConfig()
    gc.apply_to(cfg2, snap)
    check(cfg2.sensitivity == 3.1, "snapshot re-applies onto a fresh config")


# --- from_env resolution order --------------------------------------------- #
def test_from_env_precedence():
    _clear_env()
    p = _tmp()
    os.environ["JARVIS_GESTURE_CALIBRATION"] = p
    try:
        gc.save({"sensitivity": 2.5, "palm_sign": -1, "mirror": True}, p)

        # JSON layer wins over dataclass default
        cfg = GestureConfig.from_env()
        check(cfg.sensitivity == 2.5, "JSON overrides default sensitivity")
        check(cfg.palm_sign == -1, "JSON overrides default palm_sign")

        # env is a hard override on top of JSON
        os.environ["JARVIS_GESTURE_SENSITIVITY"] = "3.3"
        cfg = GestureConfig.from_env()
        check(cfg.sensitivity == 3.3, "env overrides JSON sensitivity")
        check(cfg.palm_sign == -1, "unset env leaves JSON palm_sign intact")

        # unset env -> back to the JSON value (no clobber-with-default)
        del os.environ["JARVIS_GESTURE_SENSITIVITY"]
        cfg = GestureConfig.from_env()
        check(cfg.sensitivity == 2.5, "removing env falls back to JSON, not default")
    finally:
        _clear_env()
        if os.path.exists(p):
            os.unlink(p)


def test_from_env_default_when_no_json_no_env():
    _clear_env()
    os.environ["JARVIS_GESTURE_CALIBRATION"] = _tmp()  # absent file
    try:
        cfg = GestureConfig.from_env()
        default = GestureConfig()
        check(cfg.sensitivity == default.sensitivity, "no JSON/env -> dataclass default sensitivity")
        check(cfg.palm_sign == default.palm_sign, "no JSON/env -> dataclass default palm_sign")
        check(cfg.require_palm_facing == default.require_palm_facing,
              "no JSON/env -> dataclass default palm_facing")
    finally:
        _clear_env()


def test_g62_click_knobs_roundtrip_and_apply():
    # G6.2 added dwell_right_click_s + grab_after_pinch_s to the persistable set;
    # they must round-trip through the JSON and overlay onto a GestureConfig.
    p = _tmp()
    ok = gc.save({"dwell_right_click_s": 0.9, "grab_after_pinch_s": 0.4,
                  "pinch_down": 0.28}, p)
    check(ok, "G6.2 knobs save")
    got = gc.load(p)
    check(got.get("dwell_right_click_s") == 0.9, "dwell_right_click_s round-trips")
    check(got.get("grab_after_pinch_s") == 0.4, "grab_after_pinch_s round-trips")
    cfg = GestureConfig()
    gc.apply_to(cfg, got)
    check(abs(cfg.dwell_right_click_s - 0.9) < 1e-9, "dwell applied to config")
    check(abs(cfg.grab_after_pinch_s - 0.4) < 1e-9, "grab cooldown applied to config")
    check(abs(cfg.pinch_down - 0.28) < 1e-9, "pinch_down applied to config")
    os.unlink(p)


def test_g64_grab_transit_roundtrip_and_apply():
    # G6.4's transit window is the knob that decides whether a closing fist reads
    # as a grab or matures into a right-click, so it has to be tunable per hand
    # and per camera — persistable, not env-only.
    p = _tmp()
    check(gc.save({"grab_transit_s": 0.55}, p), "grab_transit_s saves")
    got = gc.load(p)
    check(got.get("grab_transit_s") == 0.55, "grab_transit_s round-trips")
    cfg = GestureConfig()
    gc.apply_to(cfg, got)
    check(abs(cfg.grab_transit_s - 0.55) < 1e-9, "grab_transit_s applied to config")
    os.unlink(p)


def test_g55_precision_knobs_roundtrip_and_apply():
    # G5.5 shipped the precision knobs as env-only; they belong in the persistable
    # set too, or the spike's `w` save silently drops whatever was tuned live.
    p = _tmp()
    ok = gc.save({"precision": False, "precision_gain": 0.2,
                  "precision_v_lo": 0.05, "precision_v_hi": 0.8}, p)
    check(ok, "G5.5 precision knobs save")
    got = gc.load(p)
    check(got.get("precision") is False, "precision flag round-trips")
    check(got.get("precision_gain") == 0.2, "precision_gain round-trips")
    cfg = GestureConfig()
    gc.apply_to(cfg, got)
    check(cfg.precision is False, "precision applied to config")
    check(abs(cfg.precision_gain - 0.2) < 1e-9, "precision_gain applied to config")
    check(abs(cfg.precision_v_lo - 0.05) < 1e-9, "precision_v_lo applied to config")
    check(abs(cfg.precision_v_hi - 0.8) < 1e-9, "precision_v_hi applied to config")
    snap = gc.from_config(GestureConfig(), mirror=False)
    check("precision_gain" in snap, "from_config now snapshots precision knobs")
    os.unlink(p)


TESTS = [
    test_load_missing,
    test_save_load_roundtrip,
    test_g62_click_knobs_roundtrip_and_apply,
    test_g64_grab_transit_roundtrip_and_apply,
    test_g55_precision_knobs_roundtrip_and_apply,
    test_unknown_keys_dropped,
    test_type_coercion,
    test_corrupt_json_safe,
    test_non_dict_json_safe,
    test_uncoercible_value_skipped,
    test_apply_to,
    test_from_config,
    test_from_env_precedence,
    test_from_env_default_when_no_json_no_env,
]


def main():
    print("=" * 60)
    print("gesture_calibration harness")
    print("=" * 60)
    _clear_env()
    for t in TESTS:
        t()
    _clear_env()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
