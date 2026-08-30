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

## 0 · The goals — what has to be TRUE, not what has to be built

> **`tracker.html` opens on these.** A tier and a batch describe how the work is
> *organised*; a goal describes what is different for him when it is finished. Every
> ladder item and every gate batch belongs to exactly one goal below, and **the
> generator refuses to build if anything belongs to none or to two.**
>
> That refusal is the point of the arrangement rather than a nicety. A grouped page
> has one failure mode that matters: a row with no group vanishes from the view while
> still counting in the total, so the percentage and the list disagree and the page
> looks complete *because* something is missing from it. Breaking the build is the
> same bargain the staleness check already makes.
>
> Members are the ladder's own ids and the gate's own batch labels, so nothing is
> renamed to fit a grouping.

| Goal | What is different for him when it holds | Members |
|---|---|---|
| **He never claims what he did not do** | The top severity in this project, and it is about character rather than correctness: an assistant that reports an action it did not take cannot be delegated anything. ✅ **CLOSED 2026-08-29 — all nine A11 rows pass and both habit-1 guards are sealed. See §4.5.** Closing it meant finding three 🔴 first: he invented an appointment, quoted a headline from an empty source, and misstated why he refused something. One residual is named there, and it is the microphone door, which no row in this batch has ever used | `1.1` `1.2` `A11 information` |
| **He is up before you are, and stays up** | Nothing else on this page means anything on a machine where a dependency died quietly — session 4 lost every vision row to an `ollama` that was down and said nothing. ⚠️ **ONE ROW SHORT, 2026-08-30**: everything but `18.5`, which needs a camera. Three findings, one of them goal 1's own defect living in the HUD — see §4.6 | `0.2` `0.3` `0.4` `3.2` `A1 pre-flight` `A24 watchdog` `A17 resilience` |
| **He reaches for the right tool the first time** | The one number that decides whether he is *dependable* rather than *impressive*. The cause was upstream of retrieval: he was handed five file tools and asked to book a dentist. Mechanism fixed and measured offline at 39/40; the live re-measure is the open loop | `2.1` `2.2` `2.3` |
| **He acts on your behalf, with the brakes on** | The difference between a voice interface and an agent: 56 tools behind a door he opens by saying *"work through this"*, every CONFIRM read back verbatim before it fires, and a refusal that holds when the request is about someone else | `A5 workspace` `A6 OS/apps` `A7 governance` `A22 agentic` `A23 partner refusals` |
| **He hears you, across the room** | Fourteen rows and **not one has ever been run**. Everything proved so far went through a keyboard, so the whole microphone path — wake word, barge-in, contention while he is speaking — is unknown rather than working. The spoken recovery code is here too, and it is the only done item not sealed | `0.1` `A3 voice` |
| **He sees what you show him** | A camera, a screen read, and twenty-five gesture rows. The one vision row that has run invented two of its four claims, so this is where habit 1 and the senses meet | `A13 vision` `A21 camera` `A18–A20 gesture` |
| **He knows it is you, and who else is there** | An assistant with this much reach has to be certain who is asking. The face must be re-enrolled on the angles actually used before any camera row after it can be believed, and two of these batches need another person in the room | `0.5` `A16 login` `Group B` `Group C` |
| **He remembers, and it survives a restart** | What he knows has to outlive the process — and a claim that is only true for a while must not be stored as though it were permanent | `A9/A10 memory` |
| **He reaches you when you are away from the desk** | The always-on half: the phone, the cloud gateway, a briefing that arrives without the app being open. Eleven rows, none run, all needing the phone in hand | `Group D` |
| **He is still right after a week nobody watched** | The long pole, and the only item whose cost is elapsed time rather than work. Nothing here currently proves *sustained* reliability, and that is what "rely on it" means | `3.1` `3.3` |

---

## 0.5 · The standing decision — the desk first, and the app not at all

**Taken by Kaustav on 2026-08-29: no further work on the phone app until the desk
is 100%.**

That is a scope decision rather than a technical one, and it is written here
rather than in a commit message because it governs every session until he lifts
it.

**The line is the REPOSITORY, and he drew it there deliberately.**
`F:\work\JARVIS-Mobile` is frozen, and so is the OTA publish that would put
today's app-side work on the phone. **Gateway work in THIS repo continues** —
including the brain-queue items whose code lives here, even though the phone is
what eventually consumes them. A gateway change is desk work: it is written,
harnessed and deployed on this side, and it is provable from `/health` without a
phone in the room.

**What the freeze actually stops, named rather than discovered later.** Two
commits are finished, tested and *unprovable* while this holds, and neither is a
defect — they are finished code waiting on a publish:

* the app half of the capability tokens (`38a05cb`), so the phone keeps
  presenting the master and `/health.app_auth.master_calls` keeps counting;
