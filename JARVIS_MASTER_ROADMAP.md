# JARVIS — Master Roadmap

> **Single source of truth.** Read this first when resuming. It replaces and folds in
> the old `ROADMAP_TO_FULL_JARVIS.md`, `RELIABILITY_HARDENING.md`,
> `HAND_GESTURE_CONTROL_PLAN.md`, `UPGRADES_AND_FLUIDITY.md`, `MOBILE_PRESENCE_PLAN.md`,
> and `LOGIN_REVAMP_PLAN.md` (all deleted 2026-07-19). The **test plan lives
> separately** in `TEST_PLAN.md`. Last updated 2026-07-26.
>
> **Working branch:** `feat/cloud-gateway`, fully pushed as of 2026-07-26 (see §7).
> **Deep code detail:** query the `codebase-memory-mcp` graph, don't re-read whole files.

---

## 1. North Star

JARVIS should be the film's assistant, defined by four qualities (not a feature list):
**Always present · Truly agentic · Naturally conversational · Genuinely yours.**
*"I am here, Sir. I will always be here."*

Concrete end-state: JARVIS does **anything on the PC on command, flawlessly**, runs
**autonomously** (auto-fix safe/reversible, ask before risky), reaches Kaustav
**anywhere** (desk HUD when present, phone when away, cloud brain when the PC is off),
is **controllable by voice and by hand in the air**, and finally ships as a single
**Electron .exe** (notch idle-chat → fullscreen takeover overlay), then a **mobile app**.

**Order of the big milestones (agreed):** finish ALL desktop work → full `TEST_PLAN.md`
pass → **Electron packaging** → **mobile app**. Nothing jumps that queue.

---

## 2. Status at a glance

