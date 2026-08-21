# FEATURE CENSUS — everything JARVIS has, read off the code

> Built 2026-08-22, from the code rather than from the plans. `JARVIS_MASTER_ROADMAP.md`
> says what was *intended*; `LIVE_GATE_CHECKLIST.md` says what someone *wrote a row for*.
> Neither answers "what does this thing actually have?", and F-09 exists precisely
> because a feature narrated four data sources it had never read. This file is the
> third question, and it is the one the other two cannot answer.

**Why it exists.** The suite proves *properties*. The gate proves *rows*. A census
proves *coverage* — that every capability in the code is reachable by one or the
other. Gaps in either direction are findings, and the reconciliation below found
some.

**How to read a row.** Every feature carries the same four things:

| Column | Means |
|---|---|
| **Entry** | the code that receives the request — where to start reading |
| **Proof** | the harness family that drives it offline. "—" means nothing does |
| **Gate** | the `LIVE_GATE_CHECKLIST.md` group that exercises it on hardware |
| **Class** | **M** = machine-gateable, drivable end-to-end with stubs. **H** = hardware-gateable, needs Kaustav, a camera, a mic, a phone or a TV |

**The M/H split is the point.** An E2E plan that quietly cannot run is worse than
no plan. Everything marked **H** is a session with him in the chair; no amount of
cleverness automates a microphone.

---

## The count

| | |
|---|---|
| Desk actions in the catalogue | **70** — all 70 have a routing branch in `action_engine.py` |
| Desk HTTP routes + sockets | **28** routes + `/ws` |
| Cloud gateway routes + sockets | **7** routes + `/desk-link` + `/app-link`, plus 2 background ticks |
| HUD components | **31** |
| Mobile app screens | **20**, over **14** API methods |
| Harnesses | **98 files, 94 run in the suite, 2987 checks, 0 failed** |
| Owed hardware rows | **192** — 159 solo, 7 second-device, 15 second-person, 11 phone |
| **Machine-gateable families** | **9 of 15** |
| **Hardware-gateable families** | **6 of 15** |

---

## 1 · Desk backend — the 70 actions

Grouped by family, because that is how they fail: one auth token, one agent
module, one alias list per family. Every action name below is the literal
`action_type` the model emits, and every one of them resolves to an
`elif action == …` branch in `action_engine.py` — checked, not assumed.

| Family | Actions | Entry | Proof | Gate | Class |
|---|---|---|---|---|---|
| **Workspace I/O** | `workspace_read` `workspace_write` `workspace_patch` `find_file` `create_note` | `action_engine.py` routing table | `protected_paths` `store_paths` `shell_safety` `agent_files` | A5 (5 rows) 🛑 | M |
| **OS control** | `os_control` `system_status` `get_telemetry` `run_terminal_command` `os_macro` `sleep_protocol` `close_display` | same | `shell_safety` `review_batch*` | A6 (7 rows) | M |
| **Apps + GUI** | `native_app_launcher` `close_app` `open_browser` `open_calculator` `open_sticky_note` `ghost_type` `ghost_save_file` `gui_action` `agentic_gui_task` `read_screen` | same | `screen_reader` `agent_*` | A6 | **H** — needs a real desktop |
| **Focus / macros** | `enable_focus_mode` `disable_focus_mode` `run_autopilot` | same | `agent_runner` | ⚠️ **no rows** | M |
| **Email** | `check_email` `read_email` `search_email` `send_email` `gmail_read_unread` `gmail_read` `gmail_send` `gmail_reply` | `modules/gmail_agent.py` | `gmail_agent` `mail_target` | A11 (9 rows) | M offline / **H** live token |
| **Calendar** | `check_calendar` `create_event` `clear_schedule` | `modules/calendar_agent.py` | — *(see blind spots)* | A11 | M offline / **H** live token |
| **Health** | `check_vitals` | `modules/health_agent.py` | `briefing_sources` | A11 | **H** — needs the Fitness token |
| **Web** | `web_search` `tavily_search` `web_browse` `web_click` `web_type` `web_scroll` `web_back` `web_close` `web_search_image` | `modules/web_agent.py` | `web_freshness` `url_precondition` | A11 | M |
| **Memory** | `memory_recall` `remember_fact` `search_documents` | `memory_manager.py`, `modules/personal_rag.py` | `memory_*` (12 harnesses) | A9 (6) + A10 (6) 🛑 | M |
| **Partner messaging** | `message_partner` `summarize_partner_chat` `partner_contact_status` | `modules/partner_messaging.py` | `partner_messaging` `partner_contact` `partner_send_gate` | A23 (2 rows, refusals only) 🛑 | M for refusals / **H** to send |
| **Android TV** | `tv_control` `tv_play_media` `tv_search` `tv_power` `tv_volume` `tv_launch_app` | `modules/android_tv_agent.py` | `android_tv_agent` | A12 (4 rows) | **H** — TV powered, same network |
| **GitHub** | `github_status` `github_commit` `github_push` `github_log` `github_diff` | `modules/github_agent.py` | `github_agent` | A11 | M |
| **HUD control** | `hud_open_widget` `hud_close_widget` `render_chart` | `action_engine.py` → socket | `ui_bridge_e2e` `frame_bus` | A14 (5 rows) | M for the frame / **H** to see it |
| **Briefing** | `morning_briefing` | `brain.py::generate_briefing` | `briefing_truthfulness` `briefing_sources` | A15 (4 rows) | M |
| **Self-modification** | `self_improve` | `modules/agent_subagents.py` | `agent_subagents` | A22 (24 rows) | M |
| **Telegram out** | `telegram_send_file` | `modules/telegram_bot.py` | `mail_target` | A11 | **H** — phone in hand |
| **Media** | `play_music` | `action_engine.py` | — | A11 | **H** |

