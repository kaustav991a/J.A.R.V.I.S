# LIVE-GATE FINDINGS — session 1

> Desk session **2026-08-08, 21:58 → 23:00**. Worked from `LIVE_GATE_CHECKLIST.md`.
> **Session ended deliberately to fix findings before continuing** — the remaining rows resume
> in session 2 against a repaired tree.
>
> Rules this session ran under: diagnose, don't fix-and-commit. One change was made (F-06,
> requested live) and is recorded below.

## Score

| | |
|---|---|
| Owed rows (total) | **192** |
| Attempted | **15** |
| ✅ Passed | **12** |
| ❌ Failed | **1** |
| ⏸ Blocked | **1** |
| Not yet attempted | **177** |
| **Findings** | **13** — 3 high, 4 medium, 6 low (+1 withdrawn) |

**The session was worth it before it got far.** 15 rows surfaced 3 high-severity bugs, all of
the same family: **a failure the user cannot distinguish from normal operation.** A dropped
answer, an invented action, and an alert that cannot fire.

---

# 1 · ROW RESULTS

## ✅ Passed — 12

| Row | What it proves | Evidence |
|---|---|---|
| `0.1` | Watchdog boots, uvicorn comes up clean | 2nd attempt — 1st used system python (see F-02) |
| `0.2` | Governance + daemons + remote gateway start | `[GOVERNANCE] Ruleset loaded - 104 action types indexed`, `[ROUTINES]`, `[OVERWATCH]`, `[BRIDGE] ✅ Linked to cloud front door`. Row wording stale — see **F-05** |
| `0.3` | HUD renders, `/ws` connects, no app errors | Only console errors were the expected `403` and a Chrome-extension artifact |
| `0.4` | `.env` correct | `cloud_first`, numeric `TELEGRAM_USER_ID`, token set |
| `0.5` | Watchdog control endpoint alive | `watchdog: alive` |
| `1.3` | **Rapid-crash breaker** — backs off instead of spinning | Observed under a real induced fault: 5 crashes/60 s → 30 s backoff ×2 → give-up at strike 3/3 |
| `2.1` | Wake word | `[STT] Heard: 'wake up'` → `[BOOT SEQUENCE INITIATED]` |
| `2.2` | Admin override / boot sequence | briefing sequence ran |
| `2.3` | **Face ID recognises the owner** (owner half) | `[VISION] ✅ MATCH: KAUSTAV` against the freshly enrolled 12-sample set |
| `2.4` | STT accuracy | transcribed correctly (2 empty VAD triggers first — normal) |
| `16.1` | **Guided re-enroll, 12 samples** | 3rd attempt clean: `min=0.77 avg=0.85 max=0.95`, no outlier warning. The quality gate rejected two sets and passed one — it works |
| `17.6` 🛑 | **Backdoor refuses while locked, flag OFF** | `403 Forbidden` + `{"status":"refused","reason":"locked","message":"Biometric authorisation required, Sir…","flag":"JARVIS_ALLOW_BACKDOOR"}` |

> `2.3`'s **reject half** (unknown face → not admin) is untested — it needs a second person and
> belongs to Group C.

## ❌ Failed — 1

| Row | Why |
|---|---|
| `10.9` **Morning briefing** | Briefing fires and is well-formed prose, but **confabulates a destructive action it claims to have performed** (**F-09**) and is mis-framed as a morning briefing at 22:41 (**F-10**) |

## ⏸ Blocked — 1

| Row | Blocked by |
|---|---|
| `21.3` shared camera | **F-08** — the gesture daemon dies every ~2 min *without* a scan running, so the row's premise ("does the scan kill it?") can't be isolated |

## ↩ Not run — 1

| Row | Why |
|---|---|
| `17.7` backdoor after real auth | Was believed blocked by F-12; **F-12 is withdrawn** (assistant tooling, not JARVIS). Not blocked — needs a plain re-run in an authenticated session |

---

# 2 · FINDINGS

## 🔴 HIGH

### F-09 — the briefing claimed it deleted calendar items

