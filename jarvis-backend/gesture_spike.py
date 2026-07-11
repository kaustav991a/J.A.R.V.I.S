"""
gesture_spike.py — Phase G1 feasibility spike (HAND_GESTURE_CONTROL_PLAN.md)
============================================================================

Standalone proof: webcam → MediaPipe HandLandmarker (Tasks API) → cursor
follows your index fingertip. NO clicking in this spike — move only, so it
can't misfire on anything. Run it, wave your hand, read the numbers.

Run:   venv\Scripts\python.exe gesture_spike.py
Quit:  ESC in the preview window (or Ctrl+C in the terminal)

What to check (G1 gate, plan §4):
  - FPS overlay ≥ 20 with a real hand in frame
  - cursor motion feels ≤ ~100ms behind your finger
  - CPU column in Task Manager for this python.exe < ~35% of one core
Numbers go back into HAND_GESTURE_CONTROL_PLAN.md.
"""

import sys
import time
import ctypes

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions

MODEL_PATH = "models/hand_landmarker.task"
CAM_INDEX = 0
FRAME_W, FRAME_H = 640, 480

# Map only the middle of the camera frame to the whole screen so you don't
# have to reach the frame edges to hit screen corners.
MARGIN = 0.15
# Exponential smoothing (G1-simple; One-Euro filter arrives in G2).
SMOOTH = 0.35  # 0 = frozen, 1 = raw/jittery

# DPI awareness so cursor coords are physical pixels (same fix as §4.8).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

SCREEN_W = ctypes.windll.user32.GetSystemMetrics(0)
SCREEN_H = ctypes.windll.user32.GetSystemMetrics(1)


def map_to_screen(nx: float, ny: float) -> tuple[int, int]:
    """Normalised (0..1) camera coords → screen pixels, margin-cropped."""
    cx = (nx - MARGIN) / (1 - 2 * MARGIN)
    cy = (ny - MARGIN) / (1 - 2 * MARGIN)
    cx = min(max(cx, 0.0), 1.0)
    cy = min(max(cy, 0.0), 1.0)
    return int(cx * (SCREEN_W - 1)), int(cy * (SCREEN_H - 1))


def main() -> int:
    cap = cv2.VideoCapture(CAM_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    if not cap.isOpened():
        print("FAIL: camera not available (is ambient_vision holding it? "
              "stop the JARVIS backend first).")
        return 1

    landmarker = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
        )
    )
    print(f"Screen {SCREEN_W}x{SCREEN_H} | camera {FRAME_W}x{FRAME_H} | "
          f"ESC in the window to quit. Move your INDEX finger.")

    sx, sy = None, None            # smoothed cursor position
    t_last = time.perf_counter()
    fps = 0.0
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            print("camera read failed"); break
        frame = cv2.flip(frame, 1)  # mirror: move right hand right → cursor right

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
            int(time.perf_counter() * 1000),
        )

        now = time.perf_counter()
        fps = 0.9 * fps + 0.1 * (1.0 / max(now - t_last, 1e-6))
        t_last = now

        if result.hand_landmarks:
            lms = result.hand_landmarks[0]
            tip = lms[8]  # index fingertip
            tx, ty = map_to_screen(tip.x, tip.y)
            if sx is None:
                sx, sy = float(tx), float(ty)
            else:
                sx += SMOOTH * (tx - sx)
                sy += SMOOTH * (ty - sy)
            ctypes.windll.user32.SetCursorPos(int(sx), int(sy))

            # draw all 21 landmarks for the preview
            for lm in lms:
                cv2.circle(frame, (int(lm.x * FRAME_W), int(lm.y * FRAME_H)),
                           3, (0, 255, 0), -1)
            cv2.circle(frame, (int(tip.x * FRAME_W), int(tip.y * FRAME_H)),
                       8, (0, 0, 255), 2)

        cv2.putText(frame, f"{fps:5.1f} fps  ESC=quit", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.imshow("JARVIS gesture spike (G1)", frame)
        if cv2.waitKey(1) & 0xFF == 27:  # ESC
            break
        frame_idx += 1

    landmarker.close()
    cap.release()
    cv2.destroyAllWindows()
    print(f"done — last smoothed fps: {fps:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