* the app half of queue item 25 (`6d8be2d`), so a spoken turn that outlives its
  socket still arrives with no question above it — **the gateway half is live**
  and puts the transcript on the push that the phone does not yet read. Until it
  publishes this will look exactly like the original defect and is not.

Of the four remaining brain-queue items, **three are gateway work and stay
open**: `6` (the transcribe prompt overcorrects on plain English), `14` (the
situation on the persona envelope), and `15`'s gateway half (a per-message id, and
`delivered`/`read` are things only the gateway can say). Only `11` — the
notification listener — is app-side and parked, along with `15`'s app half and
`12`'s. Item 22 was never in the list: gateway-only, deployed, and it proves
itself over two mornings of ordinary briefings with nothing published.

### What "the desk is 100%" means, measured

Four ladder items and one gate. Nothing here is code this machine can finish
alone — which is the honest shape of the remaining work, and the reason the
decision costs nothing in throughput:

| | Item | Needs |
|---|---|---|
| `0.5` | re-enroll the face on the phone-camera angles actually used | him + the phone camera, 10 minutes |
| `2.1` | the `--live` tool-selection re-measure | his go-ahead — it drives real actions on his desk |
| `3.1` | the **192 hardware gate rows**, ~137 of which nobody has ever run | him at the desk, 4–6 sessions |
| `3.3` | the 7-day unattended soak | a week of wall-clock; the long pole, and it can start today |
| — | zero open findings | six are open, and all six are decisions rather than defects |

**A code session between now and then does desk work**: whatever the gate rows
turn up, the findings queue, and the soak's own instrumentation. Not the queue in
the other repo.

---

## 1 · Where JARVIS actually is — 2026-08-22

