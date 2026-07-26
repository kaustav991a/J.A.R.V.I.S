# Changelog

All notable architectural changes to **Project J.A.R.V.I.S.** are documented here.
This file follows the spirit of [Keep a Changelog](https://keepachangelog.com/).

---

## [A Butler Who Can Carry a Message — and Remember Who Said What] — 2026-07-26

Two owner-facing capabilities around the Telegram gateway, which until now could message
exactly one person (the owner) and could not tell him anything a partner had said, because
channel isolation kept her conversation inside her own session. Guests gained no new powers
in either direction.

### Added
- **`message_partner` (CONFIRM-tier) — propose and approve.** "Ask my girlfriend if she's
  eaten" → JARVIS drafts → the authorisation read-back names the **resolved partner** and
  quotes the **entire message verbatim** → "confirm" sends it, "cancel" does not. Works
  identically at the desk, on the HUD command line, and from the owner's own Telegram chat
  (session-scoped by `confirmation_id`, so a phone "yes" can only answer what that phone was
  asked). New `modules/partner_registry.py` resolves role words *and* first names
  (girlfriend/gf/mousumi, brother/kinshuk) to `TELEGRAM_GF_ID` / `TELEGRAM_BROTHER_ID` —
  **allowlist-only**: anything containing a digit is refused as a raw-chat-id attempt before
  the alias table is consulted, and unknown ("Priya") or vague ("her", "someone") recipients
  are refused honestly rather than guessed. A private message to the wrong person is the
  failure this path exists to prevent.
- **`summarize_partner_chat` (admin-only) — pull, never push.** "What did my girlfriend tell
  you" now answers instead of denying. Reads one partner's slot only, and every answer leads
  with a disclosure that it is logged data.
- **`telegram_bot.send_text_to_partner`** — the first outbound path with a recipient other
  than the owner. Re-checks the id against the recognised-identity registry and refuses the
  admin id, so a "partner" send cannot be redirected. Two callers only (transport + engine),
  asserted by the harness. Not exposed as an agent tool: the loop cannot message a person.

### Opt-in, by design
- **`JARVIS_LOG_PARTNER_CHATS`, default OFF.** `modules/partner_log.py` records a partner's
  INBOUND messages into a new `partner_messages` table **inside the existing
  `jarvis_longterm.db`** (no new store). With the flag off nothing is written — not even the
  table — so no later change of mind can read a conversation that happened while it was off.
- **Scope, ruled explicitly by the owner:** the flag governs that raw store only. Per-user
  memory extraction (`extract_and_store_memory` → `memory_manager`, keyed `user='MOUSUMI'`)
  has always run for every recognised caller and keeps running either way — it is what makes
  JARVIS know her warmly in her own chat. "Off" therefore means *no verbatim transcript*, not
  *nothing retained*. These rows are third-party personal data and belong under the planned
  encryption-at-rest work (roadmap TIER C #11); the module says so.

### Unchanged (deliberately)
- `tier_allows` and `VIP_GUEST_ALLOWED_ACTIONS` are untouched — neither new action is on the
  guest allowlist, so a guest is refused before dispatch, logging, or a governance pend.
  `governance_manager.check` is unmodified; the new rules are two lines of `governance.json`.

### Tests
- `test_partner_messaging.py` — 34 checks, weighted toward the refusals: raw ids rejected in
  every shape (string, int, embedded, phone-formatted), unknown/vague/double-named recipients
  refused, verbatim read-back proven longer than the generic 120-char one, **deny is terminal**
  across routes with a TTL so the owner can still change his mind, a staged send is not staged
  twice, admin-only summary, one partner's history never visible in another's, and the OFF
  default proven by asserting the table does not exist. Suite: **787/787, 32/32 harnesses**.

---

## [The Dev Backdoor Stops Being a Free Biometric Bypass] — 2026-07-26

`POST /api/backdoor` — the HUD command line, the regression driver, every `test:` hook —
dispatched commands with **no face scan at all**: typing "wake up" ran the full morning
briefing while the system was still locked. Convenient for testing, but the entire optical
biometrics layer was skippable by one loopback POST that nothing announced.

### Changed
- **The bypass is now opt-in** (`jarvis-backend/modules/backdoor_gate.py`, wired at the top
  of `backdoor_command` in `main.py`). Flag unset/`0` (the default): the endpoint dispatches
  only when the session is already authenticated (`SYSTEM_ONLINE`); while locked it returns
  `403 {"status":"refused","reason":"locked"}`, logs `[BACKDOOR] REFUSED (locked)`, and does
  not even record "recent activity". `JARVIS_ALLOW_BACKDOOR=1`: the old behaviour, kept for
  harnesses (`run_phase1_regression.py`, `test_ui_bridge_e2e.py`) and manual gate runs — now
  a conscious per-boot choice.
- **`test:` hooks get no special pass.** They reach the same dispatcher, so a per-command
  allowlist would just be a softer bypass.
- **HUD** surfaces the refusal as `BIOMETRIC AUTH REQUIRED // SAY THE WAKE WORD …` instead
  of a bare HTTP status (`App.jsx`).

### Unchanged (deliberately)
- Risk tiers and governance (`tier_allows`, `governance_manager.check`) are untouched — this
  gates *authentication only*. An authenticated backdoor command is exactly as constrained
  as a spoken one; the gate module is harness-asserted to never reference either.

### Tests
- `test_backdoor_gate.py` — 15 checks: full truth table (flag × auth), flag spellings,
  refusal payload shape, immutability, purity, and a source assertion that the gate runs
  **before** `_last_command_time`/`classify_intent`/`process_command` (a late gate is no gate).
- **The risk-tier guard joined the gated suite.** `test_governance.py` was the one
  pytest-gated file nobody wanted skipped, and it needed exactly one thing pytest provided
  (an `autouse` fixture clearing the pending-confirmation slot). It is now self-running —
  `_reset()` at the top of each test, and again in the runner's `finally`, so a CONFIRM
  pending in one test cannot leak into the next one's `has_pending()`. No pytest installed:
  measurement showed the install would unlock ~12–17 green tests and 6–7 red ones, and the
  real cost was a second command `run_harnesses.py` can't gate (see TEST_PLAN PART A3).
- Suite: **753/753 green, 31/31 harnesses, ~12 s**.

---

## [Level-3 Desk↔Cloud Bridge — One J.A.R.V.I.S., One Front Door] — 2026-07-04

Fuses the always-on cloud gateway and the real desk brain into a single assistant.
Until now they were two strangers: the cloud was reachable 24/7 but "lite" (no PC,
no files, no real memory), and the desk was the real thing but only reachable at the
machine. The bridge makes the cloud the **sole front door** and, whenever the PC is
online, routes every recognized Telegram message to the **real desk brain** — full
ActionEngine, ReAct planner, and your actual memory — with graceful fallback to the
cloud's local brain when the PC is off. This also solves memory sync (there is only
ever *one* memory) and the Telegram single-consumer conflict (the desk no longer
competes for the bot token).

### Added
- **Desk bridge client** (`jarvis-backend/modules/cloud_bridge.py`) — dials **out** to
  the cloud's `/desk-link` WebSocket (auto-reconnect w/ capped exponential backoff),
  authenticates with a shared `BRIDGE_SECRET`, and runs each forwarded command through
  the **same `run_remote_command` pipeline** as voice/HUD/Telegram. A `BridgeChannel`
  (`OutputChannel`) streams replies/typing back up the socket, scoped per remote chat.
- **Cloud `/desk-link` endpoint** (`cloud_gateway.py`) — secret-checked (`X-Bridge-Secret`,
  `hmac.compare_digest`) *before* accept; tracks the linked desk (last-writer-wins), relays
  the desk's `reply`/`notify` frames to the right Telegram chat, and falls back to local
  `think()` on disconnect. Telegram handler now forwards to the desk when linked.
- **Single-front-door wiring** (`main.py`) — starts **either** the bridge (when
  `JARVIS_CLOUD_BRIDGE=1` + `JARVIS_BRIDGE_URL` + `BRIDGE_SECRET`) **or** the direct
  Telegram poller, never both, since Telegram delivers to a single consumer.

### Protocol
- JSON frames over TLS WebSocket: cloud→desk `{cmd: req_id, chat_id, user, tier, honorific, text}`;
  desk→cloud `{notify}` / `{reply: chat_id, text}` / `{done: req_id}`. Cloud upper-cases the
  identity name so the desk brain keys persona/memory consistently (`KAUSTAV`/`MOUSUMI`/`KINSHUK`).
- v1 scope: text replies + typing. File delivery over the bridge is deferred (`send_document`
  returns a polite "ready on the desk" note).

### Verified
- 17/17 protocol unit tests (frame construction, uppercasing, tier/honorific passthrough,
  send-failure fallback, secret comparison, `BridgeChannel` frame shapes + 4000-char chunking).
- 6/6 live end-to-end tests against the running FastAPI endpoint (wrong/no secret rejected,
  correct secret accepted with `welcome` + identities, stable under frames). All three
  backend files compile clean.

---

## [Always-On Cloud Gateway — Reach J.A.R.V.I.S. with the PC Off] — 2026-07-03

A separate, feather-light cloud brain so J.A.R.V.I.S. is reachable from Telegram **even
when the desk PC is off**. The full desk stack (`main.py` / `action_engine`) is Windows-
and hardware-bound (mic, camera, vision, pywin32, pyautogui) and cannot run headless; this
adds a decoupled process that shares only the *voice* and `cloud_first` reasoning. Deployed
live on Render's free tier, kept warm by UptimeRobot — **$0**.

### Added
- **Cloud Gateway** (`jarvis-backend/cloud_gateway.py`) — a self-contained **FastAPI + aiogram
  v3** Telegram bot. Reuses the J.A.R.V.I.S. persona (compact variant of `brain.BASE_CORE`) and
  a self-contained **Groq key-rotation** brain (mirrors `modules/groq_key_manager.py`), with
  rolling per-chat memory (last ~12 turns) and **best-effort DuckDuckGo lookups** for factual
  queries (weather/scores/news). Chat and lookups only — privileged PC actions are politely
  **deferred** (`_PC_DEFERRAL`) since the machine is unreachable from the cloud.
- **Identity firewall** carried over from `modules/telegram_bot.py` — admin (Kaustav) + optional
  VIP guests (Mousumi / Kinshuk) recognized by numeric Telegram id; unrecognized ids are silently
  dropped.
- **Dual transport** via `CLOUD_GATEWAY_MODE`: **webhook** (default; Telegram pushes to
  `/webhook/<secret>`, survives free-tier sleep) or **polling** (for a VPS / paid worker). A
  token-derived webhook path secret means neither the bot token nor an operator-invented secret
  is ever needed in the URL.
- **`/health` endpoint** for UptimeRobot keep-alive pings (returns status + recognized identities,
  no secrets) so a free web service never idles into a cold start.
- **Deploy artifacts:** `requirements-cloud.txt` (minimal, headless-safe deps — no
  torch/tensorflow/pyaudio/pywin32), root **`render.yaml`** Blueprint (free web service, webhook
  mode, health check), and **`jarvis-backend/CLOUD_GATEWAY.md`** deploy guide (Render + UptimeRobot).

### Verified
- Imports clean against the desk venv; reads the shared `.env` (2 identities, 5 Groq keys).
- Deployed live at `https://jarvis-cloud-gateway.onrender.com` — webhook registered, UptimeRobot
  monitor 100% up, **Telegram confirmed working end-to-end**.

### Notes
- Cloud and desk run **independently** — no shared memory yet (level 1). Structured for later
  level-2 (shared hosted DB) or level-3 (bridge/relay to the desk brain when the PC is online).
- Shared bot token: keep the cloud on **webhook** and the desk on **polling** to avoid a
  poll-conflict.
- Open hardening item: the webhook currently trusts any well-formed POST (no Telegram
  secret-token header check yet) — low risk given the cloud has no privileged actions.

---

## [Autonomy Monitoring & Manual Override] — 2026-06-27

Stark-style visibility and control over the Overnight Worker: hear what J.A.R.V.I.S. is
doing, watch it happen, and kill any background agent instantly.

### Added
- **Voice Hook — background-queue status report.** "What are you working on?", "Status
  report", "What's in the queue?", "What's running?" (and similar) now read the live
  `jarvis_tasks.db` and speak a natural, **deterministic** (no-LLM, zero-hallucination)
  summary — e.g. *"I'm currently working on the Figma layout build, and I have 2 more queued,
  starting with the arc reactor search, Sir."* Backed by new `task_queue.spoken_status_report()`
  and `task_queue.status_counts()`; wired into both the backdoor and voice command paths via
  `_QUEUE_STATUS_PHRASES`. The reply also lands in the chat transcript.
- **Task HUD — the AUTONOMY QUEUE widget** (`src/components/TaskHud.{jsx,scss}`). A sleek
  top-left glass panel subscribing to `GET /api/tasks`: polls every 4 s while open
  (AbortController-guarded), refetches instantly on any worker WebSocket event, and
  **auto-opens** when a task or autopilot run starts. Rows are grouped Running → Queued →
  Needs-OK → Failed → Done → Cancelled, each with a colored status pip (running = pulsing cyan,
  queued = amber, needs-OK = gold, done = green, failed = red, cancelled = struck-through grey)
  and a live "N ACTIVE" count. Toggled by the new **TASKS** button in the command cluster.
- **HUD Kill Switch — per-row cancel (✕).** Each in-flight task (running / pending /
  needs-confirmation) shows a cancel control on hover that hits `POST /api/tasks/{id}/cancel`
  with an **optimistic update** — the row flips to struck-through grey instantly, then the next
  poll reconciles with server truth. Immediate manual override over any background agent.

### Changed
- **`App.jsx`** — worker lifecycle events (`task_started`, `task_done`, `autopilot_*`, etc.)
  are intercepted early in the WebSocket handler so they refresh the Task HUD **without**
  polluting the chat transcript, system log, or status display.

### Verified
- Spoken status report tested against the live DB (empty + active states); React production
  build compiles cleanly with both new widgets (ChatPanel + TaskHud) and the cancel control.

---

## [Local-First Autopilot & Cognitive Upgrades — Phase 3] — 2026-06-27

A shift to a **local-first, privacy-centric stack**, plus a LangGraph-powered overnight
coding autopilot. Everything is async, lazy-loaded, and degrades gracefully — no new
dependency can break startup or the voice loop if a package/key/Ollama is missing.

### Added
- **Local RAG cortex** (`modules/rag_cortex.py`) — persistent ChromaDB + **local HuggingFace
  `all-MiniLM-L6-v2`** embeddings (no OpenAI). Heading-aware chunking, idempotent ingest, async
  `aquery_standards()` / `aingest_standards()`. Embedding model loads lazily (one-time ~90 MB
  local download). Corpus: new **`frontend_standards.md`** (BEM/SCSS rules).
- **Figma extractor** (`modules/figma_parser.py`) — async `httpx` client; extracts layout modes,
  spacing, dimensions, typography, and colors (rgba→hex) from the Figma REST API.
- **Overnight Autopilot** (`modules/agent_worker.py`) — a cyclic LangGraph `StateGraph`:
  `parse_figma → retrieve_standards → generate_code → validate_syntax → save_files`, with a
  **self-healing edge** (re-generate with the error traceback on invalid HTML/SCSS, max 3 retries).
  `generate_code` uses the **Claude API** (default `claude-sonnet-4-6`, env-overridable) with a
  router heavy-path → local fallback. Runs as a background `asyncio.create_task`.
- **Tavily search** — `_tavily_search` + `tavily_search` action (governance AUTO); `web_search`
  now prefers Tavily when `TAVILY_API_KEY` is set, with automatic DDGS fallback.
- **Endpoint** `POST /api/autopilot` (lazy-imports langgraph; launches the pipeline in background).
- Installed into the venv: `langgraph`, `langchain-core`, `sentence-transformers`, `tavily-python`, `anthropic`.

### Changed
- **Router overhaul** (`modules/llm_router.py`) — `universal_llm_call` is now a local-first provider
  chain. Standard work runs on local Ollama `llama3:8b`; escalates to Groq on local
  failure/timeout, or **cloud-first** when `complexity="heavy"`. Vision (`llava`) is pinned
  local-only. New env knobs: `JARVIS_LLM_MODE`, `OLLAMA_MODEL`, `OLLAMA_VISION_MODEL`,
  `JARVIS_LOCAL_TIMEOUT`.
- **`brain.process_command`** flags `CODER` intent as `complexity="heavy"` so complex
  coding/architecture escalates to the cloud.

### Verified
- All modules import with heavy deps staying lazy; router decisions
  (standard→local, heavy→cloud, vision→local); LangGraph graph compiles; Figma token
  extraction; self-healing validator fires on malformed output.

---

## [Continuous Autonomous Agency — Roadmap §1.1] — 2026-06-27

The leap from *responder* to *agent*: the **Overnight Worker Loop**. J.A.R.V.I.S. can now
hold a durable queue of goals, pursue them on his own in the background, and report results
when you next engage.

### Added
- **`modules/task_queue.py`** — durable SQLite goal queue (`jarvis_tasks.db`). Tasks survive
  restarts; each carries a list of action payloads. Atomic `claim_next_pending()` (BEGIN
  IMMEDIATE), status lifecycle (pending → running → done/failed/needs_confirmation/cancelled),
  and `requeue_stuck_running()` to recover tasks interrupted by a crash. All access is
  synchronous and called via `asyncio.to_thread` (non-blocking).
- **`modules/worker_loop.py`** — the `OvernightWorker` daemon. Drains the queue, executes each
  task through `execute_with_retry` (inheriting self-correction + governance), and surfaces
  results to HUD/voice.
  - **The loop never dies:** every task runs inside nested try/except; a crash is logged and
    recorded against the task, and the loop continues.
  - **Safe autonomy:** each action's governance tier is pre-screened read-only
    (`governance_manager.get_tier`). Only **AUTO**-tier actions run unattended; **CONFIRM** and
    **BLOCK** actions are recorded and surfaced for interactive approval — never executed in the
    background, never touching the interactive confirmation slot.
  - **Result surfacing:** announces on completion if you're engaged; otherwise defers and
    reports on next wake via `report_pending()`.
- **Task API:** `POST /api/tasks` (enqueue), `GET /api/tasks` (list/filter), `POST /api/tasks/{id}/cancel`.
- **Backdoor test hook:** `test:enqueue_task[: <query>]` — queues a real web-search task to
  exercise the full agentic loop on demand.

### Changed
- **`main.py`** — the Overnight Worker is started as a daemon in `lifespan()` (stopped cleanly
  on shutdown), and finished-while-away results are reported on every wake (backdoor + voice paths).

### Verified
- End-to-end: `web_search` (AUTO) → **done**; `gmail_send` (CONFIRM) → **deferred/needs_confirmation**;
  `delete_file` (BLOCK) → **failed-by-policy**. Worker logs are ASCII-only (no Windows cp1252 crash).

---

## [Refinement & Polish Phase] — 2026-06-27

A two-session overhaul focused on async correctness, resource safety, conversational
memory, interruptibility, and the daily wake-up experience. All changes are additive
and defensive — no routing, persona, or governance logic was altered. Every modified
file byte-compiles on Python 3.13.

### Added

- **First-Boot Daily Briefing.** The first wake of each calendar day now delivers a
  full **Comprehensive Morning Briefing** — explicit date + time, a walk-through of
  today's calendar, unread-mail/vitals highlights, and an explicit system-readiness
  confirmation. Same-day re-wakes fall back to the standard short greeting.
  - New on-disk marker `last_boot_date.txt` + `_consume_new_day_briefing()` in
    `main.py` (fires once per day, survives restarts).
  - `generate_briefing()` in `brain.py` gained a `comprehensive=True` mode (reuses the
    existing calendar/email/health/weather fetch; richer prompt, larger token budget).
  - `_smart_briefing()` now branches: new day → comprehensive; recent activity →
    standby line; same-day idle → standard greeting.
- **`test:morning_briefing` backdoor hook.** Replays the comprehensive briefing on
  demand (full booting → waking → online UI sequence) without a date rollover or
  deleting the marker file. Mirrors the existing `test:deep_work_ui` hook.
- **Session-digest memory store.** New `session_digest` SQLite table (one rolling
  digest per user) plus helpers in `memory.py`: `consolidate_working_memory()`,
  `seed_from_last_digest()`, `save_session_digest()`, `get_last_session_digest()`.
- **Barge-in cancellation token.** Module-level `interrupt_flag = asyncio.Event()` in
  `main.py`, checked by all streaming-synthesis loops.
- **Anti-fabrication grounding rule** in the synthesis prompt (`brain.py`) — the voice
  may only state facts present in the retrieved data; missing/error data is reported as
  missing, never invented.

### Changed

- **Async Playwright Overhaul (`web_agent.py`).** `close()` rewritten so each teardown
  step (page → context → browser → playwright) is independently guarded and all handles
  are nulled even on partial failure — eliminates leaked **zombie Chromium / Playwright
  node processes** on a mid-session error. `_init_browser()` now tears down and re-raises
  on a partial init instead of leaving a half-built stack that crashes the next call.
- **Threading / non-blocking I/O (`action_engine.py`).** `ActionEngine.execute()` is
  async, but its data handlers were synchronous network/disk calls running **directly on
  the event loop**, freezing TTS streaming, the WebSocket, and all daemons. Offloaded 12
  blocking handlers to worker threads via `asyncio.to_thread`: `web_search`,
  `web_search_image`, `play_music`, `system_status`, `get_telemetry`, `check_email`,
  `read_email`, `gmail_read_unread`, `gmail_read`, `check_calendar`, `check_vitals`,
  `find_file`.
- **Short-Term Memory Consolidation (sleep/wake continuity).** Working memory is no
  longer wiped into oblivion on sleep. Before every `clear_working_memory()` (WebSocket
  STAGE 0 + backdoor sleep) the session is LLM-condensed into a persistent digest; on
  every wake (WebSocket STAGE 2 + backdoor wake) fresh working memory is re-seeded with
  that `[PREVIOUS SESSION RECAP]` so J.A.R.V.I.S. resumes with immediate context.
  Consolidation/seeding are offloaded via `asyncio.to_thread` (non-blocking SQLite).
- **True Interruptibility (`main.py`).** `_stream_synthesize_speak`,
  `_stream_briefing_speak`, and `_stream_deep_memory_speak` now check `interrupt_flag`
  at the top of every sentence iteration and break — J.A.R.V.I.S. can be cut off
  mid-monologue. `stop / quiet / shut up / cancel / enough / silence` in both the
  backdoor and voice paths set the flag **and** call `speaker.stop_audio()`; the flag is
  cleared at the start of each new valid command. Voice barge-in only fires while he is
  actually speaking, preserving "stop"/"cancel" as a governance denial otherwise.
- **Router resilience (`llm_router.py`).** The Groq→Ollama fallback no longer mutates
  the caller's shared `messages` list (it built a copy with a fresh final turn),
  preventing working-memory corruption and double-appended JSON instructions on retry.
- **Latency tuning (`brain.py`).** `classify_intent` timeout reduced 30s → 15s (tiny
  call; tighter ceiling = faster Ollama fallback on a hang, no risk of premature
  fallback on valid responses).

### Fixed

- **Chain-breaking tool exceptions.** `execute_with_retry()` wraps `execute()` in a
  universal trap: any unhandled handler exception is logged with a full traceback,
  recorded as `FAILED` in the trace ring, and returned as a clean localized string
  (`"Action failed: the '<tool>' tool encountered an unexpected error …"`) instead of
  bubbling an opaque fault that kills the whole action batch.
- **Raw-data leak to TTS.** The synthesis-stream exception path no longer yields up to
  120 chars of raw data/JSON to the voice; it now speaks a clean fallback line.

### Notes

- The comprehensive briefing fires on the **first wake after midnight** — use
  `test:morning_briefing` to replay it during the day.
- Recommended next steps (not yet implemented): a durable task/goal queue for the
  Overnight Worker Loop, and per-session scoping of the global `engine` / `active_user`
  singletons before the Telegram Gateway introduces concurrent clients.