**What happened.** Unprompted, inside the wake briefing:

> *"I did note, however, that you had instructed me to delete certain items and clear your
> schedule for the day. **I have taken care of this task, as per your request.**"*

No such instruction was given.

**Verified nothing ran.** `grep 'action == "calendar_delete"' action_engine.py` → no match, consistent
with roadmap §6.8.2 listing `calendar_create/modify/delete` among the 16 actions with no dispatch
branch. The sentence is narration, not a report of execution.

**Why it matters.** It is a confident, unsolicited claim to have taken a **destructive action on
the user's data**, in the one output that speaks with full authority every day. The user cannot
tell it from a true one. This is the exact failure the whole gate exists to catch, in the place
it is least likely to be questioned.

**Cause.** `[BRAIN] Active model: llama-3.1-8b-instant` composes the briefing.
`brain.py:2129 generate_briefing(...)`. The 8B model was already recorded as the one that
invents; this is that, in production.

**Fix shape.** The briefing composer must not be free-form over a summary. Either constrain it
to render only fields it was handed, or post-check the generated text for claims of completed
actions and strip/regenerate. A model that can say "I have taken care of this" about work it
never did should not be the last stage before speech.

**Harness owner.** New. Feed a briefing payload containing *no* action results and assert the
output contains no completed-action claim. Checkable without a live model if the composer is
made deterministic over its inputs.

---

### F-08 — the gesture camera dies every ~2 minutes, and never recovers its reader

**What happened.** `[GESTURE] session fault: camera stream died (30 consecutive read failures)`
**five times** in ~25 minutes, on a ~2 min cadence, each preceded by `[mjpeg @ …] overread 8`.
Twice **before** any face scan ran.

**The stream is not the problem — measured.** A 20 s pull from the phone returned
**15,804,273 bytes / 548 JPEG SOI markers ≈ 27 fps, no disconnect.**

**Root cause.** `modules/gesture_camera.py:232–247`:

```python
fails = 0
while not self._stop.is_set():
    ok, frame = self._cap.read()
    if not ok:
        fails += 1
        if fails > 30:
            self._dead = "camera stream died (30 consecutive read failures)"
            return          # thread exits permanently — never reopens
        time.sleep(0.05)
        continue
    fails = 0
```

**Two defects, not one:**

1. **The tolerance is a count, not a duration.** 30 × 50 ms ≈ **1.55 s**. One corrupt JPEG
   (`overread 8`) makes OpenCV's ffmpeg decoder return `ok=False` while it resyncs to the next
   SOI marker, which on a 27 fps MJPEG stream routinely exceeds 1.55 s.
2. **Death is terminal.** The reader thread `return`s. `read_new` then raises
   `CameraError("dead")` and the daemon tears down the **entire session** — releases the capture,
   re-runs auto-select, re-initialises MediaPipe — for what is a recoverable decoder desync.

**⚠️ Do not close this as the already-fixed bug.** Roadmap §7 records "30 read failures" as
fixed — that was the **scan-contention** cause. This is a **second, independent cause of the
same signature**, and the 2026-07-26 fix does not touch it.

**Blast radius: 34 rows.** A18–A21 (gesture, overlay, click/grab, camera sharing) all assume the
daemon survives minutes. `20.7` alone asks for a 60-second clean run.

**Fix shape.** Make the tolerance time-based (e.g. no successful read for 5 s), and have the
reader **reopen the capture** rather than die. Escalate to session teardown only if reopening
fails repeatedly.

**Harness owner.** `test_gesture_camera.py` (46 checks). Drive a fake capture that returns
`ok=False` for a bounded window and assert the reader survives and resumes.

---

### F-13 — a Bengali-script reply is unspeakable, so the answer is silently dropped

**What happened.** Asked how he was, JARVIS replied:

```
[JARVIS] আমি ভালো, মিঃ কাউষ্টব. আপনার কি হচ্ছে?
[SPEAKER WARNING] Segment skipped (unsynthesizable): ', আমি ভালো, মিঃ কাউষ্টব...'
```

