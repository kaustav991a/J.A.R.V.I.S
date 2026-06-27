# J.A.R.V.I.S. — Manual Test Plan

A hands-on checklist to verify J.A.R.V.I.S. behaves as intended across every subsystem.
Work top-to-bottom or jump to a section. Mark each: ✅ pass · ⚠️ partial · ❌ fail.

**Channels under test:** 🎙️ Voice · 🖥️ HUD (React) · 💬 Telegram · 🔧 Backdoor (`POST /api/backdoor`)
**Last updated:** 2026-06-27

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

## 15. Regression Suites (automated, for reference)

```bash
cd jarvis-backend
python run_phase1_regression.py --commands-file phase1_regression_commands.json --mode safe
python run_phase1_regression.py --commands-file phase2_regression_commands.json --mode safe
python run_phase1_regression.py --commands-file phase5_regression_commands.json --mode safe
python run_phase1_regression.py --commands-file phase7_regression_commands.json --mode safe
```
Expected: each suite passes at its documented KPI (see `jarvis-backend/PHASE_TRACKER.md`).

---

## Known limitations (expected ❌ — not bugs)

- **No multi-step planner** (§1.2) — complex compound goals won't fully decompose.
- **No LLM self-correction** in the worker (§1.1b) — failures use hardcoded fallbacks, no error re-prompt.
- **Not full-duplex** (§1.3) — listens *or* speaks (VAD barge-in works, but no streaming STT / echo-cancel).
- **No smart-home** (§2.1), **no home/away presence** (§2.3), **no personal-document RAG** (§4).
- **Voice ID** absent (face ID only).

See `ROADMAP_TO_FULL_JARVIS.md` for how each closes.
