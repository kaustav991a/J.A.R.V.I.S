# JARVIS — Master Roadmap

> **Single source of truth.** Read this first when resuming. It replaces and folds in
> the old `ROADMAP_TO_FULL_JARVIS.md`, `RELIABILITY_HARDENING.md`,
> `HAND_GESTURE_CONTROL_PLAN.md`, `UPGRADES_AND_FLUIDITY.md`, `MOBILE_PRESENCE_PLAN.md`,
> and `LOGIN_REVAMP_PLAN.md` (all deleted 2026-07-19). The **test plan lives
> separately** in `TEST_PLAN.md`. Last updated 2026-08-01 at `9c8c5eb`.
>
> **Working branch:** `feat/cloud-gateway`, fully pushed as of 2026-08-01 (see §7).
> Not merged to `main` — and `main` carries one commit this branch does not
> (`8d0ea4f` "Add GNU General Public License v3"), so the merge is not a fast-forward.
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
| **Automatic test baseline** | ✅ **876 checks / 39 harnesses, 0 failures** — one command: `run_harnesses.py` (`test_screen_reader.py` is a live VLM script, not counted; the pytest-only tier A3 was closed 2026-07-30) | `TEST_PLAN.md` |
| **Agentic core** (Tier C #12) | ✅ **ALL 5 PHASES DONE + LIVE-GATED 2026-07-26** (14/14 §23 rows) — pushed | §5 Tier C |
| **Memory-at-rest encryption** (Tier C #11a) | ✅ **DONE + LIVE 2026-07-30** — DPAPI + scrypt recovery, AES-256-GCM fields; `jarvis_longterm.db` encrypted, `jarvis_memory.db` retired into it | §5 #11a |
| **G6.2/G6.3/G6.4 + camera unification + frame bus + overlay hardening + stranger debounce** | ✅ DONE + pushed (`90a9bc9`) | §6.3–§6.5 |
| **G5.7** mic/voice affordance (visible) | ✅ DONE (`3d3063d`); **click-to-talk DONE 2026-07-26** (`POST /api/listen`), live-gate owed | §5 |
| **G5.3** cursor-halo + edge-toast overlays | ✅ code done, live-gate owed | §5 |
| **G5.4** distance mitigation | ✅ code done, live-gate owed | §5 |
| **G5.5** precision / dual-target filtering | ✅ code done, live-gate owed | §5 |
| **G5.7** robustness backlog | 🟡 backend 5/6 done (barge-in deferred — live audio); **frontend ALL DONE** (`0b5a0a4`) | §5 |
| **Login/wake revamp (§6.1)** | ✅ COMPLETE (code) — contract, FaceAuthOverlay + **live feed + real matching phase** (`8ae9cc0`), BootSequence + wipe, IdentityPrompt; live-gate owed | §6.1 |
| **Away→mobile presence (Track B probe)** | ✅ DONE (code) — ARP/TCP/ICMP ladder + asymmetric debounce + owner_notify routing; live-gate owed | §5 #7, §6.2 |
| **Partner-inbound — butler discretion** | ⬜ **DESIGNED, NOT BUILT** (2026-08-02) — supersedes the `summarize_partner_chat` scope; sits after `message_partner` and after the §7 live-gate | §6.7 |
| **Smart-home / IoT agent** | ⬜ MISSING | §5 |
| **Guarded self-improvement loop** | ⬜ MISSING | §5 |
| ~~Agentic core (Claude-Code-grade tool loop)~~ | ✅ **DONE** — this row said "MISSING" until 2026-07-30 while the same doc recorded all 5 phases live-gated; the duplicate row is kept struck-through so the contradiction is not re-introduced | §5 (Tier C #12) |
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
- **G4** (`cc27156`, pushed — this row said "unpushed" until 2026-08-01; the commit is on
  `origin/feat/cloud-gateway` and was verified with `git branch -a --contains`) — cursor
  arbiter (`gesture_arbiter.py`,
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
- **Backdoor** → `/api/backdoor`, the auth bypass for testing. **Gated 2026-07-26**
  (`modules/backdoor_gate.py`): default OFF ⇒ dispatch only when `SYSTEM_ONLINE`
  (a real auth passed), locked ⇒ `403 refused`; `JARVIS_ALLOW_BACKDOOR=1` restores the
  bypass consciously. Tiers/governance untouched. Harness: `test_backdoor_gate.py`.

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
   click focuses the command line. Pairs with the login IdentityPrompt.
   ✅ **Click-to-talk DONE 2026-07-26** — Tier A now has only barge-in left. The WS idea
   was dropped for the reason it never worked: nothing reads client frames while the
   voice loop is blocked inside `recognizer.listen(...)`, so `START_LISTENING` was a
   no-op. Instead NEW dependency-free `modules/listen_request.py` (`ListenRequest`:
   thread-safe, clock-injectable) + `POST /api/listen`, consumed by the loops in
   `wakeword.py` **between** listen windows — the one seam that exists. Two rules it
   holds: **one-shot** (`consume()` clears, so a press can't make him listen twice) and
   **it expires** (`ttl_s` 15s — a click during a 40s LLM turn must not pop the mic open
   afterwards). Wired at THREE points: `wait_for_jarvis` (press = called by name, plus
   the wake SFX), `wait_for_wake_word` (press = boot, returning `CLICK_WAKE_PHRASE`
   = **"wake up"**, the guest/biometric path — a button must never take the admin
   bypass), and the **no-microphone fallback** loop, where the button is the only wake
   path that exists. Checked AFTER the deafen guard on purpose: a press while he is
   speaking stays pending and fires when he stops — cutting him off is barge-in, a
   different (still deferred) feature. Frontend: `MicIndicator` is now a real button —
   `startVoiceCommand` POSTs `/api/listen`, surfaces a refusal in the system log, and
   works while offline (there it means "wake up"). `test_listen_request.py` **12**
   (one-shot, expiry either side of the boundary, refresh-on-second-press, 8-thread
   exactly-once race, plus a static check that the wake phrase is never the admin one).
   **SUITE 525 → 537.** COST TO ACCEPT: up to one listen window of latency (~3s awake,
   ~5s offline) before a press is noticed. LIVE-GATE OWED — see §7.
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
    generative HUD / brain-driven data-viz; planner cost/risk budgeting +
    hierarchical sub-plans; richer cross-task worker memory; per-session state for the
    pending-decision singletons; Telegram push + remote file search; PDF/docx RAG parsing
    + incremental re-index; life integrations (Spotify, Notion/Obsidian, banking, maps).

11a. **Memory-at-rest encryption — ✅ DONE + LIVE 2026-07-30.** (Folded in from the
    former `jarvis-backend/ENCRYPTION_DESIGN.md`, now deleted — the repo keeps 2 planning docs.)
    His rule, and the reason the design came before any code: *"encryption you can't reverse
    is data loss wearing a security costume."* Sign-off was required on the KEY STORY first.
    - **Key scheme (b): Windows DPAPI + a two-wrap recovery.** One random 32-byte DEK, wrapped
      twice — DPAPI (stdlib `ctypes`, entropy-bound, **no boot prompt**) for unattended boot,
      and scrypt over a printed one-time recovery code for disaster recovery. Either wrap opens
      the same DEK, so a rebuilt Windows profile is an inconvenience, not data loss.
      Passphrase-at-boot was **rejected**: `watchdog` respawn, `OvernightWorker` and the cloud
      gateway all boot unattended, so it would turn every crash into an outage.
    - **Honest limit, stated and accepted:** DPAPI protects data **leaving the machine** (a
      copied folder, a backup, a sync client, a repo accident). It does **not** protect against
      code running as Kaustav on that box — that is exactly what "no boot prompt" costs.
    - **App-level AES-256-GCM field encryption**, not a sqlite engine swap. `sqlcipher` was
      rejected: a compiled extension under Chroma's own sqlite could take the whole vision venv
      down for no gain. `cryptography` was already installed, so **zero new dependencies** and
      `protobuf==6.33.6` is untouched.
    - **Chroma decision (iii):** partner-derived data stays **out of Chroma entirely**. Chroma
      keeps document text in plaintext and `.bin` vectors leak approximate content via embedding
      inversion; keeping her data out removes the leak instead of managing it.
    - **Failure is loud:** a missing/wrong key raises `MemoryLockedError` → *"Long-term memory is
      LOCKED"*. Never a silent empty read, which is indistinguishable from having forgotten him.
    - **The non-obvious trap:** `UNIQUE(user, content)` silently dies under encryption — random
      nonces mean the same fact never yields the same ciphertext, so duplicates would pile up
      unnoticed. Fixed with a keyed blind index (`memories.content_hash`). **Any future encrypted
      column must be checked for the same thing.**
    - **Ships:** `modules/memory_crypto.py`, `manage_keys.py` (init/status/verify/export-key/
      restore-key/show-public), `backup_memory.py`, `migrate_memory_encryption.py`,
      `retire_jarvis_memory_db.py`. Wired into `memory_manager`, `memory.py`, `partner_log`.
      Harnesses: `test_memory_crypto` 29, `test_memory_store_encryption` 17,
      `test_memory_extraction_guard` 12, `test_store_retirement` 15.
    - **Store consolidation:** `jarvis_memory.db` **retired** — both its tables were still live
      (`remember_fact`/wake-briefing, and sleep-wake `session_digest`), so it was a redirect, not
      a delete. Everything now lives encrypted in `jarvis_longterm.db`; the old file is kept in
      `JARVIS-BACKUPS\plaintext-originals\`.
    - **cp1252 hardening of the three CLIs — ✅ DONE 2026-08-01 (`9c8c5eb`).** All three
      shipped CLIs printed box rules and arrows but never reconfigured stdout, so a piped or
      service stdout on a cp1252 locale killed them mid-run with `UnicodeEncodeError` — worst
      in `manage_keys.py`, which prints the recovery code **shown once and never recoverable**.
      Each now forces UTF-8 after its imports (the `watchdog.py` placement, **not** `main.py`'s:
      all three open with `from __future__ import annotations`, which must stay the first
      statement). Verified under a real `PYTHONIOENCODING=cp1252` shell with stdout piped — a
      plain `print` of an arrow dies in that exact shell, and all three then ran their
      read-only modes (`status`, `--report`, `--report`) to exit 0. This was the **third**
      recurrence of the root cause (after `main.py`/`watchdog.py` and the harness runner's
      children), so it is now guarded: `test_governance.py` drives every governance tier
      through a `cp1252 / errors="strict"` stdout **and** self-checks that the stream really is
      strict, so the guard cannot pass vacuously; a second test asserts `run_harnesses.py`
      still sets `PYTHONIOENCODING=utf-8` *and* still passes `env=_CHILD_ENV` to the child.
      Suite 874 → **876**. Deliberately NOT a repo-wide non-ASCII lint: 169 such prints across
      44 files, so a hard gate would be switched off within a week.
    - **STILL OPEN (needs his sign-off before code):**
      (a) **Step 3 — `.env` secrets into the key store.** Deliberately last and separable.
      (b) **Cloud→desk sealed fact backlog.** The gap: turns the cloud brain answered while the
      desk was OFF are never persisted (the level-3 bridge already forwards fine when the desk is
      UP — it shipped `b125b9a`; `cloud_gateway.py` stores nothing and Render's filesystem is
      ephemeral). Design: desk owns an **X25519 keypair — already generated in the Step 1
      ceremony**, only the PUBLIC half goes to Render; the cloud seals each turn, queues it
      **before** replying, one file per record in a private GitHub repo (durable, zero new infra,
      the PAT already exists); the desk drains on boot/reconnect, decrypts, and feeds each turn
      through the **existing** `extract_and_store_memory` so attribution is unchanged by
      construction. Records are idempotent by UUID; filenames carry no metadata.
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
    - ✅ **PHASES 1–3 DONE 2026-07-26** (`947785c` + the phase-3 commit). Nothing is wired
      yet, so today's one-shot path is untouched. **P1** — NEW `modules/tool_calls.py`
      (`ToolTurn`/`ToolCall` + `normalise_openai_*`, dependency-free) and
      `llm_router.universal_tool_call()`: cascade groq → gemini → openrouter, **no ollama
      and no local tail** (a hallucinated tool call is worse than a slow sentence; if every
      cloud provider is out, an agent task fails honestly). Gemini goes through Google's
      **OpenAI-compatibility endpoint** rather than the SDK's `FunctionDeclaration` dialect,
      so all three providers share one request/response path. Separate `GROQ_TOOL_MODEL`
      (`llama-3.3-70b-versatile`) and `OPENROUTER_TOOL_MODELS` — the 8B instant model cannot
      hold a tool loop, and plenty of good free chat models reject `tools` outright.
      Malformed arguments are FLAGGED with the raw string kept (never read as "no
      arguments", which would run a tool with defaults nobody asked for); a turn with
      neither text nor a call is a failure, not a success. **P2** — NEW
      `modules/agent_core.py`: decide→act→observe with everything injected
      (`call_model`/`execute`/`authorize`/`clock`). Caps report `stop_reason` + `ok=False`
      and never narrate success; more than 8 tools is an explicit refusal rather than a
      silent trim; ONE repair per bad call; tool errors are handed back to the model (a
      missing file is information) but three in a row stops the run; truncation is
      announced. **P3** — NEW `modules/agent_tools.py`: 10 curated tools over real
      `action_engine` action_types in named sets (`research` — read-only by construction,
      the safe first intent — plus `files` and `authoring`), `governance_manager.get_tier()`
      as the single tier source, and CONFIRM **refused** in an unattended run (a
      self-approving CONFIRM would defeat the tier system). `ActionEngine`'s
      `GOVERNANCE_BLOCKED:` / `GOVERNANCE_CONFIRM:` / `TIER_BLOCKED:` sentinels **raise**
      instead of being returned — handing a refusal back as a tool *result* reads to the
      model as success. Two static cross-checks assert every registered action_type exists
      in `governance.json` AND has a dispatch branch in `action_engine.py` (a typo would
      fail-safe to BLOCK and look like policy rather than a bug). test_tool_call 28 +
      test_agent_core 24 + test_agent_tools 27 → **SUITE 537 → 616, 26 harnesses.**
    - ✅ **PHASE 4 DONE 2026-07-26** (`dbb5fc6`) — ONE wired intent, flagged off. NEW
      `modules/agent_runner.py`: `JARVIS_AGENT_LOOP` (default OFF) plus a narrow
      `should_use_agent()` gate, wired in main.py's backdoor/HUD path BEFORE the planner,
      falling back to the one-shot pipeline on any failure — so the flag cannot cost a
      working command. `agent_core` became **async** with the engine lock taken ONLY around
      tool execution, never across a model turn, an authorisation or a human wait; sync tools
      go to `asyncio.to_thread`. CONFIRM resolves **by presence**: AT_DESK asks the HUD and
      continues in place with `governance_bypass=True`. Inbound answers arrive as
      **`POST /api/agent/confirm`** (nothing reads client→server WS frames — the same reason
      click-to-talk is a POST) and land in NEW `modules/agent_confirm.py` (Future keyed by
      `secrets.token_hex`; unanswered = REFUSAL, double-resolve is a no-op). `agent_tools`
      became a `ToolRegistry` that refuses BLOCK **at registration**. Live-verified against
      real Groq: `llama-3.3-70b-versatile` does invoke tools, and that call caught what no
      harness could — Groq sends `arguments="null"` for zero-arg tools, which the parser was
      flagging as malformed and would have burnt the ONE repair on every `system_status`.
    - ✅ **PHASE 5 DONE 2026-07-26** — the away yield, compaction, sub-agents. **Away yield:**
      NEW `modules/agent_yield.py` — when the owner is not at the desk a CONFIRM-tier call is
      **parked as a durable task** (`enqueue` → `mark_needs_confirmation` → `mark_reported`)
      and `owner_notify` pings his phone with the phrase that resumes it; "approve task
      &lt;id&gt;" then flips it to pending and the `OvernightWorker` re-runs the EXACT payload with
      `governance_bypass=True`. It writes into the task-queue lane, NOT the governance pending
      slot — that slot is an in-memory singleton the next unrelated command supersedes, and an
      away yield has to survive a restart and an overnight wait. `mark_reported` is immediate
      so the worker's report sweeper doesn't buzz the phone twice about one action; the LOOP is
      told the call was REFUSED and "NOT DONE" (a parked action returned as a tool result reads
      as success); at most ONE park per run. The phrase now also works at the desk, and the
      remote/Telegram path is wired to the loop with `presence="remote"` forced — a HUD confirm
      frame cannot be answered from Telegram, so the *channel* decides which authorisation
      surface exists. **Compaction:** `agent_core.compact_messages` drops the OLDEST completed
      steps past `max_transcript_chars` (20k) in whole assistant+tool-result groups — an
      orphaned `tool` message is a 400 from every provider — and replaces them with a note
      saying the detail is GONE rather than paraphrasing it (a paraphrase lets the model keep
      quoting a file it can no longer see). Trimming happens BEFORE the request, since the
      point is the tokens that leave the machine. **Sub-agents:** NEW
      `modules/agent_subagents.py` — one `delegate_subtask` tool that runs the same loop on a
      read-only set and returns ONE sentence. Depth 1 by construction (the helper's tool list
      cannot contain the delegate), read-only checked at build time (`UnsafeSubagentError`), a
      failed helper raises `ToolFailure` instead of an empty success, and `unlocked_tools`
      exempts the delegate from the engine lock — a nested loop takes that same lock itself and
      `asyncio.Lock` is not reentrant. HUD: `agent_parked` frame + `sub:`-tagged nested steps +
      a TRIMMED row. test_agent_core 44 + test_agent_runner 33 + test_agent_subagents 14 +
      test_agent_yield 15 → **SUITE 666 → 717, 29 harnesses.**
      A SECOND intent is now wired — a narrow **write** ("write a note called todo.md saying
      …") routed to the `authoring` set via `tool_set_for()` — because the read intent's set is
      read-only by construction, which made the desk-confirm and away-park paths code nobody
      could exercise.
    - ✅ **LIVE-GATED 2026-07-26 — TEST_PLAN §23, 14/14 rows PASS** (`eee4b3a`, `7db371f`,
      and the fallback fix). Real `llama-3.3-70b-versatile` against the real filesystem found
      **four** bugs no harness could reach, because each lived in the gap between what a tool
      returns and what a model can do with it: (1) `list_directory`'s HUD `render_file_list`
      payload made the model declare modification times "not provided" and abandon "most
      recent"; (2) a bare filename from a listing resolves against a DIFFERENT root than the
      reader tool's; (3) `list_directory` (home-only) and `workspace_read` (WORKSPACE_ROOTS)
      do not share a sandbox and the sandbox refusal arrived as ordinary DATA, so the loop
      thrashed roots to the step cap; (4) after a DENIED loop main.py fell back to the
      one-shot path, which re-attempted the write as `create_note` and staged a fresh VOICE
      confirmation — declining by silence got the owner asked again by another route. Fixes:
      `ToolEntry.format_output` + `format_directory_listing`, full paths in listing rows,
      `workspace_note()` naming listable-vs-readable separately, "Access denied" as a
      raise-sentinel, and no one-shot fallback on an authorisation boundary (desk + remote).
      Also `JARVIS_AGENT_TRANSCRIPT_CHARS`/`JARVIS_AGENT_MAX_STEPS` plus `[AGENT] limits` and
      `[AGENT] compacted` log lines — compaction was previously invisible outside the HUD.
      Evidence worth keeping: Telegram approve arrived **via the cloud bridge**; a desk expiry
      at ~124 s left **0 tasks parked** (expiry is a refusal, not a yield); an unrelated
      command answered in **under 1 s** with a prompt open; 10 compactions and 5 TRIMMED rows
      with no provider 400. Gotcha: a desk prompt needs `at_desk` **at run start** — the 10 s
      desk-freshness bound correctly routes a stale verdict to the away park instead.
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
    REMAINING for a real pass: A2 (backend up) and the manual §0–§22. **A3 no longer
    exists** — closed 2026-07-30, see the update below.

    **Update 2026-07-26 — A3 settled, no pytest.** Measured with a stub `pytest` (nothing
    installed): the 24 pytest-gated tests would give ~12–17 green and 6–7 red, so the install
    buys red tests. Decision: **do not install** — a second command `run_harnesses.py` cannot
    gate is the real cost. `test_governance.py` (the risk-tier guard, 4/4) was **converted to
    self-running** and is now inside the one command; suite **753/753, 31/31 harnesses, ~12 s**.
    Left as a separate cleanup, in priority order:
    - **convert next (both would pass as-is):** `test_android_tv_agent.py` (6, needs
      `monkeypatch` → `mock.patch`), `test_github_agent.py` (5, `tmp_path` → `tempfile`).
    - **pre-async fossils — rewrite or retire, do NOT trust:** `tests/test_briefing.py`
      (patches `action_engine.CalendarAgent`, a function-local import), `tests/test_hardware.py`
      (calls `ActionEngine.execute` synchronously; it is `async def` since the event-loop fix),
      `tests/test_scheduler.py` (patches `background_monitor.speaker`, no longer imported), plus
      one stale mock in `test_gmail_agent.py` (wires a `messages.get` metadata call the current
      `reply_email` doesn't make — production is correct, the mock is a shape behind). They
      describe an architecture JARVIS has already left; they gate nothing and count nowhere.
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

**✅ CLOSED 2026-07-26 — the HUD camera panel no longer opens its own connection to the
phone.** `/api/vision/state` used to hand the frontend the phone's raw `camera_url`, so
`CameraFeedWidget` pulled MJPEG *directly from the phone* — exactly the second-consumer
pattern `frame_bus` exists to kill (an IP Webcam endpoint serves one MJPEG client), and a
desk-camera URL in the browser besides. New contract: NEW pure
`camera_stream.stream_info(bus_active, env)` → `{stream_available, stream_path}`, spread into
`/api/vision/state` (which no longer contains a camera address at all, error branch included).
`stream_available` is False when **nobody is publishing OR `JARVIS_CAMERA_STREAM=0`**, so the
panel never requests a stream that could only 404. `ambient_vision._grab_frame` now
**publishes** the frame it takes when it falls back to its own capture — a momentary owner
was previously invisible to the bus, which is what kept the panel dark with the gesture daemon
off. Frontend: streams `${API_BASE}/api/camera/stream?fps=12&n=<nonce>`, **re-requests every
100 s** (the server caps one response at 120 s and a finished multipart `<img>` freezes
silently — no error event fires, so a timer is the only way to notice), retries 4 s after an
error, re-attaches when a publisher returns, and distinguishes **OPTICAL FEED OFFLINE**
(camera unreachable) from **OPTICAL FEED IDLE** (no owner publishing — detections may still be
flowing). `test_camera_stream` 10 → **13** (advertised payload is a local path with no camera
address; unavailable with no publisher; unavailable behind the kill switch). **SUITE 522 → 525.**
LIVE-GATE OWED — see the camera panel entry in §7.

**Phone stream is flaky:** it went unreachable mid-session and returned on `.105` within a
minute (WiFi power saving). Pin a DHCP reservation + disable battery optimisation for IP
Webcam, or the daemon keeps taking 30s blind-retry windows.

---

### 6.6 Partner messaging — outbound propose-and-approve + inbound pull (NEW 2026-07-26, DONE, live gate owed)

Owner-facing only; guests gained nothing. Two actions, both funnelling through the
existing gates rather than around them.

**`message_partner` (CONFIRM).** "Ask my girlfriend if she's eaten" → JARVIS drafts →
the confirm read-back names the **resolved partner** and quotes the **full message
verbatim** (`partner_messaging.confirm_prompt`; `agent_confirm.question_for` clips at 120
chars, which is wrong for words said to a person) → "confirm" sends, "cancel" does not.
- Recipient is **allowlist-only**: `modules/partner_registry.py` maps role words *and*
  first names (girlfriend/gf/mousumi, brother/kinshuk) to `TELEGRAM_GF_ID` /
  `TELEGRAM_BROTHER_ID`. Anything containing a digit is refused as a raw-chat-id attempt
  *before* the alias table is read; unknown ("Priya") and vague ("her", "someone") are
  refused honestly. The model picks among registered partners; it never supplies an address.
- **Deny is terminal** (`partner_messaging.SendGuard`, checked in the engine — the one
  place every route funnels through, so voice, HUD, phone, or a second action in the same
  LLM reply all hit it). Same discipline as the §5 #12 bug-#4 fix. 5-min TTL
  (`JARVIS_PARTNER_DENY_TTL_S`) so he can deliberately change his mind. A staged send is
  also not staged twice — one prompt, one send.
- **Send-only, never autonomous.** `telegram_bot.send_text_to_partner` re-checks the id
  against `_IDENTITIES` and refuses the admin id, and it has exactly two callers
  (transport + engine, harness-asserted). Not an agent tool.

**`summarize_partner_chat` (AUTO in governance, ADMIN-ONLY via `tier_allows`).** The pull
that fixes the channel-isolation blind spot: a partner's chat lives in her own session, so
"what did my girlfriend tell you" used to fail honestly. Rows are read for one slot only.
Nothing is ever pushed. Guests are refused before dispatch (neither action is on
`VIP_GUEST_ALLOWED_ACTIONS`; the allowlist was not touched).
> ⚠️ **SCOPE SUPERSEDED 2026-08-02 — see §6.7.** The transcript-on-demand direction this
> action represents has been replaced by the **butler-discretion model**. The code is still
> in the tree, still harnessed, still gated by TEST_PLAN §24 — this note supersedes the
> *design direction*, not the history. **Open decision, Kaustav's:** §6.7 answers "did she
> talk to you" with fact-of-contact and withholds content by default, which is the opposite
> of what this action does on demand. Building §6.7 without deciding this action's fate
> (remove / keep behind a second explicit flag / leave as the deliberate override) would
> leave two contradictory answers to the same question. Decide before building, not after.

**The logging is opt-in.** `modules/partner_log.py` writes a partner's INBOUND messages to
`partner_messages` **inside the existing `jarvis_longterm.db`** (no new store) and only when
`JARVIS_LOG_PARTNER_CHATS=1`. Off ⇒ no rows, no table. Every summary leads with a disclosure
that it is logged data. Kaustav switched it **on** in his `.env` 2026-07-26.
⚠️ **Scope, explicitly ruled by Kaustav:** the flag governs this raw store ONLY. JARVIS's
per-user memory extraction (`brain.extract_and_store_memory` → `memory_manager`, keyed
`user='MOUSUMI'`) has always run for every recognised caller and keeps running either way —
that is what makes him know her warmly in her own chat. So "off" means *no verbatim
transcript*, not *nothing retained*.
**TIER C #11a (encryption at rest) — ✅ DONE 2026-07-30.** `partner_messages.content` and
`.partner_name` are AES-256-GCM encrypted; `partner_slot`/`direction`/`timestamp` stay readable
because every query filters on them. So a stolen copy of the file shows *that* a slot was active
and when, but not a word of what she said. The opt-in flag is unchanged — off still writes
nothing, not even the table.

Harness: `test_partner_messaging.py` (34 checks, refusal-weighted). Live gate: TEST_PLAN §24.

---

### 6.7 Partner-inbound — the BUTLER DISCRETION model (NEW 2026-08-02, DESIGNED, **NOT BUILT**)

The "did she talk to you?" capability. **Supersedes the `summarize_partner_chat` scope in
§6.6** — this is not transcript-on-demand, and the difference is the whole point.

A good butler says *"Madam rang, around three — nothing pressing."* He does not recite what
was discussed. He would, instantly, if she had said it was urgent. That is the model.

**The four beats**

1. **Contact.** A partner messages JARVIS. He reads it — necessarily, both to help her and to
   judge what follows — and records the **fact of contact + timing**: *"Mousumi contacted
   ~3pm."* That record is the durable artefact.
2. **Assess.** Routine, or genuinely needs the owner? Routine ⇒ the content stays **private**;
   only fact-of-contact is retained. Urgent — she explicitly flags needing him, or it reads as
   an emergency — ⇒ that **flag** is surfaced to the owner.
3. **Answer.** Owner asks "did she talk to you?" ⇒ JARVIS answers the **fact**: *"Yes, around
   3pm, nothing urgent"* / *"Yes, and she said it's important you call."* He does **not**
   volunteer the **content** unless the urgency threshold was crossed.
4. **Default to discretion.** Confirm contact, timing, urgency. Keep content private unless
   it is genuinely necessary. Deliberately gentler than logging transcripts.

**Honest caveats — build this knowingly**

- **He still READS her message.** Discretion here means *read, assess, keep private* — NOT
  *never read*. The content is **processed, then kept-private or dropped**, not
  retained-readable. Anyone reasoning about this as "he doesn't see it" is reasoning about a
  different feature.
- **Still opt-in, default OFF** — a `JARVIS_LOG_PARTNER_CHATS`-style gate. Turning it on stays
  a conscious owner choice, exactly as the raw store is today.
- **The relational question does not go away.** Does she know JARVIS exists, and that the
  owner can ask whether she made contact? The butler model very likely clears the bar that
  transcript-logging did not — fact-of-contact is roughly what a housemate would observe
  anyway — but it is **still the owner's call**, not a technical one, and not one this
  document can settle for him.
- **Urgency assessment is a judgement call made by an LLM.** It will sometimes be wrong in
  both directions: a real emergency read as routine, or something private escalated because it
  used urgent-sounding words. Whatever gets built needs to fail toward *surfacing the flag*
  (a false alarm costs a phone call; a missed emergency costs more) while never surfacing
  content on a mere false alarm.

**Status: DESIGNED, NOT BUILT.** Sits *after* `message_partner` — the clean outbound half,
already done — and both sit **after the §7 live-gate** in roadmap order.
⚠️ **Tradeoff if you build it sooner:** that means reordering ahead of the live-gate session,
and the live gate is what unblocks Electron launch scripts → Electron packaging → mobile. So
building §6.7 first does not just delay the gate, it **pushes the whole mobile arc later**.
Worth it only if he decides the capability outranks that chain.

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

**PUSH STATUS 2026-08-01:** `origin/feat/cloud-gateway` is caught up with HEAD at `9c8c5eb`
(the cp1252 CLI hardening + governance guards, §11a). Suite
**876 checks / 39 harnesses, 0 failures**; 3 environmental non-greens (`test_ping` and
`test_ui_bridge_e2e` need the backend up; `test_screen_reader.py` is a live VLM script, not a
counted harness). **The old "4 need pytest" line is gone — tier A3 was closed 2026-07-30**, the
last three pytest-only files were converted, and `tests/` was retired.

*Superseded, kept for the trail —* **PUSH STATUS 2026-07-25:** commits landed since `99281e3`: `fb30e50` enroll mirror,
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
- **C#11a memory-at-rest encryption (NEW 2026-07-30) — the "locked, not amnesia" gate.**
  Normal boot first: JARVIS must wake and recall facts with **no prompt for anything** (the
  DPAPI wrap is the whole point — a key that asks for a passphrase would break watchdog
  respawn and the overnight worker). Then rename `jarvis_key.dpapi` aside and restart: asking
  him something he knows must produce **"Long-term memory is LOCKED — the key store is
  unavailable"**, NOT a cheerful "you never told me that". *A silent empty read is the single
  worst outcome here — it is indistinguishable from having forgotten you.* Rename the key
  back → recall works again. Then prove recovery end to end:
  `manage_keys.py restore-key` with the printed recovery code rebuilds the boot wrap on a
  fresh profile and the same rows still decrypt. Finally `manage_keys.py verify` → canary OK.
  Sanity-check the file itself: `jarvis_longterm.db` opened in any hex viewer must show no
  readable fact text.
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
- **Click-to-talk (2026-07-26):** with him ONLINE and idle, click the MicIndicator —
  within one listen window (~3s) the wake SFX plays and the HUD goes to LISTENING; speak a
  command and it must run. Console shows `[WAKE] Listen requested by hud (mic button)`.
  Then with him OFFLINE, click it: he must BOOT through the normal biometric face-auth
  path (identical to saying "wake up") — **never** straight in as admin. Click while he is
  mid-sentence: he must finish speaking, then start listening (that pause is deliberate;
  cutting him off is barge-in, which is still deferred). Click, then ignore him for ~20s:
  the request must EXPIRE, not open the mic late. Finally, with the backend stopped, click
  it: the log line reads `MIC REQUEST FAILED — BACKEND UNREACHABLE`, no silent nothing.
- **HUD camera panel (2026-07-26 migration):** open the panel with the gesture daemon
  running → live picture **plus** detection boxes, and the phone's own client counter must
  show **ONE** connection, not two. Kill every publisher (`JARVIS_GESTURE=0`, no scan
  running) → the panel must read **OPTICAL FEED IDLE** (not OFFLINE) and pick the feed back
  up by itself when a publisher returns. Leave the panel open **>2 minutes** — it must not
  freeze on a stale frame (the client re-requests at 100 s, ahead of the server's 120 s cap).
  Unplug/stop the phone app entirely → **OPTICAL FEED OFFLINE**. `JARVIS_CAMERA_STREAM=0` →
  no feed at all, and the panel says IDLE rather than spamming a 404.
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
  **no pytest in the venv — settled, not owed.** Decision taken 2026-07-30 (D#13): do NOT
  install pytest; the last pytest-only files were converted and `tests/` was retired, so
  every harness now runs inside the ONE gated command. `TEST_PLAN.md` §A3 is closed and
  there is no pytest-gated tier left. The cost of installing would be a second command
  `run_harnesses.py` cannot gate.
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
