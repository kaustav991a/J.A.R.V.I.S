# RESUME — pick up here

> Rewritten 2026-08-16, at the end of live-gate **session 3**; the 2026-08-17 …
> 2026-08-20 cloud-gateway sections below arrived from the laptop and were
> merged in on 2026-08-22. This file is a **bookmark, not a history**: what is
> true now, and what to do next. How anything got this way is in `git log` —
> every fix in this project carries its reasoning in the commit message,
> deliberately.
>
> Read this, then `LIVE_GATE_FINDINGS.md` (most recent section first), then
> `JARVIS_MASTER_ROADMAP.md`.

## 🏠 START HERE — 2026-08-20, 5:30 PM. The photo answered with a search request.

**First job on the desk, before anything else:**

```bash
python run_harnesses.py   # expect 81 + 4 /app-commute + 8 reasoning-leak + 17 vision-marker
```

Two days of Python-side work has never been executed on a machine with Python.
That is the whole of the risk in this repo.

### What was found and fixed this evening

A photo of a motorcycle, captioned "what is this bike? what is the mileage?",
came back on the device at 17:15 as its entire reply:

    [[LOOKUP: Royal Enfield Hunter 350 mileage ARAI real world]]

The vision half was right — it read the bike off the tank. `see()` was a parallel
implementation of `think()` that never got `think()`'s post-processing, while
sharing the persona that TEACHES that marker. So the model obeyed its
instructions and the gateway printed them.

Three defects from the one omission, and the leak is the least of them:

1. **the search never ran**, so the question asked was never answered;
2. `[[REMEMBER: …]]` stated over a photo leaked the same way **and was
   discarded** — a fact given with a picture was unstorable;
3. the raw reply went into the rolling history (`see()`, formerly line 1229), so
   a leaked marker became context on every following turn. Memory is
   Supabase-backed, so **that line survives restarts** and is still in the
   history now. Worth clearing.

**The fix is one shared `_resolve_markers()`**, called by both paths, rather than
a second copy of the block — a third caller is exactly how this happened once.
The subtle half is `see()`'s transcript: the second pass runs on the **text** leg,
so the base64 image is swapped for the same `[sent a photo] {caption}` stand-in
the history uses. What the model saw travels in its own first reply, which the
resolver appends.

Why the morning's `<think>` fix did not already cover this: that one went into
`_complete()`, which both legs share. This one lived in `think()` alone.

### What is proved, and how

- **The logic is right.** All 17 checks ran green in an online interpreter on
  2026-08-20 against a standalone copy — including one that runs `see()` *as it
  shipped* and asserts the leak, so the harness demonstrates the bug rather than
  only asserting the fix.
- **`test_vision_markers.py` has never run**, and neither has anything else here.
  It is picked up automatically by `run_harnesses.py`'s `test_*.py` glob. The
  standalone check covered logic, not imports — and the last push carried a
  `_load_commute()_load_commute()` SyntaxError that only a real import would have
  caught.

### Also true as of this evening

- The phone applied the OTA and `/health` now reads `memory.ready: true`,
  `facts_known: 16`, `apps_linked: 1`, `push_targets: 1`,
  `commute: {tz: Asia/Calcutta, departures: 1, days_on: 5}`. The gateway finally
  has a schedule to fire against.
- **The first push-delivered briefing is due tonight, 7:00–7:20 PM**, window
  `COMMUTE_FIRE_WINDOW_MIN` 20, tick 60s. Look for
  `[CLOUD] briefing pushed for Office (2026-08-20)`.
- UptimeRobot is now pinging `/health` every 5 minutes, which closes the free-tier
  spin-down hole that would have killed the loop before 19:00. `/health` was
  stone cold earlier today, so this mattered.
- Office coordinates are confirmed correct — the operator was standing in the
  office when the place was named, so tonight's forecast is for the right place.
- `LLM_PROVIDER_VISION=gemini` is still dashboard-only, undeclared in
  `render.yaml`. Vision is answering.

---

