# The pre-Electron code review — what it found, and how

> Written 2026-08-16, when the review finished. Replaces `REVIEW_PLAN.md`, which
> was the working plan and said to delete itself when the review was done.
>
> **The findings themselves are in `review-findings.json`** — all 46, each with
> what was reachable, what failed, and what was changed. This file is the part
> that outlives them: the shapes defects took, and the questions that found them.

## What was covered

**All of it.** ~17,700 lines of backend across nine areas, and 4,677 lines of
frontend. Twelve batches, 46 findings, every one fixed and harnessed. The suite
went from 65 harnesses / 1,673 checks to 80 / 2,407.

Reviewing was deliberately brought FORWARD, ahead of the §7 live gate, reversing
the planned order. The reason is the gate's own rule: **a row only passes against
the tree it passed on.** Reviewing after the gate means every fix invalidates
rows already paid for with a desk day — session 1 lost 7 rows exactly that way.

## The five root causes

Every one of the 46 findings is one of these. Ordered by how often they actually
produced a defect here.

**A. A model-supplied string reaches a SINK** — a shell, a filesystem path, a
URL, an SQL query, an ADB command, a network call. Governance approves an action
by TYPE and never inspects the ARGUMENT, and since §6.8 the argument can come
from a web page, an indexed document, a photo, or an MCP reply.

**B. A CLAIM made without the action having happened** — a success string on a
path that did nothing, a sentinel handed back as data, "Committed to memory" on
a write that failed. **JARVIS lying about himself is the top severity here**, and
it produced the most instructive finding of the review: a reply that dispatched
nothing wrote the stub that F-16's guard reads as PROOF an action ran.

**C. A GATE not wired on every path, or a blocklist that cannot be complete.**
Ask of every check: *which callers reach the sink WITHOUT passing it?*

**D. STRUCTURE encoded in a character the content may contain** — a pipe, a
colon, a comma, a newline. A memory's newlines became extra lines of the system
prompt; so did a skill's description; a photo's description broke out of the
brackets meant to contain it.

**E. A LEAK or a corrupt-state crash** — a camera, thread, subprocess, media
stream or handle not released on the error path; persisted state that kills
startup instead of reading as absent; a daemon loop with no exception guard.

## The question to ask before every fix

Findings 6, 10, 14, 17, 18, R11, S1 and S3 are **one road found eight times**:
writing the key files, then reading them through a URL, then through every other
spelling of localhost, then through the one-shot door, then through a file send,
then through the GUI Save As dialog, then through a delete, then through the
playbooks.

> **An injection class fixed one site at a time stays open.** Before fixing any
> protected-resource defect, ask: *which OTHER verb reaches this resource, and
> which other door reaches that verb?*

And fix it at the **SINK**, not only at the door. `modules/shell_safety.py`,
`modules/url_safety.py` and `modules/protected_paths.py` are what that looks
like — one place each caller must pass through, rather than a check repeated at
every call site and forgotten at the ninth.

## Four things the review taught that are not findings

**A test can pin a defect as thoroughly as code can.** Two assertions in
`test_conversational_truthfulness.py` asserted that the empty-action stub COUNTS
as evidence — codifying the bug as correct behaviour. Passing tests are not
proof the contract is right.

**Counting occurrences is not reading code.** A count-based grep over the
frontend reported six uncleaned timers; all were false positives — one component
collected its timers in an array and cleared them, another's hit was the word
`setTimeout` inside a comment. A structural test written the same way matched its
own explanatory comment.

**A green count that does not move is the most convincing way to not notice.** A
harness gained three tests and reported the identical number as before, because
it drove a hand-written list. `test_harness_integrity.py` now fails the suite if
a test is ever defined and not run.

**When a production signature gains a keyword, grep the harnesses for stubs of
it.** Three stale stubs surfaced during the review, each failing the CALL rather
than the assertion — which is a much more confusing way to find out.

## What "reviewed" does not mean

It does not mean bug-free, and it never could.

F-08, F-09, F-11, F-15 and F-16 were all found by **using** JARVIS, not by
reading him. A line-by-line read finds the five structural classes above. It
cannot find timing bugs, integration mismatches between two components that are
each correct, or anything that only appears against real hardware. And the 46
fixes are themselves 46 pieces of new code that have never met reality.

**What the review actually bought is honest failure.** Look at what most of the
fixes have in common: the cloud outbox keeps the fact instead of deleting it; the
drain holds the record instead of acking it; the vision daemon stands down loudly
instead of dying quiet; a half-delivered message says "part of it reached her"
instead of "nothing was sent"; the claim-strippers refuse to report work that did
not happen.

The old failures were all one shape: **it looked fine and it wasn't.** That is
the shape that is gone.

## Where the remaining risk is

The §7 live gate — `LIVE_GATE_CHECKLIST.md`. It finds the class a review cannot,
and five of its rows exist specifically because of fixes made here that no
harness can prove: the microphone hand-off on a HUD reload, the camera daemon
standing down, the group-chat gate, the photo fence, and an approval that must
not be triggerable by a keystroke meant for a text box.
