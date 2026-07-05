# JARVIS Reliability Hardening — Working Context & Handoff

> **Purpose:** single source of truth for the reliability-hardening effort. If you're
> a fresh session, READ THIS FILE FIRST — it holds the full plan, what's done, what's
> next, and the exact point work stopped. Last updated: 2026-07-05 (Phases 3 + 3.5 done;
> ALL §6 execution-audit items FIXED (§4.6–§4.10). PAUSED before Phase 4 — see §9 for
> the exact resume point).

---

## 0. The goal (why this effort exists)

Kaustav's vision: JARVIS should do **anything on his PC on command, flawlessly** —
clipboard, search, browser, send files, open apps, write code, arrange folders — and
run **autonomously**: detect problems, auto-fix safe/reversible ones, and **ask before
risky ones** via Telegram (PC off) or the desk HUD (PC on). Long-term: package a single
**Electron .exe** that boots frontend + backend together, with a **notch** (idle chat)
that expands to a **fullscreen "takeover" overlay** (live agent-cam of what JARVIS is
doing). Electron work is explicitly LATER.

**The real problem is NOT missing features — ~90% is already built.** It's
**reliability**: "sometimes commands work, sometimes not." The whole effort is making
existing capability work every time, then packaging it.

**Autonomy policy (confirmed):** auto-fix safe/reversible; ask before risky.
**Takeover mode (confirmed):** live agent-cam overlay of what JARVIS is doing.

---

## 1. Root causes (from three parallel audits, 2026-07-05)

1. **Fractured, non-deterministic parse layer** — the same LLM reply executed on one
   dispatch path and was spoken as prose (or dropped) on another. Temp 0.7 + json_mode
   off on most action turns made it a coin flip. → **FIXED (Phase 1).**
2. **Execution narrates false success** — `_is_failure` only caught `"error:"` prefix;
   agents don't use it, so failures became "Done, Sir". Launcher fire-and-forget →
   typing into wrong window. Blocking work on the event loop. → **FIXED (Phase 2 +
   Phase 3).**
3. **Autonomy is desk-only** — proactive alerts + worker reports never reach the phone;
   CONFIRM tier can't be answered remotely; shared pending-slot races. → **NOT STARTED
   (Phase 4).**

---

## 2. Phased plan & status

| Phase | Goal | Status | Commit |
|---|---|---|---|
| **1** | Unified parse spine + determinism + honest failure | ✅ DONE | `2493a84` |
| **Cloud** | Cloud gateway never says "go find out"; honest lookup failure | ✅ DONE | (in `2493a84`) |
| **2** | Stop narrating false success | ✅ DONE | `ea7e92c` |
| **3** | Launch→type→save chain + quick wins | ✅ DONE | (uncommitted) |
| **3.5** | GUI typing backend (pywinauto) + UIA path + UTF-8 stdout | ✅ DONE | (uncommitted) |
| **4** | Autonomy reach (notify + remote confirm) | ❌ NOT STARTED (STOP POINT) | — |
| **Electron** | Single-exe boots FE+BE; notch→takeover overlay | ❌ LATER | — |

Branch: `feat/cloud-gateway`. All commits so far are on it, **not pushed**.

---

## 3. What's DONE (verified)

### Phase 1 — parse spine (`2493a84`)
- **NEW `jarvis-backend/modules/action_parser.py`** — THE single tolerant LLM-reply →
  action(s) extractor. Handles code fences, prose around JSON, bare/singular/array
  shapes, trailing commas, truncation (string-aware brace closer), action_type alias
  remap. Never raises. Public API: `parse(raw) -> ParsedReply(actions, preamble,
  is_action)`, `extract_actions(raw) -> list[dict]`, `extract_react_decision(raw) ->
  dict|None`, `strip_fences(raw)`.
- **Wired into all 7 parse sites** (previously each did its own fragile thing):
  `main.py` × 4 (`run_remote_command` ~L1107, HUD ws ~L2439, API/backdoor ~L1632,
  `queue_goal` ~L1246), `modules/planner.py::_extract_json`, `brain.py` stub (~L1481),
  `streaming_daemon.py` (dead code but fixed to use the spine + fail honestly).
- **Determinism:** `brain.py` now runs **temp 0.0** on any action-likely turn
  (`include_actions`), was 0.7. Both `process_command` and `process_stream`.
