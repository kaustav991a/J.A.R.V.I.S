# JARVIS Reliability Hardening — Working Context & Handoff

> **Purpose:** single source of truth for the reliability-hardening effort. If you're
> a fresh session, READ THIS FILE FIRST — it holds the full plan, what's done, what's
> next, and the exact point work stopped. Last updated: 2026-07-11 (**Phase 4 —
> autonomy reach — is DONE: all 13 audit items fixed, verified by harness, code in
> working tree, NOT yet committed.** See §5 for what was built, §5.6 for the new
> capabilities in plain words, §9 for the resume point).

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
  CONFIRM tier can't be answered remotely; shared pending-slot races. → **FIXED
  (Phase 4, 2026-07-11).**

---

## 2. Phased plan & status

| Phase | Goal | Status | Commit |
|---|---|---|---|
| **1** | Unified parse spine + determinism + honest failure | ✅ DONE | `2493a84` |
| **Cloud** | Cloud gateway never says "go find out"; honest lookup failure | ✅ DONE | (in `2493a84`) |
| **2** | Stop narrating false success | ✅ DONE | `ea7e92c` |
| **3** | Launch→type→save chain + quick wins | ✅ DONE | (uncommitted) |
| **3.5** | GUI typing backend (pywinauto) + UIA path + UTF-8 stdout | ✅ DONE | (uncommitted) |
| **4** | Autonomy reach (notify + remote confirm) | ✅ DONE 2026-07-11 | (uncommitted — STOP POINT) |
| **5** | Multi-provider LLM cascade (Groq→Gemini→OpenRouter) + Gemini free vision | ✅ DONE 2026-07-11 (keys delivered, live-tested) | (uncommitted) |
| **Gesture** | Full hand-gesture mouse control (MediaPipe) | ✅ G1–G4 DONE — face-gated + presence soft-lock + cursor arbiter + calibration + HUD chip (detail in `HAND_GESTURE_CONTROL_PLAN.md`) | e1cc385 / 0ba2c5b / 2cf46f2·e25fc1b·87d2094 / cc27156 |
| **Electron** | Single-exe boots FE+BE; notch→takeover overlay | ❌ LATER | — |