| | Measured | How it was measured |
|---|---|---|
| Automatic suite | **117 harnesses, 4032 checks, 0 failed** | `jarvis-backend\venv\Scripts\python.exe run_harnesses.py` — the system python fakes failures. Remeasured 2026-08-29 after the `fix/durable-state` merge: the two harnesses it brought had never been executed by a real interpreter and both were wrong |
| Mobile app suite | **883/883** jest | its own repo, `F:\work\JARVIS-Mobile` |
| **Live tool selection** | **19/34 = 56%** | `run_evals.py --live`, 40 real tasks, 2026-08-22 |
| Hardware gate rows ticked | **~15 of 192** (8%) | rows passed through their own door |
| Rows with evidence, wrong door | ~40 (21%) | `PASS-SUB` — real code evidence, row still owed |
| **Rows never run by anyone** | **~137 (71%)** | |
| Open findings | **6** — F-52-open, F-56, F-57, F-59, F-68, F-70 | all six are decisions, not defects. F-69 was raised and closed the same day |
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
| 0.1 | Set `JARVIS_ADMIN_OVERRIDE_CODE` in `.env` — the spoken recovery path F-23 and F-25 need | ✅ **done 2026-08-22** — set, 5 characters. Short for something guessed at rather than typed; the token match means `tiberiusx` is not `tiberius`, so length is the only defence it has. His call, recorded | **The only done item not SEALED, and the gap is the DOOR, not the code.** Matching verified live against the real configured value 2026-08-22 (without printing it): 1 token, alphanumeric, survives tokenisation; it matches when spoken inside a natural sentence with different case and punctuation, and correctly REFUSES the phrase without the code, the code plus one character, and the code embedded in a longer word. Harness `test_admin_override.py` **34 checks**. What is owed is one real utterance through the **microphone** — F-23/F-25 — which needs him. ⚠️ **Named risk for that attempt:** the code is a single short alphanumeric token, so the likely failure is the TRANSCRIBER not rendering it as one word, not the matcher. If it fails live, that is the first thing to check, and a two-word natural phrase would transcribe more reliably — his call, and the list stays his |
| 0.2 | Fix `GEMINI_API_KEY` — and understand the quota | ✅ **done 2026-08-22 (evening), and this row was WRONG TWICE — the second time by me.** Ground truth, measured: **5 keys total. The 4 in `GEMINI_API_KEYS` are valid; the 1 in the legacy singular `GEMINI_API_KEY` is `400 API_KEY_INVALID`.** The original note said exactly that and I "corrected" it to "all 4 accepted" — because my own check read `GEMINI_API_KEYS` **or** `GEMINI_API_KEY` while the router **merges both**. Checking a subset and reporting confidently is the very defect the check exists to catch. Quota is separately real: `quota_value: 20` per day on `gemini-3.7-flash`, one bucket per Google project. **His action is one line: delete or replace `GEMINI_API_KEY` in `.env`.** Nothing breaks until he does — the cascade routes around it | **SEALED** — harness `test_boot_preflight.py` **104 checks** (was 47) · boot now prints `key(s) #5 of 5 are REJECTED as invalid … Replace or delete it in .env (GEMINI_API_KEY)` — **naming the right variable**, which an earlier version got wrong and would have sent him to edit the wrong line · invalid keys are **preseeded into the router's dead set at boot**, so no request pays to rediscover them · negative-tested live with a bogus key, a bogus key first, and no key at all |
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
| 2.2 | Settle **F-59** — the agent gate | ✅ **done 2026-08-22.** Measured first, and it was worse than the finding said: the gate accepts **0 of the 14** A22 phrases, not some. The two wired shapes are a file-recency read and a file write, so **six waves of tool work — 56 tools, the shelf, `search_tools`, the skills, MCP — were reachable only by a request about a file.** Neither recorded option was right: the narrowness is CORRECT (the code says why — *"a false positive routes a trivial command through a multi-step loop"*) and the rows are testing the real product. **Kaustav chose an explicit trigger:** he says *"work through this: …"* or *"figure out …"* and the whole tool layer is reachable; anything else routes exactly as it does today. No false positives to tune, because opting in is not a guess | **SEALED** — harness `test_agent_trigger.py` **52 checks**, every A22 phrase quoted verbatim · the trigger is **stripped** from the goal (the shelf's preload searches the goal text) · a triggered non-file goal gets the new `open` base of ONE tool so the preload fills the rest — handing a TV goal five file tools was tier 2.1's finding and a new door would have reintroduced it · **all 8 checked gate phrases now surface their tool** · retrieval eval still **40/40** · both doors verified to share one gate |
| 2.3 | Only if retrieval tops out and it is still wrong: the tiered brain (a stronger model for tool selection) | ☐ **still blocked by 2.1's live re-measure**, which now measures something different: before the trigger, the live eval could only exercise file goals | — |

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
| **0.2** the Gemini keys | ✅ | harness 104 checks · negative-tested live on all three branches · **5 keys: 4 valid, the legacy singular one invalid** · the bad key is named WITH ITS VARIABLE and preseeded into the router's dead set at boot · this row was wrong twice before it was right, both times by reading a subset |
| **the vision cascade's middle leg** | ✅ | Groq vision inserted between Gemini and llava: **63.4 s → 3.5 s** measured end to end on a real image the model read correctly, with Gemini genuinely out of quota · llava (4.4 GB, up to 92 s) is now the third choice, not the second · `THINKING_HEADROOM` given one home so the two callers cannot drift |
| **2.2** the agent trigger (F-59) | ✅ | harness 52 checks · the gate accepted **0 of 14** A22 phrases and now accepts all of them when he opts in, with nothing about today's routing changed · two retrieval gaps found by quoting the checklist verbatim: row 23b.9's exact words matched **nothing** (`changed`/`project` were not aliases) and `web_browse` had **no aliases at all** · the offline eval is 40/40 and missed both, because it uses its own phrasings |
| the fixes F-45 … F-67 | ✅ | each harnessed; F-60/61/62/63/66/67 also re-run live after the fix |
| the docs + dashboard | ✅ | harness 25 checks · regenerating the page must change nothing, so a stale page fails the suite |

**Two things are NOT closed, and I would rather name them than let them look done:**

1. **2.1's live number.** The mechanism is fixed and measured offline — the expected
   tool went from 4/40 to 39/40 in front of the model — but `run_evals.py --live`
   has **not** been re-run, so the 56% still stands as the last real end-to-end
   figure. It needs a run that touches his machine.
2. **Row `4.3` — the CAUSE is closed as of 2026-08-29 (F-69); the ROW is not.**
   It was never a bug in the patch path. `add.py` holds `add` twice — the function
   and the line that calls it — so the applier refuses the patch as ambiguous and
   writes nothing, correctly, since that default used to rewrite every match
   silently. What was broken sat in the three layers above it: the spoken line
   dropped the count and both remedies, one of those remedies (`*all*`) had never
   been taught to the planner so the guard was recommending an unreachable path,
   and teaching it would have made a one-line edit and a whole-file rewrite read
   back as the same sentence. Fixed together, 29 checks, reproduced against the
   bytes on disk rather than the reply. **What is owed is the live re-run** — the
   desk, a real `add.py`, and a look at the file afterwards.

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
| A24 watchdog | 5 | machine | ✅ **all 5, 2026-08-30** — `1.4` wrong token 403 / real token 200 / nothing restarted; `1.5` PASS-SUB by console control signal, the literal interactive Ctrl+C still owed |
| A5 workspace | 5 | machine | ✅ 4, `4.3` owed — cause found and fixed 2026-08-29 (F-69: the refusal was right, the three layers above it were not); needs its live re-run |
| A7 governance | 5 | machine | ✅ 5 via the text door |
| A9/A10 memory | 6 + K | machine / **his recovery code** | ✅ 9.1, 9.2, 9.6, K1, K5b · K2–K5 need him with the code in reach |
| A11 information | 9 | machine + live tokens | ✅ **all 9 VERIFIED AGAINST THEIR SOURCES, 2026-08-29** — `tools/verify_a11.py`, three consecutive clean runs: *9 verified, 0 need a person, 0 failed*. Six 🔴/🟠 found doing it: F-74, F-74b, F-75, F-77, F-78, F-79 |
| A16 login | 6 | mic + face | ✅ `17.6` 🛑, `17.8` · rest need him |
| A21 camera | 12 | phone camera | ✅ `21.1`, `21.2`, `21.9`-half |
| A22 agentic | 24 | machine + TV | ✅ 7 · **8 blocked by F-59** · TV/phone rows owed |
| A3 voice | 14 | **microphone** | ☐ none |
| A6 OS/apps | 7 | his desktop | ☐ mostly |
| A13 vision | 4 | camera + ollama | ☐ `12.1` FAILS (F-61 fixed, needs re-run) |
| A17 resilience | 7 | machine + HUD | ✅ **6 of 7, 2026-08-30** — driven unattended by `tools/verify_a17.py` + the HUD in a browser. `18.5` OWED (needs a camera). Three findings: F-80, F-81, F-82 |
| A18–A20 gesture | 25 | **his hands** | ☐ none |
| A23 partner refusals | 2 | machine | ⚠️ refused, but via the wrong path — **F-57** |
| Group B | 7 | **second device**, pinned MAC | ☐ none — set the probe up *before* the session |
| Group C | 15 | **second person** — Kinshuk, Mousumi | ☐ none — batch one visit |
| Group D | 11 | **phone in hand** | ☐ none |

---

## 4.5 · Goal 1 — CLOSED, and how it was actually closed

**"He never claims what he did not do" is done.** `1.1` and `1.2` were sealed;
all nine `A11` rows now pass **against their sources**, three runs in a row.

### It was declared done twice before it was, and that is the story

Twice I read the desk's sentences, found them well-formed, and called the goal
finished. Twice he asked whether the completed rows needed re-checking. **Both
times the numbers did not survive being checked** — which is the goal's own
failure, committed in the verification of the goal.

What closed it was not more reading. It was `tools/verify_a11.py`: it drives all
nine rows, reads what the desk actually **said** out of the log, then goes to
Gmail, Calendar and Fit **itself, in the same minute**, and compares the figures.

```
9 verified, 0 need a person, 0 FAILED        (three consecutive runs)
```

### What running it found — nine rows, six findings

| Found | What it was |
|---|---|
| **F-71** 🔴 | An expired Google token launched a browser OAuth flow **inside an HTTP handler, on the event loop**. The whole desk API hung: `/docs` at ninety seconds, process idle at 0% CPU, log silent. `py-spy dump` found it |
| **F-72** 🟠 | Gemini slow rather than down: four key rotations on **every leg of every turn**. "What's on my calendar today?" took **409 s** with Groq answering in two |
| **F-74 / F-74b** 🔴 | **He invented an appointment** — *"your next scheduled match is at 7 PM"* against an empty calendar, 62 facts with no match in them, and no commute data on this machine. And a refusal that said an unknown action was *"classified as high-risk"* when it is not classified at all |
| **F-75** 🔴 | The briefing **quoted a headline with a publisher attached** while the news lookup was returning **zero results without raising** |
| **F-76** 🔴 | *"No health data recorded today"* — while Fit held **64 steps and 277.1 kcal**. A UTC-midnight window discarding 00:00–05:30 local, which is exactly where his day's activity is |
| **F-77** 🔴 | *"201 unread emails"* for a mailbox holding **66,373**. The count came from `resultSizeEstimate`, which tracks the **page size** — 201 at maxResults 100, 501 at maxResults 500 |
| **F-78** 🟠 | The **byline the lookup never gave** — TechCrunch, Reuters, Google News, Reuters again, attached to a bare title across four briefings |
| **F-79** 🔴 | An **empty 200 ended the cascade**. OpenRouter and the new NVIDIA backstop were never tried, and no `FATAL` was logged. `_call_ollama` has raised on this since G5.7 — fixed in one leg of five |

**Five of the eight are one shape**: a source that fails, under-reports or
returns nothing *quietly*, and a layer above it that describes the result anyway.

### The nine rows, and what each was checked against

| Row | Verified by |
|---|---|
| 10.1 | a real `tavily_search` ran, and the answer claims nothing it did not do |
| 10.2 | answered from its own knowledge and **claimed no lookup** — honest. (The row's wording expects a search; that is tier 2's question) |
| 10.3 | the URL it handed the HUD was **fetched and is a real `image/jpeg`** — and the picture was watched rendering in a browser, `naturalWidth 1056×1600` |
| 10.4 | the **actions**, not the words: either `web_browse` ran, or it said *"I didn't open python.org itself"* first. Both observed, on different runs |
| 10.5 | **66,375** quoted, `labels().get("INBOX").messagesUnread` agrees |
| 10.6 | **end to end, with his go-ahead**: cancel → *"Action cancelled"*, nothing sent; confirm → a real message in the **Sent folder** (`1a04d73847fbc56e`) and *"Email sent to kaustav.wlh@gmail.com…"*. The claim and the mailbox agree |
| 10.7 | `get_today_events_structured()` really is empty, and it said so |
| 10.8 | **64 steps** quoted, Fit agrees |
| 10.9 | through `wake up`, the real trigger: Fit + Calendar + Gmail all present, and the figures match their sources |

### What is honestly still outstanding

* **Three of the eight fixes have never been watched FIRING live** — F-74 (the
  schedule guard), F-74b (the unknown-action refusal wording) and F-78 (the
  invented byline). All three are harness-proven and negative-tested, and none of
  the three defects has recurred. **That is the defect not recurring, which is
  not the same claim as the guard catching it** — and each needs the model to do
  a specific thing on demand, which cannot be forced. The other five were each
  proved live or deterministically: F-71 (the desk answers), F-72 (`breaker OPEN`
  observed), F-76 (`64 steps and 277.1 kcal`), F-77 (`66,373`), F-79 (an empty
  cascade escalating, exercised in a forced run).
* **All nine rows went through the TEXT door.** The microphone has never been
  used by any row in this batch — that is `A3 voice`, under a different goal.
* **A pass is a snapshot.** Row `10.9` genuinely passed on 2026-08-22 and failed
  today because a lookup underneath it had started returning nothing. No code
  changed; the world did. **The verifier is the answer to that** — re-taking the
  snapshot is now one command, and the bounded re-audit list below says where
  else to point it.

### The re-audit this earns, and what it is bounded to

Not "re-run everything". The risk is concentrated in **rows whose pass depends on
an external source that can fail or under-report without raising**:

| To re-check | Why it is on this list |
|---|---|
| `A11` rows quoting a **figure** | two of them were wrong today, and both read perfectly |
| `A9/A10` memory rows | a quiet store failure reads as "you never told me that", which the gate marks 🛑 at `K3` |
| `A13` vision, `A21` camera | `12.1` already failed this way (F-61 — it invented two of four claims) |
| `A22` agentic rows passed **before** the tool preload | the shelf changed underneath them |
| anything ✅ from a session with a different **provider mix** | Gemini is a 20/day burst resource now; NVIDIA NIM is a new leg |

**Not on the list, deliberately:** rows whose evidence is a refusal, a governance
verdict or a harness assertion. Those cannot rot — nothing outside the repository
can change them.

## 4.6 · Goal 2 — one row short, and the row is a camera

**"He is up before you are, and stays up."** Driven 2026-08-30 while he was away
from the desk: no speakers, no phone, no camera. Nine of its ten outstanding rows
now pass against the machine; `18.5` needs the gesture daemon, which needs a
camera, and is recorded as owed rather than reasoned about.

`0.2`, `0.3`, `0.4`, `3.2` and `A1` were already sealed. What ran today:

| Row | Verdict | Checked against |
|---|---|---|
| 18.1 | ✅ | a **cold** desk with no token: it named all three sources and quoted no figures |
| 18.2 | ✅ | 20 WebSocket pings through a 143 s action, worst loop stall **0.66 s** |
| 18.3 | ✅ | backend killed with the HUD open — reconnected with **no reload**, rendered a broadcast sent after the restart |
| 18.4 | ✅ | a structured source spoken as 397 characters of prose |
| 18.5 | ⬜ | **owed — needs a camera** |
| 18.6 | ✅ | viewport 1920 → 900: panel clamped from x=1520 to x=840, nothing stranded |
| 18.7 | ✅ | every widget fetch from `127.0.0.1:8000`, no other host — after F-80 |
| 1.1 / 1.4 / 1.5 | ✅ | watchdog relaunched twice; wrong token 403, real token 200, nothing restarted; console signal → clean stop of both |

### The finding that matters

**F-81: the HUD said VITALS OFFLINE while the vitals were fine.** The panel read
`VITALS OFFLINE` at the same moment the same URL returned `configured:true,
steps:799`. `/api/health/summary` reaches Google Fit and takes ten seconds, and
the widget's initial state was `{configured: false}` — so it declared the source
down *before making a request*. Three widgets carried the same eight lines, and
three different situations rendered as one word: not asked yet, request failed
(the catch was `/* silent */`), and genuinely unavailable.

**This is goal 1's defect, three feet from the desk.** Two days were spent making
sure he does not say what he does not know, while a panel on the screen did
exactly that — to a reader who would reasonably have gone looking for a broken
Google token. **A screen is an assertion too.** Fixed as one shared hook rather
than three repairs.

### Two test artifacts that were nearly filed as findings

Both would have looked convincing in a transcript.

* **Parking the Google token does not take a source offline** — `_get_service()`
  caches. The first `18.1` run produced a briefing quoting 799 steps and an empty
  calendar right under a log line saying Google was UNAUTHORISED. Checked against
  the sources: **all of it was true.** The desk was right and the test was wrong.
* **A 27-second "event-loop stall" was a Google round-trip.** The probe was
  `/api/health/summary`, which is not trivial. `/health` is cheap but is `def`,
  not `async def`, so it answers *even when the loop is blocked*. The probe that
  measures the loop is a **WebSocket ping** — the same loop that feeds the HUD.

Twice in one goal, the instrument was the thing at fault. That is the same lesson
goal 1 ended on, arriving from the other direction: **an unverified measurement
is not evidence, even when it is your own.**

---

## 4 · Open findings — all six are decisions

| ID | What | Whose |
|---|---|---|
| **F-52-open** | A CONFIRM step inside a multi-step plan is a dead end: the planner cancels the pending confirmation and asks for an authorisation nobody can give. `agent_yield` already solves the shape (park → notify → "approve task ab12cd34"), but wiring it changes what happens to a plan mid-flight | **his** |
| **F-56** | Row `5.7` expects a sandboxed directory listing to work; governance makes `run_terminal_command` BLOCK and refuses it. Both defensible, both cannot be true | **his** |
| **F-57** | "text Priya" routes to `send_whatsapp_message` (BLOCK) instead of the partner allowlist, so A23's actual subject — the thing that must hold before Group C's real sends — has never been exercised | **his** |
| **F-59** | `should_use_agent` is narrower than the §6.8 arc it gates | **his** |
| **F-68** | `LLM_PROVIDER_VISION=gemini` lives in the Render dashboard and **not** in `render.yaml`, which declares vision as `groq`. A Blueprint re-apply flips the photo provider with no diff to explain it. Declare it, or drop the override and accept `groq` — the middle-leg measurement now supports either | **his** |
| **F-70** | The live gateway reports `gemini_keys: 1` and all three capabilities routed to Gemini, and it had already hit `429` on 2026-08-27. Row 0.2 counted the DESK's five keys; Render holds one. Whether the desk's other keys help depends on whether they are separate Google **projects** — the 20/day quota is one bucket per project, so copying them across may change nothing | **his** |

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

## 7 · Resume point — start here

**Stamped 2026-08-30, evening.** Read the incident first; everything else can wait.

```powershell
cd F:\work\JARVIS-Project
git log --oneline -1
cd jarvis-backend
venv\Scripts\python.exe run_harnesses.py   # expect 117/117, 4032 checks, 0 failed
```

### 🔴 START HERE: he was locked out of his own desk and pulled the power

2026-08-30, away from home. The desk soft-locked, he could not get back in from
Telegram **or** from the lock screen, and he shut the machine off at the case.
**Four independent defects lined up** — F-83, F-84, F-85, F-86 in
`LIVE_GATE_FINDINGS.md`. All four are fixed and pinned by `test_soft_lock_exit.py`
(17 checks), and **none of them has been proved live**, because the desk was off
when they were written and each needs the daemon running with a camera.

**The first hardware session must gate these four before anything else:**

| Check | How |
|---|---|
| motion alone cannot arm the lock | cover the camera / point it at an empty room, wait past `JARVIS_LOCK_AFTER` (`.env` pins 120 s) — it must **NOT** lock |
| a real absence still locks | sit in frame until recognised, then leave — it **must** lock, or the feature is gone |
| "turn off the soft lock" unlocks | say it, and send it over Telegram. It must reach `unlock_desk`, **never** `os_control lock_screen` |
| the admin override opens the overlay | type `JARVIS_ADMIN_OVERRIDE_CODE` blind at the lock screen + Enter |

Until that session, know that **the desk can still lock itself while he is away.**
The remote unlock now exists, which is the door that was missing. If it is ever
needed in a hurry: `JARVIS_AUTO_LOCK=0` in `.env` disarms auto-lock at boot.

### The lesson, written down because it is mine

I told him that afternoon: *"the presence probe is disabled and the gesture
soft-lock needs the camera daemon, which isn't running."* True **when I checked
it** — and I then restarted the desk to leave it healthy, which started the G3
daemon and armed the very thing I had just called inert. I never re-checked.

**A statement about live state has a shelf life, and mine expired the moment I
changed that state myself.** He acted on a false all-clear.

### Where the goals stand

* **Goal 1, "He never claims what he did not do" — CLOSED** (§4.5). Nine A11 rows
  verified against Gmail, Calendar and Fit by `tools/verify_a11.py`. Re-run it
  monthly: `venv\Scripts\python.exe toolserify_a11.py`.
* **Goal 2, "He is up before you are, and stays up" — one row short** (§4.6).
  Everything but `18.5`, which needs a camera. Three findings, one of them goal
  1's own defect living in the HUD (F-81).
* **Next**: the hardware session above. It clears `18.5`, the four lock rows,
  `0.5`+`3.1`, and row `0.1`'s spoken override phrase in one sitting.

### What is still his

1. **`GEMINI_API_KEY`** — the legacy singular key in `.env` is `400
   API_KEY_INVALID`. Delete it.
2. **F-70** — are the four Gemini keys four separate Google **projects**? The
   20/day quota is per project.
3. **F-68** — declare `LLM_PROVIDER_VISION` in `render.yaml`, or drop the
   dashboard override.
4. **`WATCHDOG_TOKEN=`** is empty in `.env`, so the shutdown token is regenerated
   every boot and only printed to the log.
5. **Row 0.1** — say the override phrase into the microphone once.


### If the next session is a CODE session — NOT the other repo, as of 2026-08-29

**Read §0.5 first: the app REPO is frozen, gateway work is not.**

The desk ladder has **nothing machine-only left**, which is why sessions had been
going to `jarvis-mobile`'s `docs/brain-dependencies.md` — that laptop has no
Python at all (`python`/`python3` are the Microsoft Store stub), so this machine
is the only place any of it is testable. It was 9 queue items; **13, 24, 25, 22
and 12's gateway half are now written.** Four remain:

* **still open, gateway work** — `6` (the transcribe prompt overcorrects on plain
  English), `14` (the situation on the persona envelope), `15`'s gateway half (a
  per-message id; `delivered` and `read` are things only the gateway can say);
