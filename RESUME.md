# RESUME — pick up here

> Written 2026-07-30, rewritten 2026-08-02, cleanup checklist cleared 2026-08-08.
> Read this first, then `JARVIS_MASTER_ROADMAP.md` (the single source of truth).
> Structure: **state summary → pending checklist → post-Electron backlog → reference detail.**
> Delete or rewrite this file once the checklist AND the backlog below are empty — it is a
> bookmark, not a plan.

## ▶▶ 2026-08-15 — START HERE. Four commits pushed, and the suite runs again.

**HEAD `d275127` on `feat/cloud-gateway`, pushed, `0 0`.** Suite **64/64 harnesses,
1562 checks, 0 failed** — and that number matters, because *the suite had been
unrunnable since the office-PC push and nobody knew*.

### The suite was not failing. It was hanging.

`test_app_link.py` blocked forever inside a TestClient `receive_json()`, so
`run_harnesses.py` waited on it indefinitely: no summary, no failure, no name. The
cause was one stale stub — `think()` gained `context=` (the fix that stopped the
model reciting his coordinates) and the harness's `_fake_think` still took four
arguments, so the call raised and the frame the fake phone waited for was never
sent. Behind the hang sat **six real failures**, all of this harness lagging the
gateway rather than gateway defects: four compared `_decode_app_message` against
3-tuples when it returns the 4-field `AppMessage` (`photo`, added for the camera),
and two asserted that a provider's raw error reached the chat bubble, which
`_excuse` deliberately stopped doing. Both now pin the stronger property — the
fault is reported **and** the provider's words do not leak.

**`run_harnesses.py` now has a per-harness timeout** (600s, `JARVIS_HARNESS_TIMEOUT_S`).
`subprocess.run` had none. This file already warned that a zero-failure report is a
trap; no output at all is worse, because a wedged run looks like a working one.
Proven by forcing it, not by trusting it.

### Gemini is the text brain now, on both sides

Kaustav's call: it handles code-switched romanised Bengali far better than
llama-3.1-8b, which is the register this thing is actually spoken to in. Desk's
cloud cascade is env-driven — `JARVIS_CLOUD_ORDER`, default `gemini,groq,openrouter`
— and Render has `LLM_PROVIDER_TEXT=gemini` (confirmed live: `/health` reports
`brains.text: gemini`). **The cost is real: Groq's latency is why it was primary, so
the spoken path pays for this.** One line in `.env` reverts it. `TOOL_PROVIDERS` was
deliberately left alone — tool-turn ordering is about tool-call reliability, and a
40/40 eval gates it.

### The store followed whoever launched the process

`memory.py` held `CHROMA_PATH = "jarvis_chroma_db"`, resolved against the CWD, while
every sibling anchors on `__file__`. Launching from the repo root split Tier 3 in
half — a new empty store beside the repo, `episodic_memory` still on the real one,
neither raising. Reproduced: stray store had `jarvis_memory` with **0** embeddings,
the real one `jarvis_episodes` + `jarvis_memory` with **119**. It also arrived
**unignored**, because every store rule in `.gitignore` was anchored to
`jarvis-backend/` — and that store is the plaintext vector mirror of facts that are
ciphertext in `jarvis_longterm.db`. Both halves fixed; `test_store_paths.py` (11)
pins them.

### §24 IS LIVE-GATED — the first real partner message ever sent

Two messages were delivered to Mousumi through the real path: governance CONFIRM →
`consume_pending` → `ActionEngine._message_partner` → `telegram_bot.send_text_to_partner`.
`Sent to Mousumi, Sir.` twice, different bodies, no duplicate refusal. **The
`f84f644` fix holds outside its harness.** Note `normalise_body` collapses newlines,
so a multi-paragraph message arrives as one paragraph — by design, worth knowing
before composing one.

⚠️ **This jumped the documented order.** `LIVE_GATE_CHECKLIST.md` makes `6.5` a hard
gate *before* §24, precisely because §24 sends to a real person. `6.5` and A23's two
refusal rows are still owed.

### BRIDGE_SECRET rotated, and the desk key handed over

Both ends rotated together and validated by a live handshake on the new value.
**`APP_TOKEN` is its own value on Render** — proven, not assumed: `/app-link` refused
the new bridge secret with HTTP 403, so the phone needed no re-pairing.

`has_desk_key` went false → true. **It is NOT durable** — it lives in the gateway's
process memory and the free tier spins down after ~15 min. `dropped_no_key` went
4 → 10 in one hour (the Mousumi turns), then reset to 0 on the restart. Those facts
are gone, not queued. The permanent fix is the desk **public** key in Render's env
(queue item 5), and the "no new config on Render" objection is thinner now that two
vars were added anyway.

### Tavily: desk CONFIRMED working, gateway still unverified

The mobile RESUME's suspicion is half closed. From this desk, `api_key`-in-body auth
returns HTTP 200 with live results; the Bearer style works too; DDG fallback works.
**But that was the desk's key from the desk's IP.** Render has its own key and a
shared outbound address — the same thing that got Open-Meteo `429`ing in production
while a laptop got 200. `/health` reports `search: tavily` from key *presence*, not
from a successful call. **To close it: ask JARVIS on Telegram "who won the last F1
race".** A correct current answer proves the gateway path; "couldn't reach live
results" is a real finding.

Reassuring while reading that code: `_LOOKUP_FAILED_NUDGE` already forbids inventing
a score/number/price when a lookup returns empty, and forbids telling him to search
himself. The honest-failure requirement is met.

### Branches — three, and two are traps

| Branch | State |
|---|---|
| `feat/cloud-gateway` | live, `d275127`, `0 0` |
| `feat/app-full-power` | **fully contained** in cloud-gateway (`fbea514` IS the merge-base, 0 unique, 19 behind). Merging it is a no-op; merging it into `main` would land a half-working gateway. Redundant — delete when convenient, restore with `git push origin fbea5141faf5d5dd52348afdc371e99c1ae1fd76:refs/heads/feat/app-full-power` |
| `main` | 155 behind, **+1 commit we lack** — `8d0ea4f`, the GPL LICENSE. Not a fast-forward. Leave until after §7 |

**13 dependabot branches open, and GitHub reports 62 vulnerabilities on `main`**
(1 critical, 30 high). Do NOT blind-merge: `cryptography` 48→50 is load-bearing for
C#11a and the **protobuf 6.33.6 pin can move transitively**. Each wants a suite run,
which is cheap now that the suite completes.

### Mobile toolchain on THIS desk (F:\work\JARVIS-Mobile)

Cloned beside the desk repo. Docs there cross-refer `../jarvis-brain`, which does not
resolve here — the desk repo is `F:\work\JARVIS-Project`.

Done: node v24.16.0 / npm 11.13.0 · 854 packages installed · **JDK 17.0.20.8** at
`C:\Program Files\Eclipse Adoptium\jdk-17.0.20.8-hotspot` · cmdline-tools rev 22 at
`%LOCALAPPDATA%\Android\Sdk` · `JAVA_HOME`/`ANDROID_HOME`/`ANDROID_SDK_ROOT`/PATH
persisted at user scope. Device `84f40716` (chenfeng, 24053PY09I) authorised, USB
reverse tunnel `tcp:8081` up, app `com.mypersonalintelligence.jarvis` installed
2026-08-14 18:48 **with CAMERA granted** — so the `expo-image-picker` rebuild did
happen and the app runs on Metro alone without any of this.