## 🏠 PICK THIS UP — pushed 2026-08-20 from the laptop, NOTHING RUN

**No Python on the laptop, so none of this has been executed — not the new
harnesses and not the existing 81.** That is the first job here:

```bash
python run_harnesses.py   # expect 81 + 4 new /app-commute + 8 reasoning-leak
```

**Two changes, both in `cloud_gateway.py`:**

1. **A reasoning model's thinking was being shipped as the answer.** A photo sent
   on 2026-08-19 at 19:16 came back as the model's entire `<think>` monologue —
   leaking the facts block and the injected prompt with it — and ran out of
   `max_tokens` before generating any reply at all. Fixed with
   `_strip_reasoning()` + `_answerable()` in `_complete()`, and `max_tokens`
   700 → 2000. New harness: `test_reasoning_leak.py`.
2. **`POST /app-commute`** — the phone now uploads its briefing schedule, because
   the local job on the phone provably cannot deliver one. Stored, persisted,
   reported in `/health`, refused outright when unreadable. Four checks added to
   `test_app_link.py`.

**Decision owed, not made:** `LLM_PROVIDER_VISION=gemini` lives in the Render
dashboard and is **not declared in `render.yaml`**, which is the trap that file's
own comments warn about. Vision has never once succeeded through Gemini
(`/health`: `gemini_ok: 0`, quota exhausted), so every photo pays a 429 before
reaching the Groq leg that actually answers. Either declare it, or set vision to
`groq`.

**The scheduler is built too**, in the same session — see the briefing entry
below. All four steps are in: the endpoint, the loop, the forecast server-side,
and the quiet gap. What is NOT done is running any of it.

**One defect worth knowing about.** The first push of this work carried a
`_load_commute()_load_commute()` line at module scope — a SyntaxError that would
have failed the import outright. Found and fixed the same session, before any
deploy; the live gateway answered `/health` 200 throughout, so production never
saw it. It is exactly why `run_harnesses.py` matters more than usual here:
nothing Python-side has run on the laptop, so a second one would not surface
until Render tried to boot.

### What IS proved, and what is still only written

Worth separating, because "unverified" was doing too much work:

- **The gateway imports and runs.** `/health` answers with a `commute` block,
  which the previous build could not have done — it carried the SyntaxError. So
  the deployed build is the fixed one, scheduler included.
- **The ported logic is right.** All 38 checks of the pure functions —
  `_strip_reasoning`, `_answerable`, `_js_weekday`, `_hour_label`,
  `_due_departure`, `_briefing_text` — were run against a standalone copy on
  2026-08-20 and passed: the unterminated think block, the fire window at both
  edges, never-early, once-a-day per departure, rain / storm / quiet wording, the
  wrong-hours case, and a window that wraps midnight.
- **The OTA is published.** Runtime `ff3e7ae81ec0bea0`, update group
  `ad5740a1-4f54-4837-b71b-721d4746a925`, from commit `d87e291`.

Still NOT proved, and neither is a formality:

- **`run_harnesses.py` has never run.** The standalone check covered logic, not
  the harness files themselves, and not the `/app-commute` route tests which need
  FastAPI's TestClient. Run it on the desk.
- **No briefing has ever been delivered by push.** `/health` read
  `commute.departures: 0` after the OTA was published, because the phone only
  uploads once the new bundle is running and the Places screen is touched. Until
  that number is non-zero the gateway has nothing scheduled.

**Known and deliberate:** the window label prints whole hours, so a 6:30 PM
departure reads `(6 PM–9 PM)`. Inherited from `hourLabel(d.hour)` on the phone,
which ignores minutes. Left matched rather than fixed on one side only.

---

## ⚠️ PARTLY VERIFIED — 2026-08-20: the photo that answered with its own thinking

**Not run, not deployed. Still no Python on this machine**, so neither the new
harnesses nor the existing 81 have been executed. Run `run_harnesses.py` on the
desk before pushing.

### What he saw