**Nothing was spoken.** Not a style violation — a **lost answer**.

**The fix already exists and was never applied to the desk path.** Commit `4fb0821`
*"fix(cloud): force romanised reply when input is Bengali script"* is on this branch but
`git show --stat` shows it touched **one file: `cloud_gateway.py`, +23 lines**. Its own message
diagnoses this exact failure:

> *"the model mirrors that script and ignores the Latin-letters rule buried in the persona.
> Detect Bengali/Devanagari codepoints … inject a fresh high-priority SCRIPT OVERRIDE system turn
> right before the user message (recency beats a rule 300 lines up)."*

**Side by side:**

| | `brain.py` (desk) | `cloud_gateway.py` |
|---|---|---|
| Latin-letters persona rule | ✅ lines 63–74 | ✅ lines 162–173 |
| Codepoint detector (U+0980–09FF, U+0900–097F) | ❌ **absent** | ✅ line 381 |
| `SCRIPT OVERRIDE (highest priority)` injected before the user turn | ❌ **absent** | ✅ lines 391–393 |

The desk payload measured **~13,130 chars / ~3,282 tokens**, so the rule sat ~3,200 tokens above
the user's message — precisely the "300 lines up" condition the commit was written to defeat.

**Why the severity is high.** The coupling: wrong script → unsynthesizable → `speak_text`
swallows it *by design* → silence. The swallow is correct behaviour that cannot distinguish this
bug from having nothing to say.

**Fix shape.** Port `cloud_gateway.py:381` (detector) and `:391–393` (override text) into
`brain.py`, injected at the same position, in both the think and see paths. Proven code, one
file, no new behaviour.

**Harness owner.** New. Feed Bengali-script input, assert the reply contains **zero** codepoints
in U+0980–09FF. Fully checkable without a live model if the override injection is tested
structurally.

---

## ⬜ WITHDRAWN

### ~~F-12 — `/api/backdoor` does not return after authentication~~ — **NOT A BUG. Tooling artifact.**

Two POSTs from the assistant's shell timed out at 20 s and 60 s (`HTTP 000`) while the backend was
healthy and the voice path answered normally. It looked like the authenticated dispatch path
hanging, and it was written up as blocking 26 agentic rows.

**It was the assistant's sandbox mangling POST bodies.** The same request run by Kaustav from
PowerShell returned in **0.0015 s**. GET worked from the sandbox throughout; only POST hung, and
the assistant's requests never appeared in the uvicorn access log at all — the tell that they
never reached the handler.

**Kept rather than deleted, because the diagnostic tell is worth reusing:** if a request produces
no access-log line, it did not arrive; suspect the caller before the server. `17.7` is not
blocked — it needs a plain re-run.

The PowerShell form that works (backslash-escaped quotes inside a single-quoted PS string reach
curl literally and yield a `422 json_invalid`):

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/backdoor -Method Post `
  -ContentType 'application/json' -Body '{"command":"system status"}'
```

### Also checked and NOT a finding

A `403 {"reason":"locked"}` seen in the browser console *after* a successful face scan looked like
a lost authenticated session. It was **scrollback** — the React console had not been cleared since
the pre-wake attempt. No session was lost.

---

## 🟠 MEDIUM

### F-03 — a missing dotenv silently disables the "JARVIS is dead" alert

`watchdog.py:65–69`:

```python
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
```

Observed live: under an interpreter without `python-dotenv`, the watchdog ran with **no `.env` at
all**, and its give-up alert reported *"No TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID — owner alert not
sent"* while `.env` contained both. The one signal that says the server is unrecoverable fails
**silently and misleadingly**.

**Fix shape.** Don't swallow. If `dotenv` is absent, say so loudly at boot — the watchdog has no
business running blind about its own config.
**Harness owner.** `test_watchdog_policy.py` (14 checks).

### F-11 — a HUD reload starts a second voice loop

Every voice line doubled (`Loading Silero VAD` ×2, `[VAD] Speech detected` ×2, `[STT] Heard:
'wake up'` ×2, `[BOOT SEQUENCE INITIATED]` ×2), plus
`Critical System Error: Cannot call "send" once a close message has been sent.`