**BLOCKED on Kaustav:** the five SDK packages. Versions come from
`node_modules/react-native/gradle/libs.versions.toml`, not guesses —
`platforms;android-36`, `build-tools;36.0.0`, `ndk;27.1.12297006`, `cmake;3.22.1`,
`platform-tools`. Run
`scratchpad\sdk_setup_interactive.bat` (regenerate it if the scratchpad is gone).
**Two traps:** piping `y` into `sdkmanager.bat` does NOT reach the JVM's stdin from
either PowerShell or cmd — every prompt reads EOF, answers N, and sdkmanager reports
`7 of 7 SDK package licenses not accepted` while **exiting 0**. So licenses must be
accepted interactively, and any install must be verified by checking the directories
on disk. `android sdk list` (the replacement CLI) crashes with `0xC0000409`.
`sdk.dir` in `local.properties` is no longer needed — Gradle falls back to
`ANDROID_HOME` — but `prebuild --clean` still resets jvmargs to 2048m and R8 needs
**6144m**.

### ✅ F-16 IS FIXED (2026-08-15) — conversation reports too

The last keyboard-buildable item on the gate list. *"Now that I've adjusted the camera, I
can see you clearly, Sir."* It adjusted nothing, and `process_command` /
`process_stream` had no guard at all — F-09's wraps `generate_briefing` only.

**The axis is different from F-09's, and that is the whole fix.** A briefing never acts,
so any completion claim in it is false by construction. Conversation *sometimes* acts —
so the question is **"did anything actually happen?"** Tier 1 (speech, perception,
analysis) is always admissible; tier 2 (opened, closed, sent, played) needs an
`[Executed: ...]` stub within the last 6 working-memory messages — **a parse of what was
dispatched, never what the model said about itself**. Everything in neither tier goes,
which is where *adjusted*, *calibrated*, *tuned* and every verb nobody thought of land.

The guard runs only on a **prose** turn. When JARVIS really does change the volume the
reply is a JSON action and the confirmation comes from `action_engine`, untouched — so a
capability claim *in prose* is already the suspicious shape. `_MANDATE_RE` is
deliberately not ported: "as you asked" is routinely true mid-conversation.

**Two defects the harness caught first:** a dropped sentence next to a code fence took
the fence with it, and the streaming path left the raw reply in working memory — where a
fabrication becomes established context the next turn builds on.
`test_conversational_truthfulness.py` (111) drives the **real `process_stream`** against
a fake model. Suite **65/65, 1673 checks**. ⏳ **Not yet spoken to** — F-09's first fix
also passed its harness and then failed live.

### ⏭ Next, in order

1. **§7 gate session 2** — `21.3` FIRST (5 min, 34 rows depend on it), then the seven
   re-runs, then `4.4`, then `6.5`. See `LIVE_GATE_CHECKLIST.md`, which opens with the
   session-2 order. A4's four LLM-routing rows now exercise Gemini-first, so the gate
   tests the config actually intended for shipping. **Add an F-16 row while you are
   there:** an ordinary voice turn must not claim work it did not do — and it must still
   sound like JARVIS, because the guard was deliberately kept narrow.
2. **Durable desk key** (queue item 5) — small, and it demonstrably costs facts today.
3. **Dependabot session** — one at a time, suite each, watch the protobuf pin.
4. **Gateway search verification** — the one-question Telegram check above.
5. **F-15**, if it reproduces — a transient stored as a permanent Fact. It did NOT recur
   on comparable corrections the same session, so **confirm before building anything**.

**Still not built, and still worth it:** turns do not sync cloud→desk, only facts.
The right shape is a new frame type on the existing sealed bridge (same seal, same
governance, same `source` tag), not a second data path with its own Supabase
credential — the desk has no Postgres driver installed at all, deliberately.