* **parked with the app repo** — `11` (the notification listener), plus the app
  halves of `15` and `12`.

The gate rows below outrank all of it: the goals in §0 are what "100%" is measured
against, and every one of them is a desk row.

**25 and 22 landed on 2026-08-29 and NEITHER IS LIVE.** Both are gateway commits
on a branch Render has not taken, and 25's other half is an app commit that has
not been published. What they are owed is a deploy and an OTA publish, then the
device repro that found each — not more code:

* **25** (`2301ad7` here, `6d8be2d` in jarvis-mobile) — the transcript `emit()`
  result is read rather than discarded, and a transcript that met a dead socket
  rides on the reply's push as `data.transcript`. The app writes his turn from
  that field, because a transcript pushed as its own notification would be filed
  as the machine speaking. **Both halves had to land together:** an unknown field
  on a push is dropped in silence, so the gateway change alone would have looked
  like it did nothing. `test_app_link.py` 42 → 46.
* **22** — the briefing's wording rotates now, ported from the phone's
  `briefingVoice.ts` line for line: a pool per slot, one cursor shared across
  departures, figures never varied, the actionable word in every variant. The
  cursor is spent only on a briefing that actually went out — `_push_all` returns
  whether anything left, and a push that reached nobody leaves the day OPEN
  instead of marking it done, which is the rule the failed-forecast path already
  had. `test_commute_briefing.py` 15 → 26.

