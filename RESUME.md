# RESUME — pick up here

> Rewritten 2026-08-16, at the end of live-gate **session 3**. This file is a
> **bookmark, not a history**: what is true now, and what to do next. How
> anything got this way is in `git log` — every fix in this project carries its
> reasoning in the commit message, deliberately.
>
> Read this, then `LIVE_GATE_FINDINGS.md` (most recent section first), then
> `JARVIS_MASTER_ROADMAP.md`.

## STATE — 2026-08-16, end of session 3

**Branch `feat/cloud-gateway`, ELEVEN commits ahead of origin — unpushed.**
Suite **81/81 harnesses, 2575 checks, 0 failed**
(`jarvis-backend\venv\Scripts\python.exe run_harnesses.py` — the system python fakes failures).

Session 3 was short and did one thing: **another go at row `4.1`, with the
backend launched under a captured stdout**, so for the first time the gate has a
complete machine-read log instead of a reconstruction. No code was changed.

**Row `4.1` FAILED for the fourth time, on a fourth distinct cause — and this
one told the owner it had succeeded.** He heard "saved" and heard the file's
contents read back. Nothing was ever written.

### What session 3 CLOSED — four things are now proven on hardware

| Finding | Proof |
|---|---|
| **F-22** | the prompt named `C:\Users\KINGSHUK\OneDrive\Desktop\add.py`. Desktop survives OneDrive redirection — also verified offline against `_resolve_within_roots` |
| **F-29** | the prompt disclosed action, path AND size before asking |
| **F-35** | the re-ask fired on an unintelligible answer, first live outing |
| **F-37** | the pre-flight guard refused a contract-in-the-payload write, and the raw `Format:` string stayed out of TTS |

### What session 3 OPENED — six new findings, F-39 … F-44

Full write-ups in `LIVE_GATE_FINDINGS.md`, last section. The short version:

| ID | Sev | What |
|---|---|---|
| **F-40** | 🔴 | `main.py:3332` has **no else**. An answer to a live authorisation that matches no token is dispatched to the brain **as a new command**, pending slot still armed |
| **F-41** | 🔴 | The brain then **narrated a write it never performed** — "saves it to the desktop as add.py" plus the source. The real pending action expired unanswered meanwhile |
| **F-44** | 🔴 | `gemini-flash-latest` → `gemini-3.7-flash`, **20 requests per DAY**, one bucket for all four keys, #5 revoked. `classify_intent` (140 tokens) gets a bare `{` from a thinking model and **falls silently into GENERAL**, which never reaches the action engine |
| **F-42** | 🟠 | Confirm words are matched by SUBSTRING, so they are order-dependent. STT returned `'ahead go'` for "go ahead" and it matched nothing |
| **F-39** | 🟠 | `load_dotenv(override=True)` + a **present-but-empty** key in `.env` silently erases what the operator set on the command line. Two of the three exports in this file's own start block never reached the server |
| **F-43** | 🟠 | Expiry printed `[EXPIRED]` to the console and said nothing aloud. The sentence that would have corrected his false belief exists at `main.py:3296` and does not fire on this path |
| **F-37b** | 🟠 | The guard works because the prompt does not: the model still writes the contract into the payload, now on a non-Gemini model too |

## ▶ NEXT: FIX THE CONFIRM PATH BEFORE ANY MORE LIVE ROWS

**`4.1` cannot pass until F-40 and F-42 are fixed**, and rows `1`, `2`, `4`, `7`,
`10` all end in a CONFIRM, so the whole list is blocked behind them. This is
code work — no hardware needed until the retest.

Three changes, all in `main.py`, designed and reverted unwired in session 3
(deliberately: an unused approval helper next to the live buggy one is a trap):

1. **Token-set matching** for `_APPROVAL_WORDS` / `_DENIAL_WORDS`. An entry
   matches when every word in it is present, any order; drop apostrophes on both
   sides so `"don't"` also matches a transcribed `dont`.
2. **Denial wins a tie.** Approval is tested first today, so *"no, go ahead"*
   executes. A gate whose job is to not act by accident must break ties towards
   doing nothing.
3. **The missing else** at `main.py:3332`. While a confirmation is pending the
   next utterance is an ANSWER — approve, deny, or not understood. "Not
   understood" already has correct handling four lines up (F-35's re-ask); route
   there, with a counter keyed to the pending `cid` so it cannot loop (the
   existing `_confirm_reasks` is reset every turn at `:3312`). After the budget,
   cancel the pending, **say so aloud** (F-43), then process the utterance as a
   command — which is what the remote door at `:1710` already does silently.

**Root cause #4 applies**: the remote/Telegram door at `main.py:1678` has the
identical substring bug. One helper, both doors, harnessed at both.

Then F-44: pin `GEMINI_MODEL` off the evergreen alias or raise
`classify_intent`'s 140-token budget above the thinking overhead — and make the
classifier's fallback say that it fell back.

## 🔴 STILL OPEN FROM EARLIER SESSIONS — nine findings

The first four are one theme, and it is still the worst one this gate has
produced: *a security barrier whose only exit depends on the subsystem whose
failure raised it.*

