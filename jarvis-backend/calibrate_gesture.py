r"""
calibrate_gesture.py — Phase G5.2 interactive calibration wizard
=================================================================

Measures the per-hand gesture knobs instead of guessing them, then persists to
models/gesture_calibration.json (honoured by the daemon, gesture_spike.py, and
GestureConfig.from_env — defaults < JSON < JARVIS_* env). Removes the manual
JARVIS_PALM_SIGN / JARVIS_PALM_FACING fiddling and one-size-fits-all thresholds.

A ~30 s guided flow (live, camera-gated by Kaustav):
  1. Hold an OPEN PALM to the camera   -> auto-detect palm_sign + hand_size.
  2. Pinch thumb+index a few times     -> derive pinch_down / pinch_up for you.
  3. Move your open palm to the corners -> derive sensitivity (absolute) or
     base_gain (relative) from your comfortable reach.

Run:   venv\Scripts\python.exe calibrate_gesture.py [index | http://ip:port/video]
       [--relative]   calibrate for the G5.1 relative-trackpad mode
Quit:  ESC (abort, nothing saved) — the wizard saves only after all stages pass.

The DERIVATION is pure (no cv2/camera) and unit-tested in
test_calibrate_gesture.py; only the capture loop needs hardware.
"""
from __future__ import annotations

import math

from modules.gesture_engine import (
    INDEX_MCP, INDEX_TIP, MIDDLE_MCP, PINKY_MCP, RING_MCP, THUMB_TIP, WRIST,
)


# ----------------------------------------------------------------------------- #
# Pure derivation helpers (no camera — fully unit-testable)
# ----------------------------------------------------------------------------- #

def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hand_size(lm) -> float:
    """Wrist -> middle-MCP distance; the normalising length for every threshold."""
    return _dist(lm[WRIST], lm[MIDDLE_MCP])


def palm_cross_z(lm) -> float:
    """Signed z of (wrist->indexMCP) x (wrist->pinkyMCP): the palm-facing sign."""
    v1 = (lm[INDEX_MCP][0] - lm[WRIST][0], lm[INDEX_MCP][1] - lm[WRIST][1])
    v2 = (lm[PINKY_MCP][0] - lm[WRIST][0], lm[PINKY_MCP][1] - lm[WRIST][1])
    return v1[0] * v2[1] - v1[1] * v2[0]


def detect_palm_sign(lm, handedness: str = "Right") -> int:
    """palm_sign such that GestureEngine._palm_facing() reads True for THIS frame.

    Call it while the user holds an OPEN PALM toward the camera. The engine tests
    `cross_z * (palm_sign if Right else -palm_sign) > 0`, so we pick palm_sign to
    make that hold for the sampled open-palm cross product. Mirroring flips the
    cross sign, so measuring beats the manual JARVIS_PALM_SIGN guess."""
    cross_z = palm_cross_z(lm)
    hand_factor = 1 if handedness == "Right" else -1
    return 1 if (cross_z * hand_factor) > 0 else -1


def thumb_index_norm(lm) -> float:
    """Thumb-tip <-> index-tip distance, normalised by hand size (the pinch metric)."""
    hs = hand_size(lm)
    if hs < 1e-6:
        return 0.0
    return _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / hs


def palm_centroid(lm) -> tuple[float, float]:
    """Mean of the four knuckles — the cursor driver, stable through pinch/fist."""
    xs = (lm[INDEX_MCP][0] + lm[MIDDLE_MCP][0] + lm[RING_MCP][0] + lm[PINKY_MCP][0]) / 4.0
    ys = (lm[INDEX_MCP][1] + lm[MIDDLE_MCP][1] + lm[RING_MCP][1] + lm[PINKY_MCP][1]) / 4.0
    return (xs, ys)


