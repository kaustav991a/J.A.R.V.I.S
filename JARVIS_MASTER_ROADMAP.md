# JARVIS — Master Roadmap

> **Single source of truth.** Read this first when resuming. It replaces and folds in
> the old `ROADMAP_TO_FULL_JARVIS.md`, `RELIABILITY_HARDENING.md`,
> `HAND_GESTURE_CONTROL_PLAN.md`, `UPGRADES_AND_FLUIDITY.md`, `MOBILE_PRESENCE_PLAN.md`,
> and `LOGIN_REVAMP_PLAN.md` (all deleted 2026-07-19). The **test plan lives
> separately** in `TEST_PLAN.md`. Last updated 2026-07-19.
>
> **Working branch:** `feat/cloud-gateway`. Many commits are UNPUSHED (see §7).
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
| **Automatic test baseline** | ✅ 205/205 green | `TEST_PLAN.md` |
| **G5.7** mic/voice affordance (visible) | ✅ DONE (`3d3063d`); voice click-to-talk = follow-up | §5 |
| **G5.3** cursor-halo + edge-toast overlays | ✅ code done, live-gate owed | §5 |
| **G5.4** distance mitigation | ✅ code done, live-gate owed | §5 |
| **G5.5** precision / dual-target filtering | ✅ code done, live-gate owed | §5 |
| **G5.7** robustness backlog | 🟡 backend 5/6 done (barge-in deferred); frontend TODO | §5 |
| **Login/wake revamp** | 🟢 face-auth contract + FaceAuthOverlay + BootSequence + IdentityPrompt done (code); live-gate owed | §6 |
| **Away→mobile presence (Track B probe)** | ⬜ TODO | §5, §6 |
| **Smart-home / IoT agent** | ⬜ MISSING | §5 |
| **Guarded self-improvement loop** | ⬜ MISSING | §5 |
| **Agentic core (Claude-Code-grade tool loop)** | ⬜ MISSING — foundational for self-improvement | §5 (Tier B #12) |
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
2. ✅ **G5.3 overlays** — DONE (code). `cursor_overlay.py` = separate always-on-top
   **click-through** process (WS_EX_TRANSPARENT + WS_EX_NOACTIVATE — the gesture cursor
   still clicks the app beneath; the overlay only draws). Cursor halo recolours by pose
   (palm=cyan move · fist=amber grab · two_finger=cyan scroll · back_palm=dim clutch) +
   edge toasts on transitions (HAND READY / JARVIS DRIVING / YOU HAVE CONTROL /
   UNAUTHORIZED / CONTROL OFF). `gesture_daemon` spawns it (`JARVIS_GESTURE_OVERLAY=1`
   default, win32-only), streams state frames to its stdin each `_hud`, kills on stop;
   overlay polls its own cursor pos (~60fps) so smoothness is IPC-independent, exits on
   stdin EOF like `lock_overlay.py`. Live-gate owed (see §7). **Follow-up:** click-flash
   (engine exposes no pinch pose — click is an intent, not `pose`; add a click pulse when
   state carries the click event). Biggest *felt* gesture jump.
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
   - Frontend — ⬜ TODO: `BrowserWidget` iframe fallback for framing-blocked sites;
     `DataOverlay` Escape/focus-trap; `CalculatorWidget` `eval()` → safe parser;
     connection-based (not time-based) boot log; command-terminal error surfacing +
     labeled input.

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
7. **Away→mobile presence Track B** — `modules/presence_probe.py` (phone-on-WiFi
   ARP/TCP/ping + HOME/AWAY debounce) feeding `owner_notify` routing. No app. (Note:
   away→phone *already works* via Telegram; this adds *automatic* presence.)

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
    - **Native path: Claude Agent SDK** (Anthropic's lib — gives an app the exact
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
12. **Full `TEST_PLAN.md` pass** — automatic (Claude) + manual (Kaustav). Gate to Electron.
13. **Electron packaging** — single .exe boots FE+BE; notch → fullscreen takeover
    overlay (live agent-cam). ⚠️ `jarvis-frontend/package.json` lost its electron config
    in the Jul-4 history rewrite (no `main`/builder block/deps) though `node_modules`
    still has electron + a stale Jun-28 `release/*.exe`; `NotchView.jsx`/`SidecarView.jsx`/
    `electron/` are untracked. To resume: restore the package.json electron config,
    reconcile the stale Notch/Sidecar views with the current HUD, rebuild.
14. **Mobile app (Track C)** — Flutter (recommended): FCM push leg on `owner_notify`,
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
  **Follow-ups:** real `auth_face_matching` phase (needs an `on_phase` callback +
  face-box coords inside `scan_for_faces`) + live camera feed in the overlay (needs
  the cam URL exposed to the frontend). **Live-gate owed** (§7).
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
  (§7). Remaining §6.1: none blocking — only the deferred matching-phase + live-cam-feed
  follow-ups above.

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
loses-to-next-working-source). **USB still owed** — needs from
Kaustav: DroidCam PC client installed (→ USB = a webcam index) or ADB path
(`adb forward tcp:4747 tcp:4747` → `127.0.0.1:4747/video`); + app version. **Live-gate:**
kill the first source mid-run / start with only the 2nd app streaming → daemon picks the
reachable one; confirm a fully-dead list logs the summary + retries in 30s.

---

## 7. Live-gates owed by Kaustav (hardware) + push status
All gesture/UX code is committed but UNPUSHED on `feat/cloud-gateway`. Owed before push+merge:
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
- **Phase-4 phone smoke-tests + Phase-5 failover** — see `TEST_PLAN.md` §B5/B6.
- Then **push + merge** `feat/cloud-gateway`.

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