| ID | Sev | What |
|---|---|---|
| **F-25** | 🔴 | The desk soft-lock trapped the owner at his own desk — screen names the camera as the way out, and a blind camera is what armed it |
| **F-20** | 🔴 | The HUD lockdown overlay latches forever — every message that would clear it is `is_proactive` and hits an early `return` |
| **F-19** | 🔴 | The owner was declared an intruder 4 min after a successful match, and it escalated to lockdown. Identity flaps on the 60s poll |
| **F-27** | 🔴 | The typed door is bolted; the **spoken** `initiate admin override` grants admin unauthenticated — and the phrase is printed on the idle screen |
| **F-23** | 🔴 | Half fixed. A failed challenge still **terminates** instead of retrying (`main.py:3204`) |
| **F-24** | 🔴 | **Upgraded from 🔵 by F-44.** Intent classification falls back silently — and a GENERAL fallback drops the instruction entirely |
| **F-21** | 🟠 | "Initiating lockdown protocols" secures nothing — root cause #4, the second door of a fix already made in `main.py` |
| **F-09** | 🟠 | REOPENED: the briefing narrates four data sources it never read. F-32's fix is the lead |
| **F-26** | 🔵 | The HUD fetches its own typeface from the public internet |
| **F-18** | 🔵 | Stale row wording: `0.3` points at `/` not `/hud/`; the setup block says `.env\Scripts` where it means `venv\Scripts` |

## HOW TO START THE DESK (corrected — the old block was partly a placebo)

```powershell
cd F:\work\JARVIS-Project\jarvis-backend
$env:JARVIS_AUTO_LOCK='0'
.\venv\Scripts\python.exe watchdog.py
```

Per **F-39**, `JARVIS_UNLOCK_CODE` and `WATCHDOG_TOKEN` set this way are
overwritten by `.env` and do **not** reach the server. What is actually in force:

- `JARVIS_AUTO_LOCK=0` **does** survive (it is commented out in `.env`) — this is
  what keeps the F-25 trap from arming.
- The soft-lock escape code is **`itsadmin`**, from `.env:97`.
- The shutdown token is whatever `.env:32` holds — it is currently **empty**, so
  the watchdog generates one per session and prints it. To stop the desk from a
  terminal: `POST http://127.0.0.1:8009/shutdown?token=<that token>`.

To capture a log next time (worth it — session 3 found four things from the log
alone), redirect stdout to a file and read it rather than watching the console.

## ⚠️ OWED BY HAND — nothing here is code

1. **Gemini key #5 is revoked** (`API_KEY_INVALID`) — confirmed again live, the
   router drops it every process. Replace or remove it.
2. **The primary `GEMINI_API_KEY` in `.env:4` is also invalid** — a direct probe
   returned `API_KEY_INVALID`. Only the `GEMINI_API_KEYS` pool is carrying the load.
3. **The free tier is 20 requests per DAY on `gemini-3.7-flash`**, shared across
   every key — the router now prints this conclusion itself. Decide: pin an older
   model, pay, or accept that Gemini is a burst resource and the escalation
   provider is the real brain.
4. **Shred `jarvis-backend/jarvis_chroma_db.plaintext-20260816-120052/`** — the
   M5 migration's safety copy, and the last plaintext copy of the 118 memory documents.
5. **`run_evals.py --live`** — the 40/40 in the suite is the RETRIEVAL eval:
   deterministic, offline, model-independent. It says nothing about the model.
6. **Decide on `run_evals.py`'s change in `9b12df6`** — it excludes six follow-up
   prompts from the live score, raising the number by dropping 15% of the set.
7. **`F:\work\filepath`** (32 bytes) is litter from the F-37 failure. Delete when
   convenient — session 3's guard no longer creates it.

## THEN — in this order, and not before the gate

1. **The capability pass, as ONE piece of work**: `list_capabilities` built FROM
   the registry so it cannot rot; a **human-run** skill installer
   (`install_skill.py <url>` — fetch, show the whole body, ask, then write),
   never agent-callable; and a shortlist from `public-apis` scored on *would he
   use it repeatedly*.
2. **The torch move**, before the `.exe`. Its own change, protobuf under a
   microscope, never bundled.
3. **Electron packaging** — `ELECTRON_SHIP_PLAN.md`.

## THE SEVEN THINGS THAT KEEP BEING TRUE

1. **Run the suite with the venv.** Green is 81/81, ~2575 checks, ~115s.
2. **`protobuf` stays at 6.33.6.** Check after every install.
3. **A green suite proves only what its harnesses drive.** Session 3 opened with
   2575 green checks and found six findings in one hour, with one command.
4. **An injection class fixed one site at a time stays open.** Before fixing any
   protected-resource defect: *which OTHER verb reaches this resource, and which
   other door reaches that verb?* Root cause #4 has now appeared **six** times —
   F-42 is the newest, with the same substring bug on two doors.
5. **When a production signature gains a keyword or a function, grep the
   harnesses for stubs of it.**
6. **A claim requires positive evidence.** The absence of a known failure marker
   is not evidence of success — F-28, F-16, and now F-41, where the owner
   reported a success he had been told about but which never happened.
7. **Capture the log.** Session 3 changed nothing and still closed four findings
   and opened six, purely because stdout was redirected to a file and read.

## BRANCHES

| Branch | State |
|---|---|
| `feat/cloud-gateway` | live, **11 commits ahead of origin, unpushed** — the only one to work on |
| `feat/app-full-power` | 0 unique commits. Redundant; delete when convenient |
| `main` | far behind, **+1 commit we lack** (`8d0ea4f`, the GPL LICENSE). Not a fast-forward. Leave until after the gate |

## DOC MAP

| File | For |
|---|---|
| `LIVE_GATE_FINDINGS.md` | **read the last section first** — the gate's running record |
| `LIVE_GATE_CHECKLIST.md` | the §7 running order |
| `JARVIS_MASTER_ROADMAP.md` | the plan. Single source of truth |
| `REVIEW.md` / `review-findings.json` | the code review and its 46 findings |
| `TEST_PLAN.md` | the harness suite |
| `AGENT-TOOLING-REFERENCE.md` | the 18 agent-tooling rules §6.8 implements |
| `ELECTRON_SHIP_PLAN.md` | packaging, for after the gate |
| `JARVIS_MANUAL.md` / `MOBILE_CONNECT.md` | operating it |