def derive_pinch_thresholds(samples):
    """From a stream of thumb_index_norm distances spanning open<->pinched, derive
    (pinch_down, pinch_up) with a hysteresis gap. Returns (None, None) if the data
    lacks the open/pinched separation to be trustworthy (caller keeps defaults)."""
    xs = sorted(x for x in samples if x is not None and x > 0.0)
    if len(xs) < 4:
        return (None, None)
    n = len(xs)
    lo = xs[max(0, n // 10)]            # ~10th pct  -> pinched cluster
    hi = xs[min(n - 1, (n * 8) // 10)]  # ~80th pct  -> open cluster
    if hi - lo < 0.15:                  # never really opened AND pinched
        return (None, None)
    down = lo + 0.35 * (hi - lo)
    up = lo + 0.65 * (hi - lo)
    down = min(max(down, 0.2), 1.0)
    up = min(max(up, down + 0.15), 1.4)
    return (round(down, 3), round(up, 3))


def derive_sensitivity_from_reach(span: float) -> float:
    """Absolute mode: a comfortable palm reach of `span` (frame fraction) should
    cover the screen. Engine maps a band of width 2*0.35/sensitivity to the screen,
    so sensitivity = 0.7/span. Clamped to the same [0.5, 4.0] as the live +/- keys."""
    span = max(span, 1e-3)
    return round(min(max(0.7 / span, 0.5), 4.0), 2)


def derive_gain_from_reach(span: float) -> float:
    """Relative mode: base_gain so a comfortable reach traverses ~one screen at
    nominal acceleration. Clamped to the spike's [0.2, 5.0] range."""
    span = max(span, 1e-3)
    return round(min(max(1.0 / span, 0.2), 5.0), 2)


def derive_calibration(palm_samples, pinch_samples, reach_samples,
                       handedness: str = "Right", relative: bool = False,
                       mirror: bool | None = None) -> dict:
    """Fold the per-stage samples into a calibration dict ready for
    gesture_calibration.save(). Pure: the live loop just collects the samples.

    palm_samples  : list of 21-landmark frames of an OPEN PALM facing the camera.
    pinch_samples : list of thumb_index_norm floats streamed during the pinches.
    reach_samples : list of (x, y) palm centroids streamed during the reach.
    """
    out: dict = {"require_palm_facing": True}

    signs = [detect_palm_sign(lm, handedness) for lm in palm_samples]
    if signs:
        out["palm_sign"] = 1 if sum(signs) >= 0 else -1

    down, up = derive_pinch_thresholds(pinch_samples)
    if down is not None:
        out["pinch_down"], out["pinch_up"] = down, up

    if reach_samples:
        xs = [c[0] for c in reach_samples]
        ys = [c[1] for c in reach_samples]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        if relative:
            out["mapping_mode"] = "relative"
            out["base_gain"] = derive_gain_from_reach(span)
        else:
            out["mapping_mode"] = "absolute"
            out["sensitivity"] = derive_sensitivity_from_reach(span)

    if mirror is not None:
        out["mirror"] = bool(mirror)
    return out


# ----------------------------------------------------------------------------- #
# Live guided flow (hardware — Kaustav live-gates)
# ----------------------------------------------------------------------------- #

# stage tuning: how many good frames each measurement stage needs.
PALM_FRAMES_NEEDED = 20
PINCH_FRAMES_NEEDED = 60
REACH_FRAMES_NEEDED = 60


def main() -> int:  # pragma: no cover  (camera loop — live-gated, not harnessed)
    import os
    import sys
    import time

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import cv2
    from mediapipe.tasks.python import BaseOptions, vision

    from modules import gesture_calibration
    from modules.gesture_camera import CameraError, FrameSource

    relative = "--relative" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = (args[0] if args else os.getenv("JARVIS_CAM", "0")).strip()
    cam = int(src) if src.isdigit() else src
    mirror = gesture_calibration.load().get(
        "mirror", os.getenv("JARVIS_CAM_MIRROR", "1") == "1")

    try:
        fs = FrameSource(cam, 640, 480,
                         url_res=os.getenv("JARVIS_CAM_RES", "640x480") or None)
    except CameraError as e:
        print(f"FAIL[{e.kind}]: {e}")
        return 1

    landmarker = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path="models/hand_landmarker.task"),
            running_mode=vision.RunningMode.VIDEO, num_hands=1))

    stages = ["palm", "pinch", "reach", "done"]
    prompts = {
        "palm": "Hold an OPEN PALM toward the camera",
        "pinch": "PINCH thumb+index open/closed a few times",
        "reach": "Move your OPEN PALM to each screen corner",
        "done": "Calibrated — press W to save, ESC to discard",
    }
    stage = 0
    palm_samples, pinch_samples, reach_samples = [], [], []
    result: dict = {}
    seq = 0

    print("Gesture calibration wizard — follow the on-screen prompts. ESC aborts.")
    try:
        while True:
            seq, frame = fs.read_new(seq, timeout=1.0)
            if frame is None:
                continue
            if mirror:
                frame = cv2.flip(frame, 1)
            import mediapipe as mp
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
                int(time.perf_counter() * 1000))
            lm = None
            if res.hand_landmarks:
                p = res.hand_landmarks[0]
                lm = [(q.x, q.y, q.z) for q in p]

            name = stages[stage]
            if lm is not None:
                if name == "palm":
                    palm_samples.append(lm)
                    if len(palm_samples) >= PALM_FRAMES_NEEDED:
                        stage += 1
                elif name == "pinch":
                    pinch_samples.append(thumb_index_norm(lm))
                    if len(pinch_samples) >= PINCH_FRAMES_NEEDED:
                        stage += 1
                elif name == "reach":
                    reach_samples.append(palm_centroid(lm))
                    if len(reach_samples) >= REACH_FRAMES_NEEDED:
                        result = derive_calibration(
                            palm_samples, pinch_samples, reach_samples,
                            relative=relative, mirror=mirror)
                        stage += 1

            done = stages[stage] == "done"
            n = {"palm": len(palm_samples), "pinch": len(pinch_samples),
                 "reach": len(reach_samples)}.get(stages[stage], 0)
            cv2.putText(frame, prompts[stages[stage]], (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            if not done:
                cv2.putText(frame, f"samples: {n}", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            else:
                cv2.putText(frame, str(result), (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
            cv2.imshow("JARVIS gesture calibration", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                print("aborted — nothing saved.")
                return 1
            if done and key in (ord("w"), ord("W")):
                if gesture_calibration.save(result):
                    print(f"saved -> {gesture_calibration.path()}: {result}")
                    return 0
                print("save failed.")
                return 1
    finally:
        landmarker.close()
        fs.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
