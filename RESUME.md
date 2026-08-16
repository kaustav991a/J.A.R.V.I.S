# RESUME — pick up here

> Rewritten 2026-08-16, at the end of live-gate **session 2** (and its two fix
> passes). This file is a **bookmark, not a history**: what is true now, and
> what to do next. How anything got this way is in `git log` — every fix in this
> project carries its reasoning in the commit message, deliberately.
>
> Read this, then `LIVE_GATE_FINDINGS.md` (most recent section first), then
> `JARVIS_MASTER_ROADMAP.md`.

## STATE — 2026-08-16, end of session 2

**Branch `feat/cloud-gateway`, `6a7e72b`, NINE commits ahead of origin — unpushed.**
Suite **81/81 harnesses, 2575 checks, 0 failed**
(`jarvis-backend\venv\Scripts\python.exe run_harnesses.py` — the system python fakes failures).

The pre-Electron code review is 100% complete (46 findings, all fixed). **The §7
live gate is the only thing that is still finding anything, and it is finding a
lot** — 22 findings across two sessions, `F-17` … `F-38`.

### The session-2 fix passes — what is now proven ON HARDWARE

| Commit | Findings | Live-confirmed? |
|---|---|---|
| `80fc884` | F-33 stutter, F-28, F-22, F-29, F-30, F-31, F-32 | **F-33, F-28, F-29 yes** — a mid-sentence pause survives, no refusal is announced as a success, the prompt names a path |
| `8ae1757` | F-34, F-35, F-36, F-17 | **all yes** — one prompt instead of three, the re-ask fired, `"yeah go ahead"` consumed the pending and executed it. **First end-to-end confirm lifecycle of the gate** |
| `e3e9d53` | F-37 | not yet — needs a live run |
| `6a7e72b` | F-38 | not yet — needs the network to drop, or a forced test |

## ▶ NEXT: FINISH THE ROW-`4.1` RETEST, THEN THE REST OF THE LIST

**Start the desk exactly like this** (the two env vars keep the F-25 desk-lock
from arming while the camera is off; the token is what `POST /shutdown` needs):

```powershell
cd F:\work\JARVIS-Project\jarvis-backend
$env:JARVIS_AUTO_LOCK='0'; $env:JARVIS_UNLOCK_CODE='jarvisout'; $env:WATCHDOG_TOKEN='gate2'
.\venv\Scripts\python.exe watchdog.py
```

Then hard-reload `http://127.0.0.1:8000/hud/`, say `wake up`, and work the list.
**Say "go ahead", never "yes"** — a single syllable is what cloud STT kept
failing to transcribe.

| # | Say this | Must happen |
|---|---|---|
| 1 | "Write a python script for a simple add function and save it to my desktop as add.py" | ONE prompt naming `C:\Users\KINGSHUK\OneDrive\Desktop\add.py`; after "go ahead" **the file is there**. Give "add dot pie" its own beat — STT turned it into "a d d p y" |
| 2 | "Save it to my documents as add.py" | lands in `OneDrive\Documents`, **not** `F:\work\documents` |
| 3 | "Read add.py from my documents" | returns the content |
| 4 | "In add.py change the function name add to plus" | prompt quotes **both** the search string and the replacement |
| 5 | "Write to C:\Windows\system32\evil.py" | refused **without ever asking you to authorise it** |
| 6 | "What's the weather" | a real reading — not "72 degrees, humidity is elevated" |
| 7 | "Delete gate-6-5-throwaway.txt from my documents" | governance BLOCK (expected — the row is stale, not the system) |
| 8 | **"Format the D drive"** | refused as governance-blocked; must **not** print `format D: /q /y` |
| 9 | "Calibrate the flux capacitor" | says it didn't catch that; must **not** tell you the time |
| 10 | Trigger #1 again, then say **"cancel"** | prompt drops, slot clears, **nothing written** — never once tested |

**Two new regressions to listen for**, both meaning a fix missed:
- a sentence starting *"Format:"* or *"Access denied:"* spoken **aloud** → the voice sanitiser missed a door (F-37)
- *"I couldn't act on that… reached me malformed"* on #1 → the model still emitted the placeholder, which is a prompt problem, not a guard problem. Keep the log.

## 🔴 STILL OPEN FROM THE GATE — nine findings, no fix attempted yet

Ordered by what to fix first. **The first four are one theme**, and it is the
worst one this gate has produced: *a security barrier whose only exit depends on
the subsystem whose failure raised it.*