- **Honest failure:** `modules/llm_router.py` no longer returns `{"actions":[]}` on
  total provider outage (was narrated as false "Done, Sir") — returns an honest line.
- **Harness:** `jarvis-backend/test_action_parser.py` — **24/24**. Run:
  `.\venv\Scripts\python.exe test_action_parser.py` (no hardware).
- **NOTE:** `_heal_json` in `main.py` (~L272) is now orphaned/unused — harmless, left in.

### Cloud gateway (in `2493a84`)
- `cloud_gateway.py`: added persona rule "never tell operator to search themselves";
  `think()` injects `_LOOKUP_FAILED_NUDGE` when a live-info lookup returns empty so it
  fails honestly instead of punting.
- **PENDING USER ACTION:** set `TAVILY_API_KEY` in the **Render dashboard** env (it's
  set on the desk `.env` but NOT on Render → cloud fell back to DuckDuckGo, blocked
  from datacenter IPs → "go find out"). Deploying the code needs a push (Render auto-
  deploys on push to the tracked branch).

### Phase 2 — stop false success (`ea7e92c`)
- **`action_engine.py::_is_failure(result, action_type=None)`** rewritten to be
  context-aware. `_CONTENT_ACTIONS` (read_screen, workspace_read, web_search,
  tavily_search, terminal, gmail, calendar, vitals, web_browse, ...) are NEVER
  phrase-scanned. Control actions matched against `_FAILURE_PHRASES` (the real strings
  agents emit: "smart open failed", "couldn't locate", "GUI execution error",
  "SAVE_DIALOG_NOT_FOUND", "unable to reach", ...). `action_type` threaded from call
  sites (L829 main dispatch, L979 web_search fallback).
- `macro_agent.py::deep_work` — resolves a real VS Code exe (checks default install
  paths, then `shutil.which("code")`) and only reports "VS Code opened" when one
  actually launched. Added `import shutil` at top.
- `file_agent.py::organize_downloads` — handles name collisions, REPORTS skipped
  (locked/in-use) files instead of silently dropping them.
- `action_engine.py` launch fallback — verifies a new PID spawned before claiming
  success; returns `None` (honest FAILED) when nothing launched (was "Retry successful").
- **Harness:** `jarvis-backend/test_failure_detection.py` — **17/17**. Run:
  `.\venv\Scripts\python.exe test_failure_detection.py` (imports ActionEngine; heavy
  but works; `_is_failure` tested via `ActionEngine.__new__` to skip agent init).

---

## 4. Phase 3 — DONE (all three sub-tasks completed)

All three sub-tasks completed. Code in working tree, NOT YET COMMITTED.

### Task 3A — quick wins ✅
- **`unmute` state-aware** — `os_agent.py::_get_mute_state()` now reads actual system
  mute state via Windows Core Audio IAudioEndpointVolume COM interface using ONLY
  `ctypes` (no `pycaw`, no `comtypes` — both confirmed absent). Proper GUID struct
  approach with `CoCreateInstance` → `GetDefaultAudioEndpoint` → `Activate` →
  `GetMute`. Runtime-verified: returns `False` (unmuted) on live system. Falls back
  to blind toggle if Core Audio API unavailable (headless/Wine). `control_media()`
  now only sends `VK_VOLUME_MUTE` toggle when the state actually needs to change.
- **`G:/work` → dynamic derivation** — both `file_agent.py::__init__` and
  `workspace_agent.py::_build_workspace_roots` now derive the project root from
  `Path(__file__).resolve().parents[2]` (repo root) and `.parents[3]` (work dir).
  Also honors `JARVIS_PROJECTS_DIR` env var for custom setups. No hardcoded drive
  letters. Works on any drive (F:, G:, C:, etc.).

### Task 3B — offload blocking handlers off the event loop ✅
`action_engine.py::execute()` — wrapped ALL blocking sync handlers in
`asyncio.to_thread`, matching the pattern already used for `web_search` and
`system_status`. Handlers wrapped:
- `ghost_type`, `ghost_save_file`, `agentic_gui_task` (up to 30s+ vision loop)
- `tv_control`, `tv_type`, `tv_search`, `tv_play_media`, `tv_power`, `tv_volume`,
  `tv_launch_app` (ADB connect + zeroconf can block 5s+)