**Read `/health` after the deploy that carries these**, and then look at the
`briefing_voice` row surviving the one after it — a cursor a deploy resets
restarts the rotation at the same line every time, which is indistinguishable
from never having rotated.

### The security goal, taken end to end — 2026-08-29

`jarvis-mobile`'s ledger groups its rows by goal, and **"a compromised token does
not expose a life"** had four open. Three are closed in code and the fourth is
unblocked; none of them is proved, and every one of them is a deploy away from
being provable.

| Row | What changed | What is owed |
|---|---|---|
| `token-split` | `POST /app-tokens` derives one short-lived token per capability from `APP_TOKEN` — `link`, `push`, `state`, `memory`, `say`. `j1.<cap>.<exp>.<mac>`, the mac keyed by the master itself, so **nothing is stored**, any instance verifies any token, and **rotating the master revokes every derived one at once**. Minting is master-only, so a leaked token cannot renew itself | a deploy and an OTA publish, then `/health.app_auth.master_calls` going quiet |
| `token-expiry` | every derived token expires (`APP_TOKEN_TTL_DAYS`, 30, declared in `render.yaml`). `401 token_expired` is typed so the phone re-mints and retries **once**; `403 wrong_capability` is the other case, and is not retried | the same deploy |
| `desk-key` | `has_desk_key: false` was the ordinary state, not a broken desk: the key lived on a disk every deploy throws away, and `queue_fact` DROPS what it cannot seal — eighteen turns in a week. The desk's public half and the sealed queue mirror into `gateway_state` now, injected as a hook so `fact_outbox` keeps its stdlib-only discipline. Ciphertext and one public key are all that cross | the deploy, the desk on once, then `has_desk_key` still true after the deploy AFTER that |
| `bridge-secret` | not rotated — that is his — but the ordering problem that deferred it for weeks is gone. `BRIDGE_SECRET_OLD` is accepted for one window, every connect on it is logged and counted at `/health.bridge_rotation`, so the leaked value is deleted on evidence rather than on a guess | his four steps, written out in `CLOUD_GATEWAY.md` |