**Still owed off-machine:** the 6th cleartext `.env` copy in
`JARVIS-BACKUPS\pre-encryption-20260808-004215\` wants shredding, and it will keep
regenerating until `backup_memory.py` stops listing `.env` (checklist item 1a — his
call, not done unasked).

## ▶ 2026-08-13 — the phone is connected, and the gateway grew four things

All deployed on `feat/cloud-gateway` (`9c37b4d`). The phone half is in
`kaustav991a/J.A.R.V.I.S-Mobile@feat/mobile-hud` (`666a4b1`); that repo's `RESUME.md`
carries the resume point for the app side, including what is still untested.

**Rotate `BRIDGE_SECRET` when this machine is next up.** The old value was printed
into Render's access log before the redaction below landed, and it still opens
`/desk-link` — a stand-in desk was connected with it repeatedly during testing. Both
ends change together: Render env and `jarvis-backend/.env` here. `APP_TOKEN` is
already rotated and separate, so the phone is unaffected by that change.

What landed:

1. **The desk arriving is announced.** `{"type":"desk","linked":true}` to every
   attached phone, so a cloud session that silently gained PC control now says so.
   With it, `POST /app-push/register` and push through Expo's relay, because Android
   suspends a backgrounded app and the socket dies with it. Push goes out **only when
   no phone is attached** — a listening one raises its own notification, and one event
   must not arrive twice.
2. **Desk-watch alerts reach a closed app.** `intruder` / `intruder_resolved` are
   relayed to phones and pushed when none is attached, on the MAX-importance channel
   and skipping the quiet gap: rate-limiting a 30-second lock warning is the wrong
   trade. Verified end to end — a closed phone was woken and the tap opened the alert
   screen.
3. **The pairing token was being written to the access log.** uvicorn logs the whole
   request line and the token travels as a query parameter, because React Native
   cannot set headers on a WebSocket handshake. `_RedactQuerySecrets` filters it;
   confirmed live as `?token=<redacted>`.
4. **Located questions are answered from measurements.** He asked whether it was
   raining and was told it was not, while it was raining — the model answering out of
   its own weights. The phone now sends coordinates, place name, current conditions
   and the places he has named ("the office" resolves against a label rather than a
   geocoder), and the context tells the model not to contradict the figures.

Two things learned the hard way, both worth keeping:

**Open-Meteo rate-limits per IP, and Render's outbound address is shared.** The
gateway's own weather lookup was answered `429 Too Many Requests` in production while
the identical URL returned 200 from a laptop. The lookup moved to the phone; the
gateway's copy survives as a fallback with a 15-minute backoff, and serves a stale
reading in preference to nothing.

**A pre-push check that stubs the network does not test the network.** The one line
that failed in production was the one line the check had mocked out.

Still absent: live traffic. OSRM's public router knows the road graph, not the road,
so durations are free-flowing and the context says so rather than implying an ETA.
`_route_blocking` / `_route_to_blocking` are the only functions that change when
there is a Mapbox or TomTom key.

## ▶ 2026-08-12 — the mobile app now has a front door. Read `MOBILE_CONNECT.md`.

Built out of sequence, ahead of the gate, because the phone is being connected at the
office tomorrow. `WS /app-link` on the cloud gateway: the app dials the Render brain
with a pairing token, and when the desk is linked the command runs on the **real
machine** through the existing `/desk-link` bridge instead of on the cloud brain.
Voice clips are accepted and transcribed. Telemetry is polled from the desk while it
is up and is simply absent when it is not. **Telegram is unaffected** — the only shared
line of code is the desk-link reader, which hands a frame to a phone only when a phone
registered its `req_id`.

Owed: `APP_TOKEN` in the Render dashboard, an EAS rebuild of the APK (the pairing
screen is new), and a live gate on a real phone — nothing below has touched a device.
The phone's own recorder is also still owed; the server side of voice is finished.

- Harness: `jarvis-backend/test_app_link.py`, 29 checks. App: 335 tests, typecheck clean.
- Mobile repo: `kaustav991a/J.A.R.V.I.S-Mobile`, branch `feat/mobile-hud`.

## ▶▶ RESUME POINT — 2026-08-09, 02:00. READ THIS, THEN `LIVE_GATE_CHECKLIST.md`.

**The §7 live gate has STARTED. It runs in sessions, not one desk day.** Everything below this
block predates it and is still true about the *feature* queue — but the gate is now the work.

| | |
|---|---|
| HEAD | **`c3945a4`**, pushed, `0 0`, tree clean |
| Suite | **62/62 harnesses, 1509 checks, 0 failed** (was 59/1405 when the gate began) |
| Rows | **15 of 192 run** — 13 pass, 1 fail (`10.9`), 1 blocked (`21.3`) |
| Findings | **16 raised, 13 fixed.** Open: **F-15**, **F-16** |

### What happened, in order

1. **Session 1 (2026-08-08, 22:00–23:00)** — ran 15 rows, stopped deliberately. 13 findings,
   3 high-severity, all of one shape: *a failure the user cannot tell apart from working*.
2. **Fix pass (2026-08-09, 00:00–01:30)** — all 13 fixed, 8 code + 5 doc, each with a harness.
3. **Session 1b — first live run on the fixed tree (01:26–01:55).** This is the part that matters:

| Fix | Live verdict |
|---|---|
| **F-08** camera | ✅ **held.** Its one death was real (phone left the network): tolerated → 3 reopens → honest message → self-recovered. No spurious death in ~25 min. ⚠️ A decoder desync (`overread`) never occurred, so the original trigger is still unreproduced |
| **F-10** briefing period | ✅ `Comprehensive **Late Night** Briefing` at 01:28 |
| **F-11** voice loop | ✅ every `[VAD]`/`[STT]` single across many turns. ⚠️ only one WS connection — a HUD reload is still the real test |
| **F-13** romanise | ⏸ never exercised — no Bengali input |
| **F-09** briefing truth | 🔴 **FAILED, then re-fixed** (`af26d88`) — see below |

### The lesson worth carrying forward

**F-09's first guard was not too short — it was on the wrong axis.** It blocklisted mutation
verbs; the model said *"I have **closed** the current window… **muted** the room"* and
*"as per your **previous** instructions"*, none of which were on any list. **A blocklist over the
set of verbs a model might use can never be complete**, and every miss looks like a small gap.

Now inverted: `generate_briefing` **reports**, so what it may legitimately claim is a small
**closed** set (compiled, noted, prepared, reviewed, checked, found, monitored). Everything else
reading as a first-person completion is false by construction and is stripped. Proof it
generalises: *throttled*, *defenestrated*, *reticulated* are caught for free.

### ⏭ TOMORROW — do these in this order

1. **`21.3` FIRST** — five minutes, and **34 rows depend on it.** Run the daemon 5+ min with a
   face scan partway. Any `session fault: camera stream died` **that is not preceded by real TCP
   failures** means F-08 is not done. Stop there if so.
2. **Re-run 7** — `0.1` `0.2` `1.3` (F-03 boot), `2.1` `2.2` `2.4` (F-11 voice — **reload the HUD
   mid-session**, that is the true trigger), `16.1` (F-07 camera ladder).
3. **`4.4`**, then **`6.5`**. ⚠️ `6.5` is a **hard gate**: if a BLOCK-tier action executes, §24
   does not run at all.
4. **Live-confirm the two never exercised:** one Bengali sentence at the mic (**F-13** — silence
   means still broken), and a full wake briefing (**F-09** — no false claims **and** it must
   still sound like JARVIS; the guard was kept narrow on purpose).
5. **Then the ~99 remaining unblocked solo rows.** A full session, needs nobody else.

### ⏳ STILL OPEN — two findings, deliberately not fixed tonight

- **F-16** — the same confabulation on the **conversational** path: *"Now that I've adjusted the
  camera…"* It adjusted nothing. F-09's guard wraps `generate_briefing` only. The allowlist
  approach ports, **but the allowlist must be wider there** — conversation legitimately claims
  more than a report does, and reusing the briefing set would flatten ordinary speech.
- **F-15** — a transient stored as a permanent Fact (`User is not holding an umbrella`). That is
  row `9.6` failing. **It did NOT recur** on comparable corrections later the same session, so
  **confirm it reproduces before building anything against it.** It is upstream of F-09:
  `recall_all_facts()` feeds the briefing, so junk facts are the fuel.

### Not code — arrange these, they block 22 rows

- **Second device** with a pinned non-random MAC on the home SSID (7 rows). The phone cannot be
  both camera and presence probe. Phone-settings chore, do it **before** the desk day.
- **Second person** (15 rows): Kinshuk ×1, Mousumi ×11. For the Mousumi block, **her knowing and
  consenting is the real gate, not a technical one.**

> Full detail: `LIVE_GATE_FINDINGS.md` (every finding, fix, harness, and the re-run list) and
> `LIVE_GATE_CHECKLIST.md` (all 192 rows by prerequisite; **it opens with the session-2 order**).

---

## NEXT SESSION STARTS HERE — pick a number

> **2026-08-08 — the queue is NOT drained after all.** Kaustav's instruction put
> **§6.8 agent tool-layer hardening BEFORE the §7 gates**. Phase 1 is done and **Phase 2's
> catalogue is COMPLETE — all six waves, registry 11 → 56** of the 72 actions this layer can
> deliver, with 16 excluded for stated reasons (roadmap §6.8.2 lists all of them).
> **Skills (rule 18) landed too — all 18 reference rules are now satisfied.** Six playbooks
> in `jarvis-backend/skills/`, one line each in the prompt, bodies loaded on demand
> (measured: 824 chars standing in for 12 462 — 15×, and Groq has no prompt caching).
> **✅ §6.8 IS COMPLETE — all four phases.** MCP landed (dependency-free stdio client, gated
> by a new `mcp_call` **CONFIRM** rule; off unless configured) and so did measurement (metrics
> that record counts but never argument values or goal text, plus a **40-task eval set that is
> 40/40 and now a suite gate**). Item 5 — the hardware gate — **is once again the only thing
> between here and Electron.** Read roadmap §6.8 before touching any of it.
>
> **The eval set paid for itself on its first run (35/40).** *"any emails from my accountant"*
> matched **nothing** — matching is `term in haystack` and "emails" is not inside "gmail_read",
> so one letter made the whole mail catalogue unreachable from a plural. It then caught a
> ranking bug the aliases had introduced: a synonym scored at name weight, so *"turn the tv
> volume up"* went to the **power toggle**. Weights are now name > alias > description.
>
> **Six real defects surfaced while filling the catalogue, all fixed:** the **shelf had never
> been wired in production** (so every catalogue tool was registered and unreachable);
> `ToolShelf.promote` **evicted the tool it had just found** while reporting it as loaded;
> `_play_music` stripped `"on"` as a substring (*"play Moonlight"* searched *"Molight"* — the
> ordinary voice path too); `run_harnesses.py` **was not running one of its own harnesses**
> (hand-kept list, now discovery); `tavily_search` handed the model its `TAVILY_UNCONFIGURED`
> sentinel as if it were data; and wave 6's `message_partner` was **caught by the 2026-07-26
> guard** that says the loop must not be able to message a person on its own — the guard won
> and was strengthened. Live-gate rows: **TEST_PLAN §23b, 16 of them.**

**Where things stand:** HEAD on `feat/cloud-gateway`, **ahead of origin**,
**not merged to `main`**. Suite **1210 checks / 51 harnesses green**. Done:
**both halves of partner-messaging** (`message_partner` outbound + the `partner_contact_status`
butler inbound, with **two-layer urgency detection** on Kaustav's real Benglish term list),
**Chroma at-rest encryption**, the **cloud→desk sealed-fact arc**, and **memory provenance**.

**The keyboard-buildable feature queue is drained AND the cleanup checklist is empty.** The
only thing left before Electron is a hardware desk session — item 5. Nothing else can be
cleared from a chair.

### CLEANUP — ✅ ALL FOUR CLEARED (2026-08-08). Kept for the reasoning, not as work.

> Items 1, 2 and 4 were closed on 2026-08-08; item 3 closed 2026-08-02 (`eff7540`). Original
> numbering preserved so older notes still point at the right thing. **The one thread that
> stays open is not a task: the urgency term list under item 3 is Kaustav's to keep refining.**

**1. ✅ SOURCE-TAG MIGRATION RUN 2026-08-08 — provenance is live, and the arc is committed
(`326cbd2`).** `migrate_memory_source.py --apply` backfilled **58/58 rows to `desk`**;
`source_counts()` now reads `{'cloud': 0, 'desk': 58, 'untagged': 0}`. Verified before the
swap: ids, `content`, `content_hash`, `category`, `user` and `timestamp` byte-identical, and
all 58 still decrypt to the same plaintext; `PRAGMA integrity_check: ok`. The original was
**moved aside, not deleted** — `JARVIS-BACKUPS\pre-source-originals\jarvis_longterm.db.pre-source-20260808-004216`
— and a full pre-migration snapshot sits in `JARVIS-BACKUPS\pre-encryption-20260808-004215`.
The feature is no longer inert: a fact the Render gateway captured with the PC off is now
distinguishable from one he said in person, which it was not before. Re-running `--apply` is a
no-op.

> ⚠️ **THE `.env`-IN-BACKUPS LEAK IS RECURRING, NOT A ONE-OFF — AND IT HAS A CHEAP FIX.**
> The mandatory backup wrote a NEW cleartext `.env` at
> `JARVIS-BACKUPS\pre-encryption-20260808-004215\.env` (9,731 bytes), the same shape as the five
> shredded 2026-08-01. `backup_memory.py` lists `.env` among its targets, so **every backup makes
> another one** — and every future migration takes a mandatory backup first. Shredding them is
> a chore that regenerates itself, which is the wrong shape for a secret.
>
> **Two fixes, and they are not alternatives — do (a) now-ish, (b) when it comes due:**
>
> - **(a) QUICK — drop `.env` from `backup_memory.py`'s target list.** Stops the recurring leak
>   cheaply and today. The cost is real but small: a restore from backup no longer carries the
>   keys, so `.env` has to be re-created by hand. That is acceptable precisely because it is the
>   one file that must not be lying around in five copies. **Not done unasked** — it changes what
>   a restore gives you back, and that is Kaustav's call.
> - **(b) REAL — Step 3, secrets into the key store** (see the deferred item in the queue below).
>   Removes cleartext `.env` entirely, so there is nothing for a backup to copy and the problem
>   stops existing rather than being avoided. Still correctly sequenced after the §7 gate and the
>   merge to `main`.
>
> **(a) does not make (b) unnecessary** — it stops the *copies*, while the live `.env` stays
> plaintext on disk either way. Only (b) closes it.
>
> **The new copy still needs shredding — Kaustav's task**, exactly like the prior five. It was
> deliberately not touched; see the off-machine list at the bottom.

**2. ✅ `JARVIS_LOG_CONTACT_EVENTS` NOW DEFAULTS OFF (2026-08-08, Kaustav's ruling).**
`modules/contact_events.py` was default-ON, which broke this project's default-OFF discipline
for anything recording third-party behaviour. It is opt-in now, exactly like
`JARVIS_LOG_PARTNER_CHATS`: **unset, empty and unrecognised all read as OFF**, so a typo in
`.env` fails towards not recording rather than towards recording. His machine is unaffected —
`JARVIS_LOG_CONTACT_EVENTS=1` was added to `jarvis-backend\.env` alongside the chat flag, so
the butler keeps working while a **fresh clone now records nothing about anyone** until its
owner says otherwise. Pinned by `test_contact_recording_defaults_off_and_fails_towards_off`,
which drives `record()` as well as `enabled()` — a default only the predicate honours is not a
default. `test_partner_contact.py` 41 → **42**.

**3. ✅ IMPLEMENTED 2026-08-02 (`eff7540`) — Benglish urgency terms + two-layer detection.**
Kaustav's real list replaced the guess, and the detection grew a second layer.

- **The list lives in one editable place:** `partner_contact.URGENT_TERM_GROUPS`, a dict of
  labelled groups (direct · speed · call or come · distress · need) holding his terms verbatim.
  **Both layers derive from it** — the keyword regex is compiled from it, the classifier's
  prompt is built from it with the group labels included. Edit the dict and both follow;
  a harness moves the dict and proves the prompt moves with it. `URGENT_TERMS` survives as the
  flattened de-duplicated view because that is the name callers already import.
- **Two layers, `urgent = keyword OR semantic`.** Layer 1 (exact, whole-word) runs first and
  short-circuits — a hit is final, so no model call and no tokens. Layer 2 is one small LLM turn
  judging by MEANING, which exists because romanised Bengali has no settled spelling and
  inflects freely (the exact list matches `bipod`, misses `bipode porechi`; matches `joldi`,
  misses `joldii`). The semantic layer can only RAISE the flag, never lower one, and an
  unreachable or babbling model yields *no verdict* rather than False — so layer 2 failing
  degrades the butler to exactly its `ba12cc1` behaviour. `JARVIS_URGENCY_SEMANTIC` (default ON)
  switches it off.
- **Live-checked, not only harnessed** (the `f84f644` lesson — a fake model proves wiring, not
  usefulness): against the real provider chain, all six keyword-missing cases were caught by
  meaning and four routine messages were not flagged.
- Unchanged and re-pinned: only the boolean crosses into the store, `contact_events.record()`
  still has no parameter content could arrive through, still admin-only via `tier_allows`,
  still encrypted at rest. `test_partner_contact.py` 25 → **41 checks**.

> ⚠️ **THE TERM LIST IS KAUSTAV'S, AND STAYS OPEN — this is not a closed item.** Nobody else
> should rewrite it. It is meant to be refined over time as he notices how Mousumi actually
> writes, and refining it is *worth doing*, because the two layers are not equals: **the keyword
> layer is reliable and the semantic layer is best-effort.** Layer 2 needs a model, a network
> and a provider that has not drained its quota; layer 1 needs none of those and cannot drift
> between model versions. So every term he adds is a phrase moved out of "probably caught" into
> "always caught". Add terms as he sees them — that is maintenance, not rework.
>
> **Known, and his call:** `dekho` and `asho` are high-frequency in casual Bengali, so they will
> flag on ordinary chat (`ei chobi ta dekho koto sundor` flags, and by design the model cannot
> veto a keyword hit). If that noise makes the bit meaningless, the fix is a **hints-only
> group** — terms sent to the model but not exact-matched — deliberately not built unasked,
> because it weakens the layer that survives an outage.
>
> The **English escalation terms were kept** in two clearly-marked separate groups, though his
> list contains none: dropping them would make a plain "please call me, I need you" read as
> routine, the one direction §6.7 forbids. One edit from gone if he wants his list alone.

**4. ✅ CONTENT-OVERRIDE MODEL CONFIRMED 2026-08-08 — `summarize_partner_chat` STAYS.**
Kaustav ruled: keep it. So the shipped behaviour is now a decision rather than an accident —
*discreet by default via `partner_contact_status`, full content only when you explicitly ask
for it*, and the two are not interchangeable (the routing prompt in `brain.py` says so). It
remains gated by `JARVIS_LOG_PARTNER_CHATS`, so it can only ever answer from transcripts he
already opted into keeping. The rejected alternative was removing the action outright, which
would have made "what did she say" unanswerable by construction. **No code changed** — the
ruling closes an open question, it does not move anything.

### THE ONLY THING LEFT ON THIS BRANCH

> Also owed and trivial: **`git push`**. Three commits are local only — `326cbd2`, `ff83598`
> and this file's update.

**5. §7 LIVE-GATE DESK SESSION — the hardware gate to Electron.** Not a prompt-and-build; a
desk day. **No code is blocking it.** It needs your hands (gestures), a second device (Track B
presence), and a second person (stranger debounce), plus the C#11a lock check, the phone
smoke-tests, and TEST_PLAN §0–§22. It carries every owed gate at once: G4 + G5 + §6.1,
§17.6–17.8 (backdoor governance), §23 (agentic core), §24 (partner messaging).
Detail in `### Next in the queue, in order` below.

