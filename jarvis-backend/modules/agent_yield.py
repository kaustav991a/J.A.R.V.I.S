"""agent_yield.py — what the agent loop does with a CONFIRM it cannot ask about.

Agentic core, phase 5 (roadmap §5 Tier C #12). Phase 4 gave the loop a desk-side
confirmation: it asks the HUD and waits. But when the owner is NOT at the desk
there is nobody to ask, and phase 4 answered that honestly — and uselessly:

    "'workspace_write' is CONFIRM-tier and this run is unattended"

…which tells him nothing he can act on from his phone. This module is the missing
half: the action is PARKED as a durable queued task, the owner is pinged wherever
he is (`owner_notify` → Telegram/cloud bridge), and one sentence back —
"approve task ab12cd34" — resumes it.

WHY THE TASK QUEUE AND NOT THE GOVERNANCE SLOT
----------------------------------------------
`governance_manager`'s pending-confirmation slot is a SINGLETON that a new
command supersedes, and it lives in memory. An away yield has to survive an
unbounded wait (he may answer in the morning) and a restart, and it must not be
cancelled by the next unrelated thing he says to JARVIS. `jarvis_tasks.db`
already has exactly those semantics plus a resume path that predates this module:
`mark_needs_confirmation` → `find_awaiting_confirmation` → `approve_task` →
`OvernightWorker` re-runs it with `governance_bypass=True`. So the yield writes
into that lane rather than inventing a second one.

Two details that matter more than they look:

* **`mark_reported` immediately.** The worker's report sweeper announces every
  unreported `needs_confirmation` task. We are notifying the owner right here, in
  this function, so leaving it unreported buzzes his phone twice about one action.
* **Parking is not approval.** The tool did NOT run. The loop is told the call was
  refused (with the task id in the reason), because a parked action reported as a
  tool result reads to the model as success, and it would go on to narrate a file
  it never wrote.

Everything external is injected (`queue`, `notify`, `clock`), so the whole yield
is harnessed with no database, no Telegram and no engine.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Callable

#: The phrase the owner is told to say, and the one that resumes the task. Kept
#: identical to the worker's own wording (worker_loop._announce) so there is ONE
#: sentence to learn, whichever subsystem parked the work.
APPROVAL_RE = re.compile(
    r"^(approve|resume|authorise|authorize|deny|reject|drop|cancel)\s+task\s+"
    r"([0-9a-f]{4,12})\b")
APPROVE_VERBS = frozenset({"approve", "resume", "authorise", "authorize"})


@dataclass
class ParkedTask:
    """One CONFIRM-tier action waiting on the owner, wherever he is."""

    id: str
    title: str
    message: str
    delivered: dict = field(default_factory=dict)

    @property
    def short(self) -> str:
        return self.id[:8]


def _title_for(payload: dict, goal: str) -> str:
    atype = str(payload.get("action_type") or "action")
    goal = (goal or "").strip()
    return f"{atype} — {goal}"[:200] if goal else atype


async def park_for_approval(payload: dict, *, goal: str, question: str = "",
                            queue: Any | None = None,
                            notify: Callable[..., Any] | None = None,
                            user: str = "KAUSTAV") -> ParkedTask:
    """Persist one action for later authorisation and tell the owner about it.

    `payload` is an `action_engine` payload — the same `{"action_type","target"}`
    shape the worker executes — so an approval runs the exact call the model made,
    not a paraphrase of it.
    """
    if queue is None:
        from modules import task_queue as queue
    if notify is None:
        from modules.owner_notify import notify_owner as notify

    title = _title_for(payload, goal)
    tid = await asyncio.to_thread(queue.enqueue, title, [dict(payload)], user)
    note = (question or "This step needs your authorisation.") + \
        " Parked by the agent loop — the owner was not at the desk."
    await asyncio.to_thread(queue.mark_needs_confirmation, tid, note)
    # We are the ones notifying, right now — don't let the worker's report sweeper
    # buzz his phone a second time about the same action.
    await asyncio.to_thread(queue.mark_reported, tid)

    short = tid[:8]
    message = (
        f"I need your authorisation to finish that, Sir. {question or note} "
        f"Say 'approve task {short}' and I'll run it, or 'deny task {short}' to "
        f"drop it."
    )
    delivered: dict = {}
    try:
        # phone=True explicitly: the whole point is that he is elsewhere, and
        # presence routing could still be mid-probe.
        delivered = await notify(message, phone=True) or {}
    except Exception as e:  # noqa: BLE001 — a failed ping must not lose the task
        print(f"[AGENT_YIELD] notify failed for task {short}: {e}", flush=True)
    print(f"[AGENT_YIELD] Parked '{title}' as task {short} "
          f"(delivered: {[k for k, v in delivered.items() if v] or 'NOTHING'})",
          flush=True)
    return ParkedTask(id=tid, title=title, message=message, delivered=delivered)


def refusal_reason(parked: ParkedTask, action_type: str) -> str:
    """What the LOOP is told. Must read as a refusal, not as a completion."""
    return (f"'{action_type}' needs the owner's authorisation and he is not at the "
            f"desk. NOT DONE — I have parked it as task {parked.short} and asked "
            f"him to approve it. Do not retry it and do not claim it happened.")


# --- the resume side ------------------------------------------------------- #

def parse_approval(text: str) -> tuple[str, str] | None:
    """`("approve", "ab12cd34")` for an approval phrase, else None."""
    if not text:
        return None
    m = APPROVAL_RE.match(text.strip().lower())
    if not m:
        return None
    return m.group(1), m.group(2)


async def apply_approval(verb: str, prefix: str, *, queue: Any | None = None,
                         honor: str = "Sir") -> str:
    """Resolve "approve/deny task <id>" and return the sentence to say back.

    The remote (Telegram) path in main.py has had this since Phase 4 item 5; this
    is the same behaviour available to any caller, so the phrase now also works at
    the desk — where the owner ends up after his phone told him to say it.
    """
    if queue is None:
        from modules import task_queue as queue
    matches = await asyncio.to_thread(queue.find_awaiting_confirmation, prefix)
    if not matches:
        return f"No task awaiting authorisation matches '{prefix}', {honor}."
    if len(matches) > 1:
        ids = ", ".join(m["id"][:8] for m in matches)
        return (f"That matches several waiting tasks ({ids}) — give me more of the "
                f"id, {honor}.")
    task = matches[0]
    if verb in APPROVE_VERBS:
        ok = await asyncio.to_thread(queue.approve_task, task["id"])
        if ok:
            return (f"Authorised, {honor} — resuming '{task['title']}' in the "
                    f"background. I'll report when it's done.")
        return (f"I couldn't resume that task, {honor} — it may have been "
                f"cancelled already.")
    await asyncio.to_thread(queue.cancel, task["id"])
    return f"Dropped, {honor} — '{task['title']}' will not run."