**The master still opens every route, on purpose.** His installed app presents
it, and an auth change that locks him out of his own assistant to prove a point
about tokens would be worse than the leak it prevents. The migration is a number
on `/health`, not a hope.

**Live, 2026-08-29, on the deploy that carried it (`445c3a9`).** `/health` read
back `app_auth` with all five capabilities, `fact_outbox.durable: true`, and
`commute` + `push_targets` intact across the deploy for the second time running.
The desk bridge was then connected for twelve seconds by
`tools/bridge_handshake_check.py` — which answers no commands on purpose, since
the cloud believes the desk is up while it is attached — and the cloud went
`has_desk_key: false` -> `true` with the real desk's public half.

**And the deploy AFTER that one proved the point.** `fded484` is a new
container with the desk not connected, and it read `has_desk_key: true` — out of
Postgres, where nothing had ever been able to put it before. Every deploy until
today returned that flag to false, and `queue_fact` DROPS what it cannot seal.

Two operational facts fell out of doing this, both worth keeping:

* **a docs-only commit does not redeploy the gateway.** The Blueprint sets
  `rootDir: jarvis-backend`, so a push touching only root files was still on the
  old commit twelve minutes later. If a deploy is what you are testing, the
  commit has to touch that folder.
* **the desk's `.env` cannot open `/app-tokens`.** `APP_TOKEN` is unset there and
  `BRIDGE_SECRET` is refused, which is the right answer: the phone's pairing
  secret lives in the Render dashboard and in SecureStore, and the desk has no
  business holding it. It also means the live token-split proof needs his phone,
  not this machine.