Ten further branches exist in the routing table but not in the catalogue —
`launch_app`, `delete_file`, `open_link` and friends. They are internal aliases
and legacy names the model is never told about. Not defects; recorded so a future
count of 80-vs-70 is not read as a discrepancy.

---

## 2 · Desk backend — routes and sockets

| Surface | Entry | Proof | Gate | Class |
|---|---|---|---|---|
| `GET /health`, `/api/telemetry`, `/api/health/summary` | `main.py` | `review_batch*` | A1 (5 rows) 🛑 | M |
| `GET /hud`, `/hud/{path}` | `main.py:1090` | `hud_assets` | A1 row `0.3` | M |
| `POST /api/backdoor` | `main.py` | `backdoor_gate` `confirm_path` `admin_override` | A16 (6) 🛑 | M |
| `WS /ws` — the voice loop | `main.py:2955` | `voice_loop_owner` `listen_request` `identity_challenge` | A3 (14 rows) | **H** — a microphone |
| `POST /api/listen` — click-to-talk | `main.py:1237` | `listen_request` | A3 | M |
| `POST /api/agent/confirm`, `GET /api/agent/pending` | `main.py:1205` | `agent_governed` `confirm_path` | A7 (5 rows) 🛑 | M |
| `GET /api/governance/status`, `POST /api/governance/cancel` | `main.py:1351` | `governance` | A7 | M |
| `POST /api/tasks`, `GET /api/tasks`, cancel | `main.py:1381` | `agent_runner` `agent_yield` | A8 (6 rows) | M |
| `POST /api/autopilot` | `main.py:1412` | `agent_runner` | A8 | M |
| `GET /api/vision/state`, `/api/camera/stream`, `/api/presence/state` | `main.py:1171` | `camera_stream` `ambient_camera` `presence_probe` | A13 (4) + A21 (10) | **H** — a camera |
| `GET /api/gesture/state` | `main.py:1320` | `gesture_*` (7 harnesses) | A18–A20 (25 rows) | **H** — hands |
| `GET /api/email/summary`, `/api/calendar/today` | `main.py:1142` | `gmail_agent` | A11 | M |
| `GET /api/tv/status` | `main.py:1133` | `android_tv_agent` | A12 | **H** |
| `GET /api/context`, `POST /api/ui_state` | `main.py:1457` | `ui_bridge_e2e` | A14 | M |
| `GET /api/actions/runtime` | `main.py:1343` | `agent_metrics` | A22 | M |
| `GET/POST /api/regression/*` | `main.py:1104` | — | §15 **retired** | M |

---

## 3 · Cloud gateway — the always-on half

Runs on Render with the desk off. `rootDir: jarvis-backend`,
`uvicorn cloud_gateway:app`.