**The road from that session to a shipped `.exe`, in order:**

| # | Step | Notes |
|---|---|---|
| 5 | **§7 live-gate desk session** | Hardware day. Your hands, a second device, a second person. Gates everything below. |
| 6 | **Thorough pre-Electron code-review pass** | A deliberate sweep of the whole tree *before* it gets packaged and handed a version number. Cheapest moment to fix anything found; the most expensive moment is after an `.exe` is in use. |
| 7 | **Restore Electron config + package** | Electron launch scripts (still TODO — needs you present), then hash-router/config restored, then packaging. |
| 8 | **Merge to `main`** | ⚠️ Not a fast-forward — see the `8d0ea4f` note below. |
| 9 | **Ship the `.exe`** | The end of this arc. |

> **Not on the menu yet:** Step 3 (`.env` secrets into the key store) is deliberately
> sequenced *after* item 5 **and** after the merge to `main`. Deferred, not dropped — see the
> queue detail below.

## POST-ELECTRON UPGRADE BACKLOG (build one at a time, after shipping the `.exe`)

**Nothing here starts before the `.exe` ships.** Ordered by value, highest first. This list is
the answer to "what next" once the desktop arc is closed — it is not a queue to start nibbling
at early.

> **THE DISCIPLINE, WHICH IS THE POINT:** build these **ONE AT A TIME, FULLY, WITH PROVEN
> PROPERTIES** — the same standard as everything already shipped here. A property is *proven*
> when a harness drives the real code and asserts on observable behaviour, not on source text
> (the `f84f644` lesson), and when a live gate has confirmed it works outside the harness. Half
> of two features is worth less than one finished feature, and an unproven feature is worth
> less than no feature, because it is trusted and wrong.