- `movie_protocol`, `sleep_protocol`, `morning_briefing`
- `gui_action`, `native_app_launcher`, `_launch_app`, `_close_app`
- `os_macro`
FastAPI/WebSocket/TTS no longer freeze during these operations.

### Task 3C — launcher window tracking ✅
- `_native_app_launcher` now snapshots PIDs before/after `os.startfile()`, identifies
  the new process by name match, and calls `post_launch_focus()` to resolve the window
  handle. Stores `_last_launched_app`, `_last_launched_pid`, `_last_launched_hwnd`.
- Subsequent `ghost_type` / `ghost_save_file` calls now receive valid `app_hint`/
  `app_pid`/`app_hwnd` via `_refresh_launch_session_target()`, activating the Focus
  Verification Gate that was previously always skipped.
- **This is hardware-dependent — needs Kaustav to smoke-test the full chain:
  "open Notepad and write X" on cold start.**

---

## 4.5 Phase 3.5 — GUI typing backend + UIA deterministic path (DONE, uncommitted)

**Root-cause discovery (big one):** `pywinauto` was **never installed** in the venv and
was **absent from `requirements.txt`** — yet `human_gui_agent.py` imports it in
`ghost_type`, `ghost_save_file`, `handle_notepad_unsaved_prompt`,
`close_notepad_gracefully`, each guarded by `except ImportError: return
"PYWINAUTO_NOT_INSTALLED"`. So the entire text-injection/save backend was **silently
dead** — the single biggest source of "open X and write Y sometimes does nothing."
`comtypes` (UIA backend dep) was also absent.

**Fix (user-approved — dependency-aversion waived here because pywinauto was already a
required-but-missing import, not a new feature dep):**
- `pip install pywinauto==0.6.9` (pulls `comtypes==1.4.16`). Both **pinned in
  `requirements.txt`**. This alone revives all four dead methods.
- **NEW `human_gui_agent.py::_uia_set_control_text()`** — deterministic, focus-independent
  text injection via the UIA **ValuePattern** (`wrapper.iface_value.SetValue`). Tries
  control_type "Edit" then "Document" (Win11 Notepad is a "Document" with no `set_text`
  wrapper but a working, non-read-only ValuePattern). Writes the string **verbatim** —
  no send_keys escaping artifacts. Verifies via newline-agnostic `CurrentValue` read-back.
- **`ghost_type` rewired:** UIA ValuePattern is now the PRIMARY path; `keyboard.send_keys`
  is the FALLBACK (fires only if UIA returns False). Shortcut-key firing unchanged, runs
  after either path.
- **Live smoke-tested (PASS):** launched Notepad, drove real `ghost_type`, read control
  value back via UIA — `(parens) & symbols +^%` came through verbatim; the keyboard
  fallback had mangled the same input to `(parens)&& symbols`. Proof UIA is more accurate.

**Known limitation surfaced by the test:** Win11 Notepad is **single-instance/tabbed** —
a fresh `notepad.exe` folds into the existing process, so Phase-3C new-PID detection can
resolve the wrong pid for Notepad specifically. Non-issue for multi-process apps
(Chrome/VS Code/etc.); the title-based `resolve_window` fallback still finds a window.
Follow-up: for Notepad, prefer title/hwnd resolution over new-PID diffing.

**ghost_save_file UIA — TRIED & REVERTED (do not retry blindly):** applying UIA
ValuePattern to the Save-As dialog's file-name control fails on Win11 —
`SetValue` throws `(-2147023673) "operation was canceled by the user"`, AND the failed
attempt disturbed the working clipboard path (save then produced no file). Live-tested:
with the UIA attempt in place → FAIL; reverted to clipboard-first → PASS (file written
with exact content). **Conclusion: UIA is the right tool for an app's OWN editor surface
(ghost_type ✅) but NOT for the common file dialog. ghost_save_file stays clipboard-first.**

**Encoding hardening (real latent bug, fixed):** JARVIS log lines use Unicode (→, —,
emojis) but nothing reconfigured stdout. When stdout is redirected (Windows service, a
pipe, or the planned Electron shell capturing backend output), Python falls back to
cp1252 and a single such `print()` raises `UnicodeEncodeError` — aborting the in-flight
command. Repro'd live (`→` crashes under cp1252) and fixed by forcing
`sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")` at the top of
**`main.py`** and **`watchdog.py`** (before any print). Verified the same print then
succeeds. This matters most for the Electron packaging phase.