2026-08-19, 19:16 IST. A photo sent from the phone's camera button came back as
the model's entire internal monologue and no answer at all — read off the device
the next morning:

    <think>
    The user has sent a photo of what appears to be a smartwatch on a wrist.
    The watch face shows the time "07:16 PM" and the date "WED 08/19".
    ...
    Given the user has a dog named Kitty, it might be related, but it's hard to
    be certain.
    The user's prompt is just "The operator sent this photo without a caption —
    react to it helpfully."

Cut off there. No closing tag, no reply behind it.

**Three failures in one bubble**, and only the first is cosmetic:

1. the reasoning was displayed as the answer;
2. it carried the **facts block and the injected caption prompt** out with it, so
   a private note and the shape of the system prompt were both on screen;
3. `max_tokens=700` was spent thinking, so **the answer was never generated.** It
   was not badly worded. It did not exist.

### Why nothing caught it

The path was ordinary. `/health` read `vision.gemini_ok: 0`,
`last_error_was_quota: true` — the free Gemini key's vision quota was exhausted,
as it has been for every photo ever sent — so `_complete` fell back to Groq's
`GROQ_VISION_MODEL=qwen/qwen3.6-27b`, which is a **reasoning model**. Nothing was
misconfigured. The fallback leg simply had a property the primary did not, and no
test asked about it.

**Vision itself was fine**, which is the part worth keeping: the model read the
watch face, the date on it, and the blurred stairs behind it correctly. Only the
packaging was wrong, which is why `/health` showed nothing amiss.

### Fixed

- **`_strip_reasoning()`** removes `<think>` blocks, and an **unterminated** one
  takes everything after it — that is the case that shipped, and a regex for
  balanced pairs alone would have passed the whole monologue through.
- Applied in **`_complete()`**, not on the Groq leg, because which provider serves
  which capability is a runtime switch and Gemini's thinking models emit the same
  tag.
- **`_answerable()`** turns an empty result into an admission. A blank bubble
  reads as the app being broken.
- **`max_tokens` 700 → 2000**, so thinking cannot starve the sentence he reads.
- **`test_reasoning_leak.py`** pins all of it, including the verbatim leak.

**Not attempted:** Groq's `reasoning_format="hidden"`. Tidier where it applies,
model-specific (Groq answers 400 for a model that does not reason), and nothing
here can verify which configured ids accept it. Stripping needs no such knowledge.

**Worth deciding separately:** `LLM_PROVIDER_VISION=gemini` is set in the Render
dashboard and **not declared in `render.yaml`** — exactly the trap that file's own
comments warn about. Vision has never once succeeded through Gemini
(`gemini_ok: 0`), so every photo pays a 429 round-trip before reaching the leg
that actually answers. Either declare it, or point vision at `groq`, whose qwen
model is proved to read the base64 data URI `see()` builds.

---

## ⚠️ UNVERIFIED — 2026-08-20: the gateway now holds the commute schedule

Same caveat — not run, not deployed.

The phone's morning briefing cannot fire on its own, and it is not a quota
problem. Measured on the device, uid `10495`: `countInWindow=0` on both job
quotas, but `Network: 108 (blocked=REASON_APP_BACKGROUND|REASON_APP_STANDBY)` and
`#netAvail=0` in the RARE standby bucket. `expo-background-task` hardcodes
`setRequiredNetworkType(NetworkType.CONNECTED)`, so the work sits on a constraint
Android will not satisfy. Logcat caught the pending worker running **200ms after a
cold launch** — the app is the only thing that can unblock its own briefing, which
is exactly how it was reported.

A high-priority push is exempt from all of that, and this gateway already sends
one correctly. What it could not do is know *when*.

**Added:** `POST /app-commute` (bearer `APP_TOKEN`, same gate as the socket),
`_commute` persisted to `app_commute.json`, `_clean_commute()` which refuses a
schedule it cannot read rather than repairing one, and a `commute` block in
`/health`. Four checks in `test_app_link.py`.

**Replacement, never merge.** A departure the phone switched off must travel as an
absence and silence the gateway. Merging would leave a briefing firing on a
schedule the operator had already turned off.

