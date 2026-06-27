# JARVIS Phase Tracker

Tracking format: `Task | Status | Evidence | Date`

Status values:
- `DONE`
- `IN_PROGRESS`
- `NOT_STARTED`
- `BLOCKED`

---

## Phase 1: Reliability Core ✅ COMPLETE

Target: Success rate >= 95% on core command set

**Final KPI: 15/15 (100%) on safe-pack — 2026-05-01**

| Task | Status | Evidence | Date |
|---|---|---|---|
| Deterministic Notepad targeting (HWND-first, PID fallback) | DONE | `action_engine.py`, `modules/human_gui_agent.py` — HWND session tracking + focus recovery | 2026-04-30 |
| Save conflict decision flow (save as new vs overwrite) | DONE | `ghost_save_file` + pending decision resolver + prompt intercept in `main.py` | 2026-04-30 |
| Unsaved-note decision flow (save/discard/cancel) | DONE | Notepad unsaved prompt detection/handler + close-path integration | 2026-04-30 |
| Short, consistent decision prompts | DONE | Prompt wording standardized in `action_engine.py` | 2026-04-30 |
| Action runtime state machine + trace IDs | DONE | `ActionState`, trace ring, `execute_with_retry(..., return_meta=True)` | 2026-04-30 |
| Runtime telemetry endpoint | DONE | `GET /api/actions/runtime` in `main.py` | 2026-04-30 |
| Top-20 regression command suite | DONE | `phase1_regression_commands.json` — 20 command cases (safe + gui modes) | 2026-04-30 |
| Automated pass/fail + latency report | DONE | `run_phase1_regression.py` — trace-aware runner with artifact cleanup | 2026-04-30 |
| Streaming LLM → sentence-by-sentence TTS | DONE | `synthesize_info_gen()` + `_stream_synthesize_speak()` in `brain.py` / `main.py` | 2026-05-01 |
| Parallel Gmail fetch (thread-safe per-instance) | DONE | `ThreadPoolExecutor` with per-thread `build()` in `gmail_agent.py` | 2026-05-01 |
| Supervisor payload pre-processing | DONE | `_preprocess_raw_data()` — HTML strip + 1200-char cap in `brain.py` | 2026-05-01 |
| JSON mode for deterministic action routing | DONE | `response_format={"type":"json_object"}` for action-likely commands in `brain.py` | 2026-05-01 |
| Deterministic action guards (focus/display/mute) | DONE | Post-LLM injection guards in `brain.py` for clear_display, enable/disable_focus | 2026-05-01 |
| Pending-decision intercept safety (stale state) | DONE | Decision vs. command classifier in backdoor + WS handlers in `main.py` | 2026-05-01 |
| memory_recall fast path (no model cold-start) | DONE | `recall_all_facts()` first; semantic fallback only if needed in `action_engine.py` | 2026-05-01 |
| `_is_failure` false-positive fixes | DONE | SCREEN CONTENTS exempt; TV/screen error strings cleaned of failure keywords | 2026-05-01 |
| App alias table (Notes → Notepad, etc.) | DONE | `_native_app_launcher` alias dict in `action_engine.py` | 2026-05-01 |
| **Full safe-pack 15/15 — Phase 1 KPI MET** | **DONE** | Two consecutive 15/15 runs confirmed | **2026-05-01** |

---

## Phase 2: The Invisible Fast-Lane (OS & API Tools) ✅ COMPLETE

Goal: Give J.A.R.V.I.S. a set of fast, safe, headless tools so the Supervisor has
something reliable to call before an autonomous loop is worth building.

**Architectural decision (2026-05-01):** Autonomy loop (original Phase 2) deferred.
Tools must precede autonomy — a multi-step loop with only the GUI Vision agent
would be slow, fragile, and expensive. Fast-lane tools come first.