---

## 4.10 ghost_save_file locale-independence + clipboard retry (DONE, uncommitted — session 2026-07-05 afternoon)

**Bug:** Save-dialog detection relied on English title regex `^Save(\s|$)` — would miss
localized Windows (German "Speichern", French "Enregistrer", etc.). Also,
`OpenClipboard()` failed silently if another app held the clipboard lock.

**Fix:** `human_gui_agent.py::ghost_save_file`:
1. **Window class `#32770` fallback** for both pre-check (step 0) and polling (step 1).
   The common file dialog class `#32770` is locale-independent. Title keywords for
   DE/FR/ES added as secondary confirmation.
2. **Clipboard retry** — `OpenClipboard()` now retries up to 5 times with 100ms delays.
   Falls back to `send_keys` path only after all 5 attempts fail.

**Verified:** `py_compile` OK.

---

## 4.9 Playwright reliability hardening (DONE, uncommitted — session 2026-07-05 afternoon)

**Bug:** Three issues in `web_agent.py`:
- `wait_for_selector(state="attached")` clicked hidden/overlaid elements.
- Stale `data-jarvis-id` attrs after page re-renders caused missed clicks.
- Unconditional `keyboard.press("Enter")` after `fill()` submitted forms unexpectedly.

**Fix:** `web_agent.py::click()` and `type_text()`:
1. **`state="visible"`** instead of `state="attached"` — only interact with elements
   the user could actually see/click.
2. **Stale-ID recovery** — if the selector isn't found on first try, the DOM has
   re-rendered. **Corrected 2026-07-05 (supervision pass):** the original attempt
   re-marked the DOM and blind-retried the *same* id, but `_mark_and_extract_dom()`
   wipes all `data-jarvis-id` attrs and renumbers from 1, so the old id then pointed
   at a *different* element (or nothing) — a silent mis-click/mis-type hazard. Now
   `click()`/`type_text()` return the **freshly re-marked page state** with a message
   telling the agent to re-issue with a current ID. No blind retry on a dead selector.
3. **Conditional Enter** — only press Enter after fill if the input `type="search"` or
   `role="searchbox"`. All other inputs just get filled.

**Verified:** `py_compile` OK.

---

## 4.8 DPI-awareness + multi-monitor capture (DONE, uncommitted — session 2026-07-05 afternoon)

**Bug:** The vision GUI loop used `ImageGrab.grab()` (primary monitor only) and never
called `SetProcessDpiAwareness`, so on high-DPI displays (125%/150% scaling) the
pyautogui click coordinates drifted from the captured screenshot coordinates.

**Fix:** `human_gui_agent.py`:
1. **`SetProcessDpiAwareness(2)`** (PROCESS_PER_MONITOR_DPI_AWARE) at module init, with
   `SetProcessDPIAware()` fallback for Win8.0. Ensures pyautogui and ImageGrab both
   operate in physical-pixel space.
2. **`ImageGrab.grab(all_screens=True)`** in both `execute_autonomous_task` (vision loop)
   and `test_one_shot_vision` — captures all monitors so apps on secondary displays
   are visible to the vision LLM.

**Verified:** `py_compile` OK. Manual verification needed on a multi-monitor or high-DPI
setup.

---

## 4.7 ghost_save fuzzy verify false-positive fix (DONE, uncommitted — session 2026-07-05 afternoon)

**Bug:** `ghost_save_file` verification (L737-749) scanned `target_dir` for any file
whose name starts with `base_name` and was modified in the last 30 seconds. Saving
"notes.txt" would falsely succeed if "notes_backup.txt" was modified by another process.

**Fix:** `human_gui_agent.py::ghost_save_file` verification scan now:
1. **Exact filename match** (case-insensitive) first — highest confidence, 30s window.
2. **Same stem, different extension** (e.g. Notepad appended `.txt`) — 5s window only,
   to minimize false-positive window.
No more stem-prefix scanning.

**Verified:** `py_compile` OK. Needs manual verification with a real save operation.

---

## 4.6 Web-app-substring hijack fix (DONE, uncommitted — session 2026-07-05 afternoon)

