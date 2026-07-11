# JARVIS Hand-Gesture Mouse Control — Implementation Plan

> **Goal (Kaustav, 2026-07-11):** everything you do with a mouse should be doable
> with your hand in the air — **full hand control**: cursor movement, left/right
> click, double-click, drag & drop, scrolling — plus a safe way to engage/disengage
> so normal hand movement never hijacks the pointer.
>
> Status (2026-07-11): **G1 ✅ PASSED LIVE** (30–40 fps via phone IP Webcam,
> commit e1cc385). Deps installed + pinned (protobuf 6.33.6 — do not bump).
> **G2 ✅ BUILT — harness 27/27, full pipeline 53 fps headless; awaiting
> Kaustav's live run** (gesture_spike.py is now the G2 driver: clicks, drag,
> scroll, palm gate). G1 carry-forwards done (FrameSource latest-frame thread,
> URL res hint, classified camera errors). **Resume at: live G2 gate, then G3.**

---

## 1. Technology choice (constraint: CPU-only box, no GPU)

**MediaPipe Hands** is the only serious option for this machine:
- 21 3-D hand landmarks per frame, purpose-built for real-time **CPU** inference
  (~25–30 fps at 640×480 on a modern CPU core — verified widely on similar hardware).
- Fully offline/local (privacy — camera frames never leave the machine).
- Free, mature, pip-installable, no model download step.

Rejected alternatives:
- **YOLO/DL keypoint models** — need a GPU for real-time; this box has none.
- **Vision-LLM per frame (llava/Gemini)** — hundreds of ms to seconds per frame;
  a cursor needs ≤ 33 ms/frame. LLMs have no role in the hot loop.
- **Leap Motion / depth cameras** — extra hardware; goal is webcam-only.

**Cursor injection:** `SendInput` via `ctypes` (the pure-ctypes pattern already used
for Core Audio in `os_agent.py`). NOT pyautogui in the hot loop — its per-call
overhead + failsafe checks are too slow for 30 Hz cursor updates. pyautogui stays
for everything JARVIS already uses it for.

**Dependencies to approve (dependency-averse rule — ask Kaustav before install):**
- `mediapipe` (pulls its own numpy/protobuf pins — check compat with the venv)
- `opencv-python` (camera capture + frame ops; mediapipe needs it anyway)
Both pinned in `requirements.txt` when approved, mirroring the pywinauto precedent.

---

## 2. Architecture

```
Webcam ─► Frame source (shared with ambient_vision — see §5 risk 1)
              │ 640×480 BGR @ ~30fps, dedicated thread
              ▼
        MediaPipe Hands (1 hand, model_complexity=0)
              │ 21 landmarks + handedness, per frame
              ▼
        Gesture State Machine  (modules/gesture_engine.py — pure logic, NO I/O,
              │                 unit-testable with recorded landmark sequences)
              │  states: IDLE → ENGAGED → {MOVE, PINCH_DOWN, DRAG, SCROLL}
              ▼
        Pointer Backend  (modules/gesture_pointer.py — ctypes SendInput:
              │           move/click/dblclick/rightclick/drag/scroll)
              ▼
        Windows cursor
```

- **`gesture_daemon.py`** (new module): owns the capture thread + the loop; runs as a
  daemon under the existing `DaemonSupervisor` (crash → auto-restart, cap → owner
  alert — Phase 4 infrastructure reused for free).
- **Engine/IO split** is deliberate: the state machine is pure
  `landmarks -> intents`, so the gesture vocabulary is testable in a harness with
  recorded sequences (same harness discipline as Phases 1–4), and the pointer
  backend is a thin, dumb executor.

---

## 3. Gesture vocabulary (v1)

| Intent | Gesture | Notes |
|---|---|---|
| **Engage / disengage** | Open palm held facing camera ~1 s | Toggle; also voice: "Jarvis, hand control on/off" and HUD indicator. Prevents accidental control — THE most important gesture. |
| **Move cursor** | Index fingertip position while ENGAGED | Relative ("air-trackpad") mapping with a One-Euro filter for smoothing (industry standard for cursor jitter — cheap, tunable). Small deadzone so a steady hand = steady cursor. |
| **Left click** | Thumb–index pinch, quick tap (< 250 ms) | Pinch distance normalised by hand size so it works at any distance from the camera. Hysteresis (down/up thresholds differ) so it can't chatter. |
| **Double-click** | Two pinch-taps within 400 ms | Emitted as a native double-click event. |
| **Drag & drop** | Pinch and HOLD (> 250 ms) → move → release | Mouse-down on hold, mouse-up on release. |
| **Right click** | Thumb–middle-finger pinch tap | Distinct finger pair = no confusion with left click. |
| **Scroll** | Index+middle extended ("two-finger"), move up/down | Vertical hand velocity → wheel ticks; natural trackpad feel. |
| **Precision mode** | Make a fist, reopen to index-point | Halves cursor gain for small targets (close buttons, text selection). |

