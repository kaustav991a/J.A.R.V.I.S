r"""
gesture_spike.py — Phase G2 live driver (HAND_GESTURE_CONTROL_PLAN.md)
=======================================================================

G1 proved camera → landmarks → cursor (30–40 fps live, commit e1cc385).
G3 natural-grab vocabulary (click and grab are separate gestures):

    index up 1 s    START control (control starts OFF — safe)
    open palm       move cursor (palm-knuckle centroid, One-Euro smoothed)
    thumb+index tap left click (fires on pinch-land); 2nd tap <=1 s = double
    closed fist     GRAB — mouse down, move to drag, open hand to drop
    thumb+middle    right click
    index+middle    scroll (hand up = scroll up)
    back of hand    hold 1.5 s = STOP control

Run:   venv\Scripts\python.exe gesture_spike.py [index | http://ip:port/video]
       (or env JARVIS_CAM; phone IP Webcam must be on the SAME Wi-Fi as the PC)
Quit:  ESC in the preview window
Env:   JARVIS_CAM         camera index or stream URL (default 0)
       JARVIS_CAM_RES     res query for IP Webcam /video URLs (default 640x480,
                          empty string disables) — stream-lag mitigation
       JARVIS_CAM_MIRROR  1 (default) flips the frame like a mirror — right for
                          a raw webcam. IP Webcam streams are often ALREADY
                          mirrored (front cam/app setting) → cursor left/right
                          inverted: set 0, or press  m  live in the window.
       JARVIS_PALM_FACING 0 disables the palm-facing check if the engage gate
                          won't fire on your camera (mirroring flips it)
       JARVIS_PALM_SIGN   1 | -1 — flips the facing convention instead

G2 gate: engage, move, click a taskbar icon, drag a file, right-click,
scroll a page — all hands-only; gate never fires from casual waving.
"""

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:  # standalone script — pull JARVIS_CAM etc. from .env like main.py does
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import cv2
import mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions

from modules.gesture_camera import CameraError, FrameSource
from modules.gesture_engine import GestureConfig, GestureEngine
from modules.gesture_pointer import PointerBackend
from modules import gesture_calibration

MODEL_PATH = "models/hand_landmarker.task"
FRAME_W, FRAME_H = 640, 480

_src = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("JARVIS_CAM", "0")).strip()
CAM_SOURCE = int(_src) if _src.isdigit() else _src


def main() -> int:
    try:
        fs = FrameSource(CAM_SOURCE, FRAME_W, FRAME_H,
                         url_res=os.getenv("JARVIS_CAM_RES", "640x480") or None)
    except CameraError as e:
        print(f"FAIL[{e.kind}]: {e}")
        return 1
    print(f"camera source: {CAM_SOURCE!r}")

    landmarker = vision.HandLandmarker.create_from_options(
        vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MODEL_PATH),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
        )
    )
    engine = GestureEngine(GestureConfig.from_env())
    pointer = PointerBackend()
    print("control starts OFF — hold your INDEX FINGER up for 1 s to start; "
          "show the BACK of your open hand for 1.5 s to stop. ESC quits.")

    seq = 0
    stalls = 0
    fps = 0.0
    t_last = time.perf_counter()
    last_event = "-"
    # mirror: persisted calibration wins over the env default (press w to save).
    mirror = gesture_calibration.load().get(
        "mirror", os.getenv("JARVIS_CAM_MIRROR", "1") == "1")

    try:
        while True:
            seq, frame = fs.read_new(seq, timeout=1.0)
            if frame is None:
                stalls += 1
                print(f"no frame for 1s — stream stalled ({stalls}/10)")
                if stalls >= 10:
                    print("giving up on the stream")
                    return 1
                continue
            stalls = 0
            if mirror:  # right hand right = cursor right (raw webcams);
                # IP Webcam streams may arrive pre-mirrored — press m to fix
                frame = cv2.flip(frame, 1)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            now = time.perf_counter()
            result = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb),
                int(now * 1000),
            )
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - t_last, 1e-6))
            t_last = now

            if result.hand_landmarks:
                lms = result.hand_landmarks[0]
                handedness = (result.handedness[0][0].category_name
                              if result.handedness else "Right")
                pts = [(p.x, p.y, p.z) for p in lms]
                h, w = frame.shape[:2]
                for p in lms:
                    cv2.circle(frame, (int(p.x * w), int(p.y * h)),
                               3, (0, 255, 0), -1)
            else:
                pts, handedness = None, "Right"

            intents = engine.process(pts, now, handedness)
            pointer.execute(intents)
            for i in intents:
                if i[0] != "move":
                    last_event = i[0]
                    print(f"[{time.strftime('%H:%M:%S')}] {i[0]}")

            state = "ENGAGED" if engine.engaged else "off"
            prog = max(engine.start_progress, engine.stop_progress)
            if prog > 0.0:
                state += f"  hold:{int(prog * 100):3d}%"
            color = (0, 255, 0) if engine.engaged else (0, 0, 255)
            cv2.putText(frame, f"{fps:5.1f} fps  {state}  pose:{engine.pose}  last:{last_event}",
                        (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.putText(frame,
                        f"index 1s=start  back-hand 1.5s=stop  m=mirror({'on' if mirror else 'off'})  +/-=sens({engine.cfg.sensitivity:.1f})  w=save  ESC=quit",
                        (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            cv2.imshow("JARVIS gesture (G2)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == 27:  # ESC
                break
            elif key in (ord("m"), ord("M")):
                mirror = not mirror
                print(f"mirror -> {'ON' if mirror else 'OFF'} "
                      f"(persist with JARVIS_CAM_MIRROR={'1' if mirror else '0'})")
            elif key in (ord("+"), ord("=")):
                engine.cfg.sensitivity = min(engine.cfg.sensitivity + 0.1, 4.0)
                print(f"sensitivity -> {engine.cfg.sensitivity:.1f} "
                      f"(persist JARVIS_GESTURE_SENSITIVITY={engine.cfg.sensitivity:.1f})")
            elif key in (ord("-"), ord("_")):
                engine.cfg.sensitivity = max(engine.cfg.sensitivity - 0.1, 0.5)
                print(f"sensitivity -> {engine.cfg.sensitivity:.1f} "
                      f"(persist JARVIS_GESTURE_SENSITIVITY={engine.cfg.sensitivity:.1f})")
            elif key in (ord("w"), ord("W")):
                if gesture_calibration.save(
                        gesture_calibration.from_config(engine.cfg, mirror=mirror)):
                    print(f"calibration saved -> {gesture_calibration.path()} "
                          f"(sens={engine.cfg.sensitivity:.1f}, "
                          f"mirror={'on' if mirror else 'off'}, "
                          f"palm_sign={engine.cfg.palm_sign}) — survives restart")
    finally:
        pointer.release_all()  # never exit with a mouse button stuck down
        landmarker.close()
        fs.release()
        cv2.destroyAllWindows()
    print(f"done — last smoothed fps: {fps:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