| Area | Status | Ref |
|---|---|---|
| **Reliability Phases 1–5** (parse spine, honest failure, launch/type/save, GUI backend, autonomy reach, LLM cascade) | ✅ DONE + pushed (1–3.5) / committed (4–5) | §3.1 |
| **Gesture G1–G3** (spike, engine+pointer, daemon+face-gate+away-lock) | ✅ DONE | §3.2 |
| **Gesture G4** (cursor arbiter, guided enroll, calibration JSON, HUD chip) | ✅ code done, live-gate owed | §3.2 |
| **G5.0** crash/resilience (9 items) | ✅ DONE | §3.3 |
| **G5.6** gesture vocab decision | ✅ DONE | §4 |
| **G5.1** relative trackpad + accel + clutch + dwell right-click | ✅ DONE, live-gate owed | §3.3, §4 |
| **G5.2** calibration wizard | ✅ DONE, live-gate owed | §3.3 |
| **Automatic test baseline** | ✅ **522 checks, 0 failures** (`test_screen_reader.py` is a live VLM script, not counted) | `TEST_PLAN.md` |
| **G6.2/G6.3/G6.4 + camera unification + frame bus + overlay hardening + stranger debounce** | ✅ DONE + pushed (`90a9bc9`) | §6.3–§6.5 |
| **G5.7** mic/voice affordance (visible) | ✅ DONE (`3d3063d`); voice click-to-talk = follow-up | §5 |
| **G5.3** cursor-halo + edge-toast overlays | ✅ code done, live-gate owed | §5 |
| **G5.4** distance mitigation | ✅ code done, live-gate owed | §5 |
| **G5.5** precision / dual-target filtering | ✅ code done, live-gate owed | §5 |
| **G5.7** robustness backlog | 🟡 backend 5/6 done (barge-in deferred — live audio); **frontend ALL DONE** (`0b5a0a4`) | §5 |
| **Login/wake revamp (§6.1)** | ✅ COMPLETE (code) — contract, FaceAuthOverlay + **live feed + real matching phase** (`8ae9cc0`), BootSequence + wipe, IdentityPrompt; live-gate owed | §6.1 |
| **Away→mobile presence (Track B probe)** | ✅ DONE (code) — ARP/TCP/ICMP ladder + asymmetric debounce + owner_notify routing; live-gate owed | §5 #7, §6.2 |
| **Smart-home / IoT agent** | ⬜ MISSING | §5 |
| **Guarded self-improvement loop** | ⬜ MISSING | §5 |
| **Agentic core (Claude-Code-grade tool loop)** | ⬜ MISSING — foundational for self-improvement | §5 (Tier C #12) |
| **Presence/context state machine** | 🟡 partial | §5 |
| **Electron packaging** | ⬜ PARKED (after all desktop) | §5 |
| **Mobile app (Track C)** | ⬜ FUTURE (after Electron) | §6 |

---

## 3. What's DONE

### 3.1 Reliability Phases 1–5 (the "make it work every time" effort)
Root problem was reliability, not missing features (~90% was already built). Fixed in phases:
- **Phase 1** (`2493a84`, pushed) — unified LLM-reply→action parse spine
  (`modules/action_parser.py`), temp 0.0 on action turns, honest failure (no fake
  "Done, Sir" on outage). Harness `test_action_parser.py` 24/24.
- **Phase 2** (`ea7e92c`, pushed) — stop narrating false success; `_is_failure`
  context-aware. `test_failure_detection.py` 17/17.
- **Phase 3 / 3.5** (`02749a7`, pushed) — launch→type→save chain (window tracking,
  focus gate); **root-cause fix: `pywinauto` was never installed** so GUI typing was
  silently dead → installed+pinned `pywinauto==0.6.9`+`comtypes==1.4.16`; UIA
  ValuePattern primary typing path; UTF-8 stdout hardening; DPI/multi-monitor;
  web-app-substring launch hijack fixed; Playwright hardening; ghost_save locale/
  clipboard robustness.
- **Phase 4** (`6438202`, pushed) — autonomy reaches the phone: `owner_notify.py`
  fan-out (desk HUD + TTS + phone via Telegram bot OR cloud bridge), standby still
  runs safety checks, remote CONFIRM handshake (cid-scoped), `approve task <id>`,
  dead-letter after 3 attempts, cloud wedged-desk watchdog. `test_owner_notify.py` 20/20.
- **Phase 5** (`d8cace0`, pushed) — free-tier LLM cascade groq→gemini→openrouter→ollama
  + Gemini free vision (llava offline fallback). Keys in `.env` only.
- **Token trim** (`f104450`) — history window cap + empty-context-block cut.

### 3.2 Gesture control G1–G4
- **G1** (`e1cc385`) spike — live-passed via phone IP Webcam, ~30–40 fps, CPU ~10%.
- **G2** (`0ba2c5b`) — pure `gesture_engine.py` + ctypes `gesture_pointer.py` +
  `gesture_camera.py` latest-frame reader.
- **G3** (`2cf46f2`/`e25fc1b`/`87d2094` + fixes) — daemon under DaemonSupervisor;
  `modules/face_gate.py` (YuNet+SFace ONNX) owner gate on gesture frames; away
  soft-lock (`lock_overlay.py`, 6 s no-face+no-motion → all-monitor lock + screen off);
  tiered loop LOCKED 2 / IDLE 9 / ACTIVE 30 fps; HUD GESTURES button + live practice.
- **G4** (`cc27156`, **unpushed**) — cursor arbiter (`gesture_arbiter.py`,
  auto-suspends gestures during ghost_type/autopilot), guided 12-sample enroll
  (`enroll_face.py`), calibration JSON persistence (`gesture_calibration.py`),
  `GestureChip.jsx`. **Live camera gate owed** (see §7).

### 3.3 Phase G5 — smoothness / UX / robustness (in progress)
- **G5.0** all 9 crash/resilience fixes (`4a2945b`/`b1771ff`/`31afe0d`/`54f47a0`):
  briefing NameError crash; event-loop-blocking handlers → `to_thread`; WS fault
  handler UTF-8 + no teardown; one shared `DATA_ACTIONS`; frontend WS auto-reconnect;
  guarded `JSON.parse`; gesture-chip staleness (+ daemon 2 s heartbeat); `VITE_API_BASE`;
  off-screen widget clamp.
- **G5.1** (`644a750`) relative trackpad mapping + acceleration curve + first-class
  clutch + dwell right-click (thumb+middle retired); pointer `move_rel`.
  `test_gesture_engine.py` 49/49. Gated by `JARVIS_GESTURE_RELATIVE` until live gate.
- **G5.2** (`3b9a68a`) calibration wizard `calibrate_gesture.py` — measures palm_sign /
  pinch thresholds / reach instead of guessing. `test_calibrate_gesture.py` 14/14.

### 3.4 Pre-existing shipped base (from the roadmap sweep — don't rebuild)
Multi-session concurrency (`session_manager.py`); unkillable watchdog + daemon
supervisor; Telegram gateway; overnight worker + durable task queue; LLM
self-correction (`replan_after_failure`, bounded 3, AUTO-only); ReAct planner;
full-duplex voice (streaming STT vosk, AEC ~27 dB, `audio_pipeline.py`); deterministic
fast-lane (`fast_path.py`); emotion-driven prosody + Sass Index + per-user persona;
personal-doc RAG (Chroma, `search_documents`); Figma→code autopilot (`agent_worker.py`,
LangGraph self-heal); 4-tier memory; ~67 action types under 94-rule AUTO/CONFIRM/BLOCK
governance; ambient YOLO+DeepFace vision; proactive/Overwatch/Schedule daemons;
Playwright browse + morning briefing; TV(ADB)/Gmail/Calendar/Health/GitHub/OS-macros/
code-workspace agents; React HUD (21 components); cloud gateway (always-on Telegram
brain on Render, answers when PC off; Tavily live-info).

### 3.5 Login flow — already implemented (per code; §6 revamps only the VISUALS)
- **Kaustav** → face auth (`vision.scan_for_faces`, `main.py:2142+`).
- **Kinshuk** → voice passkey (relation "brother" → passkey "brotherhood",
  `main.py:2254-2293`) + face path + "brother" persona (`brain.py:1101-1106`).
- **Mousumi** → V.I.P. cinematic ceremony (`IntroductionCeremony.jsx`, `main.py:2177-2216`)
  + Madam persona (`brain.py:1093-1100`).
- **Backdoor** → `/api/backdoor` bypasses auth for testing (`main.py:1590-1609`).

---

## 4. Current gesture vocabulary (G5.1 — supersedes all older tables)

| Gesture | Action |
|---|---|
| index finger up · hold 1 s | START control (from idle) |
| open palm (facing camera) | MOVE cursor — palm-knuckle centroid. Two modes: **absolute** (position→screen band, default) or **relative** (trackpad delta + acceleration; `JARVIS_GESTURE_RELATIVE=1`) |
| thumb+index QUICK pinch | LEFT CLICK (on release) |
| 2nd quick tap ≤1 s, same spot | DOUBLE CLICK |
| thumb+index HOLD ≥0.5 s | RIGHT CLICK (dwell, on release) — *replaced the retired thumb+middle* |
| closed fist | GRAB — down · move · open = drop |
| index+middle vertical | SCROLL (hand up = scroll up) |
| back of open hand (brief) | CLUTCH — freeze + reposition, no jump on re-engage (relative mode) |
| back of open hand ≥1.5 s | STOP control |

Config resolution everywhere: **defaults < `models/gesture_calibration.json` < `JARVIS_*` env**.

---

## 5. What's LEFT — the roadmap (follow this order)

### TIER A — desktop G5 finish (buildable now, no hardware to *build*, live-gate after)
1. ✅ **G5.7 mic / voice affordance** — DONE (`3d3063d`). `MicIndicator.jsx` in the
   command terminal mirrors real voice state (READY/LISTENING/THINKING/SPEAKING/OFF),
   click focuses the command line. **Follow-up:** true voice click-to-talk needs the
   backend to READ client WS messages — the `/ws` voice loop blocks on the server-side
   mic and never consumes `START_LISTENING`; add a bidirectional trigger (or a
   `POST /api/listen`) later. Pairs with the login IdentityPrompt.
   ⬜ **STILL OPEN — the only unbuilt Tier A item besides barge-in.** Shape: a
   `POST /api/listen` that sets a flag the `/ws` voice loop checks between blocking mic
   reads (it cannot be awaited *inside* `wait_for_wake_word`), or a real bidirectional WS
   read leg. Buildable without hardware; needs a live mic to gate.
2. ✅ **G5.3 overlays** — DONE (code). `cursor_overlay.py` = separate always-on-top
   **click-through** process (WS_EX_TRANSPARENT + WS_EX_NOACTIVATE — the gesture cursor
   still clicks the app beneath; the overlay only draws). Cursor halo recolours by pose
   (palm=cyan move · fist=amber grab · two_finger=cyan scroll · back_palm=dim clutch) +
   edge toasts on transitions (HAND READY / JARVIS DRIVING / YOU HAVE CONTROL /
   UNAUTHORIZED / CONTROL OFF). `gesture_daemon` spawns it (`JARVIS_GESTURE_OVERLAY=1`
   default, win32-only), streams state frames to its stdin each `_hud`, kills on stop;
   overlay polls its own cursor pos (~60fps) so smoothness is IPC-independent, exits on
   stdin EOF like `lock_overlay.py`. Live-gate owed (see §7). ✅ **Follow-up CLOSED by G6.2** — the click-flash needed the
   state to carry the click *event* (a pinch is an intent, not a `pose`), which
   `gesture_state["last_action"]` now does, so `cursor_overlay` draws an expanding ripple
   per action (cyan click · purple right-click · amber grab). Biggest *felt* gesture jump.
   🚨 **BLACK-DESKTOP INCIDENT + HARDENING 2026-07-25.** Kaustav's whole screen went dark
   and could not be dismissed; it only came back when he Alt+F4'd the terminal running the
   backend. Diagnosis: this overlay. Three properties combined into a desk-killer — ONE
   window spanning the entire virtual desktop, filled near-black (`BG=#010203`) and made
   invisible *purely* by a colour-key; `WS_EX_NOACTIVATE`, so Alt+F4 can never target it
   (it goes to whatever *is* focused) and `overrideredirect`+`WS_EX_TOOLWINDOW` keep it out
   of the taskbar and Alt-Tab; and `keep_topmost()` re-lifting every second. When the
   colour-key silently failed, that was an opaque black screen with no dismiss path — dying
   only on stdin EOF, i.e. when the parent backend was killed. Not the G3 soft-lock: that
   overlay calls `focus_force()`, so the first Alt+F4 would have closed it, and
   monitor-power-off is undone by any keypress. THREE INDEPENDENT GUARDS now: (1) **no
   fullscreen window** — halo (~72px, follows the cursor), ripple (~132px, pinned at the
   action point) and toast (sized to its text) each get their own small window, so a
   transparency failure is a floating square, not a dead desktop; `box_place` clamps the
   window to the desktop while moving the *drawing* within it, so the ring still sits
   exactly on the cursor at screen edges. (2) **the colour-key is verified, not assumed** —
   we call `SetLayeredWindowAttributes` ourselves *after* the exstyle change (per Win32
   docs, setting `WS_EX_LAYERED` via `SetWindowLong` invalidates layered attributes already
   applied, so Tk's `-transparentcolor` can't be trusted to survive `_make_click_through` —
   the suspected root cause) and check the return; can't confirm ⇒ the process refuses to
   draw. (3) **deadman** — exits if no state frame arrives for `JARVIS_OVERLAY_DEADMAN_S`
   (default 20s ≈ 10 missed 2s heartbeats), not just on EOF; `_ensure_cursor_overlay`
   respawns it. Halo/toast/ripple logic pulled out of Tk methods into pure functions →
   NEW `test_cursor_overlay.py` **61 checks**, suite 366→**427**. Live smoke re-verified
   (frames→EOF exit 0 with the key confirmed; quiet-pipe→deadman exit). The deadman path
   needs `os._exit` — the reader thread is still blocked in `readline()` on a live pipe, and
   a normal shutdown dies with `_enter_buffered_busy: could not acquire lock for <stdin>`
   (0xC0000005). **LIVE-GATE OWED (raised priority):** confirm the halo is actually
   invisible-except-the-ring, still click-through, and that `JARVIS_GESTURE_OVERLAY=0`
   remains the kill switch.
3. ✅ **G5.4 distance mitigation** — DONE (code). Key insight: MediaPipe downscales
   any input to its ~192px model, so a distant hand in a full frame is lost regardless
   of capture res — **cropping the hand ROI** (not upscaling) makes it fill the model
   input. NEW pure `modules/gesture_roi.py` (`RoiTracker` + `hand_box`/`expand_box`/
   `to_px`/`remap_landmarks`/`face_anchored_box`): follows the tracked hand, crops around
   it (seeds from `face_gate`'s new `GateResult.face_box` before a hand is seen), and
   **remaps landmarks crop→full-frame** so the cursor never jumps when the crop moves.
   Self-adaptive — a near hand's box clamps to ≈full frame (no zoom), a far hand tightens
   to `min_frac`, so it's safe defaulted on. Daemon wiring: `JARVIS_CAM_RES=1280x720`
   capture, MediaPipe confidence floors (`JARVIS_HAND_{DET,PRESENCE,TRACK}_CONF`, lower =
   farther reach), ROI crop/remap in the hot loop gated by `JARVIS_GESTURE_ROI=1`.
   `test_gesture_roi.py` 29/29 (incl. the no-jump crop-invariance proof); adjacent
   harnesses green (baseline 205 → **234**). Live-gate owed (see §7).
4. ✅ **G5.5 precision / dual-target filtering** — DONE (code). A velocity-gated
   *precision gain* (`GestureEngine._precision_gain`): below `precision_v_lo` palm speed
   the cursor is clamped to `precision_gain` (0.35), above `precision_v_hi` no damping,
   linear between — a second stage on top of the relative accel curve, and the **only**
   precision lever absolute mode has. Applies to both modes; velocity measured in
   palm-centroid frame-units/s (same units as accel) so one threshold set covers both.
   Absolute mode EASES toward the target with the deadzone tested on the RAW target, so
   it converges to the **same landing point** as precision-off (no settling bias) — just
   a gentler, tremor-proof approach. Env `JARVIS_GESTURE_PRECISION=0` disables,
   `JARVIS_PRECISION_GAIN` tunes the floor; thresholds are calibration-JSON fields.
   `test_gesture_engine.py` +5 → 54/54 (ramp, disabled=unity, env toggle, slow-drift
   dampening, target-unbiased). Full suite 234 → **239**. Live-gate owed (see §7).
5. **G5.7 robustness backlog:**
   - Backend — ✅ **5 of 6 DONE** (code, harnessed):
     - ✅ `_call_ollama` empty-200 now RAISES (both stream + non-stream) so the cascade
       escalates to cloud instead of returning `""` (a silent false success).
     - ✅ boot config preflight — NEW `modules/boot_preflight.py` (pure, injectable),
       logged once at the top of `main.py` `lifespan`: required LLM key + model files vs
       recommended keys/files, `ok` flag; never blocks boot.
     - ✅ `working_memory` cross-thread lock — `memory._wm_lock` (RLock) guards every
       mutate/read; getters return COPIES; the LLM summarize runs OUTSIDE the lock.
     - ✅ `speak_text` TTS errors logged + swallowed (no turn crash, no unhandled-task
       vanish).
     - ✅ `watchdog.py` give-up — pure `RespawnPolicy` (harnessable) stops respawning
       after `WATCHDOG_MAX_GIVEUP_CYCLES` rapid-crash cycles + `_notify_owner_down`
       (stdlib-urllib Telegram, works even when the app won't start); a healthy run
       resets the strike count.
     - ⬜ **barge-in thread/stream leak on interrupt** — DEFERRED (needs live audio
       threads/device to exercise; not safely harnessable headless).
     - Harnesses: `test_llm_failover` 7, `test_watchdog_policy` 9, `test_boot_preflight`
       14, `test_working_memory_lock` 4, `test_speaker_errors` 5. Suite 239 → **278**.
   - Frontend — ✅ **ALL DONE 2026-07-24/25** (`0b5a0a4`, browser-gated against a mock-WS
     backend): `DataOverlay` Escape + focus management (capture-phase window keydown,
     auto-focus close, restore prior focus); `CalculatorWidget` `eval()` → `safeEvaluate()`
     (tokenizer + shunting-yard, rejects anything but `+ - * / %`, decimals, parens, unary
     ±; bundle 1084→1010kB); command-terminal error surfacing (`surfaceTerminalError` pins
     the failure in the system log 6s, failed command stays in the input);
     connection-based boot log = `BootSequence.jsx` (§6.1, gated to the real backend, no
     timers); `BrowserWidget` framing fallback = an always-present **open-externally**
     button + an `onError` panel. ⚠️ Auto-detecting `X-Frame-Options`/CSP frame blocks is
     **NOT achievable client-side** — modern Chrome fires `onLoad` and renders an
     inspection-proof error page (`contentDocument` throws exactly like a real
     cross-origin load); the about:blank heuristic + load-watchdog were tried and REMOVED
     as false-positive-prone. The external button is the answer, not detection.

### TIER B — experience upgrades (after Tier A)
6. **Login / wake revamp** (spec in §6) — staged boot, identity step, believable
   Kaustav face-auth.
6a. ✅ **G6.2 gesture click reliability** (DONE code 2026-07-19) — spec §6.3. Root cause:
    pinch detector overlapped the fist zone (`pinch_down=0.40`) so grabs-with-thumb-near-index
    read as long pinches → right_click, and slow taps crossed the 0.5s dwell → right_click
    (left/double never fired). Fix: pinch_down 0.40→0.30 (pinch=real touch, closed hand=grab),
    dwell 0.5→1.5s, don't abort an active pinch on a transient fist misread, post-pinch grab
    cooldown; all env+calibration tunable; click/grab action pulse on HUD+overlay for live
    diagnosis. `test_gesture_engine` 54→58, suite 315→**325**.
    ✅ **LIVE-GATED 2026-07-25** (phone IP Webcam 192.168.0.105:8080, `sensitivity=3.0`,
    `mirror=false`, `pinch_down=0.30`, absolute mapping): 48 events — left clicks measured
    **0.23–1.27s**, right clicks **1.54–11.14s**, so `dwell=1.5` sits inside the gap with zero
    misclassifications; 4 clean drag_start/drag_end pairs, double-click at 0.23s, 2 engage/
    disengage cycles, no click→drag bleed. **The first attempt at this gate silently ran the
    OLD bug:** `models/gesture_calibration.json` held `dwell_right_click_s: 0.75` from a
    pre-fix save, and resolution order is defaults < JSON < env, so the stale JSON shadowed
    the corrected 1.5 default — 16 of 33 left clicks fired as right clicks. Stale key dropped;
    `gesture_spike.py` now prints the RESOLVED click knobs at startup so shadowing can't hide
    again. **Never lower dwell below 1.4s on this rig** (see the comment at
    `modules/gesture_engine.py:198`). Also fixed alongside: the G5.5 precision knobs
    (`precision`, `precision_gain`, `precision_v_lo/hi`) were missing from
    `gesture_calibration.SCHEMA`, so `w` silently dropped any live precision tuning —
    added + round-tripped (`test_gesture_calibration` 37→**45**, suite **351**).
6b. ✅ **G6.3 camera source auto-select** (DONE code 2026-07-19) — spec §6.4. `JARVIS_CAM_SOURCES`
    comma-list probed in priority order (fast TCP reachability skips a dead URL before cv2 can
    hang), first that opens + delivers a frame wins; legacy single `JARVIS_CAM` fallback.
    `test_gesture_camera.py` 28/28. `.env` `JARVIS_CAM_SOURCES` set. **USB DROPPED 2026-07-19
    (Kaustav's call — WiFi only):** HyperOS never authorised adb (ADB Interface bound OK but
    `adb devices` empty — MIUI security-settings toggle / RSA prompt / charge-only mode). Not chasing.
    ✅ **EXTENDED 2026-07-25 — one camera list for the WHOLE stack.** G6.3 only rewired the
    gesture daemon; `ambient_vision.py` and `vision.scan_for_faces` still carried their own
    hardcoded `192.168.0.106:8080`, a third phone IP that had gone stale — so every face scan
    bailed with "Camera unreachable" and ambient vision saw nothing *while the gesture daemon
    streamed fine from a different address*. Both now resolve off `JARVIS_CAM_SOURCES`:
    `scan_for_faces` calls `open_first_available` (keeps the old fast-fail property, adds frame
    validation, replaces the hand-rolled urllib ping); `ambient_vision` parses the list by hand
    (it must stay import-light — no cv2), taking the first URL and skipping device indices.
    `JARVIS_CAMERA_URL` still pins ambient vision if set. New `test_ambient_camera.py` 15/15,
    suite **351→366**. `.env` list reordered `.105` first (the G6.2 gate address) so the common
    case pays zero probe delay.
7. ✅ **Away→mobile presence Track B — DONE (code, 2026-07-25, `a491de8`)** — spec §6.2. NEW
   pure `modules/presence_probe.py`: detection ladder **ARP (after a priming ping) → TCP
   connect to a phone port → ICMP**, ARP primary because a phone answers ARP with every app
   closed and a **MAC match survives a DHCP move** (`JARVIS_PHONE_MAC` is the signal worth
   pinning; `JARVIS_PHONE_IP` alone still works). `PresenceDebounce` is deliberately
   **asymmetric** — any hit ⇒ HOME instantly, AWAY only after an unbroken
   `JARVIS_PRESENCE_AWAY_GRACE`(180s) miss streak — because phones sleep their WiFi radio and
   the inverted version would announce "you left" while he's reading. `fuse()` lets the
   **face gate outrank the LAN** (on camera = AT_DESK whatever the phone is doing) and
   `routing()` maps presence → legs: AT_DESK desk-only, HOME desk+phone, AWAY phone-only.
   ⚠️ UNKNOWN (unconfigured / not yet probed) routes **everywhere** — silence is the one
   failure mode an alert path must not have, so the pre-Track-B behaviour is the fallback.
   Wiring: `owner_notify.notify_owner(speak=None, phone=None)` resolves the legs from
   presence (an explicit True/False from the caller still wins, so existing callers are
   untouched — `test_owner_notify` 20/20 unchanged); `gesture_daemon` PUSHES the desk verdict
   via `note_desk_presence()` each face check (push, not pull — `presence_probe` must never
   import the camera stack) with a 10s freshness bound so a dead daemon can't look like a
   seated owner; `PresenceMonitor` thread started in the lifespan (self-disables with no
   IP/MAC), `GET /api/presence/state` exposes the fused verdict + **which rung carried it**
   (`arp:mac`/`tcp:8080`/`icmp` — the difference between "presence works" and "presence works
   by luck"). `ping_succeeded()` reads the ping TEXT, not the exit code: Windows exits 0 for
   "Destination host unreachable". Probe faults (missing `arp` binary) degrade to a miss,
   never crash the thread. `test_presence_probe.py` **25**; suite 497 → **522**.
   **Live-gate owed** (§7).

### TIER C — bigger capability gaps (from the full-JARVIS roadmap)
8. **Smart-home / IoT agent** — `home_agent` over Home Assistant / MQTT / Matter;
   governance **lights=AUTO, locks/security=CONFIRM**.
9. **Presence/context state machine** — real working/relaxing/away/asleep states
   (Wi-Fi/BLE/geofence), superseding today's `is_focus_mode` boolean. (Track B feeds this.)
10. **Guarded self-improvement loop** — propose→branch→test→PR→human-approve.
    **NEVER auto-merge.** (Primitives `workspace_write/patch` + `github_*` exist.)
11. **Polish tier:** on-device AEC finishing; voice biometrics (face-only today);
    generative HUD / brain-driven data-viz; memory-at-rest encryption (encrypt
    `jarvis_*.db`/Chroma, secrets in a vault not `.env`); planner cost/risk budgeting +
    hierarchical sub-plans; richer cross-task worker memory; per-session state for the
    pending-decision singletons; Telegram push + remote file search; PDF/docx RAG parsing
    + incremental re-index; life integrations (Spotify, Notion/Obsidian, banking, maps).
12. **Agentic core — "Claude-Code-grade" tool loop for JARVIS (NEW 2026-07-25).**
    Goal: give JARVIS the same agentic superpowers this Claude Code session has —
    decide → call tool → observe → repeat, with sub-agents, MCP, and skills — not the
    current one-shot `process_command`. JARVIS already has the *pieces* scattered
    (tools: `gui_agent`/`human_gui_agent` computer-use, `action_engine`, browser; memory:
    `jarvis_longterm.db`/Chroma/`memory_manager`; governance). The MISSING thing is the
    **loop that wires them into a real tool-calling agent.**
    - ⚠️ **PROVIDER DECISION 2026-07-25 (Kaustav): build it on the EXISTING FREE GROQ
      CASCADE, not the Claude Agent SDK** (Anthropic API is paid, no free tier). The SDK is
      convenience, not capability — the loop itself is ~200 lines and Groq speaks
      OpenAI-style function calling, so it is provider-agnostic by construction and a paid
      key later becomes a one-line provider switch, not a rewrite. What we hand-roll
      instead of getting free: sub-agents (recursion on the same loop), MCP client, context
      compaction, skills, permission prompts — but JARVIS's governance tiers ALREADY are
      the permission system and `action_parser` already is the output-normalising spine.
      THREE REAL BLOCKERS, in order of bite:
      (1) **model** — `GROQ_MODEL` defaults to `llama-3.1-8b-instant`, which cannot hold a
      multi-step tool loop (invents tool names, drops required args, loops). Agentic turns
      need a 70B-class/strong-tool-use model via a SEPARATE `GROQ_TOOL_MODEL` env so cheap
      classification turns stay cheap.
      (2) **rate limits are the ceiling, not capability** — one agent task is 5–20 calls,
      each carrying the whole growing transcript, so tokens/task run 10–50× a one-shot. The
      5-key rotation covers desk-scale use (tens of tasks/day); continuous autonomy will hit
      daily token caps. Mitigation is compaction + small per-turn tool sets, not more keys.
      (3) **`llm_router` has no tools path** — `universal_llm_call` is text-only and
      `_call_groq` posts `messages` with no `tools`. Needs a sibling `universal_tool_call()`
      routing ONLY to tool-capable providers (groq → gemini → openrouter). **Ollama is
      excluded from tool turns** — CPU-box tool-calling is slow and unreliable.
      FIVE RULES that make free/weaker models workable: curate 5–8 tools per turn (never all
      40 — small models degrade sharply with tool count); strict schema + ONE repair attempt
      then an honest failure; hard `max_steps`/token/wall-clock caps (an agent that can't
      finish must SAY so, never narrate false success); governance checked before EVERY tool
      execution (computer-use + writes stay CONFIRM); and keep the current one-shot path —
      the loop is opt-in per intent and falls back, so today's working behaviour is never
      lost. PHASES (1–3 buildable blind + harnessable): (1) `universal_tool_call()` + route
      order, fake-HTTP harness, no keys burnt; (2) NEW `modules/agent_core.py` loop +
      validation + caps, fake-model harness; (3) tool registry wrapping ~8 existing
      `action_engine` handlers with a governance tier each; (4) wire ONE intent to the loop,
      everything else unchanged; (5) sub-agents (recursion) + compaction once 1–4 hold.
    - **Reference path (NOT chosen — paid): Claude Agent SDK** (Anthropic's lib — gives an app the exact
      Claude-Code capabilities: agentic tool loop, sub-agents, MCP client, skills, context
      compaction). JARVIS already runs Anthropic cloud (cloud_first reasoning) so this is
      native. Alt: hand-roll the same with the Messages API **tool-use loop** + own registry.
    - **Wrap existing JARVIS tools as tool defs** (a registry): `gui_agent`, `action_engine`,
      memory read/write, browser, file ops — so the model can call them.
    - **Sub-agents / fan-out** for decomposed tasks; **consume the same MCP servers**
      (codebase-memory graph, chrome) via the SDK.
    - Reuse existing DBs for **agent memory** (mirror the fact-memory discipline used in the
      Claude Code `memory/` dir). Route through **governance** — the safety spine — so the
      agent loop respects AUTO/CONFIRM tiers, especially for computer-use + writes.
    - **Foundational**: this is the substrate item #10 (guarded self-improvement) needs.
      Design first (load `claude-api` + `claude-code-guide` skills for exact SDK wiring +
      model IDs), then a thin `jarvis-agent` core, then migrate `brain.process_command` onto
      it incrementally. Governance-gated from day one — NEVER an ungoverned tool loop.

### TIER D — packaging & mobile (LAST, in this order)
13. **Full `TEST_PLAN.md` pass** — automatic (Claude) + manual (Kaustav). Gate to Electron.
    ✅ **Doc refreshed 2026-07-26** (the 2026-07-25 staleness finding is closed): PART A now
    lists all **22 self-running harnesses / 522 checks** measured, and is driven by ONE real
    command — NEW `jarvis-backend/run_harnesses.py` (subprocess per harness, per-harness
    counts + timing, totals, **exits 1 on any failure** so it works as a gate; add new
    harnesses to its `HARNESSES` list). Full run: 522/522 in ~11.5 s. PART B gained §19
    (overlay/distance/precision), §20 (click/double/right/grab), §21 (camera sharing +
    auto-select + live feed + loopback 403), §22 (presence) — checkbox rows only, the
    tuning guidance stays here in §7 so it can't drift. Also corrected in the doc: dwell
    right-click is **1.5 s** (was 0.5), auto-lock is **60 s** code / **120 s** `.env` (was 6),
    §15's `phase*_regression_commands.json` files **do not exist** (lost in the Jul-4
    rewrite — only the runner survives), home/away presence moved out of "known
    limitations", and the dead `ROADMAP_TO_FULL_JARVIS.md` pointer now points here.
    `test_screen_reader.py` is a LIVE VLM script, excluded from the total on purpose.
    REMAINING for a real pass: A2 (backend up), A3 (needs pytest), and the manual §0–§22.
14. **Electron packaging** — single .exe boots FE+BE; notch → fullscreen takeover
    overlay (live agent-cam). ⚠️ `jarvis-frontend/package.json` lost its electron config
    in the Jul-4 history rewrite (no `main`/builder block/deps) though `node_modules`
    still has electron + a stale Jun-28 `release/*.exe`; `NotchView.jsx`/`SidecarView.jsx`/
    `electron/` are untracked. To resume: restore the package.json electron config,
    reconcile the stale Notch/Sidecar views with the current HUD, rebuild.
15. **Mobile app (Track C)** — Flutter (recommended): FCM push leg on `owner_notify`,
    `POST /api/presence` ingest (supersedes Track B), live agent-cam, geofence, voice.

---

## 6. Detailed specs for near-term items

### 6.1 Login / wake revamp (visual-only — auth logic in §3.5 stays)
Three weak spots and the fix:
- **Sudden wake** → NEW `BootSequence.jsx`: staged power-on (POWER CORE → NEURAL LINK →
  MEMORY BANKS → CALIBRATING → NOMINAL), **gated to the real boot** (hold the final step
  until the backend says `waking`/`online` — never outrun reality).
- **Name prompt is voice-only, no visual** → NEW `IdentityPrompt.jsx`: three identity
  cards (KAUSTAV/KINSHUK/MOUSUMI) + live mic pulse; also serves as the missing mic
  affordance (§5 item 1).
- **Kaustav face-auth looks fake** — root cause: `FaceScanOverlay.jsx` is self-timed
  (`setTimeout` 0/1500/4000 ms), NOT synced to the blocking 10 s `vision.scan_for_faces`,
  and just vanishes (no success/fail). → REWORK to `FaceAuthOverlay.jsx` driven by a
  small **additive backend status contract**: `auth_face_start / auth_face_scanning /
  auth_face_matching / auth_face_success{user} / auth_face_fail{reason}` (emitted around
  the existing scan in `main.py:2142+`; keep legacy `security_locked`+"OPTICAL SENSORS"
  as fallback). Show the live camera feed + detected face box; green lock-on on success,
  red reject → `IdentityPrompt` on fail. Animations HOLD until the backend confirms.
- Shared easing tokens across all login overlays; `ScanlineTransition` into the HUD;
  respect `prefers-reduced-motion`.
- Build order: backend status contract → `FaceAuthOverlay` (highest impact) →
  `BootSequence` → `IdentityPrompt` → transition polish → live-gate.
- ✅ **DONE (code):** backend status contract + `FaceAuthOverlay`. NEW pure
  `modules/auth_status.py` `face_frame(stage, user, reason)` → additive
  `{"status":"auth_face_start|scanning|success|fail", ...}` frames wired around the
  `vision.scan_for_faces` call in `main.py` (legacy `security_locked`+"OPTICAL
  SENSORS" kept for the security barrier + as fallback). NEW
  `FaceAuthOverlay.jsx`/`.scss` — driven entirely by those frames: **holds** the
  scan animation (looping laser+ring) until success/fail actually arrives (never
  outruns the real scan), green lock-on on success + matched user, red reject on
  fail; `prefers-reduced-motion` respected. `App.jsx` sets `authFace` from
  `auth_face_*`, clears it when the flow advances; the new overlay supersedes the
  self-timed `FaceScanOverlay` (kept only as a fallback for an un-updated backend).
  `test_auth_status.py` 9/9; `npm run build` passes.
  ✅ **BOTH FOLLOW-UPS DONE 2026-07-25 (`8ae9cc0`)** — they were blocked on plumbing
  that now exists (`modules/frame_bus.py`: one camera owner, many readers).
  - **Real `matching` phase.** `vision.scan_for_faces(timeout, on_phase=None)` fires
    `on_phase("matching", box, frame_size)` the instant the Haar pass finds a face and
    DeepFace verification starts, and reverts to `"scanning"` if that face fails to
    match and the loop keeps hunting — so the overlay lifts off its idle animation on a
    real event. Callback runs on the scan's thread and `main.py` routes it through
    `socket_manager.schedule_ui_update` (the thread-safe leg the gesture daemon uses);
    callback exceptions are swallowed — progress reporting must never fail an auth.
    `auth_status.normalise_box()` sends the box as 0..1 fractions clamped inside the
    frame, so the overlay draws it at any feed size with no knowledge of capture res.
  - **Live camera feed.** NEW `modules/camera_stream.py` + `GET /api/camera/stream`:
    MJPEG re-broadcast of the frame bus that **never opens a camera** — no owner
    publishing ⇒ 503, so a browser tab can't become the second consumer of the phone
    stream. Ends on `max_seconds` (a forgotten `<img>` must not pin a reader), on client
    disconnect, or when the publisher goes quiet past the idle grace (a dead camera
    collapses the stream instead of freezing on its last frame). Sync generator on
    purpose — Starlette iterates it in a threadpool so the pace-sleep can't stall the
    event loop. `vision._CapFrames` now PUBLISHES what it reads, so a scan that owns the
    camera feeds the overlay even with the gesture daemon off (`clear()` on release).
    ⚠️ **SECURITY:** unlike the rest of this local API the payload is a live view of the
    owner's desk, so the endpoint is **loopback-only** with a `JARVIS_CAMERA_STREAM=0`
    kill switch — and the client check parses 127/8 as a literal IPv4, because a
    `startswith("127.")` prefix test admits the attacker-supplied Host `127.evil.com`
    (the harness caught exactly that). Overlay: dimmed mirrored feed behind the reticle
    + the real face box on `matching`; `onError` falls back to the abstract animation, so
    the feed is a bonus layer and never a requirement. Box `left` is mirrored
    arithmetically (`1 - x - w`) — a CSS `scaleX(-1)` flips the rectangle in place and
    lands it beside the face.
  `test_camera_stream.py` 10 (new), `test_auth_status.py` 9→**18**; suite 478→**497**.
  **Live-gate owed** (§7).
- ✅ **DONE (code, 2026-07-19):** `BootSequence.jsx`/`.scss` — staged power-on POWER
  CORE→NEURAL LINK→MEMORY BANKS→CALIBRATING→NOMINAL, GATED (holds CALIBRATING until
  `ready`=backend `waking`/`online`, only then NOMINAL), then **scanline-wipes into the
  HUD** (inner panel `clip-path` wipes top-down + a cyan beam rides the reveal edge,
  mirroring `ScanlineTransition`'s motion — kept in-component to avoid that component's
  z-900/children-clip semantics which are tuned for the satellite panel). NEW
  `IdentityPrompt.jsx`/`.scss` — 3 name cards KAUSTAV/KINSHUK/MOUSUMI + live mic pulse
  (the missing mic affordance), roving highlight, `pointer-events:none` (identification
  is voice-answered). NEW shared `_loginTokens.scss` (`@use` partial — first in repo;
  sass ^1.99) = palette + `$login-ease` (=HUD_EASE) + mono, used by both new files.
  `App.jsx`: `bootActive`/`bootReady` set on `booting`/(`waking`|`online`),
  `identityPrompt` shown only on `security_listening`+"IDENTIFICATION"; overlays mounted
  (z: FaceAuth 9000 < IdentityPrompt 9050 < BootSequence 9100). `prefers-reduced-motion`
  respected (wipe → plain fade, beam dropped). `npm run build` passes. **Live-gate owed**
  (§7). **§6.1 is now COMPLETE** — contract, FaceAuthOverlay (+live feed +matching
  phase), BootSequence (+scanline wipe), IdentityPrompt all shipped; only live-gating
  is outstanding.

### 6.2 Away→mobile presence
- **Track B (near-term, no app):** `modules/presence_probe.py` — detect phone on home
  LAN (ARP-table hit after a priming ping > TCP connect to a known phone port > ICMP).
  Asymmetric debounce: any hit → HOME immediately; AWAY only after a long grace
  (`JARVIS_PRESENCE_AWAY_GRACE` ~180 s) because phones sleep their WiFi radio. Config:
  `JARVIS_PHONE_IP`, `JARVIS_PHONE_MAC` (pin a fixed MAC for the home SSID to defeat MAC
  randomization). Feeds a fused presence helper → routing: **AT_DESK** (face) = desk
  HUD+TTS; **HOME-not-desk** = desk + phone; **AWAY** = phone only (+ cloud if PC off).
  Pure debounce state machine is harnessable; walk-out is live-gated.
- **Track C (after Electron):** dedicated Flutter app — FCM push, `POST /api/presence`
  (supersedes B), live agent-cam (WebRTC/MJPEG over the cloud bridge), inline
  CONFIRM/approve-task buttons, voice. Backend adds `/api/presence` + an FCM sender leg;
  device-token + shared-secret auth.

### 6.3 Gesture click reliability (NEW 2026-07-19 — live-run bug, G6.2)
Live symptoms (cursor MOVE is fine):
- **Left click never fires; double-click never fires** — pinch/click intent not reaching
  `mouse_down/up`, OR the click pose is never classified.
- **Right click fires far too often** — dwell-right-click threshold too loose (fires on
  any brief hover pause) and/or a pose is being mis-read as the right-click trigger.
- **Grab (fist) intermittent** — fist classification flickers (confidence/threshold or
  frame-to-frame jitter) so drag engages/drops.
Root-cause candidates to inspect in `modules/gesture_engine.py` + `gesture_daemon.py`:
pinch-distance click threshold + debounce; dwell-right-click radius/time; fist detection
hysteresis; whether a distinct click *event* is emitted separate from `pose` (the G5.3
follow-up noted the engine exposes no pinch pose — click is an intent, not a `pose`).
Add click/right-click/grab state to the HUD + overlay pulse so it's diagnosable. Needs a
live gate to tune thresholds (harness the pure classifier changes first).
✅ **DONE (code, 2026-07-19).** ROOT CAUSE (all three symptoms, one bug): the pinch
detector overlapped the fist zone. `pinch_down=0.40` (thumb–index dist / hand-size) is the
pinch/fist boundary in `_classify`; a fist whose thumb rests NEAR the index sits at
d≈0.3–0.5 → registered as a long pinch → **right_click** (grab fails), and any tap held ≥
`dwell_right_click_s=0.5s` (easy — hysteresis 0.40/0.60 + 2-frame debounce inflate the
measured hold) became right_click, so **left/double never fired**; grab only worked with the
thumb held out (**intermittent**). FIX in `gesture_engine.py`: (1) `pinch_down` 0.40→**0.30**
— a click needs a genuine thumb–index touch, a closed hand (thumb merely near) is a grab
(one clean boundary shared by pinch + fist); (2) `dwell_right_click_s` 0.5→**1.5s**; (3)
don't abort an ALREADY-down pinch when a release-transition frame misreads as "fist"
(`in_fist = pose=="fist" and not self._left.down`); (4) new `grab_after_pinch_s=0.25` cooldown
so a curled click can't bleed into a drag as the hand reopens. All live-tunable: env
`JARVIS_PINCH_DOWN/PINCH_UP/DWELL_RIGHT_CLICK_S/GRAB_AFTER_PINCH_S` + calibration JSON.
Diagnostics: `gesture_daemon` publishes `last_action`/`last_action_ts` (click/double/right/
grab/drop) → HUD chip + `cursor_overlay` draws an expanding **ripple** (cyan click · purple
right · amber grab) at the cursor when one fires. `test_gesture_engine` 54→58 (+slow-tap=left,
thumb-near-index=grab, curled-click-no-bleed, env-tunable), `test_gesture_calibration` +knobs
round-trip; **suite 315→325**. **Live-gate owed:** quick pinch=left, tight double=double,
pinch-and-hold=right, fist=grab-drag; watch the ripple colour matches intent; tune
`JARVIS_PINCH_DOWN`/`JARVIS_DWELL_RIGHT_CLICK_S` if his hand/camera need it.

### 6.4 Camera source auto-select — DroidCam + IP Webcam (NEW 2026-07-19, G6.3)
Today one source: `JARVIS_CAM` (int index or a single URL), no failover
(`gesture_daemon.py:363`, `gesture_camera._open_source`). Goal: **try a prioritized list
of sources, use the first that opens + delivers a frame.** Sources in play:
- **IP Webcam** (Android) — `http://192.168.0.103:8080/video` (Kaustav's; same phone as DroidCam
  — 192.168.0.103, IP Webcam :8080 vs DroidCam :4747; both use the phone cam so only one runs at a time).
- **DroidCam WiFi** — `http://192.168.0.103:4747/video` (Kaustav's, works; MJPEG path `/video`).
- **DroidCam USB** — NOT working; two fix paths: (a) DroidCam PC client's virtual-webcam
  driver → shows up as a normal `cv2.VideoCapture` **index** (0/1) via CAP_DSHOW; (b) ADB
  reverse: `adb forward tcp:4747 tcp:4747` then `http://127.0.0.1:4747/video`.
Design: `JARVIS_CAM_SOURCES` = comma-list (index or URL), tried in order with a fast
open+first-frame probe (short timeout, don't hang on a dead URL); fall back to legacy
single `JARVIS_CAM`. Pure probe/ordering logic is harnessable; the actual open is live.
✅ **DONE (code, 2026-07-19):** `modules/gesture_camera.py` — `parse_sources` (comma-list,
dedup, order-preserving, digit→index, legacy fallback), `url_reachable` (TCP connect probe,
injectable `connect`, so a dead host fails in ~timeout instead of blocking cv2),
`open_first_available` (URL→reachability gate then `_open_source`; index→straight open;
first working `(cap, source)` wins; all-fail → `CameraError("absent")` with a per-source
summary), `make_frame_source` (auto-select + wrap, `FrameSource` now accepts a pre-opened
`cap` so the reader thread never re-opens). `gesture_daemon._session` uses
`make_frame_source(parse_sources(JARVIS_CAM_SOURCES, JARVIS_CAM), …)`, logs the chosen
source, sets `gesture_state["camera"]` to it. `test_gesture_camera.py` 28/28; suite 287→315.
Recommended `.env` for Kaustav (both apps on phone 192.168.0.103, one runs at a time):
`JARVIS_CAM_SOURCES=http://192.168.0.103:4747/video,http://192.168.0.103:8080/video,0`
(DroidCam WiFi first, IP Webcam second, local index last).
✅ **FOLLOW-UP FIX (2026-07-25, from the pre-merge audit):** `_open_source` was asymmetric —
an index source read up to 10 frames before accepting, but a URL source only checked
`cap.isOpened()` and returned WITHOUT reading one, so a stream that connects and then
stalls (app backgrounded, phone camera held by another app) got auto-selected over a later
working source and `open_first_available`'s "opens AND delivers a frame" contract was false
for URLs. Both paths now share `_first_frame(cap, attempts, sleep)`
(`INDEX_FRAME_ATTEMPTS=10`, `STREAM_FRAME_ATTEMPTS=20` — WiFi MJPEG needs longer for frame 1,
`FRAME_WAIT_S=0.1`); a stalled URL raises `CameraError("stream", "connected but delivered no
frames…")` and auto-select moves on. `_open_source` gained injectable `capture=`/`sleep=`
kwargs (positional signature unchanged, so `open_first_available`'s `opener` contract holds),
making the gate harnessable: `test_gesture_camera.py` 28→46 checks (incl. stalled-stream-
loses-to-next-working-source). ~~USB still owed~~ **USB DROPPED 2026-07-19 (Kaustav's call — WiFi only).** HyperOS never
authorised ADB (interface bound, `adb devices` stayed empty), and the DroidCam PC client
path wasn't worth chasing while WiFi works. Do not reopen unless he asks. **Live-gate:**
kill the first source mid-run / start with only the 2nd app streaming → daemon picks the
reachable one; confirm a fully-dead list logs the summary + retries in 30s.

---

### 6.5 Camera sharing + closing-fist grab (NEW 2026-07-25, G6.4 — both DONE + live-gated)

Two defects found by actually running the gates rather than reasoning about them.

**G6.4 closing-fist grab** (`0863c7b`) — live: three fist poses produced ZERO grabs, and
fist→palm 1.3s later emitted a `right_click`. Root cause is ordering, not thresholds:
`process()` calls `_update_pinches` BEFORE the grab check, and a hand closing into a fist
*must* transit the pinch zone, so `d_left` < `pinch_down`(0.30) registers the pinch FIRST.
G6.2's guard (`in_fist = pose=="fist" and not self._left.down`) therefore can't fire, and a
closed fist parks the thumb at `d_left≈0.45` — under `pinch_up`(0.60) — so the pinch never
releases either: it rides out the whole grab and comes up past `dwell_right_click_s` as a
right-click, while `drag_start` (needs `not _left.down`) never runs. FIX: if the fist pose
lands within `grab_transit_s`(0.4) of pinch-down **and** `d_left <= pinch_up`, cancel the
pinch silently — no click, no dwell, and deliberately no `_pinch_up_t` (arming the grab
cooldown there would block the grab being cleared for) — then let the grab fire that frame.
The `d_left <= pinch_up` half is the PHYSICAL criterion, not a heuristic, and is what keeps
G6.2 intact: a closing fist is *stuck* at 0.450 (can never self-release) while a genuine
curled tap flicks the thumb clear to 1.733 (already releasing). Omitting it broke
`test_curled_click_does_not_bleed_into_grab` — that test doing its job is how the
distinction surfaced. Knobs: `JARVIS_GRAB_TRANSIT_S` + calibration `grab_transit_s`; **0
disables**. ⚠️ **TESTING LESSON:** every pre-existing grab test fed a fist straight from an
open palm — a motion no real hand can perform — which is why the suite was green while the
gesture was broken. Model the *transit*, not the destination.

**Camera contention → one owner, many readers** (`b7e771d`) — three subsystems each opened
their own capture on the same phone stream (daemon ~30fps, `scan_for_faces` 10s, ambient
vision every 6s). An IP Webcam MJPEG endpoint won't serve that: a face scan during a live
daemon session killed it with `camera stream died (30 consecutive read failures)` + a 30s
blind retry — and since face-auth runs at **every wake**, gesture control was guaranteed to
drop exactly as the owner walked up. NEW `modules/frame_bus.py`: the owner publishes, others
read. Dependency-free by requirement (threading+time only) because `ambient_vision` imports
it at module scope and must stay loadable without cv2/TF/YOLO. Frames stored by REFERENCE
(cv2 `read()` allocates per call — nothing to tear) and copied on READ so the memcpy costs
only when consumed, not 30×/s; monotonic `seq`+`after_seq` so a reader waits for a genuinely
new frame instead of re-recognising one stale image; 1.5s staleness bound (tolerates the
daemon's ~2fps locked tier) so nobody is handed an empty-chair frame; `clear()` on session
end. Daemon publishes **PRE-mirror** — deliberate: that's the orientation the two consumers
saw from their own captures, so this changes contention ONLY, never recognition (`face_gate`
keeps the mirrored frame, as `enroll_face` does). `scan_for_faces` got `_BusFrames`/
`_CapFrames` behind one `.read()` contract so the scan loop doesn't branch; both consumers
fall back to their own capture when nothing publishes. ⚠️ The bus is per-**process** state —
correct, since all three live in the backend process; a separate-process script gets a cold
bus and opens its own capture (which is how the bug was originally reproduced).
BONUS: the scan now returns in **1.7s vs 8.0s** (frames already flowing).

**`owner`/`stranger` flicker — FIXED (code, not yet live-gated).** SFace flipped the owner to
`stranger` on isolated checks when he was the *only* person in frame and slightly off-axis
(t+7.1/25.1/28.2/40.0 in one 60s run). Harmless while unlocked — the 3.5s owner grace covers
the gaps and stranger alerts only fire when `_locked` — but once the desk locks, every one of
those is a Telegram snapshot of the owner himself. Two layers, both pure logic:

- **NEW `face_gate.StrangerConfirmer`** — require evidence, not one check. Each consecutive
  stranger check adds 1.0 and the stranger is asserted at `needed`(3); a check with no
  stranger clears the streak, and so does a gap > `window_s`(3s) — two sightings a minute
  apart are not one person walking up. Fed **once per face check** (0.5s locked / 1.0s idle /
  1.5s engaged cadence), NOT per camera frame, so 3 = ~1.5s of continuous presence before an
  alert. Knobs `JARVIS_STRANGER_CONFIRM` / `JARVIS_STRANGER_WINDOW_S`.
- **Near-miss faces count half.** `GateResult` gained `top_score` + `uncertain`; a
  non-matching face scoring ≥ `UNCERTAIN_FLOOR`(0.25, `JARVIS_FACE_UNCERTAIN_FLOOR`) is far
  more likely the owner off-axis (live: high 0.2s/low 0.3s) than a different person (~0.0–0.1),
  so it contributes 0.5. A 2s head turn at the locked cadence therefore raises **nothing**,
  while a genuine stranger still confirms in 3 checks. Such a face is still NOT the owner —
  gestures stay denied; only the alert evidence is weaker.

Also: the HUD `owner` tick now mirrors the **grace-smoothed** owner (`OWNER_GRACE_S`), not the
raw check — the tick blinking off while control is still correctly allowed was a lie. The
second alert site (`stranger tried to use gesture control`, evaluated every frame off the
cached result) reads `stranger.confirmed`, so it inherits the debounce for free.
`test_face_gate.py` 5→**12** (single-check-no-alert, 3-clear-confirm, uncertain-needs-double,
mixed weighting, streak clear, stale gap, reset). **SUITE 471 → 478.**
**LIVE-GATE OWED:** off-axis glances in a locked session must produce zero Telegram
snapshots; a real second person must still alert within ~2s.

**OPEN — `/api/vision/state` still leaks a second camera consumer.** It hands the frontend
the phone's raw `camera_url` so the ambient-vision HUD panel pulls MJPEG *directly from the
phone* — exactly the second-consumer pattern `frame_bus` exists to kill (an IP Webcam
endpoint serves one MJPEG client). Now that `GET /api/camera/stream` re-serves the bus,
migrate that panel onto it and stop publishing the raw URL. Low risk, not yet done.

**Phone stream is flaky:** it went unreachable mid-session and returned on `.105` within a
minute (WiFi power saving). Pin a DHCP reservation + disable battery optimisation for IP
Webcam, or the daemon keeps taking 30s blind-retry windows.

---

## 7. Live-gates owed by Kaustav (hardware) + push status

> **DECISION 2026-07-25 (Kaustav): live-gating happens ONCE, at the END — "will test at
> last".** Do not stop building to wait on a gate, and do not ask him to run one
> mid-stream. Keep landing code + harnesses, keep this list growing, and run the whole
> checklist in a single desk session before the merge. Order at the desk:
> **(1) this checklist → (2) Electron launch scripts (his explicit call: very last, needs
> him present for real frameless windows) → (3) merge `feat/cloud-gateway` → `main`.**
> Consequence to accept deliberately: features stack up *unverified against hardware*, so
> every new item ships with (a) a self-running harness and (b) a one-line gate recipe added
> below — the harness is what keeps the stack honest until that session.

**PUSH STATUS 2026-07-25:** commits landed since `99281e3`: `fb30e50` enroll mirror,
`c009d8e` camera unification, `6409adf` overlay blast-radius, `17185a9` auto-lock default +
test log, `5f60a20` overlay TkTopLevel, `0863c7b` G6.4 closing-fist grab, `b7e771d` frame bus,
+ the stranger-confirmer above. Suite **478** automatic checks, 0 failures; 6 environmental
non-greens (4 need pytest — not in venv, repo convention is self-running harnesses;
`test_ping`/`test_ui_bridge_e2e` need the backend up). `test_screen_reader.py` is a live VLM
script, not a counted harness.

**GATES PASSED 2026-07-25** (strike these from the owed list below):
- ✅ **G6.3 camera auto-select** — twice, unprompted: DHCP moved the phone `.105`→`.106`→
  `.105` and the daemon skipped the dead entry in ~1.5s each time, recovering
  `camera_error`→`idle` on its own.
- ✅ **Camera unification** — `[VISION] camera source: …` → `✅ MATCH: KAUSTAV`.
- ✅ **G5.3 overlay** — halo 532 cyan px in a 59×59 box at the cursor; windows are
  72×72/132×132/200×48 with **no fullscreen window**; click-through verified on a *drawn*
  pixel (probe at cursor+RING_R resolves to the app beneath); foreground never stolen;
  deadman + EOF exits clean. The deadman also fired **unstaged in production** when the
  daemon stalled.
- ✅ **Gesture vocabulary** — `index_only`→START, `back_palm`→STOP, clutch on/off, all five
  poses, click, and (after G6.4) `pose=fist last_action=grab` → `pose=palm last_action=drop`
  with **zero** `right_click` in a 60s run.
- ✅ **Camera sharing** — real topology, publisher unharmed across a scan (+50 frames,
  `died=None`), cold-bus fallback intact.

**STILL OWED** before push+merge:
- **G4 camera gates:** arbiter during a real ghost_type/autopilot (cursor mustn't fight,
  chip shows "JARVIS DRIVING"); guided re-enroll `enroll_face.py` (12 samples →
  re-seed `owner_embeddings.npz`, currently the 1-sample seed); calibration `w`-save
  round-trip; eyeball chip states.
- **G5.1 feel:** `gesture_spike.py <url>`, press `r` for relative — accel/gain (`[`/`]`),
  clutch (brief back-of-hand → move → re-face palm, no jump), dwell right-click.
- **G5.2 wizard:** `calibrate_gesture.py [--relative] <url>` → palm/pinch/reach → `w`
  saves → restart → confirm persisted.
- **G5.0 #7:** kill the gesture daemon → HUD chip disappears within ~6 s.
- **G5.3 overlay:** START control → cyan halo tracks the cursor; fist → amber, index+middle
  → scroll dashes, back-of-hand → dim clutch ring. Toasts fire on engage (HAND READY),
  automation takeover (JARVIS DRIVING), and hand-off back (YOU HAVE CONTROL). Confirm the
  halo is **click-through** (clicks still land on the app beneath, focus never stolen) and
  that it vanishes on lock/disable. `JARVIS_GESTURE_OVERLAY=0` disables the whole process.
- **G6.1 face-auth overlay:** wake JARVIS with a biometric boot (not "admin override").
  The FaceAuthOverlay must HOLD on the scanning animation for the full real scan (up to
  10s) — not finish early and vanish. On a recognized face → green lock-on + "IDENTITY
  CONFIRMED — <USER>"; on no match → red reject before the voice challenge. Confirm it
  never "outruns" the scan, and that an un-updated path still shows the legacy overlay.
- **G6.2 gesture click/grab:** engage, then: a QUICK thumb-index pinch → LEFT click (a
  ripple should flash cyan at the cursor); two quick pinches same spot → DOUBLE; pinch-AND-
  HOLD (~1s) → RIGHT click (purple ripple); close a FIST → grab-drag (amber ripple), open to
  drop. Confirm left/double actually fire now, right-click NO LONGER fires on quick taps, and
  a grab with the thumb tucked near the index still grabs (not a right-click). If his hand/
  camera need it: `JARVIS_DWELL_RIGHT_CLICK_S` higher = harder to trigger right-click,
  `JARVIS_PINCH_DOWN` lower = pinch needs a tighter touch (more of a closed hand reads as
  grab). Tune live, then `w`-save via the calibration wizard.
- **G5.5 precision:** engage, then move the hand VERY slowly onto a tiny target (a window
  × button, a text caret between two characters) — the cursor should hold steady and let
  you land it, not wobble past. A fast flick must feel unchanged (no lag). Confirm the
  cursor still reaches the exact target (no settling short of it). `JARVIS_PRECISION_GAIN`
  lower = steadier-but-slower fine control; `JARVIS_GESTURE_PRECISION=0` disables.
- **G5.4 distance:** set `JARVIS_CAM_RES=1280x720`, step back across the room, engage —
  cursor should still track (ROI crops around the hand). Near hand must behave exactly as
  before (crop ≈ full frame, no jump). Sweep `JARVIS_HAND_DET_CONF`/`_TRACK_CONF` down
  (~0.3) if a far hand won't lock; tune `JARVIS_ROI_MIN_FRAC` (smaller = more zoom, but
  clips the hand on fast moves). Confirm cursor doesn't jump as the crop re-anchors, and
  that `JARVIS_GESTURE_ROI=0` restores plain full-frame detection. Watch per-frame CPU at
  720p on the 17GB box (motion/face run cadenced; detect runs on the small crop).
- **Stranger debounce (§6.5):** locked session (or `JARVIS_LOCK_AFTER` low + walk away),
  then glance off-axis repeatedly — **zero** Telegram snapshots of himself. Have a second
  person step in front of the lens → alert within ~2s. `JARVIS_STRANGER_CONFIRM` higher =
  more evidence needed; `JARVIS_FACE_UNCERTAIN_FLOOR` higher = fewer faces treated as
  "probably the owner".
- **§6.1 live feed + matching phase:** biometric wake → the reticle shows the REAL camera
  (dimmed/mirrored) and, the moment a face is found, a box locks onto it with "MATCHING
  IDENTITY…" before success/fail. Kill the gesture daemon and repeat — the scan owns the
  camera and the feed must still appear. Then `JARVIS_CAMERA_STREAM=0` → no feed, abstract
  animation only, auth still works. Curl the endpoint from the phone/another LAN host →
  **403** (loopback-only).
- **Track B presence:** set `JARVIS_PHONE_IP` + `JARVIS_PHONE_MAC` (pin a non-random MAC for
  the home SSID first), start the backend, hit `GET /api/presence/state` → `lan: "home"` with
  `how` reading `arp:mac`. Sit in front of the camera → `presence: "at_desk"`. Leave the desk
  but stay home → `"home"` (alerts should now ALSO buzz the phone). Take the phone out of WiFi
  range and wait out `JARVIS_PRESENCE_AWAY_GRACE` → `"away"`, and proactive alerts must stop
  talking to the empty room. Lock the phone screen and idle 5 min WITHOUT leaving — it must
  stay `home` (that is the failure the asymmetric debounce exists to prevent).
- **Phase-4 phone smoke-tests + Phase-5 failover** — see `TEST_PLAN.md` §B5/B6.
- Then **Electron launch scripts** (at the desk), then **merge** `feat/cloud-gateway`.

---

## 8. Key decisions & constraints (do NOT violate)

- **Dependency pins — do NOT bump:** `protobuf==6.33.6` (the ONE version where
  tensorflow/DeepFace + mediapipe + Gemini SDK all work; 5.x breaks tf gencode — this
  constrains the WHOLE venv), `mediapipe==0.10.35` (uses the Tasks API — legacy
  `solutions` API removed; model `models/hand_landmarker.task`),
  `opencv-contrib-python==5.0.0.93`, `sounddevice==0.5.5`, `pywinauto==0.6.9` +
  `comtypes==1.4.16`.
- **Dependency-averse:** ask Kaustav before ANY new install; pin in `requirements.txt`.
- **Hardware:** CPU-only box, no GPU. LLMs/vision-LLMs have no role in the gesture hot
  loop (cursor needs ≤33 ms/frame → MediaPipe Hands only). Cursor injection via ctypes
  `SendInput`, NOT pyautogui in the hot loop.
- **Engine/IO split:** `gesture_engine.py` stays pure (landmarks→intents, no I/O),
  unit-testable with synthetic sequences.
- **Test convention:** self-running plain-python harnesses (`if __name__=="__main__"`),
  **no pytest in the venv** (some existing tests are pytest-gated → blocked; decision
  owed on `pip install pytest` — see `TEST_PLAN.md` §A3).
- **LLM routing:** free cascade groq→gemini→openrouter→ollama; **Groq stays PRIMARY**
  for voice latency. Honest failure on total exhaustion (never fake success).
- **Benglish / Latin script:** Bengali replies in roman letters, never বাংলা/Devanagari;
  Kaustav never speaks Hindi.
- **Autonomy policy:** auto-fix safe/reversible; **ask before risky.** Governance
  AUTO/CONFIRM/BLOCK (94 rules). `workspace_patch` = CONFIRM (v1.5.0).
- **Self-improvement:** propose→branch→test→PR→human-approve. **NEVER auto-merge.**
- **Smart-home governance:** lights = AUTO, locks/security = CONFIRM.
- **TTS voice:** edge-tts `en-GB-RyanNeural` (Piper offline fallback).
- **Secrets:** keys ONLY in `jarvis-backend/.env` (git-ignored); `KEys.txt` git-ignored;
  never commit keys.
- **Safety-first gestures:** control starts OFF; palm/engage gate is the safety; casual
  waving must never engage. `owner_embeddings.npz` git-ignored (biometrics).
- **Verification:** harness for pure logic; MANUAL live-gate for hardware/GUI (Kaustav).

---

## 9. Pointers
- **Tests:** `TEST_PLAN.md` (automatic + manual; kept separate on purpose).
- **Deep code:** query `codebase-memory-mcp` (search_graph / trace_path / get_code_snippet)
  before reading whole files.
- **Cloud gateway ops:** `jarvis-backend/CLOUD_GATEWAY.md`.