**Bug:** "open google chrome" → opened google.com instead of the Chrome browser. Root
cause: substring matching in three places — `key in app_name_lower` / `web_key in
q_clean or q_clean in web_key` / `svc in app_lower or app_lower in svc` — meant that
any spoken name *containing* a web-service key ("google") matched the web gate.

**Fix (3 sites):**
1. **`os_agent.py::launch_application` web-first gate (~L540–590):** Added a
   `_KNOWN_DESKTOP_APPS` frozenset ("google chrome", "chrome", "edge", "firefox",
   "spotify", "discord", "slack", "telegram", etc.) checked BEFORE the web gate.
   If the cleaned query is in this set → skip web gate entirely, fall through to
   AppIndexer. Changed the web-gate itself from `for web_key … if web_key in q_clean`
   (substring) to `if q_clean in _WEB_ONLY` (exact dict key lookup). Also added
   `"google"` to `_WEB_ONLY` so "open google" still opens google.com.
2. **`action_engine.py::_launch_app` (~L2072–2102):** Same pattern — known-desktop
   guard + exact `if app_name_lower in web_apps` dict lookup (was `if key in
   app_name_lower` loop).
3. **`action_engine.py::_close_app` web-only-service check (~L2183–2195):** Changed
   `any(svc in app_lower or app_lower in svc …)` to `app_lower in _WEB_ONLY_SERVICES`
   (exact frozenset lookup). Added `"google"` to the set. "close google chrome" now
   correctly reaches the exe-alias table + psutil kill path.

**Verified:** `py_compile` OK for both files; `test_action_parser.py` 24/24;
`test_failure_detection.py` 17/17. Needs Kaustav smoke-test: "open google chrome"
should launch Chrome (not google.com), "open google" should still open google.com,
"open youtube" should open youtube.com, "close google chrome" should kill Chrome.

**Follow-up (2026-07-05 supervision pass) — dual-listed web fallback:** the audit
flagged that dual-listed names (Spotify, Telegram, WhatsApp, Discord, Slack) skipped
the web-first gate (desktop-preferred, correct) but then *lost* their web fallback —
"open spotify" with no desktop app returned "couldn't locate". Fixed in
`os_agent.py::launch_application`: consolidated to a single `_WEB_URLS` map used by
BOTH the web-first gate AND a new step-5 desktop-not-found fallback. Now dual-listed
apps try the native app first and fall back to the web version with an honest message
("… isn't installed as a desktop app, so I opened it in your browser"). Pure browsers
(Chrome/Edge/Firefox) are intentionally NOT in the map, so they get no URL fallback.
`py_compile` OK; harnesses still 24/24 + 17/17.

---

## 5. Phase 4 — autonomy reach (NOT STARTED) — full findings for future work

All from the autonomy audit. Key files: `background_monitor.py`, `modules/worker_loop.py`,
`modules/task_queue.py`, `governance_manager.py`, `governance.json`,
`modules/daemon_supervisor.py`, `cloud_gateway.py`, `main.py` (L665–723, 1043–1194).

1. **[HIGH] Proactive detections never reach the phone** — `ProactiveAgent` wired only
   to `safe_send_all` (HUD) + `global_speak` (desk TTS) at `main.py:677`. Even intruder
   alerts spoken to an empty room. Need a "notify owner wherever they are" fan-out
   (desk + Telegram/cloud).
2. **[HIGH] Monitoring suppressed in standby** — `background_monitor.py:55` early-returns
   when `SYSTEM_ONLINE` is False. Don't gate safety-class checks (health/intruder).
3. **[HIGH] CONFIRM tier has no remote reply path** — `main.py:1181` cancels + refuses
   remote CONFIRM actions instead of asking. Need a session-scoped approve/deny
   handshake using `Session.pending` (`session_manager.py:133`).
4. **[HIGH] Governance single `_pending_slot` races across sessions**
   (`governance_manager.py:93`) — approve A, run B. Resolve by `confirmation_id` scoped
   to `Session`.
5. **[HIGH] Worker CONFIRM tasks are a dead end** — `worker_loop.py:124–139` marks
   needs_confirmation, nothing resumes it; AUTO steps already ran (no idempotency).
6. **[HIGH] `report_pending` marks reported before delivery + desk-only**
   (`worker_loop.py:243–262`). Mark reported only after successful delivery; deliver via
   Telegram/cloud too.
