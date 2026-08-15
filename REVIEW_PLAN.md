# Finishing the pre-Electron code review — the batched plan

> Written 2026-08-16 after a 10-agent sweep burned ~38% of a session quota and
> returned **nothing** (all ten were still running when it had to be stopped; the
> journal holds ten `started` entries and no results).
> **Read this before launching any review workflow.** Delete it when the review is done.

## Why the first attempt failed, and it was not the agent count

Two separate cost mistakes:

1. **Reading files into the MAIN context is quadratic.** Every turn re-sends the whole
   transcript, so 22 000 lines read inline is paid for again on every later turn. A
   subagent has its own context and returns ~1–2k tokens of findings — all ten areas
   cost the main loop ~15k instead of ~300k. *Subagents are the right shape.*
2. **Ten unbounded agents in flight at once, plus a verifier per finding.** Nothing had
   landed when the budget ran out, so nothing survived. The verify pass would have
   doubled the cost again.

## The rules for the next run

- **Batches of 2–3 areas**, never all ten. A batch that completes is a batch that is kept.
- **Write results to `review-findings.json` after EVERY batch.** The previous run lost
  everything because it was all in flight. Nothing is "finished" until it is on disk.
- **No separate verify agents.** The fixer re-reads the code to write the fix, and that
  IS the verification. A finding that does not survive first contact is dropped then.
- **Bound each agent**: hunt the five root causes in priority order; do not "read the
  file in full" as an end in itself.
- **Fix in a second pass**, highest severity first, so a quota that runs short cuts the
  least important work rather than a random slice.

## Areas, in the order they should run

Ordered by blast radius — what executes on the real machine first, what is only
displayed last.

| # | Area | Lines | Why here |
|---|---|---|---|
| 1 | `action-engine` | 2 572 | executes every action on the real machine |
| 2 | `main-api` | 3 043 | unauthenticated `/api/*`, incl. the route that approves governance |
| 3 | `io-agents` (web/terminal/workspace/file) | 1 181 | disk and network on the model's behalf |
| 4 | `agent-support` (search/skills/files/schema/errors/tool_calls) | 1 465 | path validation, preconditions, provider normalisation |
| 5 | `agent-runner` (runner/worker/subagents/yield/metrics) | 1 402 | parked approvals, sub-agent authorizers |
| 6 | `memory` (manager/memory/crypto/fact_seal/fact_outbox) | 2 015 | SQL, encryption-at-rest, attacker-adjacent sealed facts |
| 7 | `comms` (telegram/partner/owner_notify/presence/contact_events) | 1 770 | reaches real people; a false send is the worst class here |
| 8 | `brain` | 2 490 | prompt construction, truthfulness guards, delimiters |
| 9 | `perception` (gesture/cursor/daemon/ambient/vision) | 1 746 | resource leaks, degradation across sessions |
| 10 | `frontend` | 4 240 | no shell, no filesystem — lowest risk, goes last |

**Total ~21 900 lines.** Already reviewed and NOT to be re-read: `agent_core.py`,
`agent_confirm.py`, `agent_tools.py`, `modules/url_safety.py`, `agent_runner.py`'s
authorizers and its `run_agent_loop` call, and the specific sites of findings 1–18.

## The five root causes — this is what the agents hunt

Every one of the 18 findings so far is one of these. They are ordered by how often
they have actually produced a defect in this codebase.

- **A. A model-supplied string reaches a SINK** — a shell, a filesystem path, a URL,
  an SQL query, an ADB command, a network call. *8 of 18 findings.* Governance approves
  an action by TYPE and never inspects the ARGUMENT, and since §6.8 the argument can
  come from a web page, an indexed document or an MCP reply.
- **B. A CLAIM made without the action having happened** — a success string on a path
  that did nothing, a sentinel handed back as data, a "Done, Sir" where the effect
  needed a frame nobody sent. JARVIS lying about itself is top severity here.
- **C. A GATE not wired on every path, or a blocklist that cannot be complete.** Ask of
  every check: *which callers reach the sink WITHOUT passing it?*
- **D. STRUCTURE encoded in a character the content may contain** — pipe, colon and
  comma delimiters in a target string built from model output.
- **E. A LEAK or a corrupt-state crash** — a camera, thread, subprocess or handle not
  released on the error path; persisted state that kills startup instead of reading as
  absent.

## The question to ask before every fix

Findings 6, 10, 14, 17 and 18 are **one road found five times**: writing the key files,
then reading them through a URL, then through every other spelling of localhost, then
through the one-shot door, then through a file send.

> **An injection class fixed one site at a time stays open.** Before fixing a
> protected-resource defect, ask: *which OTHER verb reaches this resource, and which
> other door reaches that verb?*

And fix it at the **sink**, not only at the door — `modules/shell_safety.py` and
`modules/url_safety.py` are what that looks like.