**And `/health` now names the commit it is running** (`RENDER_GIT_COMMIT`,
short-form). Every rule above is a claim about the code that is *running*, and
until this field existed "read /health after every deploy" could confirm the
service was up but never that it was the service you just pushed — a fix
deployed, a symptom unchanged, and no way to tell which of the two did not
happen.

**Its line numbers are stale** — written from a checkout 42 commits behind. Verified
here on 2026-08-29: item 25's transcript `emit()` is at `cloud_gateway.py:3979` and
`deliver()`, the pattern to copy, is at `:3817`. Item 25 also has an app half in
`src/lib/notify.ts`; `replyFromData` returns `{text, at}` and nothing else, so a new
push field is dropped in silence and the gateway change looks like it did nothing.
**Land both halves together.**

### For a hardware session

Set the two secrets, launch with `JARVIS_AUTO_LOCK=0`, and **capture stdout to a
file**. That one habit has opened findings in four consecutive sessions without a
line of code being touched.

```powershell
venv\Scripts\python.exe watchdog.py *> gate-session-5.log
```

**And read `/health` after every deploy.** Both of 2026-08-29's live findings came
from one HTTP GET, and no harness can reach a deployed service:
`test_boot_preflight.py` has 104 checks about Gemini keys and every one reads the
DESK's environment, because that is where a harness runs.