7. **[MED-HIGH] Cloud→desk bridge black-holes** if desk connected but wedged
   (`cloud_gateway.py:537,704` — no reply correlation/timeout on `req_id`). Await ack;
   fall back to `think()`.
8. **[MED-HIGH] Daemon restart cap permanently disables a daemon**
   (`daemon_supervisor.py:69–73`) — decay restarts after healthy uptime; alert owner
   when cap hit.
9. **[MED] Governance inconsistent** — `workspace_patch` AUTO vs `workspace_write`
   CONFIRM (`governance.json:18` vs `85`); `run_autopilot`/`agentic_gui_task` AUTO let
   the worker drive GUI unattended. Make file-mutation family consistent; re-tier
   autopilot/gui to CONFIRM for unattended.
10. **[MED] Poison task can wedge queue** — `task_queue.py:127` increments `attempts` but
    nothing enforces a cap; crash-loop re-serves oldest. Dead-letter after 3–5 attempts.
11. **[MED] Calendar reminders desk-only + one-shot** (`background_monitor.py:379–385`).
12. **[MED] Proactive event ordering** — intruder check sits behind wellness nudges,
    one `return` per cycle; intruder alert never re-fires. Check security first.
13. **[LOW] Planner advertises tools it can't run** (`planner.py:132` lists
    `run_terminal_command`, blocked in `governance.json:108`). Trim catalogue to AUTO-tier.

---

## 6. Remaining Phase-2/3 execution-audit items not yet done (lower priority)
- ~~`human_gui_agent.py` ghost_save fuzzy verify false-positive~~ → **FIXED (§4.7).**
  Exact filename match replaces stem-prefix scan; stem+ext fallback tightened to 5s window.
- ~~DPI-unaware clicks + primary-monitor-only capture~~ → **FIXED (§4.8).**
  `SetProcessDpiAwareness(2)` at module init + `ImageGrab.grab(all_screens=True)`.
- ~~Web-app substring matching hijacks desktop launches~~ → **FIXED (§4.6).** All
  three sites (os_agent web-first gate, action_engine `_launch_app`, action_engine
  `_close_app` web-only check) hardened to exact match + known-desktop-app guard.
- ~~Playwright stale-ID clicks / `state="attached"` not "visible" / unconditional Enter~~ →
  **FIXED (§4.9).** `state="visible"` + stale-ID re-mark + conditional Enter.
- ~~`ghost_save_file` English-title / clipboard-lock dependence~~ → **FIXED (§4.10).**
  Window class `#32770` fallback for locale independence; clipboard retry (5 attempts).

---

## 7. Key decisions / constraints (do NOT violate)
- **Groq-only LLM routing is INTENTIONAL** — `llm_router._route_order` `return ["groq"]`
  (~L104) was Kaustav's request ("no gemini no local"). He CONFIRMED keep Groq-only on
  2026-07-05 (declined re-enabling Gemini fallback). Do not re-enable without asking.
- **Benglish/Latin script:** Bengali replies in roman letters, never বাংলা/Devanagari;
  Kaustav never speaks Hindi.
- **Dependency-averse:** avoid adding Python deps without asking (relevant to pycaw).
  EXCEPTION now in tree: `pywinauto==0.6.9` + `comtypes==1.4.16` added 2026-07-05 with
  Kaustav's explicit approval (pywinauto was already imported everywhere but missing →
  GUI typing was dead). Both pinned in `requirements.txt`. See §4.5.
- **Verification approach:** harness for parse/logic layers (done for P1/P2); MANUAL for
  GUI/hardware-bound actions (Kaustav runs JARVIS). Two phases of core-pipeline change
  are IN but NOT yet runtime-tested end-to-end.

## 8. How to run / verify
- Backend: `cd jarvis-backend; .\venv\Scripts\python.exe watchdog.py` (supervised) or
  `python -m uvicorn main:app --host 127.0.0.1 --port 8000`.
- Harnesses: `.\venv\Scripts\python.exe test_action_parser.py` (24/24),
  `.\venv\Scripts\python.exe test_failure_detection.py` (17/17).
- Compile-check pattern used: `.\venv\Scripts\python.exe -m py_compile <file>`.
- PowerShell 5.1: no `&&`; use `; if ($?) {…}`. Commit via `git commit -F <msgfile>`
  (here-strings got mangled).

## 9. Immediate next action when resuming  ← STOP POINT