Deferred to v2: zoom (two-hand pinch-spread), swipe app-switch, custom gesture →
JARVIS-action bindings (e.g. thumbs-up = confirm a pending CONFIRM action).

---

## 4. Phased build

### Phase G1 — feasibility spike (½ day) — ✅ BUILT 2026-07-11, awaiting Kaustav's live run
- **DONE:** deps installed with Kaustav's approval and pinned in requirements.txt:
  `mediapipe==0.10.35`, `opencv-contrib-python==5.0.0.93`, `sounddevice==0.5.5`,
  and **protobuf re-pinned 7.35.0 → 6.33.6** — the ONE version where tensorflow
  (DeepFace), mediapipe AND the Gemini SDK all work (5.x breaks tf/mediapipe
  gencode; verified empirically: tf 2.21 imports, Gemini live call OK,
  HandLandmarker initialises).
- **NOTE: mediapipe 0.10.35 removed the legacy `solutions` API** — this plan's
  code uses the Tasks API (`mediapipe.tasks.python.vision.HandLandmarker`).
  Model downloaded to `jarvis-backend/models/hand_landmarker.task` (7.8 MB).
- **DONE:** `jarvis-backend/gesture_spike.py` — camera → landmarker → cursor
  follows index fingertip (mirror-corrected, margin-mapped, EMA-smoothed,
  DPI-aware SetCursorPos). Move-only, no clicks. ESC quits. FPS overlay.
- **Benchmark (headless, no camera):** 30 synthetic 640×480 frames → **~90 fps**
  on this CPU — G1 gate (≥20 fps) passed with 4× headroom.
- ✅ **LIVE RUN PASSED (Kaustav, 2026-07-11 night):** camera = phone running
  **IP Webcam** at `http://192.168.0.105:8080/video` (no USB webcam on this PC —
  spike gained URL-source support: `gesture_spike.py <index|url>` or env
  `JARVIS_CAM`). Results: **30–40 fps** (gate: ≥20 ✅), **CPU ~10 % max**
  (gate: <35 % ✅), cursor tracks with **light lag** — attributed to the WiFi
  MJPEG stream, not the tracking. **G1 GATE: PASSED → G2 is a go.**
- G2 notes carried forward: (a) mitigate stream lag — request lower-res stream
  (`/video?640x480`), drop stale frames (read-latest thread), consider phone on
  5 GHz or USB tethering; (b) network gotcha hit during setup: the phone showed
  its MOBILE-DATA ip (192.0.0.x) first — IP Webcam must be on the SAME Wi-Fi as
  the PC (PC subnet 192.168.0.x); (c) improve the no-camera error message to
  distinguish "no device present" from "device busy".

### Phase G2 — gesture engine + pointer backend — ✅ BUILT 2026-07-11, awaiting live gate
- **DONE** `modules/gesture_engine.py`: pure state machine (no I/O, no mediapipe
  import) — One-Euro filter, margin map 0.15, deadzone 0.004, pinch hysteresis
  (down < 0.40 / up > 0.60 of hand size = wrist→middle-MCP) + 2-frame debounce,
  palm-facing gate (1 s hold toggles; `palm_sign` calibratable — mirroring flips
  it), tap < 250 ms = click, 2nd tap < 400 ms = double, hold > 250 ms = drag,
  thumb–middle = right click, index+middle = scroll (hand up = scroll up),
  cursor freezes during a pending tap AND during the open-palm gate pose,
  lost tracking → drag released 0.2 s / disengage 2 s. Emits intent tuples.
- **DONE** `modules/gesture_pointer.py`: ctypes SendInput,
  `MOUSEEVENTF_ABSOLUTE|VIRTUALDESK` (multi-monitor), DPI-aware (§4.8 pattern),
  `send_fn` injectable for tests, `release_all()` so no button sticks on exit.
- **DONE** `modules/gesture_camera.py` (G1 carry-forwards): `FrameSource`
  latest-frame reader thread (consumer always gets the newest frame — kills the
  WiFi MJPEG lag), `decorate_url` appends `?640x480` to IP-Webcam /video URLs
  (env `JARVIS_CAM_RES`), `CameraError` kinds absent/busy/stream/dead with the
  same-Wi-Fi + mobile-data-IP-trap hints baked into the messages.