**Cause.** The mic/wake loop lives inside `websocket_endpoint` (`main.py:2435`), so each WS
connection starts its own and the old one is not torn down on close. Two loops means the wake
word can boot twice and a stale loop writes to a closed socket.

**Fix shape.** Own the voice loop at application scope, not per-connection; or cancel it in the
endpoint's teardown.
**Harness owner.** New — assert one loop survives N connect/disconnect cycles.

### F-10 — "Comprehensive **Morning** Briefing" at 22:41

`[BRAIN] New day detected -> delivering Comprehensive Morning Briefing` at 22:41, whose text opens
*"Good evening, Sir. As you begin your evening…"* and closes by offering *"Shall I proceed with
your morning briefing…?"* — after delivering one. The greeting logic knows the hour; the briefing
selector doesn't.
**Harness owner.** Whatever owns `generate_briefing` — assert selection is a function of the hour.

### F-07 — `enroll_face.py` can't follow the camera

`enroll_face.py:181`:

```python
_src = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("JARVIS_CAM", "0")).strip()
```

Reads a **single** `JARVIS_CAM`; the backend reads the **`JARVIS_CAM_SOURCES` ladder** with
auto-select. Observed live: the backend auto-selected `.106` while enroll hard-failed on `.105`.
The one tool that re-seeds biometric identity is the only consumer that can't follow a DHCP move —
a move roadmap §7 records happening twice unprompted.

**Fix shape.** Resolve through the same `gesture_camera` source list the daemon uses; keep
`argv[1]` as an explicit override.
**Harness owner.** `test_enroll_face.py` (17 checks).

---

## 🔵 LOW