```powershell
curl.exe https://jarvis-cloud-gateway.onrender.com/health
```

### Four things that will not be obvious from the code

* **The agent has a door.** Say *"work through this: …"* or *"figure out …"* and the
  request goes through the 56-tool agent loop. Anything else routes exactly as it
  always did. **Every A22 row needs that prefix.**
* **Vision has three legs**, Gemini then Groq then local llava. A vision answer
  taking 90 seconds means the first two failed and llava is loading under memory
  pressure — designed, not a hang.
* **An ambiguous patch is refused, and that is correct.** A search string matching
  more than once writes nothing. Prefix the path with `*all*` to change every
  occurrence, or send a longer string — `def add(`, not `add`. The refusal now says
  so out loud, and the CONFIRM read-back states scope before he authorises it.
* **The cloud refuses to store a fact that is only true for a while.** "currently",
  "today", "right now" and *"he asked about X"* are rejected at the sink when the
  MODEL proposes them. Typing one into the Memory screen still works — that door
  passes `source="operator"` on purpose, and the harness pins the asymmetry.

---

## 8 · The dashboard

`tracker.html` — open it in a browser for the same state as a page. **It opens on
the goals in §0**, each with its members and a completion bar, then the ladder, the
sealed evidence, the open loops, the gate batches, open findings and the ship gates.

Goals first is deliberate: a tier and a batch describe how the work is *organised*,
which is not the question somebody opens a dashboard to ask. The generator
**refuses to build** unless every ladder item and every gate batch belongs to
exactly one goal — because a row belonging to none vanishes from the view while
still counting in the totals, so the page's percentage and the page's list disagree
and it reads as complete *because* something is missing from it. That is
unobservable from the page, so the build is where it has to be caught. All three
refusals — an orphan, a member in two goals, a typo'd member — are driven in
`test_tracker_html.py` against a temp tree, not merely asserted.

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