| Surface | Entry | Proof | Gate | Class |
|---|---|---|---|---|
| `GET /health` — `app_link`, `memory`, `facts_known`, `has_desk_key` | `cloud_gateway.py:2961` | `app_link` `ping` | §24 | M |
| `POST /{webhook}` — Telegram | `:3026` | `app_link` `mail_target` | §24 | **H** — phone |
| `WS /desk-link` — the desk's socket | `:3125` | `app_link` `shared_memory` | §24 | M |
| `WS /app-link` — the phone's socket | `:3753` | `app_link` (42 checks) | Group D (11 rows) | M for the contract / **H** for the phone |
| `POST /app-fact`, `/app-say` | `:3567` | `fact_transport` `fact_seal` | A10 🛑 | M |
| `POST /app-push/register` | `:3640` | `app_link` `owner_notify` | Group D | **H** — a real Expo token |
| `POST /app-commute` | `:3702` | `commute_briefing` (15 tests) | ⚠️ **no rows** | M |
| `_commute_tick` — the morning briefing | `:2308` | `commute_briefing` | ⚠️ **no rows** | M |
| `_nudge_tick` — "he speaks first" | `:2467` | `shared_memory` | ⚠️ **no rows** | M |
| Shared memory across all three surfaces | `_memory_key` `:243` | `shared_memory` (29 checks) | ⚠️ **no rows** | M |
| Vision on a photo from the phone | `see()` `:1221` | `vision_markers` `reasoning_leak` | Group D | **H** — a photo |

---

## 4 · HUD — 31 components

Machine-gateable via the mock-WS browser gate (already passed once; see
`jarvis-frontend-livegate` in memory). **H** only where a camera or a hand is in
the loop.

| Cluster | Components | Proof | Gate | Class |
|---|---|---|---|---|
| Boot + identity | `BootSequence` `FirstBootSequence` `IntroductionCeremony` `IdentityPrompt` `FaceAuthOverlay` `FaceScanOverlay` `ScanlineTransition` | `auth_status` `identity_challenge` | A16 (6) 🛑 | **H** |
| Security | `LockdownOverlay` `UplinkOverlay` `ScreenScanOverlay` | `lockdown_exits` | A13 | M for the latch / **H** to see it |
| Widgets | `CalendarWidget` `EmailWidget` `HealthWidget` `ClockWidget` `MapWidget` `NotepadWidget` `CalculatorWidget` `BrowserWidget` `CameraFeedWidget` | `ui_bridge_e2e` | A14 (5 rows) | M |
| Chat + status | `ChatPanel` `StatusDisplay` `TypewriterText` `Visualizer` `DataOverlay` `HudReticle` | `ui_bridge_e2e` | A15 (4 rows) | M |
| Agent | `AgentTrace` `TaskHud` | `agent_metrics` | A22 (24 rows) | M |
| Gesture | `GestureChip` `GestureGuide` `MicIndicator` | `gesture_*` | A18–A20 (25 rows) | **H** |
| Windows | `NotchView` `SidecarView` | — | Electron, post-gate | **H** |
| Typefaces | `src/fonts.css` | `hud_assets` (22 checks) | A1 | M |

---

## 5 · Mobile app — `F:\work\JARVIS-Mobile`

Its own suite: **883/883** jest tests. Frames and channel ids match the gateway
(`general-v8`, `desk-watch-v2` — verified against `DEFAULT_CHANNELS`).

| Cluster | Screens | API | Class |
|---|---|---|---|
| Connection | `LaunchScreen` `ConnectionScreen` `UpdatesScreen` | `healthSummary` `telemetry` | M |
| Conversation | `ChatScreen` `CommandResultScreen` `HomeScreen` | `backdoor` | M |
| Governance | `SecurityScreen` `LockScreen` `WatchAlertScreen` | `pending` `confirm` `answerWatch` | M for the contract / **H** for a real alert |
| Memory | `MemoryScreen` `JournalScreen` | `facts` `remember` `forget` | M |
| Capability | `CapabilitiesScreen` `ScriptsScreen` `ScriptDetailsScreen` | `tasks` | M |
| Presence | `PlacesScreen` `ActivityScreen` `ReportsScreen` | `presence` `syncCommute` `registerPush` | **H** — a phone that moves |
| Settings | `SettingsScreen` `AppearanceScreen` `AboutScreen` | — | M |

⚠️ **Environment note, found while verifying:** `expo-sqlite` and `expo-updates`
were declared in `package.json` and missing from `node_modules`, so **all 70 of
the app's suites had been unable to run**. Installed 2026-08-22; manifests
untouched.

---

## 6 · Blind spots — what the reconciliation actually found

Reported honestly, including the one where my first number was wrong.