| Task | Status | Evidence | Date |
|---|---|---|---|
| Terminal Agent (`terminal_agent.py`) — sandboxed OS shell with blocked-pattern safety | DONE | `modules/terminal_agent.py` — 17-pattern blocklist, path confinement, timeout | 2026-05-01 |
| Telemetry Agent (`telemetry_agent.py`) — full psutil snapshot (CPU/RAM/Disk/Net/procs) | DONE | `modules/telemetry_agent.py` — supersedes `os_agent.get_system_diagnostics()` | 2026-05-01 |
| Tool Registry Integration — wire new agents into `action_engine.py` | DONE | `run_terminal_command` + `get_telemetry` dispatch; `system_status` upgraded | 2026-05-01 |
| Supervisor prompt updated — `brain.py` BASE_CORE lists new action types | DONE | `get_telemetry` + `run_terminal_command` added to Available Actions block | 2026-05-01 |
| `_ACTION_FORCE_KEYWORDS` expanded for Phase 2 actions | DONE | Terminal / telemetry / process / network keywords added to deterministic routing in `brain.py` | 2026-05-01 |
| Phase 2 regression command suite (10 safe-mode cases) | DONE | `phase2_regression_commands.json` — telemetry, fs, process, network, security smoke | 2026-05-01 |
| Phase 2 KPI baseline run | **DONE** | **13/13 (100%) — 2026-05-01** — Security tests: LLM-level refusal on all 3 malicious commands | 2026-05-01 |
| Agentic Core Loop (deferred from original Phase 2) | NOT_STARTED | Will be Phase 2b once skill packs are stable | |

---

## Phase 3: The Code Specialist (Native Workspace I/O) ✅ COMPLETE

Goal: Give J.A.R.V.I.S. the ability to natively read, write, and patch project files
without opening any GUI. Prerequisite for building a Dev Persona in Phase 4.

**Architectural decision (2026-05-01):** Persona Discipline (original Phase 3) deferred to Phase 4.
A Dev Persona requires code I/O tools to be meaningful. Tools first.

**Final KPI: 6/6 (100%) on safe-pack — 2026-05-01**

| Task | Status | Evidence | Date |
|---|---|---|---|
| `workspace_agent.py` — read_file, write_file, patch_file with workspace confinement | DONE | `modules/workspace_agent.py` — WORKSPACE_ROOTS guard, binary block, size caps | 2026-05-01 |
| Tool Registry — wire `workspace_read/write/patch` into `action_engine.py` | DONE | 3 new dispatch entries + `_workspace_*` private methods | 2026-05-01 |
| Supervisor prompt — `brain.py` BASE_CORE lists new workspace action types | DONE | `workspace_read`, `workspace_write`, `workspace_patch` in Available Actions | 2026-05-01 |
| Working memory injection — write/read results fed back to LLM context | DONE | `action_engine.py` `_workspace_write/read` inject `[workspace_* result]` into memory | 2026-05-01 |
| Payload minification — BASE_CORE action list trimmed ~35% | DONE | `brain.py` BASE_CORE compacted; removed redundant examples | 2026-05-01 |
| Groq client timeouts — all 6 API calls have explicit timeouts | DONE | `timeout=20–60s` on every `client.chat.completions.create` call | 2026-05-01 |
| Model switchable via `GROQ_MODEL` env var | DONE | `.env` `GROQ_MODEL=llama-3.3-70b-versatile`; 7 call sites use `_GROQ_MODEL` const | 2026-05-01 |
| File-extension routing guard — filenames with ext → workspace_, not native_app_launcher | DONE | `brain.py` post-LLM guard + `_FILE_EXT_RE` regex | 2026-05-01 |
| Workspace result humanizer — strips diffs/metadata from TTS | DONE | `_sanitize_for_speech()` in `main.py`; raw output still shown in UI | 2026-05-01 |
| Phase 3 regression suite (6 cases: write/read/patch/security×2/graceful) | DONE | `phase3_regression_commands.json` | 2026-05-01 |
| **Full safe-pack 6/6 — Phase 3 KPI MET** | **DONE** | **6/6 (100%) confirmed — 2026-05-01** | **2026-05-01** |

---

## Phase 4: Persona & Response Discipline ✅ COMPLETE

Goal: Stable "MCU feel" — concise, consistent, character-true responses.
Now possible because Phase 3 gives JARVIS the code tools to back up a Dev Persona.