- **DONE** harness `test_gesture_engine.py`: **27/27** — synthetic 21-landmark
  hands (no camera): engage gate + wave/back-of-hand rejection, deadzone,
  tap/double/chatter, drag + loss-releases-drag, scroll, pointer flags, URL
  helper. Plain-python runner (no pytest in venv — project convention).
- **DONE** `gesture_spike.py` rewritten as the G2 live driver: engine + pointer
  end to end, state overlay, event log, env `JARVIS_PALM_FACING`/`JARVIS_PALM_SIGN`
  to calibrate the facing check live. Control starts OFF (palm gate is the safety).
- **Bench:** full pipeline (landmarker + engine + pointer) **53 fps** headless
  on 640×480 — 2.6× the 20 fps gate.
- **G1 live finding (Kaustav): cursor left/right INVERTED, up/down fine** —
  double-mirror: the spike flips the frame assuming a raw webcam, but IP Webcam
  streams often arrive pre-mirrored (front cam/app mirror setting). Fixed:
  mirror is now toggleable — press **m** in the preview window, or set
  `JARVIS_CAM_MIRROR=0`. The palm-facing check is stable under the toggle
  (cross-product and handedness label flip together).
- **⏳ LIVE GATE (Kaustav):** `venv\Scripts\python.exe gesture_spike.py http://192.168.0.105:8080/video`
  — if left/right is inverted press **m** once; engage with palm, click a
  taskbar icon, drag a file, right-click, scroll; casual waving must never
  engage. If the palm gate won't fire → set `JARVIS_PALM_FACING=0`
  (then report — palm_sign needs flipping instead).

### Phase G3 — JARVIS integration (1 day) — ⏳ NOT STARTED
- `gesture_daemon.py` adopted by `DaemonSupervisor` in main.py's lifespan.
- Voice + fast-path commands: "hand control on/off" (deterministic fast-lane, no
  LLM); governance entry `gesture_control: AUTO` (reversible, low-risk).
- HUD status chip (engaged/disengaged) via `send_ui_update`.
- **Mutual exclusion:** auto-suspend gestures while `agentic_gui_task` /
  `ghost_type` / autopilot drive the GUI (two cursor owners = chaos), and resume
  after. Hook where the engine dispatches those actions.
- Camera sharing with ambient_vision resolved per §5 risk 1.

### Phase G4 — polish + hardening (1 day, after real use) — ⏳ NOT STARTED
- Calibration routine ("hold your hand comfortably, pinch twice") → per-user
  thresholds in a config JSON.
- Precision mode, edge-of-frame handling, lost-tracking grace (~200 ms before
  disengage so a dropped frame doesn't release a drag).
- Low-light behaviour check; optional exposure bump via OpenCV.
- Fatigue ergonomics: relative mapping + clutch (disengage, reposition hand,
  re-engage — like lifting a mouse) rather than absolute arm-in-the-air mapping.

---

## 5. Risks & mitigations

1. **Camera contention** — `ambient_vision` (face/intruder) already owns the webcam.
   Two `cv2.VideoCapture` handles on one device fail on Windows. Options, in order
   of preference: (a) single shared capture thread publishing frames to both
   consumers (ambient_vision reads 1 fps, gestures read every frame); (b) exclusive
   handoff — "hand control on" pauses ambient vision, off resumes it (simpler, lose
   intruder detection only while actively gesturing). **Decide in G3; (b) is the
   safe first ship.**
2. **CPU budget** — MediaPipe ≈ 25–35 % of one core at 30 fps on CPU. This box also
   runs STT/TTS. Mitigation: 20 fps cap (still fine for cursor), `model_complexity=0`,
   process frames at 480p, and the daemon only runs while engaged.
3. **Jitter on small targets** — One-Euro filter + deadzone + precision mode; if still
   inadequate, add "magnetic" slow-zone near the cursor's last rest position.
4. **False engagement** (hand waving in conversation) — palm-facing-camera gate +
   1 s hold + on-screen/HUD confirmation before control starts.
5. **Numpy/protobuf version conflicts** from mediapipe in the existing venv —
   check `pip check` after install; worst case pin an older mediapipe.

---

## 6. Acceptance tests (definition of "full hand control")

- [ ] Open Chrome from the taskbar, click a link, scroll the page — hands only.
- [ ] Drag a file from Desktop into a folder — hands only.
- [ ] Right-click → context menu → pick an item — hands only.
- [ ] Double-click to open a file — hands only.
- [ ] Select a word in a text editor via precision mode.
- [ ] Walk across the room talking with hands — cursor never moves (gate holds).
- [ ] "Jarvis, hand control off" → gestures dead instantly; "on" → back in < 2 s.
- [ ] Gesture daemon crash → supervisor restarts it; owner notified if it caps out.