| # | Upgrade | Why it is where it is |
|---|---|---|
| 1 | **Mobile app (Flutter)** — JARVIS on the phone: push notifications, tap-to-confirm for CONFIRM-tier actions, presence, voice. | The highest-value thing left. It is the **clean answer to phone-reach** — the capability WhatsApp integration was wanted for, with **no ToS risk and no ban risk**. Built with Claude Code, then maintained by JARVIS itself. |
| 2 | **Tiered brain** — free Groq cascade stays the default, frontier model on demand for genuinely hard reasoning turns. | Raises the ceiling without raising the floor cost. **Unlocks the code-companion** and materially better md→HTML / Figma output. Needs a routing rule for what counts as "hard", not just a second key. |
| 3 | **GPU vision acceleration** — move YOLO / face-recognition off the CPU onto the RX 7600 via DirectML or Vulkan. | Lifts every perception feature at once on **hardware already owned**. ⚠️ **Measure first** — baseline the current CPU frame budget before touching a backend, or there is no way to tell whether it helped. |
| 4 | **Smart-home (#8)** — local-control devices + Home Assistant + a `home_agent`. | Cheap to build against a working Home Assistant, and the **emotional payoff per line of code is the highest on this list**. Gated on having the gear. |
| 5 | **MCP client for the agentic core** — consume external tool servers (Figma, GitHub, …), governance-gated like every other action. | Big capability-per-effort ratio: each server added is a new skill for free. Every tool call must pass `governance_manager` — an external tool server is not a trusted caller. |
| 6 | **Guarded self-improvement (#10)** — propose → branch → test → PR. **Never auto-merge.** | The most interesting item and the one most able to do damage, hence below the safer wins. The guard rails *are* the feature: a human reviews every PR, and the harness suite is the gate it cannot talk its way past. |
| 7 | **Security cameras + Frigate** — dedicated always-on vision, separate from the desk camera. | Real value, but it is a hardware purchase and a second always-on service. Waits for the gear. |
| 8 | **Presence state machine (#9)** — real working / away / asleep states rather than inferred-per-call. | Last because Track B presence already covers the case that mattered; this is refinement, and it is most useful *after* the mobile app is feeding it real signals. |

**AVOID — settled, not open questions:**

- **WhatsApp integration** — unofficial libraries risk the **account being banned**, and the
  account is the thing being protected. Item 1 above is the sanctioned replacement.
- **WhatsApp calls** — there is no API for it. Not a hard problem; an impossible one.
- **Removing or weakening confirm gates** — the CONFIRM tier is why an approved partner send
  cannot fire twice and why a drained cloud fact cannot write unattended. Convenience is never
  a reason to remove one; if a gate is annoying, the fix is a faster way to *answer* it (see
  item 1's tap-to-confirm).

## BOTH SIGN-OFF DECISIONS ARE CLOSED

The encryption arc (C#11a) closed 2026-08-01. The **cloud→desk sealed-fact
backlog closed 2026-08-02** — all three phases built, committed and pushed. What
remains on this branch needs Kaustav's hands, not more code.

## THE ENCRYPTION ARC (C#11a) IS FULLY CLOSED — BOTH STORES

Nothing about memory-at-rest encryption is outstanding. Do not reopen it looking for work.
**Both halves now encrypt content at rest: the memory store AND the vector store.**

- `jarvis_longterm.db` is encrypted; `jarvis_memory.db` is retired into it. **58/58 rows
  decrypt** through DPAPI → DEK → AES-256-GCM (re-verified 2026-08-01, counts only).
- **`personal_chroma_db` is encrypted (2026-08-02, `c173c2e`).** Document text and the
  sensitive metadata (`path`, `name`) are sealed with the *same* C#11a field encryption before
  they reach Chroma — same DEK, same DPAPI wrap, same recovery code, no new dependency, pins
  untouched. Chroma had been keeping the text in plaintext twice (`embedding_metadata` plus
  the FTS5 shadow tables) with the metadata beside it. Gated on `keys_ready()`, and a locked
  keystore **raises** rather than returning `[]`, because an empty result set is
  indistinguishable from "no relevant documents". 15 checks in `test_chroma_crypto.py`, which
  asserts on the bytes in `chroma.sqlite3` rather than on the API.
  - **Residual, accepted:** the **vectors stay plaintext**. They are computed from the
    plaintext (that is what makes semantic search work) and the encoder `all-MiniLM-L6-v2` is
    public, so they leak approximate content by inversion. Encrypting them would destroy the
    search the store exists for. Pinned by `test_the_vectors_are_deliberately_not_encrypted`.
  - **Applies to documents ingested from now on.** There was no migration because the store
    held no real documents — only **3 rows of stale test-fixture residue** (dated 2026-07-26,
    pointing at a since-deleted `%TEMP%\tmpjbkgwzac\decisions.md`). Those rows are still
    plaintext and are *still returned by search*, injecting a fake "PostgreSQL over MongoDB /
    Hetzner not AWS" decision into results. Deleting `jarvis-backend/personal_chroma_db/`
    clears them; it is gitignored and rebuilt on next ingest. **Kaustav has not yet ruled on
    this — do not delete it unasked.**
  - **Still plaintext, out of scope, real data:** `jarvis_chroma_db` (119 rows —
    `jarvis_memory` + `jarvis_episodes`, written by `memory.py:366` and
    `episodic_memory.py:110`) and `memory/vector_db` (1 row). These are the *vector mirror* of
    facts C#11a already sealed in SQLite — the same sentence is ciphertext in
    `jarvis_longterm.db` and readable in `jarvis_chroma_db/chroma.sqlite3`. `chroma_crypto` is
    collection-parametrised so the pattern extends directly, but unlike the RAG store these
    have real rows and would need a migration. `action_chroma_db` (42 rows) is a static
    command catalogue, not secret.
- **Recovery code: re-issued, saved off-disk, and PROVEN.** A fresh code was issued
  2026-08-01 (`manage_keys.py export-key`), which **voided every earlier code** — including
  the misplaced one and any that had touched a terminal transcript. Kaustav holds the new one
  in his password manager, off this disk. He ran the round-trip himself: **recovery wrap
  opens · MATCHES the DPAPI key · canary decrypts — all True.** So both wraps are proven to
  open the *same* DEK, which is the property that matters.
- Unattended boot is unaffected: `status` reports `boot wrap: unwraps OK (no prompt needed)`.
- The three encryption CLIs are cp1252-hardened, guarded by two tests in `test_governance.py`.

## Current state

| | |
|---|---|
| Branch | `feat/cloud-gateway`, **AHEAD of origin and not pushed** (the provenance arc, the contact-events flip, and the whole §6.8 tool-layer arc), and **not merged to `main`** |
| Suite | **1405 checks / 60 harnesses green, 0 failed, 0 broken** — `venv\Scripts\python.exe run_harnesses.py` (venv python; system python fakes failures). Harnesses are **discovered** now, not listed — a new `test_*.py` is in the suite the moment it exists |
| Working tree | **clean of feature work.** The `source`-column arc that used to live here is committed (`326cbd2`); the only untracked file left is the pre-existing `jarvis-frontend/public/favicon.zip`, which is nobody's from this arc. |
| Live store | `jarvis_longterm.db` — 58 rows, **all tagged `source=desk`**, all decrypting. The provenance column is populated, not merely present. |

Note for anyone running the suite from a **bare checkout** (fresh clone, or a `git worktree`):
`test_memory_store_encryption.py`, `test_store_retirement.py` and `test_gmail_agent.py` fail
there — 12 checks — because the keystore and the local `.db` files are gitignored and so are
absent, which silently means *encryption is off* (`a locked key returned [...] instead of
raising`). Pre-existing and expected, not a regression. `test_chroma_crypto.py` self-skips
its encryption cases in that situation instead of failing.

⚠️ **The merge to `main` is not a fast-forward.** `main` carries one commit this branch does
not: **`8d0ea4f`** — *"Add GNU General Public License v3"*, 2026-07-27, one file, `LICENSE`,
+674 lines. A clean add with no overlap against this branch's 77 commits, so expect no
conflict — but it has to be reconciled rather than ignored.

The commits that got here:

| Hash | What |
|---|---|
| `312bf5c` | encryption subsystem (DPAPI + scrypt recovery, AES-256-GCM fields, blind-index dedup) |
| `e93cc34` | D#13 harness conversions, `tests/` retired, the cp1252 crash fixed |
| `c2d1a8c` + `dc84a88` | docs — C#11a folded into the roadmap, stale rows fixed, resume state |
| `9c8c5eb` | cp1252: the three encryption CLIs hardened + two guards in `test_governance.py` |
| `5093c37` | docs — six stale roadmap/TEST_PLAN rows reconciled against the tree at 876/39 |
| `1b37558` | **C#11a Step 4 phases 1+2** — sealed-fact seal/unseal core + queue/bridge transport (NOT wired to live memory) |
| `fee66a0` | **C#11a Step 4 phase 3** — the governed sink; the cloud→desk arc is CLOSED |
| `f84f644` | **`message_partner` actually works** — the approved send was refused by its own confirm prompt on 100% of attempts; + `test_partner_send_gate.py`, the first partner harness that asserts on transport call count instead of source text |
| `c173c2e` | **the Chroma RAG store is encrypted at rest** — text + sensitive metadata sealed with the existing C#11a field encryption, vector left plaintext for search; blind-index companions so `where` filters and the re-ingest delete still work against randomised ciphertext; + `test_chroma_crypto.py` (15) |
| `ba12cc1` | **the butler — `partner_contact_status`, the INBOUND half of partner-messaging** — "did she message" answered from a content-free encrypted contact-event store; admin-only via `tier_allows`; urgency is a write-time boolean; + `test_partner_contact.py` (25) |
| `eff7540` | **the butler reads urgency in the Benglish she actually writes** — Kaustav's real term list in one editable dict (`URGENT_TERM_GROUPS`) that BOTH layers derive from, plus a second semantic layer that judges by meaning so inflections and re-spellings are caught; OR-combined so the model can only raise a flag, never lower one; live-checked against the real provider chain; `test_partner_contact.py` 25 → 41 |
| `326cbd2` | **every memory now says how it arrived** — additive `source` column (`desk` \| `cloud`) so a drained cloud fact stops being byte-identical to one he said in person; plaintext by design because a sealed column cannot satisfy `WHERE source = ?`; NOT part of the dedup key, so the first writer's provenance stands; `migrate_memory_source.py` **RUN** — 58/58 backfilled and verified; + `test_memory_source.py` (26) |
| `ff83598` | **contact-event recording is opt-in, not opt-out** — `JARVIS_LOG_CONTACT_EVENTS` flipped to default OFF, and unset/empty/unrecognised all read as OFF so a typo in `.env` fails towards not recording; a fresh clone records nothing about anyone until its owner says so; `test_partner_contact.py` 41 → 42 |

## THE CLOUD→DESK SEALED-FACT BACKLOG IS COMPLETE

All three phases built, committed and pushed. Nothing here is outstanding; do not reopen it
looking for work. 92 checks across `test_fact_seal.py` (29), `test_fact_transport.py` (30) and
`test_fact_governance.py` (33).

Both design rulings held: transport is the **existing BRIDGE_SECRET WebSocket bridge** (no
GitHub queue, no new service, no new secret on Render), and the desk private unseal key is
**DPAPI-wrapped** through the C#11a chain (DPAPI → DEK → X25519 private, straight from the
Step 1 ceremony).

- PyNaCl `crypto_box_seal`, ephemeral keypair per record, so **Render cannot re-open its own
  queue** — asserted, not claimed.
- Cloud seals + queues each PC-off turn **before** replying; outbox flushes on the desk's
  `fact_key` handshake; records leave the outbox **on the ack, not the send**.
- Two dedup layers: record-UUID ledger (`jarvis_fact_ledger.db`) stops a replay before it is
  unsealed; the C#11a `content_hash` blind index stops the same fact arriving as a NEW record.
- `modules/fact_sink.py` is the **only** way a drained fact reaches memory, and it runs
  `governance_manager.check("remember_fact")` before the write, then hands off to
  `memory_manager.extract_and_persist` — the same call `brain.extract_and_store_memory`
  delegates to, minus that wrapper's catch-all (it would have silently eaten a backlog).
- Poison AND refused records dead-letter to `fact_quarantine/` **and are acked**, so neither
  can wedge the queue. A locked key store or a broken governance engine HOLDS instead: acks
  nothing, quarantines nothing, retries on the next connect.

### Four things about it worth not re-deriving

- **`tier_allows` is deliberately NOT on the drain path**, and was left untouched. It answers
  "may this CALLER INVOKE this action"; a memory extraction is not caller-invoked live or
  drained (`main.py` fires it for every recognised identity, partners included, outside the
  action pipeline). Applying it would have stored *less* than the live path, silently, for
  exactly one person. The gate that does apply is fail-closed identity: `who` must be in the
  roster derived from `partner_registry.SLOTS`, `tier` must be one this desk issues. A harness
  test pins the VIP allowlist so this cannot drift.
- **An unattended CONFIRM is refused AND its pending slot cancelled.** `check()` parks a
  CONFIRM in a single slot before returning; leaving it there would let the next spoken "yes",
  meant for something else, approve a write he never saw.
- **Refusal reasons carry no payload values** — they are written verbatim into the unencrypted
  dead-letter file, so lengths, types and field names only. The sealed record sits beside them
  for whoever holds the key. Same rule for the ledger: a refused record's claimed `who` is the
  field that just failed to check out, so it is not persisted.
- **Drained rows carry NO provenance.** A drained fact is stored as `(Fact, ciphertext, WHO)` —
  byte-for-byte indistinguishable from a live desk write. The `"cloud_fact_drain"` string
  exists only in the payload handed to the governance check and is **not** persisted. If that
  property is ever wanted, it needs an additive `source` column on `memories` threaded through
  `add_memory` — a separate, reviewable change, not a tweak.

### Next in the queue, in order

> Detail behind the checklist at the top of this file. The numbering here is the older
> queue order and does **not** match the checklist — item 1 below is checklist item 5.
> Items 3 and 4 below are now history (both shipped); they are kept for the reasoning.

1. **THE LIVE-GATE DESK SESSION (roadmap §7) — this is what is next, and it is his.**
   No code is blocking it. It needs his hands, a phone, and a second person for the
   stranger-debounce row. It carries every owed gate: G4 + G5 + §6.1, §17.6–17.8 (backdoor
   governance), §23 (agentic core), §24 (partner messaging). **It gates the whole road to the
   `.exe`** — pre-Electron code review, launch scripts, packaging, the merge to `main` — and,
   through that, everything in the post-Electron backlog. Full sequence in the table at the top.

2. **Step 3 — move `.env` secrets into the key store. ⏸ DEFERRED 2026-08-01 (Kaustav),
   triggered by item 1.** **Not dropped.** Resume it *after* the §7 session **and** after the
   merge to `main`, because it rewrites every boot-time key read (cloud gateway, LLM
   cascade, Telegram legs) and doing that underneath an un-gated tree adds variables to the one
   session that needs his hands. **Whoever reads this after the merge lands: this is due.**
   Context so it need not be re-derived: `.env` currently holds every API key in plaintext
   (37 keys — `GROQ_API_KEYS`, `TELEGRAM_BOT_TOKEN`, `BRIDGE_SECRET`, `TAVILY_API_KEY`, …),
   and the key store that would protect them already exists and is proven (DEK + DPAPI wrap +
   verified recovery code). It was **deliberately sequenced last** in the C#11a plan because
   it is separable. Exposure while deferred is local-disk only: `.env` is gitignored and has
   never been tracked, and the five cleartext backup copies were shredded 2026-08-01.

3. **Partner-outbound (`message_partner`) — DONE 2026-08-02, and it had never worked.**
   *(With item 4 below now shipped, **both halves of partner-messaging are complete**.)*
   The action shipped 2026-07-26 in `3185cd8` and was recorded here as "already done". It was
   not: **every approved send was refused as a duplicate of its own confirmation prompt**, on
   100% of attempts. Building the CONFIRM read-back calls `guard.note_staged(slot, body)` so one
   LLM reply cannot raise two prompts; the approval then re-enters the engine with the same
   `(slot, body)` and the duplicate arm refused it — `already_awaiting_approval`, nothing
   delivered. `STAGE_TTL_S` and governance's `_CONFIRM_TTL_SECS` are **both 90 s**, so there was
   never a window in which an approval was still valid and the mark had expired.

   Fixed by threading `governance_bypass` into the guard as `approved=`: the duplicate arm is
   skipped for the post-approval invocation and the mark retired, while **the denial arm runs in
   both modes and is checked first** — an approval sentinel can never overturn a refusal.

   **Why 34 passing tests missed it, and the lesson that generalises:** `SendGuard` is correct in
   isolation, and every wiring test matched *source text* (`assert "guard.refusal(" in body`)
   rather than running the sequence. A grep-level test cannot tell "refused" from "nothing was
   sent". `test_partner_send_gate.py` (24 checks) now drives the real governance manager, real
   registry, real engine and main.py's real read-back — compiled out of main.py's source with
   `ast` so a drift in main's body fails the harness instead of passing a substring check — and
   asserts on **transport call count**. Recipient allowlist re-proven by execution at the same
   time: 16 hostile shapes (raw ids as str/int/negative/unicode-digit/nested, unknown names,
   vague words) all reach the transport zero times.

   Still owed: the **§24 live gate**, which would have caught this on the first real send.

4. **Partner-inbound, the "did she talk to you" feature — ✅ DONE 2026-08-02 (`ba12cc1`).**
   **Both halves of partner-messaging are now complete: outbound `message_partner` (item 3)
   and inbound `partner_contact_status`.** Spec was roadmap §6.7; it is built to it.

   `partner_contact_status` (ADMIN-ONLY, AUTO in governance) answers *"Yes, Sir — Mousumi
   messaged around 3pm. Nothing urgent."* / *"…and twice more since; she flagged it as
   important. You may want to call her."* / *"No, Sir — nothing from Mousumi today. Last I
   heard from her was yesterday, around 12:30pm."* Times are deliberately coarse — "at
   15:12:44" is surveillance phrasing.

   **The design evolved during the build, and the change is the interesting part.** The first
   version scanned her logged messages and withheld the content when answering. It worked and
   was green, but discretion was a property of the *formatting code* — one careless refactor
   from leaking, and it coupled the gentle capability to the invasive one, since it needed
   verbatim transcript logging switched on to work at all. The shipped version instead reads
   `modules/contact_events.py`, a store whose schema is `(id, partner_key, partner_slot,
   timestamp, urgency)` — **no content column, and `record()` has no parameter through which
   content could arrive.** The urgency scan runs upstream in memory and only its boolean
   crosses the boundary. Content-free by construction beat scan-then-withhold.

   - **Encrypted at rest**, C#11a keystore, no new dependency: `partner_slot`, `timestamp` AND
     `urgency` are all sealed. More than `partner_log` seals, deliberately — there the secret
     is the message body and the timestamp is incidental; here the timestamp *is* the payload,
     since the table's whole content is a contact pattern. Ordering comes from the
     autoincrement `id` (insertion order is already chronological), which is what makes
     encrypting the timestamp affordable. `partner_key` is a keyed blind index, because
     randomised ciphertext can never satisfy `WHERE partner_slot = ?`.
   - **`JARVIS_LOG_CONTACT_EVENTS` (default OFF since 2026-08-08)** is independent of
     `JARVIS_LOG_PARTNER_CHATS` on purpose — that is the entire reason for the separate store,
     so the discreet answer works on a machine where keeping her words is off. A harness pins
     that the write did not drift behind the transcript flag. It shipped default-ON and was
     flipped by Kaustav's ruling; see checklist item 2 above for the reasoning and the `.env`
     line that keeps his own machine recording.
   - **Fails honestly.** Recording off, or a keystore that will not open, says so — never "no,
     she didn't message", which would be a confident answer manufactured by a failure.
   - **No migration** — new table, created on first write.
   - Harness `test_partner_contact.py` (25 checks at `ba12cc1`, **41 after `eff7540`**). The
     leak checks push a rare marker word through the real write path and scan the raw db file
     for it, rather than asserting the code looks careful.

   **`summarize_partner_chat` survives as the deliberate explicit override** — "what did she
   say" is a different, more explicit request than "did she call". That settles the §6.6 open
   decision; the routing prompt in `brain.py` now states the two are not interchangeable.
   ✅ **Confirmed by Kaustav 2026-08-08** (checklist item 4) — it is his ruling now, not an
   artefact of how it happened to get built.

   **Still his call, not technical:** whether Mousumi knows JARVIS exists and that Kaustav can
   ask whether she made contact. The butler model very likely clears the bar transcript-logging
   did not — fact-of-contact is roughly what a housemate would observe — but no document
   settles it for him.

   ✅ **The Benglish urgency terms are no longer a guess** — Kaustav's real list landed in
   `eff7540`, along with a second semantic layer. See checklist item 3 at the top; the list
   itself stays his to keep refining.

   Unchanged and pinned against regression: `extract_and_store_memory` still runs for every
   recognised caller ahead of the partner gate, and `partner_log` still honours its own opt-in
   flag. "Off" still means *no transcript*, **not** *nothing retained*.

5. **One open call, his, not blocking:** the cloud cannot seal before it has the public half,
   so after a Render restart with the PC off, facts are **not queued** — counted and logged
   loudly every time (`dropped_no_key`, surfaced in `/health`), never stored in plaintext.
   Closing it means putting the desk **public** key in Render's env, which crosses his
   "no new config on Render" line. Left open deliberately.

⚠️ **The merge is still not a fast-forward** — see the `8d0ea4f` note above. Order is: §7 live
gate → pre-Electron code review → restore Electron config + package → merge to `main` → ship
the `.exe` → Step 3 → then the post-Electron backlog, one item at a time.

## Off-machine (only Kaustav can do these)

- [x] **Recovery code stored OFF this disk — DONE 2026-08-01.** Fresh code in his password
      manager; all earlier codes void; round-trip verified working (see the top section).
- [ ] **A 6th cleartext `.env` copy is owed a shred — `JARVIS-BACKUPS\pre-encryption-20260808-004215\.env`,
      9,731 bytes**, created 2026-08-08 by the backup the source-tag migration takes before it
      touches anything. Same job as the five below. **This will keep happening on every backup
      until fix (a) or (b) under checklist item 1 lands** — the sweep that "now finds no `.env`
      anywhere under that tree" is true only until the next backup runs.
- [x] **The 5 cleartext `.env` copies in `JARVIS-BACKUPS` were shredded 2026-08-01** — one per
      `pre-encryption-*` folder, 9,731 bytes each. The recursive sweep that found no `.env`
      anywhere under that tree was true **on that date only** — the 2026-08-08 backup put one
      back (see the entry above). The live `jarvis-backend\.env` is untouched, so nothing was
      lost. Folder structure and every `.db`/`.npz`/Chroma file left intact (verified: each
      folder exactly −1 file / −9,731 bytes, `plaintext-originals` byte-identical).
- [ ] **Biometric + Chroma backup copies still owed a shred** — his task, off-tooling.
      5× `models\owner_embeddings.npz` (6,686 B each) and 5 Chroma sets per backup folder
      (`jarvis_chroma_db` / `action_chroma_db` / `personal_chroma_db` / `memory\vector_db`,
      20 `chroma.sqlite3` files in total). Chroma keeps document text **plaintext** and its
      `.bin` vectors leak approximate content via embedding inversion — the same reasoning
      that kept partner data out of Chroma entirely.
- [ ] **The plaintext memory net is KEPT BY CHOICE, not by necessity.** 2 plaintext
      `jarvis_longterm.db` copies + 5 plaintext `jarvis_memory.db` copies (that store was
      never encrypted at any point in its life), plus the tracked
      `plaintext-originals\jarvis_longterm.db.plaintext-20260730-002550`. **The recovery path
      is now proven, so this CAN be pruned at any time** — it is retained only until he calls
      the encrypted store production-ready. Three `jarvis_longterm.db` copies in the later
      `pre-encryption-*` folders are almost certainly already *encrypted* (they post-date the
      migration; sizes 40,960 / 49,152 / 53,248 grow with ciphertext overhead) — confirm with
      a hex viewer before treating them as spillage.
- Keep `JARVIS-BACKUPS` on this machine — do not sync, zip to a shared drive, or upload it.
  It sits outside the repo (`F:\work\JARVIS-BACKUPS` vs `F:\work\JARVIS-Project`) and is not
  a git repo, so no `git add` can ever reach it; nothing sensitive is tracked and every key
  path is gitignored.

## Three things worth not re-learning

- **`UNIQUE(user, content)` cannot work on encrypted columns** — random nonces mean the same
  fact never produces the same ciphertext. Dedup lives in the keyed blind index
  `memories.content_hash`. Any future encrypted column needs the same treatment.
- **A crashed harness reports `0 failed`.** `run_harnesses.py` counts `broken` separately, so
  the line to trust is `N/N harnesses green`, never `0 failed` on its own.
- **Two sealed-queue invariants that look like bugs if you forget them.** (1) A record leaves
  the cloud outbox **on the ack, never on the send** — that is what makes a socket dying
  mid-batch cost a redelivery instead of a fact, and redelivery is normal, not exceptional.
  (2) An **empty sink means HELD**, not dropped: nothing acked, nothing ledgered. If facts seem
  to vanish, check whether a sink is installed before suspecting the transport.
- **A CLI that prompts cannot be answered from the PowerShell tool** — its stdin is the null
  device, so a piped `y` never arrives and the prompt declines. `manage_keys.py export-key`
  failed safe that way ("Cancelled. Nothing was changed.") before succeeding under a shell
  that can deliver stdin. Worth knowing before assuming a key command is broken.