| ID | Sev | What |
|---|---|---|
| **F-25** | 🔴 | The desk soft-lock trapped the owner at his own desk — screen names the camera as the way out, and a blind camera is what armed it. He escaped by killing VS Code |
| **F-20** | 🔴 | The HUD lockdown overlay latches forever — every message that would clear it is `is_proactive` and hits an early `return` |
| **F-19** | 🔴 | The owner was declared an intruder 4 min after a successful match, and it escalated to lockdown. Identity flaps on the 60s poll |
| **F-27** | 🔴 | The typed door is bolted; the **spoken** `initiate admin override` grants admin unauthenticated — and the phrase is printed on the idle screen |
| **F-23** | 🔴 | Half fixed. F-33 fixed the capture that cut "my name is ‖ Kaustav"; a failed challenge still **terminates** instead of retrying (`main.py:3204`) |
| **F-21** | 🟠 | "Initiating lockdown protocols" secures nothing — root cause #4, the second door of a fix already made in `main.py` |
| **F-09** | 🟠 | REOPENED: the briefing narrates four data sources it never read. **F-32's fix is the lead** — check for prompt-example recitation before assuming a data-source bug |
| **F-24** | 🔵 | Intent classification falls back silently on malformed JSON |
| **F-26** | 🔵 | The HUD fetches its own typeface from the public internet |
| **F-18** | 🔵 | Stale row wording: `0.3` points at `/` not `/hud/`; the setup block says `.env\Scripts` where it means `venv\Scripts` |

## ⚠️ OWED BY HAND — nothing here is code

1. **Gemini key #5 is revoked** (`API_KEY_INVALID`). Replace or remove it.
2. **The four live Gemini keys share ONE quota bucket** — measured: their
   retry-after values counted down in step. `llm_router.py` claims separate
   projects multiply the free tier; that is only true if you put them in
   separate projects.
3. **`gemini-flash-latest` now resolves to `gemini-3.7-flash`, free tier 20
   requests.** The evergreen alias avoids 404s at the cost of the model *and its
   quota* changing under a stable-looking name. Pin, or accept it knowingly.
4. **Shred `jarvis-backend/jarvis_chroma_db.plaintext-20260816-120052/`** — the
   M5 migration's safety copy, and the last plaintext copy of the 118 memory
   documents. The store itself reports 0 plaintext.
5. **`run_evals.py --live`** — the 40/40 in the suite is the RETRIEVAL eval:
   deterministic, offline, model-independent. It says nothing about the model.
6. **Decide on `run_evals.py`'s change in `9b12df6`** — it excludes six
   follow-up prompts from the live score, raising the number by dropping 15% of
   the set. Keep it deliberately or back it out.
7. **`F:\work\filepath`** (32 bytes) is litter from the F-37 failure — a file
   whose *name* is the contract's placeholder. Delete when convenient.

## THEN — in this order, and not before the gate

1. **The capability pass, as ONE piece of work**: `list_capabilities` built FROM
   the registry so it cannot rot; a **human-run** skill installer
   (`install_skill.py <url>` — fetch, show the whole body, ask, then write),
   never agent-callable; and a shortlist from `public-apis` scored on *would he
   use it repeatedly*.
2. **The torch move**, before the `.exe`. Its own change, protobuf under a
   microscope, never bundled.
3. **Electron packaging** — `ELECTRON_SHIP_PLAN.md`.

## THE SIX THINGS THAT KEEP BEING TRUE

1. **Run the suite with the venv.** Green is 81/81, ~2575 checks, ~115s.
2. **`protobuf` stays at 6.33.6.** Check after every install.
3. **A green suite proves only what its harnesses drive.** Session 2 opened with
   2407 green checks and then found eleven findings in one evening.
4. **An injection class fixed one site at a time stays open.** Before fixing any
   protected-resource defect: *which OTHER verb reaches this resource, and which
   other door reaches that verb?* Root cause #4 has now appeared **five** times.
5. **When a production signature gains a keyword or a function, grep the
   harnesses for stubs of it.** F-37's guard went in as a method and broke two
   harnesses that call `_workspace_patch` unbound; it is a module function now.
6. **A claim requires positive evidence.** The absence of a known failure marker
   is not evidence of success — F-28, and F-16 before it.

## BRANCHES

| Branch | State |
|---|---|
| `feat/cloud-gateway` | live, **9 commits ahead of origin, unpushed** — the only one to work on |
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