| Task | Status | Evidence | Date |
|---|---|---|---|
| Persona mode profiles (TACTICAL / CINEMATIC / DEV) | DONE | `RESPONSE_MODE_*` overlays in `brain.py`; `classify_intent` returns `response_mode` | 2026-05-01 |
| Strict response brevity templates by action outcome | DONE | `BrevityManager` class in `brain.py` with per-mode word limits + error humanizer | 2026-05-01 |
| Non-essential narration suppression | DONE | `_sanitize_for_speech()` covers GUI/Terminal/PC_OP/OS/workspace; silent for intermediate steps | 2026-05-01 |
| No technical leakage in user-facing speech | DONE | `_strip_metadata()` removes PIDs, HWNDs, hex addresses, diff markers, raw paths from all TTS | 2026-05-01 |
| Dynamic salutations — skip briefing if active ≤10 min | DONE | `_smart_briefing()` in `main.py`; rotates standby phrases instead of full briefing | 2026-05-01 |
| Routing discipline — file extensions always workspace_agent, never Notepad GUI | DONE | `brain.py` file-ext routing guard + absolute routing rules in BASE_CORE | 2026-05-01 |
| GUI fallback ban for code files | DONE | Brain.py rules + post-LLM override guard | 2026-05-01 |
| Phase 4 regression suite (workspace smoke + TTS leak scan + classify persona) | DONE | `phase4_regression_commands.json`; `JARVIS_REGRESSION_ROUTES=1`; runner spoken + classify hooks | 2026-05-01 |

**Phase 4 KPI: 8/8 regression cases when backend runs with regression routes enabled ✓**

---

## Phase 5: Memory OS ✅ COMPLETE

Goal: Personalized, persistent behaviour across sessions.

**Status:** Closed — persistence, extraction, prompt injection, and balanced retrieval are wired for production use.

**Regression KPI:** 4/4 safe-pack — `jarvis_longterm.db` gains rows on Fact/Preference/Correction; workspace read yields `{"memories": []}` (no transient leak). Re-run:  
`python run_phase1_regression.py --commands-file phase5_regression_commands.json --mode safe ...`

| Task | Status | Evidence | Date |
|---|---|---|---|
| Phase 5 regression suite (fact / preference / correction / transient-no-store) | DONE | `phase5_regression_commands.json`; runner polls `jarvis_longterm.db` | 2026-05-01 |
| Background extraction → SQLite (`memory_manager` + `extract_and_store_memory`) | DONE | Groq `{memories:[…]}`; `_mem_err`; single `extract_and_store_memory` in `brain.py` | 2026-05-01 |
| Supervisor prompt injection (`[LONG-TERM MEMORY]`) | DONE | `build_dynamic_prompt(..., long_term_memory_block)`; `MEMORY_OS` rule in `BASE_CORE` | 2026-05-01 |
| Balanced retrieval / freshness bands | DONE | `get_balanced_memories_for_prompt()` — Correction → Preference → Fact + newest-first within band; cap via `MEMORY_OS_PROMPT_LIMIT` (default 14) | 2026-05-01 |
| Preference memory (natural language + recall in prompts) | DONE | `Preference` category + injector (structured path/naming profile schema optional later) | 2026-05-01 |
| Correction memory ("next time do X") | DONE | `Correction` category + regression P5-C01 | 2026-05-01 |

---

## Phase 6: Capability Skill Packs + Safety & Governance

Goal: High-value domain execution with risk-tiered safety controls.

**Agentic TV loop (Phase 6.x):** ✅ **Architecture locked — code complete.** Dependencies noted (`youtube-search-python` optional; HTML fallback when VideosSearch/httpx incompatible). **Physical TV smoke testing pending** on operator hardware (2026-05-01).

Remaining in this phase bucket: Gmail compose/reply maturity, GitHub skill pack (not started), auditable logs (planned overlap with Phase 10).