**Built in the same session.** `_commute_loop` ticks every 60s, `_due_departure`
decides whether anything is owed, `_forecast_blocking` reads Open-Meteo in a
thread, `_briefing_text` writes it in the phone's voice, and
`_push_all(..., force=True)` sends it. `_briefed` holds the once-a-day mark,
persisted to `app_briefed.json`.

Four decisions not worth re-litigating:

- **The loop starts FIRST in `_startup`**, above the Telegram checks. Every
  return below that point is about the bot, and the briefing has nothing to do
  with Telegram — started underneath them it would never run under a missing
  `BOT_TOKEN` or an unset `PUBLIC_URL`.
- **It fires at the departure time or up to 20 minutes after, never before.** The
  phone's window was ±30 minutes because Android chose when its job ran. Nothing
  chooses for this loop.
- **`force=True` on the push**, the second caller ever to use it. The quiet gap
  exists so a flapping desk cannot become a burst; a briefing is once per
  departure per day and cannot burst. Dropping it because a status push went out
  four minutes earlier would be the gap policing the one thing it was never for.
- **A failed forecast does not consume the day.** No mark is written, so the next
  tick tries again — the mistake the phone made and had to be talked out of.

**The wording is a second copy** of `src/lib/commute.ts`, deliberately: the phone
keeps its version as a fallback and for PREVIEW. `test_commute_briefing.py` is
what keeps the two honest.

---

## ⚠️ UNVERIFIED — 2026-08-19: the pocketed reply, diagnosed properly and fixed

**Not run, not deployed.** This machine still has no Python, so
`run_harnesses.py` has now never run against `15b8f72`, against `a1c4892`, or
against this. **Run the suite before pushing.** Expect 81 harnesses.

### Bug C was never fixed, and the 08-18 change explains why

Measured on the phone on 2026-08-19: a question sent **one second** before
backgrounding produced **no notification in fifty seconds**, with `/health`
reporting `push_targets: 1` and `apps_linked: 1`. The token was registered.
Nothing ever asked to use it.

The 2026-08-18 guard in `deliver()` was right about the cause and could not act
on it. Its own docstring says a peer's close arrives on the ASGI *receive*
channel and that the handler is blocked in `think()` during a turn — and the
handler was the only thing calling `receive()`. So for the whole length of a
turn **nothing was awaiting that channel**: the disconnect sat unread, `alive`
stayed true, `_app_clients` still held the socket, `send_json` wrote into a dead
connection without raising, `emit()` reported success, and the push never fired.

The guard was asking a question nothing had answered.

### What changed

1. **`reader()`** — one task per connection whose only job is to be awaiting
   `receive()` at all times. On disconnect it clears `alive` and discards the
   socket from `_app_clients` **before** putting its sentinel on the queue, so a
   turn reaching `deliver()` at that moment finds the truth.
2. **The main loop reads from `inbox`**, never from the socket. `receive()` is
   now called in exactly one place, because two coroutines awaiting one ASGI
   channel is undefined behaviour.
3. **`deliver()` checks `alive`, not `_app_clients`.** That set is global: with
   a second phone attached — a release build installed beside the debug one is
   enough — it stays non-empty after *this* phone leaves, and the answer belongs
   to this connection.

### Pushed WITHOUT the suite, deliberately, and the suite is owed

Raised as a risk and overruled on purpose: a fault here takes out `/app-link`
entirely — every phone, every turn — and it went out untested because bug C had
already survived two attempts and the alternative was leaving it broken for
another day.

**So the first thing on the desk is:**

```
jarvis-backend\venv\Scripts\python.exe run_harnesses.py
```

Expect **81** harnesses, not 80: `test_web_freshness.py` has still never
executed. Three commits are now unproven by it — `15b8f72`,
`a1c4892` and this one — and if it comes back red, the app-link handler is the
first place to look, because it is the only one of the three that changed
control flow.

**If `/app-link` is refusing connections, revert this commit first and ask
questions after.** The phone falls back to nothing when that socket is gone.

---

