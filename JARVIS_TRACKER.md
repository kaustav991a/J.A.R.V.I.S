# JARVIS TRACKER — the one document. Complete, then reliable, then shipped.

> **Start here. Every session.** This replaces `RESUME.md`, `TEST_PLAN.md`,
> `FEATURE_CENSUS.md`, `GATE_SESSION_4.md`, `REVIEW.md` and `review-findings.json`
> — see [What moved where](#what-moved-where) at the bottom.
>
> Rules for keeping it honest:
> 1. **Numbers are measured, never estimated.** If a number is a guess it says so.
> 2. **A claim needs the evidence beside it.** This project's worst defects were
>    all claims without evidence; a tracker that claims progress it cannot show is
>    the same bug in a document.
> 3. **Update it in the same commit as the work.** A tracker that lags is worse
>    than none, because it is believed.

---

## 1 · Where JARVIS actually is — 2026-08-22

| | Measured | How it was measured |
|---|---|---|
| Automatic suite | **101 harnesses, 3386 checks, 0 failed** | `jarvis-backend\venv\Scripts\python.exe run_harnesses.py` — the system python fakes failures |
| Mobile app suite | **883/883** jest | its own repo, `F:\work\JARVIS-Mobile` |
| **Live tool selection** | **19/34 = 56%** | `run_evals.py --live`, 40 real tasks, 2026-08-22 |
| Hardware gate rows ticked | **~15 of 192** (8%) | rows passed through their own door |
| Rows with evidence, wrong door | ~40 (21%) | `PASS-SUB` — real code evidence, row still owed |
| **Rows never run by anyone** | **~137 (71%)** | |
| Open findings | **4** — F-52-open, F-56, F-57, F-59 | all four are decisions, not defects. F-60, F-61, F-62, F-63, F-66, F-67 closed |
| Branch | `feat/cloud-gateway`, pushed | `main` is far behind; merge is **not** a fast-forward |

**The honest summary:** the harnessed parts are solid, the parts nothing drives are
unknown, and the single number that decides whether JARVIS is *dependable* is the
56%.

> **Correction, 2026-08-22.** The ladder figure read 33% until the dashboard
> generator learned to tell a *score* row from an *item* row: two rows of the
> competence table were being counted as completed ladder items. The real figure
> is **3 of 13 done**. Nothing regressed — the number was wrong, and it is the
> kind of wrong this tracker's first rule exists to catch.

---

## 2 · The ladder

Four tiers, in dependency order. Do not start a tier before the one above it, and
the reason is empirical: session 4 spent a third of itself debugging bugs that
Tier 1 work would have caught before a row was ever attempted.

### Tier 0 · Stop the bleeding

| | Item | Status | Verified end to end? |
|---|---|---|---|
| 0.1 | Set `JARVIS_ADMIN_OVERRIDE_CODE` in `.env` — the spoken recovery path F-23 and F-25 need | ✅ **done 2026-08-22** — set, 5 characters. Short for something guessed at rather than typed; the token match means `tiberiusx` is not `tiberius`, so length is the only defence it has. His call, recorded | `.env` re-measured — **the spoken path is a gate row, still owed** |
| 0.2 | Fix `GEMINI_API_KEY` — and understand the quota | ☐ **low priority, and the reason matters.** Audited all five keys 2026-08-22: the primary is **`400 API key not valid` — NOT exhausted.** `400 not valid` and `429 RESOURCE_EXHAUSTED` are different failures; waiting fixes the second and never the first. It is also not one of the four pool keys. The four ARE valid and all `429`, `limit 20 on gemini-3.7-flash`, and their retry-after counted down **in step** (49/48/48/47s, spread 2.3s) → **one shared bucket**, so more keys in the same Google project add nothing. Multiplying the quota needs **one key per separate Google Cloud project**. Not urgent: this entire session ran with Gemini exhausted and Groq carrying it, exactly as the cascade is designed to | — |
| 0.3 | Boot preflight that asks providers whether the configured models still exist | ✅ **done** — 11 ids checked, catalogues only, zero tokens, `JARVIS_MODEL_PREFLIGHT=0` to disable | **SEALED** — harness 8 checks + live boot (`all 11 configured model id(s) exist`) + negative-tested against the two ids that were really dead |
| 0.4 | ollama auto-starting — it was down all of session 4, so every vision feature was dead and nothing said so | ✅ **done 2026-08-22** — `tools\ensure_ollama.ps1` (idempotent: starts the **HTTP server**, waits for READY, exits 0 if already listening) + `tools\install_ollama_task.ps1` registering the logon task **JARVIS ensure ollama**. Not a Windows service: a per-user logon task needs no elevation and he can see and disable it in Task Scheduler. Windows' own Startup shortcut launches the **tray app**, which is not what JARVIS depends on | **SEALED** — both paths run live: recovery from down in **13s** then **6s** (`4 model(s) available`), and the idempotent path (`already listening -- nothing to do`); task registered, state **Ready**; and `boot_preflight` now calls a dead local daemon **NOT RUNNING** rather than "unverified" — verified live with ollama stopped. Harness `test_boot_preflight.py` **47 checks** |
| 0.5 | Re-enroll the face on the **phone-camera angles actually used** — until then every camera feature mistrusts him and F-62 recurs | ☐ **needs him + the phone** | — |

### Tier 1 · Make the two habits fail the suite

Session 4 raised 16 findings. **15 of them were two habits.** Fixing instances is
losing; the habits have to become suite failures.

| | Item | Status | Verified end to end? |
|---|---|---|---|
| 1.1 | **The claims layer.** Audited first: the taxonomy was NOT scattered — `brain.py` already owned four strippers coherently, so consolidation would have been churn. The real gap was **coverage**, and one uncovered class. Closed: the F-60 capability rule (both the promise form and the request form), a coverage **inventory** of every LLM-text function with the guard it carries *or a written decision that it carries none*, and a scan for invisible control bytes | ✅ **done** — `test_claims_guard.py`, 91 checks | **SEALED** — harness 91 checks + live on the desk (pizza refused, calendar unaffected). Caveats stated in the harness: 3 synthesis paths carry a recorded decision, and `transfer 500 rupees` is a documented miss |
| 1.2 | **`test_single_source.py`** — root cause #4 asked mechanically, 7 pins | ✅ **done** — found F-66 and F-67 on its first run | **SEALED** — harness 27 checks; it found F-66 and F-67 on its first run. Pins only known surfaces, by design |

### Tier 2 · Competence — the 56%

The eval localises it precisely. **Fix retrieval before building anything.**

One row per category, so the table sums to the headline: **19/34**.

| Group | Live score |
|---|---|
| tv | 5/5 |
| files | 2/2 |
| mail | 2/2 |
| hud | 1/1 |
| media | 1/1 |
| git | 2/3 |
| apps | 2/4 |
| memory | 1/2 |
| partner | 1/2 |
| system | 1/2 |
| web | 1/4 |
| calendar | 0/3 |
| misc | 0/3 |

**The diagnosis was wrong, and the correction is the useful part.** This section
used to say the misses were retrieval — descriptions, aliases, ranking. They were
not: the **offline retrieval eval is 40/40**, so the catalogue surfaces the right
tool for every one of these requests.

The cause was upstream. `tool_set_for()` returns only `files` or `authoring`, and
**not one** of the fifteen missed tools is in either set — so the model was handed
five file tools and asked to book a dentist appointment. Reaching `check_calendar`
required it to decide, unprompted, that nothing it could see fit and to call
`search_tools`. Sometimes it did (tv 5/5); for calendar and misc it never did, and
reached for `find_file` three times instead.

Fixed by making that search once, for the model, with the goal as the query,
filling only slots that were already free. Measured across the eval's 40 tasks —
expected tool resident **before the model's first turn**:

| | |
|---|---|
| before the preload | **4/40 (10%)** — the baseline the 56% came from |
| after the preload | **39/40 (97%)** — only `tv-04` still needs to search |

`research` is defined in the registry and **never selected** by `tool_set_for` —
harmless now that the preload reaches the web tools anyway, but recorded.

| | Item | Status | Verified end to end? |
|---|---|---|---|
| 2.1 | ~~Fix descriptions/aliases/ranking~~ — **the diagnosis was wrong**; retrieval was already 40/40. The shelf is **preloaded from the goal** instead: expected tool in front of the model **4/40 → 39/40**, harnessed in `test_shelf_preload.py` (34 checks). **The `--live` re-measure is still owed** — it drives real actions on his desk, so it needs his go-ahead | ⚠️ **mechanism fixed + measured offline; live number pending** | harness 34 checks + offline **4/40 → 39/40**. ⚠️ **the LIVE number is NOT re-measured** — this is the one open loop |
| 2.2 | Settle **F-59** — `should_use_agent` accepts two sentence shapes while A22 has 24 rows written against goals it will not accept. Widen the gate or rewrite the rows; until then A22 cannot validate any of this | ☐ **decision** | — |
| 2.3 | Only if retrieval tops out and it is still wrong: the tiered brain (a stronger model for tool selection) | ☐ blocked by 2.1 | — |

### Tier 3 · Unattended reliability

| | Item | Status | Verified end to end? |
|---|---|---|---|
| 3.1 | Run all 192 rows once and fix what they find — **4–6 sessions**, most needing him at the desk | ☐ see §3 | — |
| 3.2 | A RAM budget — **and measuring it overturned the design.** The plan was to REFUSE a local model that would not fit. Measured live first: with **2.56 GB free**, far under `llava`'s 4.41 GB, the call loaded and answered correctly in **91.9 s** — slow, not broken. Refusing would have deleted a working feature at the one moment it is the only option left (the vision cascade reaches llava only after Gemini has already failed). The two REAL defects the measurement exposed: a fixed **120 s** deadline over a **92 s** call leaves 28 s of margin, so a working answer gets cancelled and reported as *"vision offline"*; and **nothing ever set `keep_alive`**, so one screen read parked 4.4 GB for ollama's default **5 minutes** | ✅ **done 2026-08-22** — `modules/ram_budget.py` **advises, never blocks**: a tight load gets a **longer** deadline (240 s) and a short `keep_alive` (30s). Footprints are **read** from ollama, so they stay right when he pulls a new model; a model already resident is free however little RAM is left | **SEALED** — harness `test_ram_budget.py` **49 checks**, all values injected so it does not depend on what the machine is doing. Live measurements recorded in the module: llava 4.41 GB on disk / 4.39 resident, llama3.2:3b 1.88 / 2.55, free RAM **2.74 → 6.87 GB** the instant llava was unloaded. Both vision legs go through one function (root cause #4); the **text leg is excluded by a written decision**, pinned by the harness |
| 3.3 | **7-day unattended soak** — no false intruder alert, no fabricated claim in the logs, no silent config rot. Nothing in this project currently proves *sustained* reliability, and that is what "rely on it" means | ☐ | — |

---

## 2b · What is SEALED, and what is still open

**"Sealed" has a definition here, or the word is worthless:** the code is written,
a harness in the suite pins the behaviour so it cannot silently regress, **and** it
was proven on the running desk — with any boundary stated rather than left to be
discovered. An item that meets all four does not get revisited.

| | Sealed | Evidence |
|---|---|---|
| **0.3** model liveness at boot | ✅ | harness 8 checks · live boot printed `all 11 configured model id(s) exist` · negative-tested by feeding it the two ids that really were dead · offline → UNVERIFIED not DEAD · cp1252-safe |
| **1.1** the claims layer | ✅ | harness 91 checks · live: *"I have no way to order anything, Sir"* and *"book me a table"* refused, while *"check my calendar"* still answered |
| **1.2** single-source pins | ✅ | harness 27 checks · it found **F-66 and F-67 on its first run**, both fixed |
| **0.4** ollama autostart | ✅ | harness 47 checks · both script paths run live (recovery 13s/6s, then `already listening`) · logon task **Ready** · preflight verified live with the daemon stopped: **NOT RUNNING**, not "unverified" |
| **3.2** the RAM budget | ✅ | harness 49 checks, every value injected · the design was **overturned by measurement** — 91.9 s at 2.56 GB free proved refusal wrong · free RAM 2.74 → 6.87 GB on unload · one function for both vision legs · the text-leg exclusion is a written, pinned decision |
| **reference compliance** (rules 1 + 11) | ✅ | harness `test_reference_compliance.py` **28 checks** · 11 descriptions rewritten and 7 confusable pairs made MUTUAL · every backticked tool name in every description now resolves to a real tool (**56 cross-references**), with the six non-tool identifiers declared and reasoned · rule 11's serial execution is a **written decision** at the loop, pinned so it cannot be silently "finished" · retrieval eval re-run **40/40**, so the rewrites cost nothing |
| the fixes F-45 … F-67 | ✅ | each harnessed; F-60/61/62/63/66/67 also re-run live after the fix |
| the docs + dashboard | ✅ | harness 25 checks · regenerating the page must change nothing, so a stale page fails the suite |

**Two things are NOT closed, and I would rather name them than let them look done:**

1. **2.1's live number.** The mechanism is fixed and measured offline — the expected
   tool went from 4/40 to 39/40 in front of the model — but `run_evals.py --live`
   has **not** been re-run, so the 56% still stands as the last real end-to-end
   figure. It needs a run that touches his machine.
2. **Row `4.3`.** `workspace_patch` stages its confirmation correctly and then never
   applies the edit. Seen twice, cause not yet found. It is the one gate row that
   failed for a reason still unexplained.

And the standing one, which is not a defect but must not be forgotten: **every
`PASS-SUB` row still owes its own door.** The text command line proves the brain and
says nothing about the microphone, the camera, the hands or the phone.

---

## 3 · The gate — 192 rows, batched by what they need

Detail and running order: **`LIVE_GATE_CHECKLIST.md`**. Findings ledger:
**`LIVE_GATE_FINDINGS.md`** (read last section first).

| Batch | Rows | Needs | State |
|---|---|---|---|
| A1 pre-flight | 5 | machine | ✅ all 5 |
| A24 watchdog | 5 | machine | ✅ 3 of 5 (`1.5` needs a real Ctrl+C) |
| A5 workspace | 5 | machine | ✅ 4, `4.3` owed — patch stages and never applies |
| A7 governance | 5 | machine | ✅ 5 via the text door |
| A9/A10 memory | 6 + K | machine / **his recovery code** | ✅ 9.1, 9.2, 9.6, K1, K5b · K2–K5 need him with the code in reach |
| A11 information | 9 | machine + live tokens | ✅ 10.2, 10.5, 10.7, 10.9 |
| A16 login | 6 | mic + face | ✅ `17.6` 🛑, `17.8` · rest need him |
| A21 camera | 12 | phone camera | ✅ `21.1`, `21.2`, `21.9`-half |
| A22 agentic | 24 | machine + TV | ✅ 7 · **8 blocked by F-59** · TV/phone rows owed |
| A3 voice | 14 | **microphone** | ☐ none |
| A6 OS/apps | 7 | his desktop | ☐ mostly |
| A13 vision | 4 | camera + ollama | ☐ `12.1` FAILS (F-61 fixed, needs re-run) |
| A17 resilience | 7 | machine + HUD | ☐ |
| A18–A20 gesture | 25 | **his hands** | ☐ none |
| A23 partner refusals | 2 | machine | ⚠️ refused, but via the wrong path — **F-57** |
| Group B | 7 | **second device**, pinned MAC | ☐ none — set the probe up *before* the session |
| Group C | 15 | **second person** — Kinshuk, Mousumi | ☐ none — batch one visit |
| Group D | 11 | **phone in hand** | ☐ none |

---

## 4 · Open findings — all four are decisions

| ID | What | Whose |
|---|---|---|
| **F-52-open** | A CONFIRM step inside a multi-step plan is a dead end: the planner cancels the pending confirmation and asks for an authorisation nobody can give. `agent_yield` already solves the shape (park → notify → "approve task ab12cd34"), but wiring it changes what happens to a plan mid-flight | **his** |
| **F-56** | Row `5.7` expects a sandboxed directory listing to work; governance makes `run_terminal_command` BLOCK and refuses it. Both defensible, both cannot be true | **his** |
| **F-57** | "text Priya" routes to `send_whatsapp_message` (BLOCK) instead of the partner allowlist, so A23's actual subject — the thing that must hold before Group C's real sends — has never been exercised | **his** |
| **F-59** | `should_use_agent` is narrower than the §6.8 arc it gates | **his** |

Everything else raised in sessions 1–4 is fixed and harnessed. One finding, **F-64,
was withdrawn** — I asserted "no search tool was called" from too small a grep
window when the call was 39 lines lower. The lesson is in the ledger.

---

## 5 · The two habits, and the five root causes

Kept here because they are cited constantly and they earn their place: **15 of
session 4's 16 findings were the first two.**

**Habit 1 — a claim with nothing behind it** (7 of 16). **Now a suite failure** — `test_claims_guard.py` pins the taxonomy, the coverage inventory and the capability rule. A three-word prefix spoken
as an answer; the model's own reasoning read aloud; an invented Google Sheets
window; an intruder accusation from a failed match; an intruder flag over an empty
room; an offer to order a pizza it cannot order.

**Habit 2 — root cause #4: fixed at one door, open at its siblings** (8 of 16). **Now a suite failure** — `test_single_source.py`, 7 pins, which found F-66 and F-67 on its first run. A
model id in five files; a guard the cloud had and the desk did not; a path form
fixed in the relative case only; a schema parameter the layer beneath ignored; two
debounces disagreeing; a flag set in one branch only; and twice inside a single
file while fixing something else.

From the pre-Electron review of ~17,700 lines (46 findings, all fixed), ordered by
how often each actually produced a defect:

- **A · A model-supplied string reaches a SINK** — a shell, a path, a URL, SQL, an
  ADB command. Governance approves by TYPE and never inspects the ARGUMENT, and
  since §6.8 the argument can come from a web page, a document, a photo or an MCP
  reply.
- **B · A CLAIM made without the action having happened.** *JARVIS lying about
  himself is the top severity in this project.*
- **C · A GATE not wired on every path.** Ask of every check: *which callers reach
  the sink without passing it?*
- **D · STRUCTURE encoded in a character the content may contain** — a pipe, colon,
  comma, newline.
- **E · A LEAK or corrupt-state crash** — a camera, thread, subprocess or handle
  not released on the error path.

> **The question to ask before every fix:** *which OTHER verb reaches this
> resource, and which other door reaches that verb?* One injection class was found
> eight separate times before anyone asked it.

---

## 6 · Ship — measurable gates, not a feeling

Sequence (from the roadmap's after-the-gate list, which still stands):

1. Every row in §3 green or waived **in writing** with a reason
2. Pre-Electron review is **done** (46 findings, all fixed — see git history for
   `REVIEW.md` and `review-findings.json`)
3. **62 dependabot alerts** on the default branch — they clear when this branch
   merges, since the 13 bumps were applied here 2026-08-15. `protobuf` **must**
   stay `6.33.6`
4. Electron: `ELECTRON_SHIP_PLAN.md` — needs him present for real frameless windows
5. Merge `feat/cloud-gateway` → `main`. **Not a fast-forward**: `origin/main`
   carries `8d0ea4f` (LICENSE) which this branch does not. **Fetch first**
6. Package the `.exe`
7. **Then** roadmap Step 3 — `.env` secrets into the key store (deferred on purpose;
   it rewrites every boot-time key read)

**The four gates I would hold to before calling it shipped:**

- ☐ All 192 rows green or waived with a reason
- ☐ `run_evals.py --live` **≥ 85%**
- ☐ **Zero open 🔴**
- ☐ **7-day unattended soak clean**

---

## 7 · Next three actions

```powershell
cd F:\work\JARVIS-Project
git log --oneline -3
cd jarvis-backend
venv\Scripts\python.exe run_harnesses.py    # expect 96/96, 3135 checks, 0 failed
```

Then, in order:
1. **Tier 0.1 + 0.2** — the two secrets. His, two minutes, and they unblock a
   recovery path that currently does not exist.
2. **Tier 2.1** — tool descriptions, aliases and ranking, then re-measure
   `run_evals.py --live`. This is the 56%, and it is what decides whether JARVIS
   can be relied on at all. `AGENT-TOOLING-REFERENCE.md` is the doc to work from.
3. **Tier 0.4** — ollama as a service, so vision is never silently dead again.

For a hardware session: set the two secrets, launch with `JARVIS_AUTO_LOCK=0`, and
**capture stdout to a file** — that one habit has opened findings in two
consecutive sessions without a line of code being touched.

```powershell
venv\Scripts\python.exe watchdog.py *> gate-session-5.log
```

---

## 8 · The dashboard

`tracker.html` — open it in a browser for the same state as a page: the ladder with
a completion bar, the gate batches, open findings and the ship gates.

It is **generated from this file**, not maintained beside it:

```powershell
jarvis-backend\venv\Scripts\python.exe tools\build_tracker_html.py
```

Self-contained — no script, no external asset, no network — so it opens straight
from disk. `test_tracker_html.py` asserts that regenerating it changes nothing, so
a stale page fails the suite rather than quietly misinforming you. If a number
there looks wrong, fix it *here*; the page has no figures of its own.

---

## What moved where

Seven documents were retired into this one on 2026-08-22. Every word of every one
of them is still in `git log` — nothing was lost, only de-duplicated.

| Retired | Why | Where its content is now |
|---|---|---|
| `RESUME.md` | it was this document, under another name | §1, §4, §7 |
| `TEST_PLAN.md` | `LIVE_GATE_CHECKLIST.md` was **generated from it** and said "tick here, then mark TEST_PLAN when the day is done" — two-place bookkeeping guarantees drift. Its PART A is now just `run_harnesses.py` | §1 (the suite), §3 (the rows), and the checklist is now the single row source |
| `FEATURE_CENSUS.md` | its central claim, "zero code findings open", was disproved the same day by 16 findings. A census of what EXISTS cannot tell you what WORKS | §1, §3 |
| `GATE_SESSION_4.md` | a session record whose findings already live in the ledger | §3 verdicts; findings in `LIVE_GATE_FINDINGS.md` |
| `REVIEW.md` | the review is finished; the taxonomy is what outlives it | §5 |
| `review-findings.json` | 46 findings, all fixed and harnessed | git history |
| `MOBILE_CONNECT.md` | operational how-to, not a plan | appended to `JARVIS_MANUAL.md` |

**Kept, because each answers a question nothing else does:** `README.md` (front
door) · `JARVIS_MANUAL.md` (how to use it) · `CHANGELOG.md` (history) ·
`JARVIS_MASTER_ROADMAP.md` (the build plan) · `LIVE_GATE_CHECKLIST.md` (the 192
rows, in running order) · `LIVE_GATE_FINDINGS.md` (the findings ledger, with the
reasoning) · `AGENT-TOOLING-REFERENCE.md` (the tool layer) ·
`ELECTRON_SHIP_PLAN.md` (live and unexecuted — delete it when the `.exe` ships).