| Task | Status | Evidence | Date |
|---|---|---|---|
| Gmail read/send robust workflows | IN_PROGRESS | Parallel fetch complete; full compose/reply flows pending | 2026-05-01 |
| GitHub commit/PR workflows | NOT_STARTED | | |
| Android TV — agentic playback loop | DONE | `modules/tv_agent.py` — mDNS/ADB TCP, `tv_play_media`, Hotstar keystrokes; YouTube sniper (VIEW deep link + sleep + ENTER 66); VideosSearch + HTML fallback; `_tv_search` → TVAgent in `action_engine.py`; `tv_*` governance AUTO. **Physical device verification pending.** | 2026-05-01 |
| Risk-tiered permissions (AUTO / CONFIRM / BLOCK) | DONE | `governance.json`, `governance_manager.py`, gate in `action_engine.execute()`, sentinels + intercept in `main.py`, `test_governance.py` | 2026-05-01 |
| Auditable action logs + rollback paths | NOT_STARTED | Planned scope: Phase 10 (builds on trace ring + governance) | |

---

## Phase 7: Health, Calendar & Proactive Routines ✅ COMPLETE

Goal: Finish “digital life” surfaces (body + schedule) and idle-time automation without burning CPU.

**Final KPI: 7/7 (100%) on Phase 7 suite — `phase7_regression_commands.json` — 2026-05-01**

| Task | Status | Evidence | Date |
|---|---|---|---|
| Health — Fit / vitals (`check_vitals`) | DONE | `modules/health_agent.py`; dispatch in `action_engine.py`; regression P7-H01, P7-H02 | 2026-05-01 |
| Calendar — today / schedule (`check_calendar`) | DONE | `modules/calendar_agent.py`; regression P7-C01, P7-C02 | 2026-05-01 |
| Morning briefing aggregate (Fit + Calendar + Gmail) | DONE | `morning_briefing` in `action_engine.py`; `[BRIEFING_DATA]` → `synthesize_briefing_gen` / `_stream_briefing_speak` in `brain.py` / `main.py`; regression P7-B01–B03 | 2026-05-01 |
| Phase 7 regression suite | DONE | `phase7_regression_commands.json`; `run_phase1_regression.py --commands-file phase7_regression_commands.json` | 2026-05-01 |
| Tool-output synthesis fidelity (calendar/vitals/email — no invented weather/reminders) | DONE | `_synthesis_delivery_rule_three()` strict Rule 3 for batched `[check_*]` / `[read_screen]` tags in `synthesize_info_gen` + `synthesize_info` (`brain.py`) | 2026-05-01 |
| Sequential streaming TTS (no cross-command audio overlap) | DONE | `await speaker.speak_text()` per sentence in `_stream_synthesize_speak` / `_stream_briefing_speak` (`main.py`) | 2026-05-01 |
| Routine scheduler hook (background, low churn) | DONE | `modules/routines.py`; lifespan activation (`[ROUTINES] Background scheduler active` in server logs) | 2026-05-01 |

**Deferred (follow-up polish — not blocking Phase 7 closure):** Briefing stream persona still allows cinematic flourishes (“I’ve taken the liberty…”, closing questions). Later: tighten **`_iter_briefing_sentences_from_stream`** / **`synthesize_briefing_gen`** prompts so fidelity/end-flat rules mirror the tool-output path (`_synthesis_delivery_rule_three` pattern), unless the raw `[BRIEFING_DATA]` explicitly supports an action.

---

## Phase 8: Local Multimodal & Streaming UX ← NEXT

Goal: Voice stack resilience (local STT/TTS fallback) and streaming daemon alignment with the React HUD.

| Task | Status | Evidence | Date |
|---|---|---|---|
| Lazy-load local STT/TTS + cloud fallback | IN_PROGRESS | `recorder.py`, `speaker.py`, `wakeword.py` | |
| Streaming voice daemon ↔ frontend | IN_PROGRESS | `streaming_daemon.py`; `POST /api/ui_state` in `main.py` | |
| HUD widget parity (Notepad / Browser / Calculator) | IN_PROGRESS | Widget toggles in `action_engine.py` + `jarvis-frontend` | |

---

## Phase 9: Ambient Perception & Proactive Security

Goal: Richer context from ambient vision and safer proactive behaviours.

| Task | Status | Evidence | Date |
|---|---|---|---|
| Known vs unknown user tracking | NOT_STARTED | `ambient_vision.py` | |
| Intruder / absence detection hooks | NOT_STARTED | `background_monitor.py` | |
| GUI automation consolidation | NOT_STARTED | `modules/gui_agent.py`, `human_gui_agent.py` | |