| ID | What | Where |
|---|---|---|
| **F-01** | Checklist names the calibration backup as `jarvis-backend/gesture_calibration.json`; it is **`models/gesture_calibration.json`** (`modules/gesture_calibration.py:25`) | `LIVE_GATE_CHECKLIST.md` |
| **F-04** | Row `0.1` says `python watchdog.py`; must pin the venv interpreter. `sys.executable` propagates to uvicorn, so the wrong interpreter takes the whole server down | `TEST_PLAN.md` §0 |
| **F-05** | Row `0.2` expects `[TELEGRAM] ✅ Gateway online`, which **cannot** appear when the cloud bridge is on — `main.py:837–847` starts one or the other, never both. Should read "one of Telegram gateway **or** cloud bridge linked" | `TEST_PLAN.md` §0 |
| **F-06** | `ambient_vision_daemon.start()` was unconditional while every comparable daemon had an opt-out. Flag added this session (see Tree state). **It did not achieve its stated purpose** — RAM was 86.3% with ambient on, 86.9% with it off. Keep as a gap-fill; the RAM question needs per-process measurement, which is backlog item 3's "measure first" | `main.py:770` |
| **F-14** | `temp_speech_2b2369_0.mp3` left untracked in the repo root — TTS temp files are neither cleaned up nor gitignored | TTS path |
| **F-02** | *(not a bug — recorded so it isn't rediscovered)* First boot used system python; no uvicorn, no dotenv. Cause of the F-03 observation | — |

---

# 3 · FIX CHECKLIST — ordered for the deliberate pass

Grouped so related work lands together. **Each fix gets the harness named beside it** — a live
gate proving something once is not the same as a property being pinned.

### Group 1 — silent failures (do first; these lie to the user)

- [ ] **F-13** port the SCRIPT OVERRIDE from `cloud_gateway.py:381,391–393` into `brain.py`
      → new harness: Bengali input ⇒ zero U+0980–09FF in the reply
- [ ] **F-09** constrain the briefing composer so it cannot claim completed actions
      → new harness: empty action results ⇒ no completed-action claim in the text
- [ ] **F-03** stop swallowing a missing `dotenv` in `watchdog.py:65–69`
      → `test_watchdog_policy.py`

### Group 2 — reliability blockers (unblock the most rows)

- [ ] **F-08** time-based tolerance + reopen instead of die, `gesture_camera.py:232–247`
      → `test_gesture_camera.py` — **unblocks 34 rows**

### Group 3 — correctness

- [ ] **F-11** voice loop out of `websocket_endpoint` (`main.py:2435`)
      → new harness: one loop across N connect/disconnect cycles
- [ ] **F-10** briefing selection as a function of the hour
- [ ] **F-07** `enroll_face.py:181` resolves via the `JARVIS_CAM_SOURCES` ladder
      → `test_enroll_face.py`

### Group 4 — docs

- [ ] **F-01** fix the calibration path in `LIVE_GATE_CHECKLIST.md`
- [ ] **F-04** pin the venv interpreter in `TEST_PLAN.md` `0.1`
- [ ] **F-05** rewrite `TEST_PLAN.md` `0.2`'s gateway expectation
- [ ] Also still owed from the earlier audit: `TEST_PLAN.md` PART A table is stale (787 checks /
      34 harnesses; actual 1405 / 59) and still says "add its filename to `HARNESSES`"

### Group 5 — housekeeping

- [ ] **F-06** commit the ambient flag with an honest message (it is a gap-fill, **not** a RAM fix)
- [ ] **F-14** clean up TTS temp files and/or gitignore them

---

# 4 · RE-RUN LIST — rows that must be re-gated after the fixes

A passed row is only valid against the tree it passed on.

| Fix | Invalidates |
|---|---|
| F-03 (`watchdog.py`) | `0.1`, `0.2`, `1.3` |
| F-11 (`main.py` voice loop) | `2.1`, `2.2`, `2.4` |
| F-07 (`enroll_face.py`) | `16.1` |
| F-13, F-09, F-10, F-08 | nothing already passed |

**7 rows to re-run.** The other 5 passes (`0.3`, `0.4`, `0.5`, `2.3`, `17.6`) stand.

---

# 5 · STILL UN-GATED — 177 rows, and what each needs

| Blocker | Rows | What it needs |
|---|---|---|
| **F-08** camera stability | ~34 (A18–A21) | the fix above. A phone-side quality reduction was attempted and not verified before shutdown |
| **Second device** | 7 (§22 + `23b.22`) | a second phone/tablet/laptop with a **pinned non-random MAC** on the home SSID, so the probe device can leave WiFi while the camera keeps feeding. **Not yet obtained** |
| **Second person** | 15 (Group C) | any face ×2, a 2nd Telegram account ×1, **Kinshuk** ×1, **Mousumi** ×11. **Not yet scheduled** |
| Nothing — just time | ~95 | A4–A11, A14–A17, A23, A24 + Group D |

**Note for session 2:** the untested bulk is not blocked on anything. A5 (workspace I/O, incl. the
🛑 `4.4` sandbox row) and A7 (governance, incl. the 🛑 `6.5` BLOCK row) were queued and not
reached. **Run the remaining stop-the-line rows early** — `4.4`, `6.5`, `24.5`/`24.6` — because
`6.5` gates whether the partner-messaging rows are safe to run at all.

---

# 6 · TREE STATE

| | |
|---|---|
| Baseline at session start | **`10ff55f`**, clean |
| Uncommitted at session end | **`main.py`** — +13/−1, the F-06 ambient flag |
| Suite | **59/59 harnesses, 1405 checks, 0 failed** — re-run *with* the edit in place |
| Untracked | `temp_speech_2b2369_0.mp3` (F-14), `jarvis-frontend/public/favicon.zip` (pre-existing) |

**Data changed on disk this session:** `models/owner_embeddings.npz` was **re-seeded** by `16.1`
(1-sample seed → 12-sample set). Backup of the original:
`F:\work\JARVIS-BACKUPS\pre-livegate-20260808\`. Google OAuth token was refreshed mid-boot after
an `invalid_grant`.

**Environment restored:** `JARVIS_AMBIENT_VISION` was set to `0` for one boot and should be
**unset** in session 2 — it saved no RAM and disables intruder detection (`12.2`).
