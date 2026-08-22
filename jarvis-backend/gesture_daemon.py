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
    JARVIS_LOCK_AFTER   seconds of no-face+no-motion before locking (default 60)
    JARVIS_CAM / JARVIS_CAM_RES / JARVIS_CAM_MIRROR /
    JARVIS_PALM_FACING / JARVIS_PALM_SIGN — same as gesture_spike.py
    JARVIS_UNLOCK_CODE  blind-typed overlay escape hatch (optional)
    JARVIS_GESTURE_OVERLAY  0 disables the G5.3 cursor-halo overlay (default 1, win32)
    JARVIS_GESTURE_ROI      0 disables the G5.4 distance ROI crop (default 1)
    JARVIS_CAM_RES          capture/stream WxH, e.g. 1280x720 for reach (default 640x480)
    JARVIS_HAND_DET_CONF / JARVIS_HAND_PRESENCE_CONF / JARVIS_HAND_TRACK_CONF
                            MediaPipe confidence floors (default 0.5; lower = farther reach)
    JARVIS_ROI_EXPAND / JARVIS_ROI_MIN_FRAC / JARVIS_ROI_FOLLOW /
    JARVIS_ROI_WIDEN_AFTER / JARVIS_ROI_RESET_AFTER — ROI crop tuning (see gesture_roi.py)

Voice fast-path: "hand control on/off" -> set_gestures_enabled() (fast_path.py).
HUD: broadcasts {"type": "gesture_state", ...} via socket_manager (thread-safe)
and GET /api/gesture/state mirrors the same dict (main.py).

Cursor arbiter (G4): when JARVIS's own GUI agents (execute_autonomous_task /
ghost_type / ghost_save_file, or any move/click/type/press/scroll) drive the
cursor, modules/gesture_arbiter.py auto-suspends gesture output for the duration
plus a short self-healing tail, then resumes — no need to say "hand control off"
first. That suspend is independent of the user's gestures_enabled switch (an
automation suspend is not a manual off), and the HUD/notch reports it as
state="suspended" via gesture_state["suspended"/"suspend_reason"].
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from modules import gesture_arbiter  # G4: hand vs JARVIS-GUI cursor referee
from modules import gesture_calibration  # G4: persisted live-tuned gesture knobs
from modules import gesture_roi  # G5.4: crop-around-hand distance ROI

MODEL_PATH = os.path.join("models", "hand_landmarker.task")
FRAME_W, FRAME_H = 640, 480   # legacy default; per-session res now via _cam_res()


def _cam_res() -> tuple[int, int, str]:
    """(width, height, raw) from JARVIS_CAM_RES (e.g. '1280x720'); default 640x480.
    720p gives the G5.4 ROI crop real pixels for across-the-room control."""
    raw = os.getenv("JARVIS_CAM_RES", "640x480") or "640x480"
    try:
        w, h = raw.lower().split("x")
        return int(w), int(h), raw
    except Exception:  # noqa: BLE001
        return 640, 480, "640x480"


def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default

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
    "suspended": False,      # JARVIS's own GUI automation is driving the cursor
    "suspend_reason": None,
    "start_progress": 0.0,
    "stop_progress": 0.0,
    "camera": None,
    # G6.2: last discrete action (click/right_click/double_click/grab/drop) +
    # wall-clock ts, so the HUD chip + cursor overlay can pulse when one fires —
    # makes the click/right-click/grab bug diagnosable live.
    "last_action": None,
    "last_action_ts": 0.0,
    "ts": 0.0,
}

