# JARVIS — Test Plan (Automatic + Manual)

> The **one** test doc. Prove JARVIS is bug-free on the desktop **before** the
> Electron build. Split into **PART A — Automatic** (Claude runs, no human/hardware)
> and **PART B — Manual** (Kaustav runs: voice / camera / phone / GUI). Run PART A
> after every change; run PART B once, right before Electron. Roadmap: `JARVIS_MASTER_ROADMAP.md`.
> Last automatic baseline: **2026-08-01 — 876/876 green, 39/39 harnesses, ~21 s.**
> (Was 874/39 on 2026-07-30; +2 checks from the cp1252 guards in `test_governance.py` —
> no new harness. Was 787/32 on 2026-07-26: +5 harnesses from C#11a memory-at-rest
> encryption, +3 from the D#13 pytest conversions; A3 is closed and `tests/` is retired.)
> Legend: ✅ pass · ⚠️ partial · ❌ fail · ☐ not yet run.

---

# PART A — AUTOMATIC (Claude runs, no hardware)

**One command**, from `jarvis-backend`, with the venv:

```
.\venv\Scripts\python.exe run_harnesses.py
```

It runs every self-running harness in a subprocess, prints per-harness checks +
timing, totals them, and **exits 1 if anything fails** — so it works as a gate.
Pass/fail comes from the exit code; the counts are parsed from each harness's own
summary line. When a new harness lands, add its filename to `HARNESSES` in that file.

| Harness | Checks | Covers | Status |
|---|---:|---|---|
| `test_action_parser.py` | 24 | LLM-reply → action parse spine (fences, prose, arrays, truncation) | ✅ |
| `test_agent_core.py` | 44 | agentic loop: caps, one-repair, governance hook, honest stops, compaction | ✅ |
| `test_agent_runner.py` | 40 | wired intents + flag, desk confirm, away park, delegation, narration | ✅ |
| `test_agent_subagents.py` | 14 | sub-agent: depth 1, read-only check, answer-not-output, honest failure | ✅ |
| `test_agent_tools.py` | 49 | tool registry: curated sets, tier adapter, refusal sentinels | ✅ |
| `test_agent_yield.py` | 15 | away yield: exact payload parked, one ping, "approve task &lt;id&gt;" resume | ✅ |
| `test_ambient_camera.py` | 15 | `ambient_vision` resolves its camera off `JARVIS_CAM_SOURCES` | ✅ |
| `test_auth_status.py` | 18 | face-auth WS frame contract + `normalise_box` (G6.1) | ✅ |
| `test_backdoor_gate.py` | 15 | `/api/backdoor` auth gate: flag OFF ⇒ 403 while locked, ON ⇒ bypass, wiring | ✅ |
| `test_boot_preflight.py` | 14 | boot preflight: required/recommended env + model files | ✅ |
| `test_calibrate_gesture.py` | 14 | G5.2 wizard derivations (palm_sign, pinch, reach) | ✅ |
| `test_camera_stream.py` | 13 | MJPEG re-broadcast generator, **loopback-only** guard, feed advertisement | ✅ |
| `test_cursor_overlay.py` | 61 | halo/toast/ripple geometry, colour-key verify, deadman, clamping | ✅ |
| `test_enroll_face.py` | 17 | enrollment quality gate + diversity report | ✅ |
| `test_face_gate.py` | 12 | owner/stranger/absence + `StrangerConfirmer` + uncertain floor | ✅ |
| `test_failure_detection.py` | 17 | honest failure vs false "Done, Sir" | ✅ |
| `test_frame_bus.py` | 30 | one camera owner, many readers: seq, staleness, copy-on-read | ✅ |
| `test_gesture_arbiter.py` | 28 | cursor ownership referee (hold/mark/self-heal) | ✅ |
| `test_gesture_calibration.py` | 48 | calibration JSON schema, persistence, defaults<JSON<env | ✅ |
| `test_gesture_camera.py` | 46 | source list parse, reachability probe, open-first-available | ✅ |
| `test_gesture_engine.py` | 64 | gesture state machine: relative/clutch/dwell/precision/grab-transit | ✅ |
| `test_gesture_roi.py` | 29 | distance ROI crop + landmark remap (no-jump proof) | ✅ |
| `test_governance.py` | 4 | **risk tiers**: AUTO pass, BLOCK, CONFIRM pending→consume, unknown-action fail-safe | ✅ |
| `test_listen_request.py` | 12 | click-to-talk flag: one-shot, expiry, cross-thread exactly-once | ✅ |
| `test_llm_failover.py` | 7 | ollama empty-200 raises → cascade escalates instead of "" | ✅ |
| `test_owner_notify.py` | 20 | owner fan-out (desk/TTS/phone) + presence-aware legs | ✅ |
| `test_partner_messaging.py` | 34 | partner send/pull gates: allowlist-only recipient, raw-id refused, verbatim confirm, terminal deny, admin-only summary, logging OFF default | ✅ |
| `test_presence_probe.py` | 25 | ARP/TCP/ICMP ladder, asymmetric debounce, fuse + routing | ✅ |
| `test_speaker_errors.py` | 5 | TTS failure logged + swallowed, flag still resets | ✅ |
| `test_tool_call.py` | 35 | tool-turn normalisation + `universal_tool_call` cascade (fake HTTP) | ✅ |
| `test_watchdog_policy.py` | 14 | respawn give-up policy + owner-down alert (live log untouched) | ✅ |
| `test_working_memory_lock.py` | 4 | RLock concurrency + snapshot copies | ✅ |
| **Total** | **787** | | **✅ 0 failed** |

**A2 — semi-automatic (need the backend up; Claude can drive):** `test_ping.py`,
`test_ui_bridge_e2e.py`. Start `uvicorn main:app --host 127.0.0.1 --port 8000`, then run.
⚠️ `test_ui_bridge_e2e.py` drives `POST /api/backdoor`, which is now gated: boot that
backend with **`JARVIS_ALLOW_BACKDOOR=1`** (or authenticate first by wake word), else the
POST comes back `403 {"status":"refused"}` and the harness sees no UI frames.

**A3 — ✅ CLOSED 2026-07-30 (D#13). There is no pytest-only tier any more.**

Every prediction in the 2026-07-26 table below was confirmed by actually running the code.

| File | Tests | Outcome |
|---|---:|---|
| ~~`test_governance.py`~~ | 4 | ✅ converted 2026-07-26 — risk-tier guard runs inside PART A |
| `test_android_tv_agent.py` | 6 | ✅ **converted, 6/6** — `monkeypatch` → an explicit save/restore context manager |
| `test_github_agent.py` | 5 | ✅ **converted, 5/5** — `tmp_path` → `tempfile`, cleaned up by the harness |
| `test_gmail_agent.py` | 3 | ✅ **converted, 3/3** — and the predicted defect was real (see below) |
| ~~`tests/test_briefing.py`~~ | 2 | ❌ **RETIRED** — `action_engine.CalendarAgent` no longer exists (function-local import); 0/2 |
| ~~`tests/test_hardware.py`~~ | 2 | ❌ **RETIRED** — `ActionEngine.execute` is a coroutine; the test asserts on the un-awaited object; 0/2 |
| ~~`tests/test_scheduler.py`~~ | 2 | ❌ **RETIRED** — patches `background_monitor.speaker`, an attribute that module no longer has; 0/2 |

**`test_gmail_agent.py` was NOT a stale test — it was a stale *mock*, and the diagnosis here was
exactly right.** Its `reply_email` fixture wired a second `messages.get` metadata call that the
implementation never makes; `reply_email` reads the headers straight off the
`threads().get(format="metadata")` response. Production was correct, the mock was a shape behind,
so the test asserted an empty `In-Reply-To`. Fixed by moving the headers inline into the thread
response. **The file had never once run, so nothing had ever caught it** — which is the argument
for A3 existing at all.

**`tests/` is deleted.** Coverage honestly lost: briefing concurrency + graceful degradation, TV
intent routing + unreachable-hardware fallback, scheduler dedup + midnight flush. Rewriting those
against the current async API is worth doing and is NOT done — it was outside this chore.

**Decision 2026-07-26 (Kaustav), still standing: no pytest install.** The cost isn't the ~5 MB,
it's a second command `run_harnesses.py` doesn't gate — which is exactly how a suite starts
drifting. Everything worth keeping was converted instead, so PART A is now the whole automatic gate.

**Not a harness:** `test_screen_reader.py` takes a real screenshot and calls the VLM —
a live script, deliberately excluded from the total.

---

# PART B — MANUAL (Kaustav runs)

> The detailed per-subsystem checklist below (§0–§14) is the 2026-06-27 manual plan,
> preserved. NEW subsystems added after it: §16 gesture control (G3/G4/G5.1/G5.2),
> §17 login/wake, §18 G5.0 resilience, §19 overlay/distance/precision (G5.3–G5.5),
> §20 click + grab (G6.2/G6.4), §21 camera sharing + auto-select (G6.3 + frame bus),
> §22 presence (Track B). Do the whole thing once before Electron.
>
> **Decision 2026-07-25 (Kaustav): gates run ONCE, at the end** — building does not
> stop to wait on them. Roadmap **§7** holds the long-form recipe (which knob to turn
> when a gate misbehaves) for everything from §19 down; the rows here are the checkboxes.

---

## 0. Pre-flight

| # | Step | Expected | ✓ |
|---|---|---|---|
| 0.1 | `cd jarvis-backend && python watchdog.py` | Watchdog banner; `uvicorn main:app` boots; no traceback | ☐ |
| 0.2 | Watch console on boot | `[GOVERNANCE] Ruleset loaded`, `[TELEGRAM] ✅ Gateway online`, daemons start (`[ROUTINES]`, overwatch, ambient) | ☐ |
| 0.3 | Open the React HUD in the browser | HUD renders; WebSocket `/ws` connects (no console errors) | ☐ |
| 0.4 | Confirm `.env` | `JARVIS_LLM_MODE=cloud_first`, `TELEGRAM_USER_ID`=your numeric id, token set | ☐ |
| 0.5 | Health check | `curl http://127.0.0.1:8009/health` → `watchdog: alive` | ☐ |

> Tip: to send a text command without voice, `curl -X POST http://127.0.0.1:8000/api/backdoor -H "Content-Type: application/json" -d '{"command":"<text>"}'`
>
> ⚠️ **The backdoor is gated (2026-07-26).** With `JARVIS_ALLOW_BACKDOOR` unset it only
> works on an already-authenticated session; while JARVIS is locked/asleep it answers
> `403 {"status":"refused","reason":"locked"}` and dispatches nothing. To run a whole gate
> from the HUD command line without face-scanning first (how §23 was driven), boot with
> `$env:JARVIS_ALLOW_BACKDOOR="1"` — a conscious, per-run choice, not a default.

---

## 1. Resilience / Watchdog (§3.1)

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 1.1 | Auto-restart on crash | In Task Manager, kill the `python … uvicorn main:app` child (NOT the watchdog) | Watchdog logs `💥 Server process exited`, relaunches within ~2 s; HUD reconnects | ☐ |
| 1.2 | Crash log | After 1.1, open `jarvis-backend/watchdog.log` | Crash + restart entries with timestamps | ☐ |
| 1.3 | Rapid-crash breaker | (Optional) Force repeated immediate failures | After `WATCHDOG_MAX_RAPID_FAILS`, backs off 30 s instead of spinning | ☐ |
| 1.4 | Authenticated shutdown | `curl -X POST "http://127.0.0.1:8009/shutdown?token=WRONG"` then with the real token | Wrong → `403`; right → `200`, watchdog + server stop, **no** restart | ☐ |
| 1.5 | Ctrl+C | Press Ctrl+C in the watchdog console | Clean shutdown of watchdog and child | ☐ |

---

## 2. Voice Loop (§1.3, biometrics)

| # | Test | Say / Do | Expected | ✓ |
|---|---|---|---|---|
| 2.1 | Wake word | "Hey J.A.R.V.I.S." / "Jarvis" | Wake acknowledged; HUD shows listening state | ☐ |
| 2.2 | Admin override | "Wake up" / "Initiate admin override" | Boot/briefing sequence | ☐ |
| 2.3 | Face ID | Be on camera at login | Recognized as KAUSTAV; unknown face → not authorized as admin | ☐ |
| 2.4 | STT accuracy | Speak a normal command | Transcribed correctly (Google STT) | ☐ |
| 2.5 | STT offline fallback | Disconnect internet, speak | Falls back to faster-whisper (`local_stt`); still transcribes | ☐ |
| 2.6 | TTS streaming | Ask anything long | Speaks sentence-by-sentence as it generates (not one big delay) | ☐ |
| 2.7 | Prosody tags | (Dev) feed text with `[pause:500]`/`[sigh]` | Audible pause / sigh | ☐ |
| 2.8 | Keyword barge-in | While he's speaking, say "stop" | Audio cuts immediately | ☐ |
| 2.9 | VAD barge-in | While he's speaking, just start talking | Audio interrupts on any speech | ☐ |
| 2.10 | TTS offline fallback | Disconnect internet, trigger a reply | Piper local voice speaks (`local_tts`) | ☐ |
| 2.11 | Click-to-talk (awake) | idle + online, click the MicIndicator | within ~3 s: wake SFX, HUD → LISTENING, spoken command runs; console `[WAKE] Listen requested by hud` | ☐ |
| 2.12 | Click-to-talk (offline) | system offline, click the MicIndicator | boots through the **normal biometric** path, same as saying "wake up" — **never** straight in as admin | ☐ |
| 2.13 | Click while speaking | click mid-sentence | he finishes speaking, **then** listens (deliberate — cutting him off is barge-in, still deferred) | ☐ |
| 2.14 | Click expires | click, then ignore him ~20 s | request expires; the mic does NOT open late | ☐ |
| 2.15 | Click with no backend | stop the backend, click | log reads `MIC REQUEST FAILED — BACKEND UNREACHABLE` (never a silent nothing) | ☐ |

---

## 3. LLM Routing & Latency (§3.4)

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 3.1 | Fast cloud path | Send "Hi" via Telegram | Reply in ~1–2 s; console shows Groq, **no** `[ROUTER] 'ollama' route failed` | ☐ |
| 3.2 | Local breaker (if local_first) | Temporarily set `JARVIS_LLM_MODE=local_first`, restart, send 2 messages | 1st may lag once; breaker opens (`⚡ circuit breaker OPEN`); 2nd is fast | ☐ |
| 3.3 | Vision stays local | Trigger a screen/scene read | Uses local `llava` regardless of mode | ☐ |
| 3.4 | Reset | Restore `JARVIS_LLM_MODE=cloud_first`, restart | Back to fast cloud reasoning | ☐ |

---

## 4. Code & Workspace I/O (NO-GUI rule)

> Intent: file/code operations must use **`workspace_*`** (Python under the hood) — **never** open Notepad + type.

| # | Test | Command | Expected | ✓ |
|---|---|---|---|---|
| 4.1 | Write a script | "Write a python script for a simple add function and save it to my desktop as add.py" | Routes to **`workspace_write`**; file created directly; **Notepad does NOT open**; fast | ☐ |
| 4.2 | Read it back | "Read add.py from my desktop" | `workspace_read` returns content | ☐ |
| 4.3 | Patch it | "In add.py change the function name add to plus" | `workspace_patch` edits in place; verify on disk | ☐ |
| 4.4 | Confinement | "Write to C:\\Windows\\system32\\evil.py" | Blocked / confined to workspace roots | ☐ |
| 4.5 | Extension routing | Any command naming a `.py/.js/.json/.md` file | Goes to `workspace_*`, never `native_app_launcher`/GUI | ☐ |

---

## 5. OS Control & Apps

| # | Test | Command | Expected | ✓ |
|---|---|---|---|---|
| 5.1 | Launch app | "Open Notepad" / "Open Chrome" | App launches (dynamic resolver; typo-tolerant) | ☐ |
| 5.2 | Web-app fallback | "Open Spotify" (if not installed) | Opens web fallback in browser | ☐ |
| 5.3 | Media control | "Pause" / "Play" / "Next track" | SMTC media control works; "unmuted" not misread as "muted" | ☐ |
| 5.4 | Volume / mute | "Mute" / "Volume up" | Correct action + correct spoken confirmation | ☐ |
| 5.5 | Lock | "Lock the screen" | Workstation locks | ☐ |
| 5.6 | Telemetry | "System status" / "How's the CPU?" | Single clean spoken metric (not a raw dump) | ☐ |
| 5.7 | Terminal | "List the files in my downloads folder" | `run_terminal_command`; sandboxed; blocked patterns refused | ☐ |

---

## 6. Governance (§3.x safety)

| # | Test | Command | Expected | ✓ |
|---|---|---|---|---|
| 6.1 | AUTO tier | "What's the weather?" | Runs immediately, no prompt | ☐ |
| 6.2 | CONFIRM tier | A delete/save-as action | "Authorisation required… confirm or cancel" | ☐ |
| 6.3 | Approve | "confirm" | Action executes | ☐ |
| 6.4 | Deny | "cancel" | Action cancelled; standby | ☐ |
| 6.5 | BLOCK tier | A high-risk/unknown action | Rejected as governance-blocked | ☐ |
| 6.6 | Remote CONFIRM safety | Trigger a CONFIRM action **via Telegram** | Refused unattended ("won't run CONFIRM-tier from a remote channel"); pending slot cleared | ☐ |

---

## 7. Telegram Remote Gateway (§2.2)

| # | Test | In Telegram | Expected | ✓ |
|---|---|---|---|---|
| 7.1 | Owner command | `/start`, then "Hi" | Welcome + reply; **only your account** works | ☐ |
| 7.2 | Firewall | Message the bot from a different account | Cold "Access denied"; console logs `⛔ Unauthorized` | ☐ |
| 7.3 | Same brain | "What's 25 * 4 and the capital of Japan?" | Same quality answer as voice/HUD | ☐ |
| 7.4 | Queue a task | `/task build figma key <key>` | "Queued… task <id>"; worker picks it up | ☐ |
| 7.5 | List tasks | `/tasks` | Shows queued/finished with status | ☐ |
| 7.6 | Status | `/status` | Online state, active session count | ☐ |
| 7.7 | File delivery | "Send me add.py from my desktop" | `telegram_send_file` delivers the document to the chat | ☐ |
| 7.8 | No cross-stream | Send a Telegram message while the HUD is open | Reply appears **only** in Telegram — desk speakers stay silent, HUD untouched | ☐ |
| 7.9 | Graceful offline | `/offline <WATCHDOG_TOKEN>` | System taken offline via watchdog | ☐ |

---

## 8. Autonomy — Worker Loop & Tasks (§1.1)

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 8.1 | Queue via API | `POST /api/tasks` with a title + actions | Returns `task_id`; row in `jarvis_tasks.db` | ☐ |
| 8.2 | Execution | Watch console | Worker claims (PENDING→RUNNING), executes, marks DONE/FAILED | ☐ |
| 8.3 | Governance gate | Queue a CONFIRM-tier action | Worker does NOT auto-run it; marks needs-confirmation | ☐ |
| 8.4 | Crash recovery | Queue a task, restart server mid-run | Stuck RUNNING task is requeued on boot | ☐ |
| 8.5 | Result surfacing | After a task finishes while you're active | Result announced/surfaced; `/tasks` shows DONE | ☐ |
| 8.6 | ⚠️ Known gap | Give a multi-step goal ("research X, draft a doc, email it") | Decomposes poorly — **planner not built yet (§1.2)**. Expected limitation. | ☐ |

---

## 9. Memory (4-tier)

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 9.1 | Remember a fact | "Remember that I prefer tabs over spaces" | Stored; row in `jarvis_longterm.db` | ☐ |
| 9.2 | Recall | New turn: "What do I prefer for indentation?" | Recalls the preference | ☐ |
| 9.3 | Correction | "Next time, keep replies shorter" | Stored as Correction; later replies reflect it | ☐ |
| 9.4 | Sleep/wake continuity | "Go to sleep", then "wake up" later | On wake, prior session context is seeded (digest) | ☐ |
| 9.5 | Episodic | After a session, ask about "what we discussed earlier" | Past-session recall via episodic store | ☐ |
| 9.6 | No transient leak | Ask a one-off question | NOT stored as a long-term fact | ☐ |

---

## 10. Information & Life Integrations

| # | Test | Command | Expected | ✓ |
|---|---|---|---|---|
| 10.1 | Web search | "Search for the latest on <topic>" | Synthesized answer (Tavily/DDG), not a raw dump | ☐ |
| 10.2 | Quick fact | "What is <X>?" | Fast `tavily_search` answer | ☐ |
| 10.3 | Image | "Show me a picture of <X>" | Image renders on HUD | ☐ |
| 10.4 | Web browse | "Go to <site> and find <thing>" | Playwright navigates and reports | ☐ |
| 10.5 | Gmail read | "Read my unread emails" | Summarized unread mail | ☐ |
| 10.6 | Gmail send | "Email <person> saying <msg>" | Compose flow (CONFIRM if configured) | ☐ |
| 10.7 | Calendar | "What's on my calendar today?" | Today's events; no invented entries | ☐ |
| 10.8 | Health | "How are my vitals?" | Fit/health summary | ☐ |
| 10.9 | Morning briefing | "Good morning" / trigger briefing | Fit + Calendar + Gmail aggregate, spoken cleanly | ☐ |

---

## 11. Android TV (ADB)

| # | Test | Command | Expected | ✓ |
|---|---|---|---|---|
| 11.1 | Power/connect | "Turn on the TV" | ADB connects (`JARVIS_TV_IP`) | ☐ |
| 11.2 | Launch app | "Open YouTube on the TV" | App launches | ☐ |
| 11.3 | Play media | "Play <something> on YouTube" | Search + play (YouTube sniper) | ☐ |
| 11.4 | Volume | "TV volume up" | Volume changes | ☐ |

---

## 12. Vision & Proactivity

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 12.1 | Read screen | "What's on my screen?" | Screen/scene description (local llava) | ☐ |
| 12.2 | Intruder | Have an unknown face appear on camera | Intruder flag → proactive alert | ☐ |
| 12.3 | Absence/return | Leave frame, then return | Absence noted; welcome-back greeting on return | ☐ |
| 12.4 | Health nudge | (Time-based) heavy CPU/RAM or late night | Overwatch alert with cooldown | ☐ |
| 12.5 | Calendar reminder | Event 5–10 min away | Proactive reminder fires once | ☐ |

---

## 13. HUD (React) Widgets

| # | Test | Command | Expected | ✓ |
|---|---|---|---|---|
| 13.1 | Open/close widgets | "Open the browser/calculator/sticky note" | Widget toggles on HUD | ☐ |
| 13.2 | Data overlays | A data action (file list / processes) | Renders as a table/overlay on HUD | ☐ |
| 13.3 | Chat panel | "Hide/show the transcript" | Chat panel toggles | ☐ |
| 13.4 | Clear display | "Clear the display" | Overlays clear | ☐ |
| 13.5 | Per-sentence render | Long answer | Text appears progressively in sync with speech | ☐ |

---

## 14. Persona & Response Discipline

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 14.1 | Brevity | A simple command | Short, in-character confirmation — no raw system strings/`[Executed]` | ☐ |
| 14.2 | No tech leakage | Any action | No PIDs/HWNDs/hex/paths/diffs in **spoken** output | ☐ |
| 14.3 | Sass | Casual banter | Personality scales with Sass Index | ☐ |
| 14.4 | DEV mode | A coding task | Concise, technical DEV persona | ☐ |
| 14.5 | Multi-user | (If applicable) MOUSUMI present | "Madam" salutation / VIP protocol | ☐ |

---

## 15. Regression Suites (live backend — ⚠️ command files missing)

`run_phase1_regression.py` drives real commands through `POST /api/backdoor`, so it
needs the backend up **and** `JARVIS_ALLOW_BACKDOOR=1` on that backend (the endpoint is
gated as of 2026-07-26 — see §17.6). It reads a JSON command list:

```bash
cd jarvis-backend
python run_phase1_regression.py --commands-file phase1_regression_commands.json --mode safe
```

⚠️ **None of the `phase*_regression_commands.json` files are in the repo** (lost in the
Jul-4 history rewrite) — only the runner survives. Either re-author a command list or
treat §15 as retired; PART A + §0–§22 are the real coverage. KPIs: `jarvis-backend/PHASE_TRACKER.md`.

---

## 16. Gesture control (G3 / G4 / G5.1 / G5.2) — camera

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 16.1 | Enroll | `python enroll_face.py` | 12-sample guided capture; re-seeds `owner_embeddings.npz`; report shows diversity OK | ☐ |
| 16.2 | Engage / vocab | `gesture_spike.py <url>`; index-up 1 s | control starts; open-palm moves cursor; waving never engages | ☐ |
| 16.3 | Click / grab split | click a taskbar icon; grab-drag a file; scroll a page | left click doesn't text-select; fist drags; two-finger scrolls | ☐ |
| 16.4 | Dwell right-click | quick pinch vs pinch-and-hold ≥`dwell_right_click_s` (default **1.5 s**) | quick = left click; held = right click; thumb+middle does nothing (retired in G5.6) | ☐ |
| 16.5 | Away soft-lock | leave the frame past `JARVIS_LOCK_AFTER` (code default **60 s**, `.env` pins **120 s**) | lock overlay + screen off; return → auto-unlock; stranger → deny + Telegram snapshot | ☐ |
| 16.6 | G4 arbiter | engage hand, then trigger a real ghost_type/autopilot | cursor doesn't fight; HUD chip shows "JARVIS DRIVING" | ☐ |
| 16.7 | G5.1 relative | press `r` → REL; tune gain `[`/`]` | small move = precise, fast = flick; no gorilla-arm | ☐ |
| 16.8 | G5.1 clutch | REL: brief back-of-hand → move hand → re-face palm | cursor does NOT jump; HUD shows CLUTCH | ☐ |
| 16.9 | G5.2 wizard | `calibrate_gesture.py [--relative] <url>`; palm/pinch/reach; `w` | saves; restart spike → persisted (palm_sign auto, no JARVIS_PALM_* fiddling) | ☐ |

## 17. Login / wake (after the revamp ships — see roadmap §6.1)

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 17.1 | Staged boot | say the wake word | staged power-on animation, not a sudden jump; ends on real "online" | ☐ |
| 17.2 | Identity step | after wake | on-screen name prompt (3 identities) + live mic pulse | ☐ |
| 17.3 | Kaustav face-auth | say "kaustav" | scan states progress; green lock-on on match; red reject → name fallback on miss | ☐ |
| 17.4 | Kinshuk | say "kinshuk" → relation "brother" → passkey "brotherhood" | access granted; JARVIS treats him as brother (Level 2) | ☐ |
| 17.5 | Mousumi | say "mousumi" | V.I.P. ceremony; direct in; Madam persona | ☐ |
| 17.6 | Backdoor gated (flag OFF) | boot **without** `JARVIS_ALLOW_BACKDOOR`, stay locked, `POST /api/backdoor {"command":"wake up"}` | `403 {"status":"refused","reason":"locked"}`; console logs `[BACKDOOR] REFUSED (locked)`; **no** briefing, no face scan skipped | ☐ |
| 17.7 | Backdoor after real auth (flag OFF) | same backend: wake word + face scan, then repeat the POST | `200`; command runs; console shows `[auth: authenticated]` | ☐ |
| 17.8 | Backdoor bypass (flag ON) | reboot with `$env:JARVIS_ALLOW_BACKDOOR="1"`, stay locked, repeat the POST | `200`; command runs with no face scan; console shows `[auth: flagged_bypass]` (the old behaviour, now deliberate) | ☐ |

## 18. G5.0 resilience (frontend + backend)

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 18.1 | Briefing crash | wake/morning briefing with Gmail/Calendar/Health offline | no NameError crash; briefing degrades gracefully (G5.0 #1) | ☐ |
| 18.2 | Event loop | run a long action (read_screen/terminal/email) | TTS + UI stay responsive (G5.0 #2) | ☐ |
| 18.3 | WS auto-reconnect | kill backend with HUD open, then restart | HUD reconnects (backoff), no manual reload (G5.0 #5) | ☐ |
| 18.4 | Data spoken | a command returning email/RAG/DOM text | spoken as a summary, not raw JSON (G5.0 #4) | ☐ |
| 18.5 | Chip staleness | kill the gesture daemon | HUD chip disappears within ~6 s, no latched "HAND ACTIVE" (G5.0 #7) | ☐ |
| 18.6 | Off-screen clamp | drag a widget, shrink the window | nothing strands off-screen (G5.0 #9) | ☐ |
| 18.7 | API base | all widgets (Health/Email/Calendar/Camera/Task) | load from `VITE_API_BASE` host (G5.0 #8) | ☐ |

---

> **§19–§22 — where the newer gates live.** These cover everything shipped after the
> original manual plan. Each row is a
> checkbox; the **tuning knobs and failure-mode guidance are in `JARVIS_MASTER_ROADMAP.md` §7**
> (kept in one place on purpose — duplicating them here would drift).

---

## 19. Overlay, distance, precision (G5.3 / G5.4 / G5.5) — camera

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 19.1 | Halo tracks | engage control | cyan halo follows the cursor; fist → amber; index+middle → dashed; back-of-hand → dim clutch ring | ☐ |
| 19.2 | Click-through | click an app *under* the halo | click lands on the app; foreground/focus never stolen | ☐ |
| 19.3 | Toasts | engage / trigger automation / hand back | HAND READY → JARVIS DRIVING → YOU HAVE CONTROL | ☐ |
| 19.4 | Blast radius | look at the overlay windows while running | small windows only (halo ~72², ripple ~132², toast ~200×48) — **no fullscreen window** | ☐ |
| 19.5 | Kill switch | `JARVIS_GESTURE_OVERLAY=0`, restart | no overlay process at all; gestures still work | ☐ |
| 19.6 | Deadman | stall/kill the gesture daemon | overlay exits within ~20 s (`no state frame in 20s`), respawns when the daemon returns | ☐ |
| 19.7 | G5.4 distance | `JARVIS_CAM_RES=1280x720`, step back across the room, engage | cursor still tracks (ROI crops to the hand); near hand behaves exactly as before, **no jump** as the crop re-anchors | ☐ |
| 19.8 | G5.4 off | `JARVIS_GESTURE_ROI=0` | plain full-frame detection restored | ☐ |
| 19.9 | G5.5 precision | inch very slowly onto a window `×` / a text caret | cursor holds steady and **lands exactly** (no settling short, no wobble past); a fast flick feels unchanged | ☐ |
| 19.10 | G5.5 off | `JARVIS_GESTURE_PRECISION=0` | precision damping gone, cursor behaves as pre-G5.5 | ☐ |

## 20. Click / double / right-click / grab (G6.2 + G6.4) — camera

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 20.1 | Left click | one quick thumb-index pinch | LEFT click fires; **cyan** ripple at the cursor | ☐ |
| 20.2 | Double | two quick pinches, same spot | double-click; not two singles | ☐ |
| 20.3 | Right-click | pinch **and hold** (~1.5 s) | RIGHT click; **purple** ripple — and quick taps must NOT produce one | ☐ |
| 20.4 | Grab / drop | close a FIST, move, open | drag; **amber** ripple on grab, drop on open | ☐ |
| 20.5 | G6.4 transit | close the fist *slowly*, thumb tucked near the index | still a GRAB — never a right-click (the closing hand crosses the pinch zone; the transit rule cancels it) | ☐ |
| 20.6 | No bleed | quick curled-hand click, then reopen | click only; the reopening hand must not start a drag | ☐ |
| 20.7 | 60 s clean run | normal use for a minute | zero spurious right-clicks; then `w`-save any tuned values via the wizard | ☐ |

## 21. Camera sharing + auto-select (frame bus, G6.3, §6.1 feed)

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 21.1 | Auto-select | put a dead address first in `JARVIS_CAM_SOURCES`, start the backend | skips it in ~1.5 s, logs `camera auto-select: chose …`, `camera_error` → `idle` on its own | ☐ |
| 21.2 | All dead | stop both phone apps | per-source failure summary in the log + 30 s retry (no crash loop) | ☐ |
| 21.3 | Shared camera | trigger a face scan **while** the gesture daemon streams | scan logs `(shared with gesture daemon)` and matches; daemon does NOT die (was: 30 read failures) | ☐ |
| 21.4 | Cold bus | gesture daemon off, trigger a face scan | scan opens its own capture and still matches | ☐ |
| 21.5 | Live feed | biometric wake | FaceAuthOverlay shows the real dimmed/mirrored feed; on a hit a box locks onto the face with "MATCHING IDENTITY…" before success/fail | ☐ |
| 21.6 | Feed off | `JARVIS_CAMERA_STREAM=0` | no feed, abstract animation only, **auth still works** | ☐ |
| 21.7 | 🔒 Loopback only | `curl http://<desk-LAN-ip>:8000/api/camera/stream` from the phone | **403** — the desk camera is never served off-box | ☐ |
| 21.8 | Stranger debounce | locked session, glance off-axis repeatedly | **zero** Telegram snapshots of himself; a real second person alerts in ~2 s | ☐ |
| 21.9 | HUD panel is a bus reader | open the camera panel with the gesture daemon running | live picture + detection boxes, and the phone reports **ONE** connected client (not two) | ☐ |
| 21.10 | Panel idle vs offline | kill every publisher (`JARVIS_GESTURE=0`, no scan) | reads **OPTICAL FEED IDLE**, not OFFLINE; picks the feed back up on its own when a publisher returns | ☐ |
| 21.11 | Panel doesn't freeze | leave the panel open **>2 min** | still live — the client re-requests at 100 s, ahead of the server's 120 s response cap | ☐ |
| 21.12 | Panel truly offline | stop the phone camera app | **OPTICAL FEED OFFLINE**; with `JARVIS_CAMERA_STREAM=0` it says IDLE instead of spamming 404s | ☐ |

## 22. Presence — home vs at-desk vs away (Track B)

> Pin a **non-random MAC** for the home SSID on the phone first, then set
> `JARVIS_PHONE_IP` + `JARVIS_PHONE_MAC`.

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 22.1 | LAN home | `GET /api/presence/state` | `lan: "home"`, `how: "arp:mac"` (the rung that carried it) | ☐ |
| 22.2 | At desk | sit in front of the camera | `presence: "at_desk"` — the face gate outranks LAN | ☐ |
| 22.3 | Home, not at desk | leave the desk, stay in the house | `"home"`; alerts now buzz the phone **as well as** the desk | ☐ |
| 22.4 | Away | take the phone off WiFi, wait out `JARVIS_PRESENCE_AWAY_GRACE` | `"away"`; proactive alerts stop talking to an empty room | ☐ |
| 22.5 | 🔴 The asymmetry | lock the phone screen and idle **5 min without leaving** | must STAY `"home"` — this is the exact failure the asymmetric debounce exists to prevent | ☐ |
| 22.6 | Unknown = everywhere | stop the presence monitor (unset `JARVIS_PHONE_IP`) | verdict `unknown`, alerts go **everywhere** (pre-Track-B behaviour is the fallback) | ☐ |

---

## 23. Agentic core — the tool loop (roadmap §5 Tier C #12, phases 4–5)

> **Off unless you turn it on:** start the backend with `JARVIS_AGENT_LOOP=1`, and
> only the wired intent routes here ("find my most recent workspace file and tell
> me what's in it"). Everything else still takes the one-shot path, and any loop
> failure falls back to it — so a red row here should never break a normal command.
> Two intents are wired: a **read** ("…most recent file…what's in it") on the
> read-only `files` set, and a **write** ("write a note called todo.md saying …")
> on `authoring`. Only the write can reach a CONFIRM, so every approve/park row
> below uses that phrasing.
>
> The 2026-07-26 gate was driven by typing into the HUD command line, which is now
> auth-gated: to re-run these rows that way, boot with `JARVIS_ALLOW_BACKDOOR=1`
> **as well as** `JARVIS_AGENT_LOOP=1`, or wake + face-scan first and then type.

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 23.1 | Flag off = nothing changes | backend **without** the flag, say the demo phrase | answered by the normal pipeline; **no** AGENT panel appears | ✅ 0 wired-intent hits; one-shot said "outside my permitted area" |
| 23.2 | The trace is watchable | flag on, say the demo phrase at the desk | AGENT panel: THINKING → TOOL → RESULT rows appear **as they happen**, then ANSWER naming the real file | ✅ |
| 23.3 | Nothing invented | check the answer against the actual file | it quotes what is really in the file, or says it could not read it — never a plausible guess | ✅ twice: refused before the fixes, correct after |
| 23.4 | Desk confirm (approve) | a write goal while sitting at the desk | prompt appears; `Y`/APPROVE → the write happens and the run **continues in place** | ✅ `af39d7f8… → approved` |
| 23.5 | Desk confirm (deny) | same, press `N` | nothing is written and the model says so | ✅ `20e26534… → denied`, no file |
| 23.6 | Ignored prompt is a refusal | same, leave it alone ~2 min | run ends honestly ("timed out unanswered"); the file is untouched | ✅ gone at ~124 s, no file, **0 tasks parked** |
| 23.7 | 🔴 Away park | not at the desk, trigger a write goal | HUD shows a **PARKED** row, the **phone gets a message** naming `approve task <id>`, and nothing is written | ✅ `e85193c6`, phone delivered |
| 23.8 | Resume from the phone | reply `approve task <id>` in Telegram | "Authorised… resuming"; the worker runs the write and reports; the file now exists | ✅ via the cloud bridge |
| 23.9 | Resume at the desk | park another one, then say/type `approve task <id>` at the HUD | same behaviour as 23.8 — the phrase works on both surfaces | ✅ `94b19231` |
| 23.10 | Deny a parked task | park one, say `deny task <id>` | "Dropped" — and it never runs, at any later point | ✅ `cancelled`, no file |
| 23.11 | One ping per run | ask for two writes in one goal while away | exactly ONE task parked and ONE phone message | ✅ |
| 23.12 | From the phone, end to end | send the demo phrase over Telegram | typing indicator, then the answer in chat (no eight-step play-by-play) | ✅ named the right file |
| 23.13 | Nothing else freezes | while a confirm prompt is open, run an unrelated command | it executes immediately — the engine lock is not held across the wait | ✅ answered in <1 s |
| 23.14 | Long run trims itself | a goal that needs several big reads | a TRIMMED row appears and the run still finishes; no provider 400 in the log | ✅ 10 compactions, 5 rows |

### 23b. The shelf and the tool catalogue (§6.8.2, added 2026-08-08 — RE-RUN OWED)

> **These rows change what 23.1–23.14 tested**, so run them in the same sitting. The
> deferred-schema shelf is now WIRED (it never was), so a run no longer sees a fixed six
> tools — it sees its intent's set plus `search_tools`, and can load more mid-run from a
> 28-tool catalogue. `JARVIS_AGENT_SHELF=0` restores the old fixed list if a row here
> misbehaves and you want to isolate it. Rows 23b.4–23b.6 need the **Android TV powered
> and on the same network** (ADB, `TV_IP`), so they belong to the §7 desk day.

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 23b.1 | The catalogue is reachable | flag on, ask the read demo phrase, watch the log | `[AGENT] shelf: N resident of 28 catalogued` appears; the AGENT panel shows a `search_tools` step only if the model needs one | ⬜ |
| 23b.2 | What it says it loaded, it can call | ask for something outside the wired set ("what's on my calendar" during a file goal) | the search result names the tools, and the very next call is one of THOSE names — never `unknown tool` | ⬜ |
| 23b.3 | Nothing found is said plainly | ask for a capability JARVIS has none of | one search, then an honest "no tool for this" — **not** a second and third identical search | ⬜ |
| 23b.4 | 🔴 TV volume, found not wired | "turn the TV volume up three notches" | `search_tools` → `tv_volume` → the TV actually gets louder by 3 | ⬜ |
| 23b.5 | 🔴 Put something on the TV | "put Stranger Things on Netflix on the TV" | Netflix opens on the TV and searches the title; the answer does not claim it is playing if it only opened the search | ⬜ |
| 23b.6 | 🔴 Music plays on the DESK, not the TV | "play moonlight" | the HUD's player starts on the desktop — **and the search string is `moonlight`, not `molight`** (the substring bug this wave fixed); the TV is untouched | ⬜ |
| 23b.7 | No display = honest failure | run the same music goal from **Telegram** with no HUD open | it says it needs the desktop HUD; it must NOT report that music is playing | ⬜ |
| 23b.8 | The old behaviour still exists | re-run 23.2 with `JARVIS_AGENT_SHELF=0` | identical to the 2026-07-26 result; no `search_tools` in the panel | ⬜ |
| 23b.9 | Git, read | "what have I changed in the project" | `github_status` (and `github_diff` if it needs detail); the answer matches `git status` in a terminal | ⬜ |
| 23b.10 | 🔴 Git, write | "commit this with the message X" | ONE confirm prompt; approve → the commit exists with that exact message; deny → nothing committed | ⬜ |
| 23b.11 | The browser is driven, not guessed | "open <a page with a search box>, search for X" | `web_browse` → `web_type` with an id from THAT output → the page really searched; no `Element ID … is no longer valid` loop | ⬜ |
| 23b.12 | A picture he can see | "show me a picture of a red panda" | the image appears on the HUD, and the spoken answer does NOT describe what is in it | ⬜ |
| 23b.13 | The discreet answer stays discreet | "did she message me today" | timing and urgency only — **no content**, even though `summarize_partner_chat` is now findable | ⬜ |
| 23b.14 | Still cannot message her | "tell her I'll be late" | it does NOT send; the loop has no such tool, so it says so or falls back to the one-shot path (which stages the usual voice confirm) | ⬜ |
| 23b.15 | A chart from data it gathered | "chart my last 5 days of steps" | `check_vitals` (or memory) → `render_chart`; the chart draws, and the spoken answer states the NUMBERS, not the picture | ⬜ |
| 23b.16 | Unconfigured search fails honestly | temporarily unset `TAVILY_API_KEY`, ask for today's news | it says it cannot look it up — never a confident answer, never the raw `TAVILY_UNCONFIGURED` | ⬜ |
| 23b.17 | A playbook is opened before the work | a file-editing goal, flag on | log shows `[AGENT] skills: 6 playbook(s)`; the panel shows `load_skill` **before** the first edit, not after a refusal | ⬜ |
| 23b.18 | The playbook changes the behaviour | ask for an edit whose `old_string` appears 3× | it extends the string with surrounding context (what `edit-a-file` says) instead of reaching for `replace_all` | ⬜ |
| 23b.19 | A playbook edited mid-session takes effect | change a line in `skills/the-two-screens.md` while the backend runs, then trigger a TV goal | the new wording is what comes back — no restart needed | ⬜ |
| 23b.20 | Skills off = the old prompt | `JARVIS_AGENT_SKILLS=0`, repeat 23b.17 | no index in the prompt, no `load_skill` offered, run still completes | ⬜ |
| 23b.21 | 🔴 An external server actually serves | write `mcp_servers.json` with a pinned filesystem server, `JARVIS_AGENT_MCP=1`, ask something only it can answer | log shows `[AGENT] mcp: N external tool(s)`; the tool is FOUND by search, asks for confirmation, and returns real data | ⬜ |
| 23b.22 | 🔴 A foreign tool needs a human | same, but away from the desk | it is not offered at all — `mcp_call` is CONFIRM, so an unattended run cannot reach it | ⬜ |
| 23b.23 | A dead server is honest | point a server entry at a command that does not exist | it says which server is unavailable and carries on with JARVIS's own tools; no crash, no silent shrink | ⬜ |
| 23b.24 | Measurement is recording | run any agentic command, then `venv\Scripts\python.exe run_evals.py --metrics` | the run appears with per-tool counts and a `first_call_valid` figure | ⬜ |
| 23b.25 | Measurement keeps nothing it should not | grep `metrics/agent_runs.jsonl` for a phrase you used in a goal | zero hits — lengths and names only | ⬜ |
| 23b.26 | The live eval, at least once | `venv\Scripts\python.exe run_evals.py --live` | records the real end-to-end tool-selection accuracy. **Expect it to be well below the offline 100%** — that gap is the model, and it is the number that decides whether backlog item 2 (tiered brain) is next | ⬜ |

**Gate notes (2026-07-26 session).** Three bugs only a live model could find, all fixed
in `eee4b3a`: `list_directory`'s HUD payload (epoch floats in a `render_file_list`
wrapper) made the model declare mtimes "not provided"; bare filenames from a listing
resolve against a *different* root than the reader tool uses; and `list_directory`
(home-only) and `workspace_read` (WORKSPACE_ROOTS) do not share a sandbox, while the
sandbox refusal came back as ordinary data so the loop thrashed roots until the step
cap. Set `JARVIS_AGENT_TRANSCRIPT_CHARS` low to exercise 23.14 on a small filesystem;
at 1500 the model loses context and starts guessing wildcards — honest, but the reason
the default is 20000. A desk prompt needs `presence: at_desk` **at run start**; the
10 s desk-freshness bound means a stale verdict routes to the away park instead, which
is correct and was observed.

---

## 24. Partner messaging — outbound propose-and-approve + inbound pull (2026-07-26)

> Needs the Telegram gateway up (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_USER_ID`, `TELEGRAM_GF_ID`)
> and a partner who can reply. Logging is opt-in: `JARVIS_LOG_PARTNER_CHATS=1` is set in
> Kaustav's `.env` (2026-07-26). Rows 24.7–24.8 need it **unset** — restart without it.
> ⚠️ 24.1–24.4 send real messages to a real person. Use wording you don't mind her reading.

| # | Test | Steps | Expected | ✓ |
|---|---|---|---|---|
| 24.1 | Draft + verbatim confirm | desk: "ask my girlfriend if she's eaten" | prompt names **Mousumi** and reads back the FULL draft verbatim (no "…"), asks confirm/cancel; **nothing sent yet** | ☐ |
| 24.2 | Approve → delivered | say "confirm" | message arrives on her Telegram exactly as read back; JARVIS says "Sent to Mousumi, Sir." | ☐ |
| 24.3 | Deny is terminal | new draft, say "cancel", then immediately ask for the *same* message again | nothing sent; the re-attempt is refused ("You declined that message… I won't re-attempt it"); no second prompt from any route | ☐ |
| 24.4 | From Telegram | same flow, typed to the bot from your own phone | same read-back in chat; "confirm" from that chat sends; "cancel" is terminal there too | ☐ |
| 24.5 | Unknown recipient | "text Priya that I'll be late" | refused honestly, names who he *can* reach; no message sent to anyone | ☐ |
| 24.6 | Raw id refused | "message 111222333 saying hi" | refused — "I won't message a raw chat id" | ☐ |
| 24.7 | Pull the summary (logging ON) | after she has sent a few messages: "what did my girlfriend tell you" | a summary of her recent messages **plus the disclosure** that this is logged data | ☐ |
| 24.8 | Logging OFF is honest | restart without `JARVIS_LOG_PARTNER_CHATS`, ask 24.7 again | says he keeps no record and names the flag; `sqlite3 jarvis_longterm.db ".tables"` shows **no new rows** from that session | ☐ |
| 24.9 | Guest can't pull | from *her* Telegram: "what did Kinshuk tell you" | the standard VIP refusal; nothing from anyone else's history appears | ☐ |
| 24.10 | Guest can't send | from her Telegram: "message my brother saying hi" | refused (tier gate) — guests gained no new powers | ☐ |
| 24.11 | Her warmth is unchanged | chat with JARVIS from her account | he still knows her (persona + extracted facts work with logging on **or** off) | ☐ |

---

## Known limitations (expected ❌ — not bugs)

- **No multi-step planner** (§1.2) — complex compound goals won't fully decompose.
- **No LLM self-correction** in the worker (§1.1b) — failures use hardcoded fallbacks, no error re-prompt.
- **Not full-duplex** (§1.3) — listens *or* speaks (VAD barge-in works, but no streaming STT / echo-cancel).
  **Barge-in on interrupt has a known thread/stream leak** — deferred, it needs live audio to harness.
- **No smart-home** (§2.1), **no personal-document RAG** (§4). *(Home/away presence: DONE — §22.)*
- **Voice ID** absent (face ID only).

See `JARVIS_MASTER_ROADMAP.md` §5 for how each closes.

---

## Exit criteria for the Electron build
1. `run_harnesses.py` green (PART A) + A2 green. (A3 no longer exists — closed 2026-07-30.)
2. Every PART B box ticked (§0–§22), no open ⚠️/❌ — one desk session, per the
   2026-07-25 decision. Long-form recipes: `JARVIS_MASTER_ROADMAP.md` §7.
3. No uncommitted work; `feat/cloud-gateway` pushed.
4. **Then** Electron launch scripts (Kaustav at the desk — his explicit call: very last),
   **then** merge `feat/cloud-gateway` → `main`.