## ⚠️ UNVERIFIED — 2026-08-18, two cloud-gateway fixes that were NOT run

**Landed on `feat/cloud-gateway` from the laptop, and the suite was never run on
them: that machine has no Python at all.** `test_web_freshness.py` (11 checks) is
written and registered in `run_harnesses.py` but has **never executed**. Run it
first, with the venv:

```
jarvis-backend\venv\Scripts\python.exe run_harnesses.py
```

Expect 81 harnesses, not 80. If the new one fails, the two changes below are the
only suspects — nothing else was touched.

**Neither fix does anything until Render redeploys.** Both are in
`jarvis-backend/cloud_gateway.py`, which is what `render.yaml` runs.

1. **`deliver()` consults `_app_clients` before trusting the write.** A reply to a
   phone that had been pocketed vanished — no notification, three attempts running
   on the device on 2026-08-18. `emit()` treated a successful `send_json` as proof
   of delivery, and it is not: a peer's close arrives on the ASGI *receive* channel,
   and during a turn the handler is blocked in `think()`, so the disconnect has not
   been consumed. The write succeeds into a connection nobody holds and the push
   never fires.
   **This fix depends on a jarvis-mobile change of the same day** — `LinkMachine.suspend`
   closes the socket when the app backgrounds, so `_app_clients` is finally truthful
   (measured: `apps_linked` 1 → 0 when the phone leaves). Before that the list was
   full of phantoms and this guard would have *suppressed* the push it exists to
   trigger. The two repos have to move together; do not revert one alone.
2. **Web lookups carry publication dates.** He was warned about rain using a **2025**
   monsoon article. Nothing was broken: the phone already sends local wall time, so
   the model knew what *today* was — it never knew how old the *evidence* was,
   because `_tavily_lookup` discarded Tavily's `published_date`. Snippets are now
   `- [2025-06-14] title: content`, undated ones say `[date unknown]` out loud, the
   block is stamped with today's date, and recency-hinted queries ask for
   `topic="news"` + `days` instead of appending "latest result today" to the query —
   which was a hint to the ranker, not a filter, and a well-ranked year-old article
   satisfied it completely.

**OWED BY HAND, and it is env-only:** `GROQ_VISION_MODEL` is still
`meta-llama/llama-4-scout-17b-16e-instruct` at `cloud_gateway.py:105` — 404 since
2026-08-14 — and `render.yaml` has no key for it. Photos therefore fail on both
providers: Gemini vision is quota-dead (`/health` showed `vision.gemini_ok: 0`,
`last_error_was_quota: true`) and the Groq fallback does not exist. **Set
`GROQ_VISION_MODEL=qwen/qwen3.6-27b`** — checked 2026-08-18, it is the only
vision-capable model Groq still serves, it is production rather than preview, and
it takes the base64 data URI `see()` already builds. Put it in `render.yaml` too, or
a Blueprint re-sync will drop a dashboard-only value.

**A Render restart wipes in-process state, and it cost real data on 2026-08-18.**
Changing one env var redeployed the service and `/health` went from
`facts_known: 17` to `0`, `has_desk_key: true` to `false`, and
`fact_outbox.depth: 26` to `0` — the desk's public key and 26 sealed turns, gone,
because both live in process RAM. This is the concrete argument for the roadmap's
"gateway memory out of process RAM" item; it is no longer hypothetical.

## STATE — 2026-08-16, end of session 3

**Branch `feat/cloud-gateway`, eleven gate commits — merged with origin on
2026-08-22; the merge and those eleven are still unpushed.**
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
| `feat/cloud-gateway` | live, **merged with origin 2026-08-22 (the merge + 11 gate commits unpushed)** — the only one to work on |
| `fix/vision-markers` | same commit as `origin/feat/cloud-gateway` (`97841a0`). Nothing unique; delete when convenient |
| `fix/durable-state` | **3 commits NOT in cloud-gateway** (`6f96b4c`, `f720deb`, `c86d176` — briefing disarmed by a deploy, and a nudge deciding on a substring). Unmerged; decide on it |
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