---

## Phase 10: Agentic Supervisor & Operations

Goal: Multi-step autonomy only after governance + tools are stable; ops-grade traceability and deployment.

| Task | Status | Evidence | Date |
|---|---|---|---|
| Agentic core loop (Phase 2b — deferred) | NOT_STARTED | Phase 2 tracker row | |
| Auditable action logs + rollback / replay | NOT_STARTED | Extends governance + `ActionState` trace ring | |
| Production hardening (secrets, health checks, updates) | NOT_STARTED | | |

---

## Immediate Next Actions

1. **Physical TV verification** — Smoke `tv_play_media` / follow-up app picks on target Android TV (YouTube sniper, Hotstar macro, Netflix/Prime paths).
2. **Skill packs** — Gmail compose/reply hardening; GitHub workflows when ready.
3. **Memory OS optional hardening** — dedupe `remember_fact` vs Memory OS same-turn; structured profile keys (default paths); semantic decay beyond banded retrieval.
4. **Regression CI** — document `JARVIS_REGRESSION_ROUTES=1` for Phase 4 runners if applicable.
5. **Phase 8 voice/HUD** — local STT/TTS fallback maturity; streaming daemon ↔ React HUD parity (`PHASE_TRACKER.md` Phase 8 table).

---

## Phase 8.6.8: Media OS Overhaul & Persona Polish ✅ COMPLETE

**Date:** 2026-05-13

| Task | Status | Evidence | Date |
|---|---|---|---|
| SMTC Upgrade — replace blind `keybd_event` with native `winrt` SMTC API | DONE | `modules/os_agent.py` — `_run_smtc_sync()` bridges async SMTC to sync action_engine; context-aware no-media detection; `winrt-Windows.Media.Control` installed | 2026-05-13 |
| Unmute Confusion Fix — `"muted" in r` false-positive on "unmuted" result | DONE | `main.py` `_sanitize_for_speech`: checks `"unmuted"` BEFORE `"muted"`; SMTC no-media → "There is no media currently playing, Sir." | 2026-05-13 |
| Robotic Text Fix — LLM emitting `[Executed]` / raw system strings in brevity mode | DONE | `brain.py` `MODULE_PC_OP` + `RESPONSE_MODE_TACTICAL`: added `TOGGLE AWARENESS` and `NO SYSTEM TEXT` strict rules | 2026-05-13 |
| Calendar Bleed Audit — `[CALENDAR]` log firing during unrelated OS commands | DONE | Root cause: frontend `/api/calendar/today` polling concurrently. Fix: lazy import of `CalendarAgent` in `action_engine.py` (removed top-level import, added `from modules.calendar_agent import CalendarAgent` inside each handler); log renamed `[CALENDAR_WIDGET]` to distinguish widget-poll singleton build from voice-command calendar calls | 2026-05-13 |

---

## Phase 8.6.9: Dynamic App Resolution Engine ✅ COMPLETE

**Date:** 2026-05-13

| Task | Status | Evidence | Date |
|---|---|---|---|
| `AppIndexer` class — startup cache of 178 entries from 3 sources | DONE | `modules/os_agent.py` — `_build_app_index()` scans Start Menu `.lnk` (All Users + Current User) + HKLM/HKCU Registry App Paths + hardcoded essentials; background daemon-thread warm on import | 2026-05-13 |
| Fuzzy matcher — `difflib.get_close_matches` (cutoff=0.68) | DONE | 3-tier resolve: exact → prefix/substring → fuzzy; typo `"file exploreer"` → `explorer.exe`; cutoff tuned to prevent false-match (e.g. `spotify` vs `mxnotify`) | 2026-05-13 |
| `os.startfile()` execution — ShellExecute for all app types | DONE | Replaces `subprocess.Popen` + `shell=True "start"` fallback; handles `.lnk` UWP shortcuts, `.exe` paths, File Explorer, Edge, Chrome | 2026-05-13 |
| Web-app fallback — for apps not installed as desktop apps | DONE | `_WEB_FALLBACKS` dict in `launch_application()`: Spotify → `open.spotify.com`, YouTube, Gmail, Netflix, etc. — opens in default browser | 2026-05-13 |
| `_native_app_launcher` delegation — `action_engine.py` slimmed to 35 lines | DONE | Removed 65-line 4-tier cascade; delegates to `OSAgent.launch_application()`; `os.startfile()` fire-and-forget; `post_launch_focus` still asserts window for ghost_type chaining | 2026-05-13 |
| **Validated:** Notepad, File Explorer, Edge, Chrome, VS Code, typo tolerance | **DONE** | `python -W error` clean; 6/6 resolution checks passed | 2026-05-13 |