Branch: `feat/cloud-gateway`. Phases 1–3.5 + §6 are committed AND pushed
(`2493a84`, `ea7e92c`, `02749a7`). **Phase 4 is in the working tree, uncommitted.**

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
- ~~PENDING USER ACTION~~ ✅ RESOLVED 2026-07-11: Kaustav set `TAVILY_API_KEY` in the
**Render dashboard** env. (History: it was on the desk `.env` but NOT on Render →
cloud fell back to DuckDuckGo, blocked
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

## 5. Phase 4 — autonomy reach (✅ DONE 2026-07-11, uncommitted)

All 13 items from the autonomy audit are FIXED. Files touched: `main.py`,
`background_monitor.py`, `cloud_gateway.py`, `governance.json`,
`modules/owner_notify.py` (NEW), `modules/telegram_bot.py`, `modules/cloud_bridge.py`,
`modules/worker_loop.py`, `modules/task_queue.py`, `modules/daemon_supervisor.py`,
`modules/planner.py`, `test_owner_notify.py` (NEW harness, 20/20).

1. ✅ **Proactive detections now reach the phone** — NEW **`modules/owner_notify.py`**:
   the single "notify the owner wherever he is" fan-out (desk HUD + desk TTS + phone).
   The phone leg tries `telegram_bot.send_text_to_owner()` (NEW — direct poller), then
   `cloud_bridge.send_alert_to_owner()` (NEW — pushes an `alert` frame up the bridge
   socket; the cloud relays it to the admin chat — NEW handler in `cloud_gateway.py`'s
   `/desk-link` loop). Honest per-leg delivery report; no leg ever raises. Registered
   in `main.py` lifespan via `owner_notify.configure()`. Intruder + health alerts in
   `ProactiveAgent` now use it.
2. ✅ **Standby no longer silences safety monitoring** — `_check_cycle` runs
   intruder + system-health checks even when `SYSTEM_ONLINE` is False; in standby they
   skip desk TTS and go to the phone. Ambient/wellness checks stay suppressed.
3. ✅ **CONFIRM tier answerable from the phone** — `run_remote_command` no longer
   cancels+refuses. Admin remote callers get "Authorisation required … reply 'confirm'
   or 'cancel'"; the staged action lives in THAT channel's `Session.pending`, keyed by
   `confirmation_id` (90s TTL). The next short yes/no from the same channel resolves
   exactly that action (executed with `governance_bypass=True`); any other command
   supersedes+cancels it. VIP guests still get the hard refusal.
4. ✅ **Pending-slot race fixed — everything is confirmation_id-scoped** — the desk
   pins the cid it asked about (`_DESK_PENDING` in main.py; both backdoor + WS voice
   intercepts consume/cancel BY id), remote sessions carry their own cid, and
   `planner.py` cancels its mid-plan CONFIRM by id. A desk "yes" can never run an
   action pended by a phone chat, and vice versa. Verified live: two pended actions
   resolved independently by id.
5. ✅ **Worker CONFIRM tasks are resumable** — the worker now STOPS at the first
   CONFIRM-tier step (no more out-of-order execution of later steps), persists the
   REMAINING steps back onto the task (`task_queue.set_remaining_actions` — an
   approved resume re-runs nothing that already succeeded), and reports "say 'approve
   task <id8>'". NEW deterministic admin-only grammar in `run_remote_command`:
   `approve/resume/deny/drop task <id-prefix>` → `task_queue.approve_task()` (flips to
   pending with `approved=1`; CONFIRM steps then run with governance_bypass) or
   `task_queue.cancel()`. NEW `approved` column (auto-migrated via ALTER TABLE).
6. ✅ **`report_pending` delivers BEFORE marking reported + phone fallback** — items
   stay queued for the next wake if no delivery leg succeeded. `_announce` now pushes
   task outcomes to the phone when the user is away (was: silently dropped).
7. ✅ **Cloud→desk bridge black-hole fixed** — the cloud tracks each forwarded
   `req_id`; the desk's `BridgeChannel` echoes it on every reply/notify frame. A
   watchdog answers from the cloud brain (`think()`) if the desk shows NO sign of life
   within `DESK_REPLY_TIMEOUT_SECS` (default 45s); typing-notify frames are heartbeats
   that extend the window, so a slow-but-alive desk is never double-answered.
8. ✅ **Daemon restart cap decays + owner alert** — 10 min of healthy uptime restores
   one restart credit (a transient crash storm can't permanently kill a daemon); when
   the cap IS hit, `notify_owner()` tells Kaustav the daemon is down instead of only
   logging it.
9. ✅ **Governance consistency** — `workspace_patch` re-tiered AUTO → **CONFIRM**
   (matches `workspace_write`; governance.json v1.5.0 — NOTE: interactive patches now
   ask too). `run_autopilot`/`agentic_gui_task` stay AUTO interactively but are
   CONFIRM-class for the UNATTENDED worker (`OvernightWorker._UNATTENDED_CONFIRM`).
10. ✅ **Poison task dead-letter** — `claim_next_pending` dead-letters any task already
    claimed `MAX_ATTEMPTS` (3) times → FAILED with an honest note, surfaces via the
    normal report path, queue moves on. Verified on a scratch DB.
11. ✅ **Calendar reminders reach the phone** — `ScheduleDaemon` takes `is_online_fn`;
    when the system is in standby the reminder goes to the phone instead of the desk
    speakers.
12. ✅ **Intruder check runs FIRST** — moved to the top of `_check_cycle`, ahead of all
    wellness/briefing checks (each cycle returns after one event, so a break nudge
    could previously mask an intruder for a full cycle).
13. ✅ **Planner catalogue trimmed** — `run_terminal_command` (BLOCK-tier) removed from
    the ReAct tool catalogue so plans stop being built around a tool that can't run.

**Verification:** all edited files `py_compile` OK; `test_action_parser.py` 24/24;
`test_failure_detection.py` 17/17; NEW `test_owner_notify.py` 20/20 (fan-out legs,
fallback order, honest reporting, chunking, failure isolation); governance cid-scoping
verified live; task-queue approve/resume + dead-letter verified on a scratch DB.
(`test_governance.py` needs pytest, which is NOT in the venv — pre-existing gap, not
from this work; its territory was covered by the live cid-scoping check instead.)

### 5.4a What these upgrades mean in practice (plain words)

- **JARVIS can now reach you anywhere.** Intruder alerts, CPU/RAM/disk alarms, daemon
  failures, finished/failed background tasks, and calendar reminders land on your
  Telegram when you're away from the desk — via the direct bot or the cloud bridge,
  whichever is live. Standby no longer means blind.
- **You can authorise risky actions from your phone.** Ask for something CONFIRM-tier
  over Telegram → JARVIS asks "confirm or cancel?" and executes on your reply. Each
  chat's confirmation is isolated — no cross-channel mix-ups.
- **Background tasks pause instead of dying.** A queued goal that hits a protected
  step pauses, tells you its id, and "approve task <id>" (from any remote channel)
  finishes it exactly where it stopped. "deny task <id>" drops it.
- **The queue can't wedge and daemons can't silently die.** Crash-looping tasks are
  dead-lettered after 3 attempts; a daemon that exhausts its restarts alerts you;
  healthy uptime earns restart credits back.
- **Telegram never black-holes.** If the desk is linked but frozen, the cloud answers
  by itself after ~45s instead of leaving you on read.
- **Behaviour changes to know about:** `workspace_patch` now asks for confirmation
  (even at the desk); the overnight worker no longer drives autopilot/GUI tasks
  unattended — it asks first.
- **Cloud redeploy needed:** items 1 & 7 touch `cloud_gateway.py` — Render picks them
  up on the next push of the tracked branch.

---

## 5.5 Phase 5 — multi-provider LLM cascade + free Gemini vision (✅ DONE 2026-07-11)

**IMPLEMENTED 2026-07-11** — Kaustav delivered the keys (4 Gemini across 2 accounts +
1 OpenRouter; keys live in `jarvis-backend/.env`, NEVER in git — `KEys.txt` is now
git-ignored too). What was built in `modules/llm_router.py`:

- **Route order restored + extended:** cloud_first/heavy → `groq → gemini →
  openrouter → ollama`; local_first → `ollama → groq → gemini → openrouter`.
  The temporary `return ["groq"]` line is GONE (Kaustav-approved reversal, §7).
  Unconfigured providers are auto-dropped; the ollama circuit breaker still applies.
- **Gemini key rotation** (`_run_with_gemini_rotation`): merges `GEMINI_API_KEYS`
  (comma-separated, one per Google project — free quota is per-PROJECT) with the
  legacy `GEMINI_API_KEY`; rotates on any error, sticky on the key that worked.
  **All 5 keys live-tested OK.** Model: `gemini-flash-latest` (evergreen alias —
  the pinned `gemini-2.5-flash` id 404s for new accounts; .env updated).
- **NEW OpenRouter provider** (`_call_openrouter`): OpenAI-compatible HTTP via
  `requests` (no new dependency), sync + SSE streaming + json_mode. Walks a
  fallback list `OPENROUTER_MODELS` because individual :free models get
  rate-limited upstream or retired without notice (live-verified: three big
  models 429'd, chain landed on `nemotron-nano-9b-v2:free` and answered).
- **NEW vision cascade** `universal_vision_call(prompt, img_b64)`: **Gemini flash
  first** (big quality upgrade over CPU llava), local llava via Ollama as the
  offline fallback. `screen_reader.py` default `JARVIS_VLM_PROVIDER=auto` uses it
  ("ollama"/"groq" still force a single provider). Live-tested with a real image.
- Honest failure preserved: total cascade exhaustion still returns the Phase-1
  honest-failure line, never a fake "Done, Sir".

**ENV (desk .env, already set):** `GEMINI_API_KEYS` (4 keys), `OPENROUTER_API_KEY`,
`GEMINI_MODEL=gemini-flash-latest`. Optional: `OPENROUTER_MODELS` to override the list.

Original plan (kept for reference):
Each provider has a SEPARATE free quota, so cascading multiplies effective headroom.

**Target route order** (all free tier):
- **Reasoning / voice:** `groq → gemini-flash → openrouter(:free)`
- Groq stays PRIMARY (unbeatable latency for real-time voice).
- Gemini `gemini-2.0-flash` / `gemini-2.5-flash` second (best free reasoning + huge context).
- OpenRouter `*:free` models third (aggregator safety net; daily-capped, quality varies → last).
- **Vision:** `gemini-flash → local llava` (Gemini free vision is a big upgrade over local
llava on this CPU-only box; llava becomes the offline fallback, not the default).

**Why now is a good time:** the unified `action_parser.py` spine (Phase 1) tolerates
per-provider format differences, so swapping providers mid-cascade is safe.

**Implementation notes:**
- `llm_router._route_order` currently hard-returns `["groq"]` (~L104) — this is the exact
line to change. See §7 (it was an INTENTIONAL Kaustav decision; this phase reverses it
WITH his approval).
- Each provider needs the Benglish + JSON-action system prompt to behave — smoke-test each
one individually before trusting it in the chain.
- Route the vision path (currently local llava) to Gemini first; keep llava as offline fallback.
- Honest failover: on total cascade exhaustion, fail honestly (never narrate false success) —
reuse the Phase-1/2 honest-failure discipline.

**Keys / quota guidance (given to Kaustav 2026-07-05):**
- **Groq:** 5 keys already held — SUFFICIENT for single-user (limits are per-key → 5×
headroom). Add more only if logs show real rate-limiting.
- **Gemini:** free-tier rate limit is **per-PROJECT, not per-key** — multiple keys in one
project share one quota (no gain). 1 key is enough as a fallback; for headroom make 2–3
keys in **separate Google Cloud projects / accounts**. Get from aistudio.google.com →
"Get API key". No billing needed.
- **OpenRouter:** 1 key (it's already an aggregator; no rotation). From openrouter.ai →
Keys → Create Key. Use `*:free` models; note the shared daily cap.
- **Claude:** NOT free as an API (Anthropic API is paid-only; claude.ai free tier is web-UI,
not callable). Excluded from the free stack; only worth adding later as a PAID option for
the agent/worker loop if reasoning quality justifies cost.

**ENV to add when building:** `GEMINI_API_KEYS` (comma-separated for rotation, mirror the
Groq pattern), `OPENROUTER_API_KEY`. Wire key rotation for Gemini only if separate-project
keys are provided.

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
**UPDATE (2026-07-05 evening): Kaustav APPROVED reversing this in Phase 5** — add a
Groq→Gemini→OpenRouter free cascade + Gemini free vision. See §5.5.
**UPDATE (2026-07-11): Phase 5 LANDED — the Groq-only line is GONE; the full cascade
is live (Groq stays PRIMARY for voice latency).**
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
`.\venv\Scripts\python.exe test_failure_detection.py` (17/17),
`.\venv\Scripts\python.exe test_owner_notify.py` (20/20, Phase 4).
- Compile-check pattern used: `.\venv\Scripts\python.exe -m py_compile <file>`.
- PowerShell 5.1: no `&&`; use `; if ($?) {…}`. Commit via `git commit -F <msgfile>`
(here-strings got mangled).

## 9. Immediate next action when resuming  ← STOP POINT

> **⏹ STOPPED HERE 2026-07-18 — PHASES 1–5 + GESTURE G1–G4 ALL COMPLETE + COMMITTED.**
> Phases 4/5 committed+pushed (`6438202`/`d8cace0`). Gesture control G1–G4 done: G1 spike
> (`e1cc385`), G2 engine+pointer (`0ba2c5b`), G3 daemon + face-gate + away soft-lock
> (`2cf46f2`/`e25fc1b`/`87d2094`, live fixes `839efad`/`053172b`), and G4 — cursor arbiter
> + guided 12-sample enroll + calibration-JSON persistence + HUD chip (`cc27156`, NOT yet
> pushed). Full gesture detail in `HAND_GESTURE_CONTROL_PLAN.md`. Harnesses green: arbiter
> 28/28, enroll 17/17, calibration 31/31, engine 38/38, face-gate 5/5.
>
> **➡ RESUME AT: Kaustav's live camera gates for G4 (item 1 below), then push + merge the
> PR (`feat/cloud-gateway`). Electron packaging (single exe, notch → takeover) is the last
> milestone. The Phase-4/5 manual smoke-tests below are still owed.**

**Uncommitted working-tree changes from Phase 4 (branch `feat/cloud-gateway`):**
- `jarvis-backend/modules/owner_notify.py` — NEW: owner-notify fan-out (desk HUD + TTS + phone)
- `jarvis-backend/modules/telegram_bot.py` — NEW `send_text_to_owner()` (chunked, honest bool)
- `jarvis-backend/modules/cloud_bridge.py` — live-socket tracking, `send_alert_to_owner()`, req_id echoed on reply/notify frames
- `jarvis-backend/cloud_gateway.py` — `alert` frame relay; req_id correlation + wedged-desk watchdog (`DESK_REPLY_TIMEOUT_SECS`, default 45s) with `think()` fallback — **needs a push to deploy on Render**
- `jarvis-backend/background_monitor.py` — intruder check first; standby runs safety checks (phone delivery, no TTS); `_trigger_event(critical=, speak=)`; `ScheduleDaemon` phone reminders in standby
- `jarvis-backend/main.py` — owner_notify wiring; session-scoped remote CONFIRM handshake; `approve/deny task <id>` grammar; `_DESK_PENDING` cid pinning in both desk intercepts + both sentinel handlers
- `jarvis-backend/modules/worker_loop.py` — stop-at-CONFIRM + remaining-steps persistence; approved-task governance_bypass; `_UNATTENDED_CONFIRM` (run_autopilot, agentic_gui_task); deliver-then-mark reporting with phone fallback
- `jarvis-backend/modules/task_queue.py` — `approved` column (auto-migrated); `approve_task` / `set_remaining_actions` / `find_awaiting_confirmation`; dead-letter after `MAX_ATTEMPTS=3`
- `jarvis-backend/modules/daemon_supervisor.py` — restart-credit decay (10 min healthy); owner alert on cap hit
- `jarvis-backend/modules/planner.py` — cancel mid-plan CONFIRM by cid; `run_terminal_command` removed from catalogue
- `jarvis-backend/governance.json` — v1.5.0: `workspace_patch` AUTO → CONFIRM
- `jarvis-backend/test_owner_notify.py` — NEW harness (20/20)
- `RELIABILITY_HARDENING.md` (this file)

**Remaining work (in priority order):**
1. **Gesture G4 live camera gates (Kaustav)** — code done + harness-green (`cc27156`);
   live verification owed: (a) **arbiter** — engage the hand, then trigger a real
   ghost_type / autopilot; the cursor must not fight and the HUD chip must show
   "JARVIS DRIVING"; (b) **enroll** — `python enroll_face.py` 12-sample guided capture
   (re-seeds `owner_embeddings.npz`, currently only the 1-sample seed); (c) **calibration**
   — in `gesture_spike.py` tune `+/-` sensitivity and `m` mirror, press `w`, restart,
   confirm it persisted; (d) eyeball the HUD chip states. Then push + merge the PR.
2. **(Still pending from §4.5/§4.6) Kaustav's 2 GUI/hardware smoke-tests:**
   cold-start "open Notepad and write a poem" (ghost_type/UIA), and
   "open google chrome" → Chrome (not google.com) / "open google" → google.com.
3. **Manual smoke-tests for Phase 4 (need a phone + live system):**
   - With the PC in standby, stress CPU or trigger the camera intruder path → alert
     should land on Telegram.
   - Over Telegram (bridge or direct): ask for a CONFIRM-tier action (e.g. "send an
     email to …") → expect the confirm/cancel question → reply "confirm".
   - `/task` a goal containing a CONFIRM step → expect the pause report → send
     "approve task <id>" → task finishes.
   - Wedge test (optional): desk linked but frozen → cloud should answer by itself
     after ~45s.
4. **Manual smoke-test for Phase 5:** normal voice command with Groq keys pulled from
   .env (or a forced Groq failure) → reply should still arrive via Gemini; check
   `[ROUTER]` log lines for the escalation.
5. ~~`TAVILY_API_KEY` on Render~~ — ✅ **DONE (Kaustav, 2026-07-11): keys added in the
   Render dashboard, mirroring the desk .env.** Verify on next cloud lookup.
6. **Electron packaging** (single exe, notch → takeover overlay) — the last milestone,
   after the G4 gates. ⚠️ PARKED MID-BUILD: `jarvis-frontend/package.json` lost its
   electron config (no `main`, no electron-builder block, no electron deps) in the Jul-4
   git history rewrite, even though `node_modules` still has electron + electron-builder
   and `jarvis-frontend/release/*.exe` is a stale Jun-28 build. `NotchView.jsx` /
   `SidecarView.jsx` / `electron/` are untracked and predate the gesture work. To resume:
   restore the package.json electron config, reconcile the stale Notch/Sidecar views with
   the current HUD (now has the GESTURES button + GestureGuide + GestureChip), rebuild.

### 9.1 Token-trim (input-side, 2026-07-11 evening — DONE, see commit)

Kaustav asked to cut LLM input tokens (the real cost — output was already capped).
Two cuts in `brain.py` + `memory.py`, both harness-verified:
- **History cap:** the LLM now sees only the last `JARVIS_HISTORY_TURNS` (default 12)
  working-memory messages per turn instead of the full 30-message buffer. NEW
  `memory.get_context_window()` — always preserves a leading `[CONTEXT SUMMARY]`
  message; the full buffer is untouched for compression/consolidation. Both
  `process_command` and `process_stream` use it. Set `JARVIS_HISTORY_TURNS=30`
  to restore old behaviour.
- **Empty-block cut:** `build_dynamic_prompt` state block now skips sections with
  no data (empty semantic/episodic recall, offline camera) instead of shipping
  "none found" labels + instruction boilerplate every turn (~85 tokens/turn).
Verified: py_compile OK; window cap 30→12 with summary preserved; empty vs full
prompt comparison (filler dropped when empty, sections kept when present).