> **⏹ STOPPED HERE 2026-07-05 (evening) — supervision pass complete, work COMMITTED + PUSHED.**
> Phases 1, 2, 3, 3.5, ALL §6 items (§4.6–§4.10) are DONE, audited line-by-line, and
> the whole reliability batch is committed on `feat/cloud-gateway` and pushed to origin.
> **Phase 4 (autonomy reach) is the next thing to start — see §5 for the full findings.**
>
> **Supervision-pass outcome (this session):**
> - Re-verified the entire §6 batch independently: 8 files `py_compile` OK, parser 24/24,
>   failure-detection 17/17, plus 3 line-by-line code audits.
> - **Fixed 1 real bug:** §4.9 Playwright stale-ID recovery was self-defeating (re-mark
>   renumbers ids from 1, then blind-retried the old id → mis-click risk). Now returns a
>   fresh element map and makes the agent re-issue with a current id. See §4.9.
> - **Fixed 1 design gap:** dual-listed apps (Spotify/Telegram/…) lost their web fallback
>   under the §4.6 desktop-first change. Added a step-5 web fallback in
>   `os_agent.launch_application`. See §4.6 follow-up.
> - README: added pywinauto/Playwright to the automation tech-stack row.
> - **Still owed by Kaustav (human-only, can't automate):** the 2 GUI/hardware smoke-tests
>   below (Notepad cold-start ghost_type; "open google chrome" launches Chrome).
>
> **➡ RESUME AT: Phase 4, item 1 in §5 — "Proactive detections never reach the phone"
> (owner notify fan-out: desk + Telegram/cloud).**

**Phases 1, 2, 3, 3.5 + all §6 items are DONE and COMMITTED/PUSHED** on branch
`feat/cloud-gateway`. Phase 4 is intentionally NOT started; this is the resume point.

**Uncommitted working-tree changes (all on branch `feat/cloud-gateway`):**
- `jarvis-backend/action_engine.py` — Phase 3B (asyncio.to_thread offload) + 3C (launcher tracking) + §4.6 (web-app substring fix in `_launch_app` + `_close_app`)
- `jarvis-backend/modules/file_agent.py` — Phase 3A (dynamic roots, no G:/work)
- `jarvis-backend/modules/workspace_agent.py` — Phase 3A (dynamic roots)
- `jarvis-backend/modules/os_agent.py` — Phase 3A (Core Audio mute-state reader) + §4.6 (web-first gate exact-match hardening)
- `jarvis-backend/modules/human_gui_agent.py` — Phase 3.5 (UIA ValuePattern) + §4.7 (ghost_save verify) + §4.8 (DPI + multi-monitor) + §4.10 (locale + clipboard retry)
- `jarvis-backend/modules/web_agent.py` — §4.9 (Playwright visible state + stale-ID recovery + conditional Enter)
- `jarvis-backend/requirements.txt` — Phase 3.5 (pywinauto==0.6.9, comtypes==1.4.16 pinned)
- `jarvis-backend/main.py`, `jarvis-backend/watchdog.py` — Phase 3.5 (UTF-8 stdout reconfigure)
- `RELIABILITY_HARDENING.md` (this file, untracked at repo root)

**Verified before stopping (2026-07-05 afternoon session):** all files `py_compile` OK;
`test_action_parser.py` 24/24; `test_failure_detection.py` 17/17.
NOTE: `pywinauto`+`comtypes` are now installed in the venv.

**When resuming, pick up here (in priority order):**
1. **(Still pending) Kaustav live smoke-test** the full chain on a real cold start:
   "open Notepad and write a poem" — confirm ghost_type hits the correct window and the
   UIA path engages. (Automated smoke-tests already pass; this is the human confirmation.)
2. **(Still pending) Kaustav smoke-test the §4.6 fix:** "open google chrome" should
   launch Chrome (not google.com). "open google" should still open google.com.
   "open youtube" should still open youtube.com. "close google chrome" should kill Chrome.
3. **ALL §6 execution-audit items are now FIXED (§4.6–§4.10).** No remaining items.
4. **NEXT: Phase 4** (autonomy reach): proactive detections → phone, CONFIRM tier remote
   reply path, governance `_pending_slot` race, worker CONFIRM dead-end. Full findings in §5.
5. Optional commit of all phases if Kaustav approves.
