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
| **G5.4** distance mitigation | ⬜ TODO | §5 |
| **G5.5** precision / dual-target filtering | ⬜ TODO | §5 |
| **G5.7** backlog (mic affordance + robustness) | ⬜ TODO | §5 |
| **Login/wake revamp** | ⬜ TODO (spec ready, §6) | §6 |
| **Away→mobile presence (Track B probe)** | ⬜ TODO | §5, §6 |
| **Smart-home / IoT agent** | ⬜ MISSING | §5 |
| **Guarded self-improvement loop** | ⬜ MISSING | §5 |
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
3. **G5.4 distance mitigation** — crop-around-face ROI (reuse `face_gate` detection),
   720p option, tracking-confidence knobs → control from across the room.
4. **G5.5 precision / dual-target filtering** — when the hand is slow, clamp harder so
   tiny targets (×, text lines) are selectable.
5. **G5.7 robustness backlog:**
   - Backend: barge-in thread/stream leak on interrupt; boot config preflight ("what's
     missing" from `.env`); `_call_ollama` empty-200 should fail over, not return `""`;
     `working_memory` cross-thread lock; log swallowed `speak_text` TTS errors;
     `watchdog.py` give-up + owner alert on a permanently-broken respawn loop.
   - Frontend: `BrowserWidget` iframe fallback for framing-blocked sites;
     `DataOverlay` Escape/focus-trap; `CalculatorWidget` `eval()` → safe parser;
     connection-based (not time-based) boot log; command-terminal error surfacing +
     labeled input.

### TIER B — experience upgrades (after Tier A)
6. **Login / wake revamp** (spec in §6) — staged boot, identity step, believable
   Kaustav face-auth.
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