| # | Gap | Severity |
|---|---|---|
| 1 | **`os_macro` and the focus modes have no gate row.** Zero mentions of "macro" or "focus mode" in the checklist. They are harnessed, so this is a gate gap, not an untested feature | 🔵 |
| 2 | **The whole cloud-gateway commute/nudge/shared-memory arc has no rows.** `_commute_tick`, `_nudge_tick`, `/app-commute` and `_memory_key` are harnessed (44 checks between them) and appear nowhere in the 192 rows. The gate predates them | 🟠 |
| 3 | **`calendar_agent` has no harness of its own.** Only reached incidentally through `briefing_sources`. F-09 found the calendar narrating a date the owner had not marked, so this is the source with the worst track record and the least direct proof | 🟠 |
| 4 | **`play_music` and `open_sticky_note` have no direct harness.** Both are in the catalogue, both route, neither is driven | 🔵 |
| 5 | **`GEMINI_API_KEY` in `.env` is invalid.** The live API answers `API_KEY_INVALID`; all four `GEMINI_API_KEYS` work. F-36 recorded "one of them is not a key" — the odd one out is the primary, and it is dead. His to rotate | 🟠 |
| 6 | **`JARVIS_ADMIN_OVERRIDE_CODE` is unset,** which means the spoken admin override is *closed*. That is the correct default after F-27, but the recovery path F-23 and F-25 need does not exist until he sets it | 🟠 |

**A correction to my own count.** An earlier pass reported "53 of 70 actions are
not named in the checklist". That was an artifact of matching action names against
prose — the checklist describes rows in English ("open the calculator"), not by
`action_type`. Checked properly, the real gaps are the two in row 1 above.

---

## 7 · What closed on 2026-08-22

Eleven findings, all in code, all harnessed. Suite went 81 → 94 harnesses and
2575 → 2987 checks.

| Finding | Was | Now |
|---|---|---|
| **F-40** 🔴 | "no, go ahead" executed — approval tested before denial | denial breaks the tie |
| **F-42** 🟠 | `"no"` matched "now", "know", "nothing"; `"stop"` matched "stopwatch" | token matching, one helper, all three doors |
| **F-43** 🟠 | a non-answer to a live prompt ran as a command with the prompt still armed | re-asks twice, then cancels aloud and acts |
| **F-27** 🔴 | `initiate admin override` granted admin from a substring, and the idle screen advertised it | authenticated by a spoken code; unset refuses; idle line names only "wake up" |
| **F-23** 🔴 | one attempt, and "my name is" cut off = "Interaction terminated" | three attempts, three distinct reasons, and the two sub-challenges retry too |
| **F-25** 🔴 | armed on a *reachable* camera, cleared only on a *recognised face* — trapped the owner at his own desk | both read a fresh verdict; a blind gate releases; the overlay prints its exits; a code always exists |
| **F-20** 🔴 | the lockdown overlay dropped every message that could lift it | the proactive gate does not apply during lockdown |
| **F-19** 🔴 | the seated owner declared an intruder, escalated to his phone, greeted by name next cycle | streak + known-person grace, on both paths |
| **F-21** 🟠 | "Initiating lockdown protocols" locked nothing | says what it does, at both doors |
| **F-24 / F-44** 🔴 | 140-token classifier budget died on every live flash model; the fallback was indistinguishable from a reading | 1024, measured; `classified: False`; the catalogue is carried when intent is unknown |
| **F-09** 🟠 | the briefing narrated four sources it never read | absence reaches the model as absence; a state-claim guard drops the rest |
| **F-26** 🔵 | the typeface came from a CDN, three times per load, and 404'd | self-hosted, 77 KB, one declaration, zero CDN calls in `dist/` |
| **F-18** 🔵 | `0.3` sent you to a JSON endpoint; the setup path was corrupted | row names `/hud/`; the `\v`-as-vertical-tab damage is repaired |

Plus two memory holes and one new class, none of which had a finding number:

- **desk-answered turns were never filed** in the shared history — so with the
  desk *linked*, the normal state at home, the one case shared memory exists for
  was the one case that skipped it;
- **the unprompted voice wrote to the raw `APP_CHAT_ID`**, a different key from
  the one `think()` reads, so "he speaks first" landed where nothing reads it;
- **48 files printed Unicode with no stdout guard.** `sys.stdout.encoding` is
  `cp1252` here and such a print *raises inside the operation that was logging*.
  `brain.py` had one on the `close_app guard` path and one on
  `Code-file guard -> workspace_write`, so under `run_evals.py` or the worker a
  log glyph sat between an instruction and a file write.

---

## 8 · What is left, and it is all hardware

| | |
|---|---|
| **Code findings open** | **none** |
| **Owed hardware rows** | **192** (A: 159 · B: 7 · C: 15 · D: 11) + K1–K5 |
| **Owed by hand, not code** | rotate `GEMINI_API_KEY`; set `JARVIS_ADMIN_OVERRIDE_CODE` |
| **Owed after the gate** | Electron packaging, the six blind spots above |

Read `RESUME.md` for state, then `LIVE_GATE_FINDINGS.md` last-section-first, then
`LIVE_GATE_CHECKLIST.md` for the running order.
