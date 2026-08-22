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
| Automatic suite | **98 harnesses, 3246 checks, 0 failed** | `jarvis-backend\venv\Scripts\python.exe run_harnesses.py` — the system python fakes failures |
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

---

## 2 · The ladder

Four tiers, in dependency order. Do not start a tier before the one above it, and
the reason is empirical: session 4 spent a third of itself debugging bugs that
Tier 1 work would have caught before a row was ever attempted.

### Tier 0 · Stop the bleeding

| | Item | Status |
|---|---|---|
| 0.1 | Set `JARVIS_ADMIN_OVERRIDE_CODE` in `.env` — the spoken recovery path F-23 and F-25 need does not exist without it | ☐ **his, 2 minutes** |
| 0.2 | Rotate `GEMINI_API_KEY` — measured invalid twice (`400 API key not valid`) | ☐ **his** |
| 0.3 | Boot preflight that asks providers whether the configured models still exist | ✅ **done** — 11 ids checked, catalogues only, zero tokens, `JARVIS_MODEL_PREFLIGHT=0` to disable |
| 0.4 | ollama auto-starting as a service — it was down all of session 4, so every vision feature was dead and nothing said so. Running now, but started by hand | ☐ |
| 0.5 | Re-enroll the face on the **phone-camera angles actually used** — until then every camera feature mistrusts him and F-62 recurs | ☐ **needs him + the phone** |

### Tier 1 · Make the two habits fail the suite

Session 4 raised 16 findings. **15 of them were two habits.** Fixing instances is
losing; the habits have to become suite failures.

| | Item | Status |
|---|---|---|
| 1.1 | **The claims layer.** Audited first: the taxonomy was NOT scattered — `brain.py` already owned four strippers coherently, so consolidation would have been churn. The real gap was **coverage**, and one uncovered class. Closed: the F-60 capability rule (both the promise form and the request form), a coverage **inventory** of every LLM-text function with the guard it carries *or a written decision that it carries none*, and a scan for invisible control bytes | ✅ **done** — `test_claims_guard.py`, 91 checks |
| 1.2 | **`test_single_source.py`** — root cause #4 asked mechanically, 7 pins | ✅ **done** — found F-66 and F-67 on its first run |

### Tier 2 · Competence — the 56%

The eval localises it precisely. **Fix retrieval before building anything.**

| Group | Live score | |
|---|---|---|
| tv | 5/5 | ✅ |
| files, mail, hud, media | 2/2, 2/2, 1/1, 1/1 | ✅ |
| git | 2/3 | |
| apps | 2/4 | |
| memory, partner, system | 1/2 each | |
| **web** | **1/4** | 🔴 |
| **calendar** | **0/3** | 🔴 |
| **misc** | **0/3** | 🔴 |

The 15 misses share one shape: **the shelf offers `search_documents`/`find_file`
for calendar, vitals and web goals.** That is retrieval — descriptions, aliases,
ranking in the tool registry — not model capability.

| | Item | Status |
|---|---|---|
| 2.1 | Fix tool descriptions/aliases/ranking; re-measure with `run_evals.py` offline **and** `--live`. Target **≥85%** | ☐ |
| 2.2 | Settle **F-59** — `should_use_agent` accepts two sentence shapes while A22 has 24 rows written against goals it will not accept. Widen the gate or rewrite the rows; until then A22 cannot validate any of this | ☐ **decision** |
| 2.3 | Only if retrieval tops out and it is still wrong: the tiered brain (a stronger model for tool selection) | ☐ blocked by 2.1 |

### Tier 3 · Unattended reliability

| | Item | Status |
|---|---|---|
| 3.1 | Run all 192 rows once and fix what they find — **4–6 sessions**, most needing him at the desk | ☐ see §3 |
| 3.2 | A RAM budget. 16 GB, ~6 free; `llava` alone loads 4.41 GB. Vision and reasoning cannot both be resident | ☐ |
| 3.3 | **7-day unattended soak** — no false intruder alert, no fabricated claim in the logs, no silent config rot. Nothing in this project currently proves *sustained* reliability, and that is what "rely on it" means | ☐ |

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