---

## Phase 4 (Autonomy Roadmap): Remote Gateway & Resilience ✅ COMPLETE

**Date:** 2026-06-27

Goal: Untether J.A.R.V.I.S. from the desk — command him remotely from a phone via
Telegram, isolate concurrent conversations so output never crosses streams, and make
the server effectively unkillable. (Roadmap track, building on the local Autonomy phase.)

| Task | Status | Evidence | Date |
|---|---|---|---|
| Telegram Remote Gateway — async aiogram bot as a background task on the FastAPI loop | DONE | `modules/telegram_bot.py` — `start_bot()` launched in `lifespan` with `handle_signals=False`; `aiogram==3.29.0` installed | 2026-06-27 |
| Owner firewall — strict `TELEGRAM_USER_ID` validation, cold rejection + logging for all others | DONE | `_is_owner()` gates every handler; `_reject()` cold-rejects and logs `⛔ Unauthorized access attempt`; brain/engine never reached for non-owner | 2026-06-27 |
| Remote commands route through the SAME brain + engine as voice | DONE | `run_remote_command()` in `main.py` → `process_command` (llm_router) + `engine.execute_with_retry` (action_engine); no reasoning re-implemented | 2026-06-27 |
| Queue background tasks from Telegram (`/task <goal>`) | DONE | `remote_queue_goal()` plans goal → action JSON → `task_queue.enqueue` for the Overnight Worker; `/tasks`, `/status` commands | 2026-06-27 |
| Outbound files — JARVIS sends documents back to the chat | DONE | `telegram_send_file` action in `action_engine.py` → `telegram_bot.send_document_to_owner`; registered in `brain.py` BASE_CORE + `governance.json` (AUTO) | 2026-06-27 |
| Concurrent Session Scoping — `OutputChannel` / `Session` / `SessionManager` keyed by ws/telegram id | DONE | `modules/session_manager.py`; replies routed only through the originating channel — never `send_ui_update`, `speaker.speak_text`, or global `active_user` | 2026-06-27 |
| `COMMAND_LOCK` serialises the shared ActionEngine across channels | DONE | `modules/session_manager.py` asyncio.Lock held per engine action in `run_remote_command`; HUD + Telegram interleave safely | 2026-06-27 |
| Safe remote governance — CONFIRM-tier actions refused unattended | DONE | `run_remote_command` clears pending slot and replies "Authorisation required…" for `GOVERNANCE_CONFIRM`; never auto-runs | 2026-06-27 |
| Unkillable Watchdog — standalone supervisor restarts the server on any death | DONE | `watchdog.py` launches `uvicorn main:app` via subprocess; respawns on crash/kill with rapid-crash circuit breaker; logs to `watchdog.log` | 2026-06-27 |
| Authenticated graceful shutdown for the watchdog | DONE | localhost-only `POST /shutdown?token=…` (timing-safe `secrets.compare_digest`) + Ctrl+C/SIGTERM; Telegram `/offline <token>` bridges to it | 2026-06-27 |
| **Verification** | **DONE** | `py_compile` + full `import main` wiring asserts; channel-routing tests (action/conversational/CONFIRM-refusal) reply via channel with zero speaker/HUD calls; governance `telegram_send_file → AUTO`; watchdog control endpoint 403/200 auth verified | 2026-06-27 |

**Live-deploy note:** Set `TELEGRAM_USER_ID` to your **numeric** Telegram id (from @userinfobot), not a bot @handle — `start_bot()` parses it with `int()` and disables the gateway otherwise.

