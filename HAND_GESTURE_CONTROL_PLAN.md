# JARVIS Hand-Gesture Mouse Control — Implementation Plan

> **Goal (Kaustav, 2026-07-11):** everything you do with a mouse should be doable
> with your hand in the air — **full hand control**: cursor movement, left/right
> click, double-click, drag & drop, scrolling — plus a safe way to engage/disengage
> so normal hand movement never hijacks the pointer.
>
> Status: **PLANNED — not started.** Start at Phase G1. Dependencies need Kaustav's
> install approval first (see "Dependencies").

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

### Phase G1 — feasibility spike (½ day)
- Install approved deps; standalone script: camera → MediaPipe → draw landmarks +
  move cursor with index finger. No clicks, no integration.
- **Gate: measure** fps, end-to-end latency, and CPU % on THIS machine
  (`model_complexity=0` vs `1`, 480p vs 720p). Proceed only if ≥ 20 fps at < 35 %
  of one core. Record numbers in this file.

### Phase G2 — gesture engine + pointer backend (1–2 days)
- `modules/gesture_engine.py`: state machine above; One-Euro filter; pinch
  hysteresis; palm-gate engage/disengage; hand-size normalisation.
- `modules/gesture_pointer.py`: ctypes SendInput backend (move absolute,
  button down/up, wheel). Multi-monitor + DPI aware — reuse the §4.8
  `SetProcessDpiAwareness` work so coordinates match physical pixels.
- **Harness `test_gesture_engine.py`**: recorded landmark sequences (JSON fixtures)
  → expected intent streams: tap vs hold, chatter rejection, deadzone, engage gate.
  No camera needed — same no-hardware pattern as the other harnesses.

### Phase G3 — JARVIS integration (1 day)
- `gesture_daemon.py` adopted by `DaemonSupervisor` in main.py's lifespan.
- Voice + fast-path commands: "hand control on/off" (deterministic fast-lane, no
  LLM); governance entry `gesture_control: AUTO` (reversible, low-risk).
- HUD status chip (engaged/disengaged) via `send_ui_update`.
- **Mutual exclusion:** auto-suspend gestures while `agentic_gui_task` /
  `ghost_type` / autopilot drive the GUI (two cursor owners = chaos), and resume
  after. Hook where the engine dispatches those actions.
- Camera sharing with ambient_vision resolved per §5 risk 1.

### Phase G4 — polish + hardening (1 day, after real use)
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