# discrete gesture intents -> a short label for the HUD/overlay action pulse.
# Scroll is deliberately excluded (fires every frame — would spam broadcasts).
_ACTION_LABEL = {
    "click": "click", "double_click": "double", "right_click": "right",
    "drag_start": "grab", "drag_end": "drop",
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
    # F-25. How long a face verdict stays usable. The gate is checked every
    # 0.5s while locked and every 1.0-1.5s otherwise, so this is more than an
    # order of magnitude of slack: if nothing real has come back in this long,
    # the camera is not answering, whatever `available` says.
    GATE_STALE_S = float(os.getenv("JARVIS_GATE_STALE_S", "8"))
    ALERT_COOLDOWN_S = 60.0  # min gap between stranger Telegram alerts
    DENY_TOAST_S = 8.0       # min gap between UNAUTHORIZED HUD toasts
    HUD_HEARTBEAT_S = 2.0    # re-send gesture_state even if unchanged, so the HUD can
                             # tell "state is stable" apart from "daemon is dead" (silence)

    def __init__(self, loop=None):
        self.loop = loop                    # asyncio loop for the phone-alert leg
        self.running = False
        self.thread: threading.Thread | None = None
        self.gestures_enabled = os.getenv("JARVIS_GESTURE", "1") == "1"
        self.auto_lock = os.getenv("JARVIS_AUTO_LOCK", "1") == "1"
        # Mirror both switches into the published state. Live-gate session 4:
        # the daemon was launched with JARVIS_AUTO_LOCK=0 and honoured it
        # internally, while GET /api/gesture/state kept answering
        # `"auto_lock": true` — the dict literal's default, which only the
        # voice-toggle setters ever corrected. The HUD and the phone read this
        # endpoint, so an operator who turned a switch OFF at boot was shown it
        # ON, which is the same "the barrier said one thing and did another"
        # class as F-19/F-21/F-25. Env-configured OFF is a real state, not a
        # default to be overwritten by one.
        gesture_state["enabled"] = self.gestures_enabled
        gesture_state["auto_lock"] = self.auto_lock
        # 60s, NOT 6s: at 6 a glance away from the camera blanked the monitor
        # (_lock spawns the lock overlay AND powers the display off), and a fresh
        # setup gets this default the moment face enrollment makes the gate
        # available. Kaustav's .env runs 120.
        self.absent_after = float(os.getenv("JARVIS_LOCK_AFTER", "60"))
        # F-25. The typed escape hatch defaulted to DISABLED on a barrier whose
        # only other exit was biometric — `lock_overlay.py` returns "break" for
        # every key when no code is set, so the hatch the docstring advertised
        # did not exist on a default install. One is generated when none is
        # configured, and printed here rather than only at lock time, because by
        # then the monitor is off.
        _cfg_code = (os.getenv("JARVIS_UNLOCK_CODE") or "").strip()
        if _cfg_code:
            self._unlock_code = _cfg_code
            print("[GESTURE] soft-lock unlock code: taken from "
                  "JARVIS_UNLOCK_CODE (not printed).", flush=True)
        else:
            import secrets
            self._unlock_code = f"{secrets.randbelow(10**6):06d}"
            print(f"[GESTURE] soft-lock unlock code for this session: "
                  f"{self._unlock_code}  — type it blind at the lock screen and "
                  f"press Enter. Set JARVIS_UNLOCK_CODE to choose your own.",
                  flush=True)
        self._overlay: subprocess.Popen | None = None
        # G5.3 cursor-halo + edge-toast overlay (separate click-through process)
        self._cursor_overlay: subprocess.Popen | None = None
        self._cursor_overlay_enabled = (
            os.getenv("JARVIS_GESTURE_OVERLAY", "1") == "1" and sys.platform == "win32")
        self._cursor_overlay_next_try = 0.0
        self._locked = False
        self._last_owner_t = -1e9
        # F-25: when the gate last produced a verdict about the PRESENT, and the
        # counter that proves it did. Both start "never", so the lock cannot arm
        # until the camera has actually answered once — a barrier that arms
        # before its own sensor works is a barrier with no exit.
        self._last_verdict_t = -1e9
        self._last_checks_ok = -1
        self._last_alert_t = -1e9
        self._last_toast_t = -1e9
        self._last_hud: dict | None = None
        self._last_hud_beat = -1e9   # wall-clock of the last gesture_state frame sent
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
        self._kill_cursor_overlay()

    # ---- cursor overlay (G5.3) ------------------------------------------ #

    def _ensure_cursor_overlay(self) -> None:
        """Lazily (re)spawn the click-through halo/toast process, rate-limited."""
        if not self._cursor_overlay_enabled:
            return
        if self._cursor_overlay is not None and self._cursor_overlay.poll() is None:
            return
        now = time.monotonic()
        if now < self._cursor_overlay_next_try:
            return
        self._cursor_overlay_next_try = now + 10.0
        try:
            self._cursor_overlay = subprocess.Popen(
                [sys.executable, "cursor_overlay.py"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdin=subprocess.PIPE,
                creationflags=0x08000000 if sys.platform == "win32" else 0)
        except Exception as e:  # noqa: BLE001
            print(f"[GESTURE] cursor overlay spawn failed: {e}", flush=True)
            self._cursor_overlay = None

    def _feed_cursor_overlay(self, frame: dict) -> None:
        """Push one gesture-state frame to the overlay's stdin (best-effort)."""
        if not self._cursor_overlay_enabled:
            return
        self._ensure_cursor_overlay()
        ov = self._cursor_overlay
        if ov is None or ov.stdin is None:
            return
        try:
            ov.stdin.write((json.dumps(frame) + "\n").encode("utf-8"))
            ov.stdin.flush()
        except Exception:
            # overlay died / pipe broke — drop it; _ensure respawns (rate-limited)
            try:
                ov.terminate()
            except Exception:
                pass
            self._cursor_overlay = None

    def _kill_cursor_overlay(self) -> None:
        if self._cursor_overlay is not None:
            try:
                self._cursor_overlay.terminate()
            except Exception:
                pass
            self._cursor_overlay = None

    # ---- HUD ------------------------------------------------------------ #

    def _hud(self, engine, state: str, denied: bool = False, force: bool = False) -> None:
        from socket_manager import schedule_ui_update

        gate = gesture_state
        gate.update({
            "state": state,
            "pose": getattr(engine, "pose", "none") if engine else "none",
            "engaged": bool(engine and engine.engaged),
            "clutch": bool(engine and getattr(engine, "clutch", False)),
            "denied": denied,
            "locked": self._locked,
            "suspended": gesture_arbiter.is_suspended(),
            "suspend_reason": gesture_arbiter.active_reason(),
            "start_progress": round(getattr(engine, "start_progress", 0.0), 2) if engine else 0.0,
            "stop_progress": round(getattr(engine, "stop_progress", 0.0), 2) if engine else 0.0,
            "ts": time.time(),
        })
        key = {k: v for k, v in gate.items() if k not in ("ts", "last_action_ts")}
        now_wall = time.time()
        stale = (now_wall - self._last_hud_beat) >= self.HUD_HEARTBEAT_S
        if force or key != self._last_hud or stale:
            self._last_hud = key
            self._last_hud_beat = now_wall
            schedule_ui_update({"type": "gesture_state", **gate})
            self._feed_cursor_overlay({
                "state": gate["state"], "engaged": gate["engaged"],
                "clutch": gate.get("clutch", False), "suspended": gate["suspended"],
                "denied": gate["denied"], "pose": gate["pose"],
                "locked": gate["locked"],
                "last_action": gate.get("last_action"),
                "last_action_ts": gate.get("last_action_ts", 0.0),
            })

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
            # F-25: the overlay is a separate process, so the code has to travel
            # to it. Passed in the environment rather than on argv, which is
            # readable from any process list on the machine.
            _env = dict(os.environ)
            _env["JARVIS_UNLOCK_CODE"] = self._unlock_code
            self._overlay = subprocess.Popen(
                [sys.executable, "lock_overlay.py"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdin=subprocess.PIPE,
                env=_env,
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
        from modules.face_gate import (
            AbsenceTracker, FaceGate, MotionDetector, StrangerConfirmer)
        from modules.gesture_camera import (
            CameraError, make_frame_source, parse_sources)
        from modules.gesture_engine import GestureConfig, GestureEngine
        from modules.gesture_pointer import PointerBackend
        from modules import frame_bus

        # G6.3 camera auto-select: probe a prioritized list (JARVIS_CAM_SOURCES,
        # comma-separated indices/URLs), use the first that opens + delivers a
        # frame; falls back to the single legacy JARVIS_CAM.
        sources = parse_sources(os.getenv("JARVIS_CAM_SOURCES"), os.getenv("JARVIS_CAM", "0"))
        cam_w, cam_h, cam_res = _cam_res()
        try:
            fs = make_frame_source(sources, cam_w, cam_h, url_res=cam_res)
        except CameraError as e:
            gesture_state["state"] = "camera_error"
            print(f"[GESTURE] camera unavailable [{e.kind}] — retry in 30s\n{e}",
                  flush=True)
            time.sleep(30.0)
            return
        # ── everything from here to the main loop must release the camera ────
        # `make_frame_source` has already opened a cv2.VideoCapture AND started a
        # reader thread, but the try/finally that releases it does not begin until
        # the loop below. Between the two sit model loads and file reads that can
        # raise — FaceGate pulls in face recognition, gesture_calibration.load()
        # touches disk. A raise in that window leaked the capture and left its
        # reader thread calling read() forever, so the NEXT session opened a second
        # capture on a device the first one still held. That degrades over
        # sessions, which is the shape F-08 kept presenting as and never
        # reproduced on demand.
        # `make_frame_source` has ALREADY opened a cv2.VideoCapture and started a
        # reader thread, but the try/finally that releases it does not begin until
        # the main loop below. Between the two sit a model load and a disk read
        # that can raise — FaceGate pulls in face recognition, and
        # gesture_calibration.load() touches a file. A raise anywhere in that
        # window leaked the capture and left its reader thread calling read()
        # forever, so the NEXT session opened a second capture on a device the
        # first one still held. That degrades across sessions rather than failing
        # once, which is the shape F-08 kept presenting as and was never
        # reproduced on demand.
        try:
            gesture_state["camera"] = str(fs.source)
            if len(sources) > 1:
                print(f"[GESTURE] camera auto-select: chose {fs.source} "
                      f"from {sources}", flush=True)

            landmarker = None
            try:
                import mediapipe as mp
                from mediapipe.tasks.python import BaseOptions, vision
                landmarker = vision.HandLandmarker.create_from_options(
                    vision.HandLandmarkerOptions(
                        base_options=BaseOptions(model_asset_path=MODEL_PATH),
                        running_mode=vision.RunningMode.VIDEO,
                        num_hands=1,
                        # G5.4: lower these (env) to lock onto a faint distant hand
                        min_hand_detection_confidence=_envf("JARVIS_HAND_DET_CONF", 0.5),
                        min_hand_presence_confidence=_envf("JARVIS_HAND_PRESENCE_CONF", 0.5),
                        min_tracking_confidence=_envf("JARVIS_HAND_TRACK_CONF", 0.5)))
                mp_image_fmt = mp.ImageFormat.SRGB
                mp_image = mp.Image
            except Exception as e:  # noqa: BLE001
                print(f"[GESTURE] hand model unavailable ({e}) — presence-only mode", flush=True)

            engine = GestureEngine(GestureConfig.from_env())
            pointer = PointerBackend()
            gate = FaceGate()
            motion = MotionDetector()
            absence = AbsenceTracker(absent_after_s=self.absent_after)
            # one off-axis check must not fire an intruder alert on the owner
            stranger = StrangerConfirmer(
                needed=_envf("JARVIS_STRANGER_CONFIRM", 3.0),
                window_s=_envf("JARVIS_STRANGER_WINDOW_S", 3.0))
            mirror = gesture_calibration.load().get(
                "mirror", os.getenv("JARVIS_CAM_MIRROR", "1") == "1")
            roi_enabled = os.getenv("JARVIS_GESTURE_ROI", "1") == "1"  # G5.4 distance ROI
            roi = gesture_roi.RoiTracker.from_env()
        except BaseException:
            # Release and re-raise: the retry shell still sees the real fault, but
            # it no longer inherits a camera nobody owns.
            print("[GESTURE] session setup failed — releasing the camera before "
                  "the retry, so the next attempt is not fighting this one.",
                  flush=True)
            try:
                fs.release()
            except Exception:  # noqa: BLE001
                pass
            raise

        seq = 0
        try:
            while self.running:
                # ---- state-tiered pacing (the optimisation core) ----
                if self._locked:
                    time.sleep(0.5)                    # ~2 fps
                elif gesture_arbiter.is_suspended() or not (engine.engaged and self.gestures_enabled):
                    time.sleep(0.11)                   # ~9 fps idle scan
                # ACTIVE: no sleep — read_new blocks on the camera (~30 fps)

                seq, frame = fs.read_new(seq, timeout=1.0)
                if frame is None:
                    engine.process(None, time.perf_counter())
                    continue
                # This daemon OWNS the camera; scan_for_faces and ambient_vision
                # read its frames off the bus instead of opening their own
                # capture, which used to kill this stream mid-session (an IP
                # Webcam MJPEG endpoint won't serve three readers). Published
                # PRE-mirror: that is the orientation those two consumers saw
                # from their own captures, so sharing changes contention only,
                # never what they recognise. face_gate below keeps using the
                # mirrored frame, as enroll_face does.
                frame_bus.publish(frame, source=str(fs.source))
                if mirror:
                    frame = cv2.flip(frame, 1)
                now = time.perf_counter()

                # ---- face + motion (cheap, cadenced) ----
                moving = motion.update(frame)
                face_every = 0.5 if self._locked else (1.5 if engine.engaged else 1.0)
                if now - self._last_face_t >= face_every and gate._ensure():
                    self._last_face_t = now
                    res = gate.check(frame)
                    # F-25: `check()` hands back the PREVIOUS verdict on a fault,
                    # so the only way to know this one is about now is that the
                    # gate's completed-pass counter moved.
                    if gate.checks_ok != self._last_checks_ok:
                        self._last_checks_ok = gate.checks_ok
                        self._last_verdict_t = now
                    if res.owner_present:
                        self._last_owner_t = now
                    # HUD shows the GRACE-smoothed owner, not the raw check:
                    # SFace drops the owner for a check whenever he looks away
                    # from the lens, and the tick flickering off is a lie while
                    # control is still (correctly) allowed.
                    gesture_state["owner"] = (
                        (now - self._last_owner_t) <= self.OWNER_GRACE_S)
                    # Track B: hand the desk verdict to presence_probe (push, not
                    # pull — that module must never import the camera stack) so
                    # owner_notify can route "he's right here" vs "he's home".
                    from modules import presence_probe
                    presence_probe.note_desk_presence(gesture_state["owner"])
                    if stranger.update(res.stranger_present, now,
                                       uncertain=res.uncertain):
                        gesture_state["stranger"] = True
                        if self._locked:
                            self._stranger_alert(
                                frame, "approached the desk while you were away")
                    else:
                        gesture_state["stranger"] = False
                res = gate.last
                owner_ok = (now - self._last_owner_t) <= self.OWNER_GRACE_S
                if gate.available is False:
                    owner_ok = True   # gate unenrolled/broken -> don't brick control

                # F-25. The trap was an asymmetry: arming needed only a camera
                # that was REACHABLE, clearing needed a RECOGNISED FACE. A camera
                # that is reachable but blind — lens covered, pointed at a wall,
                # stream frozen mid-decode — satisfies the first and can never
                # satisfy the second. That is not a narrow window; it is most of
                # the ways a camera fails. The desk locked, the monitor powered
                # off on top of the overlay, keys and clicks were swallowed, and
                # the owner got out by closing VS Code.
                #
                # So the two conditions read the same evidence now: a verdict
                # about the present moment.
                _gate_fresh = (now - self._last_verdict_t) <= self.GATE_STALE_S

                # ---- locked: the owner, or a gate that has stopped answering --
                if self._locked:
                    if owner_ok:
                        self._unlock()
                        absence.reset(now)
                    elif not _gate_fresh:
                        # The subsystem whose failure raised the barrier is no
                        # longer able to make the decision that lifts it. A
                        # barrier that cannot decide must not hold the desk: the
                        # cost of releasing is a machine left unlocked while
                        # nobody can see the room, and the cost of holding is the
                        # owner physically shut out of his own desk with the
                        # monitor dark. Those are not close.
                        print(f"[GESTURE] F-25: no usable face verdict for "
                              f"{now - self._last_verdict_t:.0f}s — releasing the "
                              f"soft lock rather than holding it on a blind camera.",
                              flush=True)
                        self._unlock()
                        absence.reset(now)
                    self._hud(engine, "locked")
                    continue

                # ---- absence -> soft lock ----
                # `_gate_fresh` as well as `gate.available`: see the note above.
                if self.auto_lock and gate.available and _gate_fresh and absence.update(
                        res.owner_present, res.any_face, moving, now):
                    if engine.engaged:
                        engine.process(None, now + 10.0)  # force-release drag state
                        engine.engaged = False
                    self._lock(pointer)
                    self._hud(engine, "locked", force=True)
                    continue

                # ---- hands ----
                suspended = gesture_arbiter.is_suspended()
                if landmarker is None or not self.gestures_enabled or suspended:
                    if suspended and engine.engaged:
                        # JARVIS's own GUI automation took the cursor — release
                        # any in-progress drag and disengage so the two never
                        # issue pointer input at the same time (G4 arbiter).
                        engine.process(None, now + 10.0)
                        engine.engaged = False
                        engine._reset_motion_state()
                        pointer.release_all()
                    if suspended:
                        state = "suspended"
                    elif not self.gestures_enabled:
                        state = "disabled"
                    else:
                        state = "idle"
                    self._hud(engine, state)
                    continue

                # G5.4 distance ROI: crop around the tracked hand (or, before a
                # hand is seen, around/below the owner's face) so a far hand fills
                # MediaPipe's ~192px model input. Landmarks are remapped back to
                # full-frame space so the cursor never jumps when the crop moves.
                fh_px, fw_px = frame.shape[0], frame.shape[1]
                crop = None
                if roi_enabled:
                    face_box_norm = None
                    if res.face_box is not None:
                        bx, by, bw, bh = res.face_box
                        face_box_norm = (bx / fw_px, by / fh_px, bw / fw_px, bh / fh_px)
                    crop = roi.next_crop(fw_px, fh_px, face_box_norm)

                if crop is not None:
                    rx, ry, rw, rh = crop
                    rgb = cv2.cvtColor(frame[ry:ry + rh, rx:rx + rw], cv2.COLOR_BGR2RGB)
                else:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                result = landmarker.detect_for_video(
                    mp_image(image_format=mp_image_fmt, data=rgb), int(now * 1000))
                if result.hand_landmarks:
                    raw = [(p.x, p.y, p.z) for p in result.hand_landmarks[0]]
                    pts = (gesture_roi.remap_landmarks(raw, crop, fw_px, fh_px)
                           if crop is not None else raw)
                    handedness = (result.handedness[0][0].category_name
                                  if result.handedness else "Right")
                    if roi_enabled:
                        roi.update(gesture_roi.hand_box(pts))
                else:
                    pts, handedness = None, "Right"
                    if roi_enabled:
                        roi.miss()

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
                    if pts is not None and stranger.confirmed:
                        self._stranger_alert(frame, "tried to use gesture control")
                    if time.monotonic() - self._last_toast_t > self.DENY_TOAST_S:
                        self._last_toast_t = time.monotonic()
                        from socket_manager import schedule_ui_update
                        schedule_ui_update({
                            "type": "gesture_state", **gesture_state,
                            "state": "denied", "denied": True})

                pointer.execute(intents)
                action = next((_ACTION_LABEL[i[0]] for i in intents
                               if i[0] in _ACTION_LABEL), None)
                if action:
                    gesture_state["last_action"] = action
                    gesture_state["last_action_ts"] = time.time()
                self._hud(engine, "active" if engine.engaged else "idle", denied,
                          force=bool(action))
        finally:
            try:
                pointer.release_all()
            except Exception:
                pass
            if landmarker is not None:
                landmarker.close()
            fs.release()
            # camera is gone — drop the shared frame so no reader treats the last
            # one as live and skips opening its own capture
            frame_bus.clear()


gesture_daemon = GestureDaemon()
