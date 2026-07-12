r"""
gesture_daemon.py — Phase G3 always-on gesture + presence daemon
================================================================

One camera loop, three jobs, tiered so the 17 GB CPU-only box never sweats:

    LOCKED  (~2 fps)   face gate only — owner's face unlocks; strangers alert
    IDLE    (~9 fps)   watch for the owner's index-up START gesture; presence
    ACTIVE  (~30 fps)  full gesture control (engine + SendInput pointer)

Security model (owner = enrolled face, models/owner_embeddings.npz):
    * gestures execute ONLY while the owner's face was verified in the last
      few seconds — anyone else's hands are ignored (deny + HUD toast +
      rate-limited Telegram snapshot alert)
    * owner away (no face AND no motion for JARVIS_LOCK_AFTER seconds) ->
      soft lock: fullscreen lock_overlay.py subprocess + monitor power-off
    * owner back -> overlay killed, monitor woken, HUD unlocked — hands-free

Env:
    JARVIS_GESTURE      0 disables the whole daemon (default 1)
    JARVIS_AUTO_LOCK    0 disables the away-lock (default 1)
    JARVIS_LOCK_AFTER   seconds of no-face+no-motion before locking (default 6)
    JARVIS_CAM / JARVIS_CAM_RES / JARVIS_CAM_MIRROR /
    JARVIS_PALM_FACING / JARVIS_PALM_SIGN — same as gesture_spike.py
    JARVIS_UNLOCK_CODE  blind-typed overlay escape hatch (optional)

Voice fast-path: "hand control on/off" -> set_gestures_enabled() (fast_path.py).
HUD: broadcasts {"type": "gesture_state", ...} via socket_manager (thread-safe)
and GET /api/gesture/state mirrors the same dict (main.py).

Known limit (documented in HAND_GESTURE_CONTROL_PLAN.md §5): when JARVIS's own
GUI agents (agentic_gui_task / ghost_type / autopilot) drive the cursor, say
"hand control off" first — an arbiter flag is the G4 follow-up.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

MODEL_PATH = os.path.join("models", "hand_landmarker.task")
FRAME_W, FRAME_H = 640, 480

# Live state mirror for GET /api/gesture/state (single-writer: daemon thread).
gesture_state: dict = {
    "enabled": True,        # gesture control switch (voice-toggleable)
    "auto_lock": True,      # presence-lock switch
    "state": "starting",    # starting|locked|idle|active|disabled|camera_error
    "pose": "none",
    "engaged": False,
    "owner": False,
    "stranger": False,
    "denied": False,
    "locked": False,
    "start_progress": 0.0,
    "stop_progress": 0.0,
    "camera": None,
    "ts": 0.0,
}


def _monitor_power(off: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        if off:
            # WM_SYSCOMMAND / SC_MONITORPOWER 2 = off, broadcast
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        else:
            # a tiny relative mouse jiggle wakes the panel
            ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)
            ctypes.windll.user32.mouse_event(0x0001, -1, 0, 0, 0)
    except Exception as e:  # noqa: BLE001
        print(f"[GESTURE] monitor power fault: {e}", flush=True)


class GestureDaemon:
    """Thread daemon (ambient_vision Pattern B) — start() in lifespan, stop() on shutdown."""

    OWNER_GRACE_S = 3.5      # gestures allowed this long after the last owner sighting
    ALERT_COOLDOWN_S = 60.0  # min gap between stranger Telegram alerts
    DENY_TOAST_S = 8.0       # min gap between UNAUTHORIZED HUD toasts

    def __init__(self, loop=None):
        self.loop = loop                    # asyncio loop for the phone-alert leg
        self.running = False
        self.thread: threading.Thread | None = None
        self.gestures_enabled = os.getenv("JARVIS_GESTURE", "1") == "1"
        self.auto_lock = os.getenv("JARVIS_AUTO_LOCK", "1") == "1"
        self.absent_after = float(os.getenv("JARVIS_LOCK_AFTER", "6"))
        self._overlay: subprocess.Popen | None = None
        self._locked = False
        self._last_owner_t = -1e9
        self._last_alert_t = -1e9
        self._last_toast_t = -1e9
        self._last_hud: dict | None = None
        self._last_face_t = -1e9

    # ---- public toggles (voice fast-path / API) ------------------------- #

    def set_gestures_enabled(self, on: bool) -> None:
        self.gestures_enabled = on
        gesture_state["enabled"] = on

    def set_auto_lock(self, on: bool) -> None:
        self.auto_lock = on
        gesture_state["auto_lock"] = on
        if not on and self._locked:
            self._unlock()

    # ---- lifecycle ------------------------------------------------------ #

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="gesture-daemon")
        self.thread.start()
        print("[GESTURE] G3 daemon started.", flush=True)

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=3.0)
        self._kill_overlay()

    # ---- HUD ------------------------------------------------------------ #

    def _hud(self, engine, state: str, denied: bool = False, force: bool = False) -> None:
        from socket_manager import schedule_ui_update

        gate = gesture_state
        gate.update({
            "state": state,
            "pose": getattr(engine, "pose", "none") if engine else "none",
            "engaged": bool(engine and engine.engaged),
            "denied": denied,
            "locked": self._locked,
            "start_progress": round(getattr(engine, "start_progress", 0.0), 2) if engine else 0.0,
            "stop_progress": round(getattr(engine, "stop_progress", 0.0), 2) if engine else 0.0,
            "ts": time.time(),
        })
        key = {k: v for k, v in gate.items() if k != "ts"}
        if force or key != self._last_hud:
            self._last_hud = key
            schedule_ui_update({"type": "gesture_state", **gate})

    # ---- lock / unlock --------------------------------------------------- #

    def _lock(self, pointer) -> None:
        if self._locked:
            return
        self._locked = True
        gesture_state["locked"] = True
        try:
            pointer.release_all()
        except Exception:
            pass
        try:
            self._overlay = subprocess.Popen(
                [sys.executable, "lock_overlay.py"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdin=subprocess.PIPE,
                creationflags=0x08000000 if sys.platform == "win32" else 0)
        except Exception as e:  # noqa: BLE001
            print(f"[GESTURE] overlay spawn failed: {e}", flush=True)
            self._overlay = None
        _monitor_power(off=True)
        from socket_manager import schedule_ui_update
        schedule_ui_update({"status": "security_locked",
                            "message": "USER ABSENT. DESK SOFT-LOCKED."})
        print("[GESTURE] desk soft-locked (owner away).", flush=True)

    def _kill_overlay(self) -> None:
        if self._overlay is not None:
            try:
                self._overlay.terminate()
            except Exception:
                pass
            self._overlay = None

    def _unlock(self) -> None:
        if not self._locked:
            return
        self._locked = False
        gesture_state["locked"] = False
        self._kill_overlay()
        _monitor_power(off=False)
        from socket_manager import schedule_ui_update
        schedule_ui_update({"status": "online",
                            "message": "OWNER VERIFIED. DESK UNLOCKED."})
        print("[GESTURE] owner back — desk unlocked.", flush=True)

    # ---- stranger alert --------------------------------------------------- #

    def _stranger_alert(self, frame, context: str) -> None:
        now = time.monotonic()
        if now - self._last_alert_t < self.ALERT_COOLDOWN_S:
            return
        self._last_alert_t = now
        snap_path = None
        try:
            import cv2
            os.makedirs("captures", exist_ok=True)
            snap_path = os.path.join(
                "captures", f"stranger_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
            cv2.imwrite(snap_path, frame)
        except Exception:
            snap_path = None
        msg = (f"Security notice, Sir: an unrecognised person {context}. "
               f"Gesture control stayed locked to you.")
        if self.loop is not None:
            import asyncio

            async def _send():
                sent = False
                try:
                    if snap_path:
                        from modules.telegram_bot import send_document_to_owner
                        sent = await send_document_to_owner(snap_path, caption=msg)
                except Exception:
                    sent = False
                if not sent:
                    from modules import owner_notify
                    await owner_notify.send_to_phone(msg)

            try:
                asyncio.run_coroutine_threadsafe(_send(), self.loop)
            except Exception as e:  # noqa: BLE001
                print(f"[GESTURE] alert dispatch fault: {e}", flush=True)
        print(f"[GESTURE] STRANGER: {context} (alert sent, snap={snap_path})", flush=True)

    # ---- main loop -------------------------------------------------------- #

    def _run(self) -> None:
        while self.running:  # outer retry shell — daemon never dies
            try:
                self._session()
            except Exception as e:  # noqa: BLE001
                import traceback
                print(f"[GESTURE] session fault: {e}\n{traceback.format_exc()}", flush=True)
                gesture_state["state"] = "camera_error"
                time.sleep(10.0)

    def _session(self) -> None:
        import cv2
        from modules.face_gate import AbsenceTracker, FaceGate, MotionDetector
        from modules.gesture_camera import CameraError, FrameSource
        from modules.gesture_engine import GestureConfig, GestureEngine
        from modules.gesture_pointer import PointerBackend

        _src = os.getenv("JARVIS_CAM", "0").strip()
        source = int(_src) if _src.isdigit() else _src
        gesture_state["camera"] = str(source)
        try:
            fs = FrameSource(source, FRAME_W, FRAME_H,
                             url_res=os.getenv("JARVIS_CAM_RES", "640x480") or None)
        except CameraError as e:
            gesture_state["state"] = "camera_error"
            print(f"[GESTURE] camera unavailable [{e.kind}] — retry in 30s", flush=True)
            time.sleep(30.0)
            return

        landmarker = None
        try:
            import mediapipe as mp
            from mediapipe.tasks.python import BaseOptions, vision
            landmarker = vision.HandLandmarker.create_from_options(
                vision.HandLandmarkerOptions(
                    base_options=BaseOptions(model_asset_path=MODEL_PATH),
                    running_mode=vision.RunningMode.VIDEO,
                    num_hands=1))
            mp_image_fmt = mp.ImageFormat.SRGB
            mp_image = mp.Image
        except Exception as e:  # noqa: BLE001
            print(f"[GESTURE] hand model unavailable ({e}) — presence-only mode", flush=True)

        engine = GestureEngine(GestureConfig(
            require_palm_facing=os.getenv("JARVIS_PALM_FACING", "1") == "1",
            palm_sign=int(os.getenv("JARVIS_PALM_SIGN", "1"))))
        pointer = PointerBackend()
        gate = FaceGate()
        motion = MotionDetector()
        absence = AbsenceTracker(absent_after_s=self.absent_after)
        mirror = os.getenv("JARVIS_CAM_MIRROR", "1") == "1"

        seq = 0
        try:
            while self.running:
                # ---- state-tiered pacing (the optimisation core) ----
                if self._locked:
                    time.sleep(0.5)                    # ~2 fps
                elif not (engine.engaged and self.gestures_enabled):
                    time.sleep(0.11)                   # ~9 fps idle scan
                # ACTIVE: no sleep — read_new blocks on the camera (~30 fps)

                seq, frame = fs.read_new(seq, timeout=1.0)
                if frame is None:
                    engine.process(None, time.perf_counter())
                    continue
                if mirror:
                    frame = cv2.flip(frame, 1)
                now = time.perf_counter()

                # ---- face + motion (cheap, cadenced) ----
                moving = motion.update(frame)
                face_every = 0.5 if self._locked else (1.5 if engine.engaged else 1.0)
                if now - self._last_face_t >= face_every and gate._ensure():
                    self._last_face_t = now
                    res = gate.check(frame)
                    gesture_state["owner"] = res.owner_present
                    gesture_state["stranger"] = res.stranger_present
                    if res.owner_present:
                        self._last_owner_t = now
                    if res.stranger_present and self._locked:
                        self._stranger_alert(frame, "approached the desk while you were away")
                res = gate.last
                owner_ok = (now - self._last_owner_t) <= self.OWNER_GRACE_S
                if gate.available is False:
                    owner_ok = True   # gate unenrolled/broken -> don't brick control

                # ---- locked: watch for the owner only ----
                if self._locked:
                    if owner_ok:
                        self._unlock()
                        absence.reset(now)
                    self._hud(engine, "locked")
                    continue

                # ---- absence -> soft lock ----
                if self.auto_lock and gate.available and absence.update(
                        res.owner_present, res.any_face, moving, now):
                    if engine.engaged:
                        engine.process(None, now + 10.0)  # force-release drag state
                        engine.engaged = False
                    self._lock(pointer)
                    self._hud(engine, "locked", force=True)
                    continue

                # ---- hands ----
                if landmarker is None or not self.gestures_enabled:
                    self._hud(engine, "disabled" if not self.gestures_enabled else "idle")
                    continue

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = landmarker.detect_for_video(
                    mp_image(image_format=mp_image_fmt, data=rgb), int(now * 1000))
                if result.hand_landmarks:
                    pts = [(p.x, p.y, p.z) for p in result.hand_landmarks[0]]
                    handedness = (result.handedness[0][0].category_name
                                  if result.handedness else "Right")
                else:
                    pts, handedness = None, "Right"

                intents = engine.process(pts, now, handedness)

                denied = False
                if intents and not owner_ok:
                    # hands moving but the verified owner isn't in front of the
                    # camera -> deny. Never leave a stranger an engaged session.
                    denied = True
                    if engine.engaged:
                        engine.engaged = False
                        engine._reset_motion_state()
                    pointer.release_all()
                    intents = []
                    if pts is not None and res.stranger_present:
                        self._stranger_alert(frame, "tried to use gesture control")
                    if time.monotonic() - self._last_toast_t > self.DENY_TOAST_S:
                        self._last_toast_t = time.monotonic()
                        from socket_manager import schedule_ui_update
                        schedule_ui_update({
                            "type": "gesture_state", **gesture_state,
                            "state": "denied", "denied": True})

                pointer.execute(intents)
                self._hud(engine, "active" if engine.engaged else "idle", denied)
        finally:
            try:
                pointer.release_all()
            except Exception:
                pass
            if landmarker is not None:
                landmarker.close()
            fs.release()


gesture_daemon = GestureDaemon()
