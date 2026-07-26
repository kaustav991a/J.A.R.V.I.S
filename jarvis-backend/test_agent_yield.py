"""Harness for modules/agent_yield.py — agentic core phase 5, the away yield.

Fake queue, fake notifier: no sqlite, no Telegram. What matters here is that a
CONFIRM-tier action the owner cannot be asked about becomes something he CAN
answer later, and that nothing about it reads as done:

  * the parked task carries the EXACT action payload, not a paraphrase;
  * it is marked reported immediately, so the worker's report sweeper doesn't
    buzz his phone a second time about the same action;
  * the message names the phrase that resumes it;
  * a failed notification still leaves the task parked (losing the work because
    Telegram was down would be the worst outcome);
  * the reason handed to the model says NOT DONE.
"""

import asyncio
import sys

from modules import agent_yield as ay


def run(coro):
    return asyncio.run(coro)


class FakeQueue:
    """Enough of modules/task_queue to exercise the yield, with a call log."""

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self._n = 0

    def enqueue(self, title, actions, user="KAUSTAV"):
        self._n += 1
        tid = f"{self._n:012x}"
        self.tasks[tid] = {"id": tid, "title": title, "actions": actions,
                           "user": user, "status": "pending", "reported": 0}
        self.calls.append(("enqueue", tid))
        return tid

    def mark_needs_confirmation(self, tid, note):
        self.tasks[tid]["status"] = "needs_confirmation"
        self.tasks[tid]["result"] = note
        self.calls.append(("mark_needs_confirmation", tid))

    def mark_reported(self, tid):
        self.tasks[tid]["reported"] = 1
        self.calls.append(("mark_reported", tid))

    def find_awaiting_confirmation(self, prefix=None):
        return [t for t in self.tasks.values()
                if t["status"] == "needs_confirmation"
                and (not prefix or t["id"].startswith(prefix))]

    def approve_task(self, tid):
        t = self.tasks.get(tid)
        if not t or t["status"] != "needs_confirmation":
            return False
        t["status"], t["approved"] = "pending", 1
        self.calls.append(("approve_task", tid))
        return True

    def cancel(self, tid):
        self.tasks[tid]["status"] = "cancelled"
        self.calls.append(("cancel", tid))
        return True


class FakeNotifier:
    def __init__(self, report=None, boom=False):
        self.report = report or {"hud": True, "tts": False, "phone": True}
        self.boom = boom
        self.sent: list[tuple[str, dict]] = []

    async def __call__(self, message, **kwargs):
        if self.boom:
            raise RuntimeError("telegram is down")
        self.sent.append((message, kwargs))
        return self.report


PAYLOAD = {"action_type": "workspace_write", "target": "notes.py|print('hi')"}


def park(queue=None, notify=None, payload=None, goal="write my notes file"):
    queue = queue or FakeQueue()
    notify = notify or FakeNotifier()
    parked = run(ay.park_for_approval(payload or PAYLOAD, goal=goal,
                                     question="JARVIS wants to run workspace_write "
                                              "on notes.py — approve?",
                                     queue=queue, notify=notify))
    return parked, queue, notify


# ---- parking --------------------------------------------------------------- #

def test_the_parked_task_carries_the_exact_payload():
    """An approval must re-run the model's actual call, not a reconstruction."""
    parked, queue, _ = park()
    assert queue.tasks[parked.id]["actions"] == [PAYLOAD]


def test_the_task_lands_in_the_needs_confirmation_lane():
    parked, queue, _ = park()
    assert queue.tasks[parked.id]["status"] == "needs_confirmation"
    assert queue.find_awaiting_confirmation(parked.short) == [queue.tasks[parked.id]]


def test_it_is_marked_reported_so_the_phone_is_not_buzzed_twice():
    parked, queue, _ = park()
    assert queue.tasks[parked.id]["reported"] == 1
    assert [c[0] for c in queue.calls] == [
        "enqueue", "mark_needs_confirmation", "mark_reported"]


def test_the_owner_is_told_the_phrase_that_resumes_it():
    parked, _, notify = park()
    message, kwargs = notify.sent[0]
    assert f"approve task {parked.short}" in message
    assert f"deny task {parked.short}" in message
    assert kwargs.get("phone") is True, "the whole point is that he is elsewhere"


def test_the_short_id_is_what_the_approval_regex_accepts():
    parked, _, _ = park()
    assert ay.parse_approval(f"approve task {parked.short}") == ("approve", parked.short)


def test_a_failed_notification_still_leaves_the_task_parked():
    """Telegram being down must not lose the work — he can still ask for it."""
    queue = FakeQueue()
    parked, queue, notify = park(queue=queue, notify=FakeNotifier(boom=True))
    assert queue.tasks[parked.id]["status"] == "needs_confirmation"
    assert parked.delivered == {}


def test_the_title_names_the_action_and_the_goal():
    parked, _, _ = park()
    assert parked.title.startswith("workspace_write — ")
    assert "write my notes file" in parked.title


def test_the_refusal_reason_says_not_done():
    """Parked reported as a tool result would be narrated as a success."""
    parked, _, _ = park()
    reason = ay.refusal_reason(parked, "workspace_write")
    assert "NOT DONE" in reason and parked.short in reason
    assert "do not claim it happened" in reason.lower()


# ---- the resume side ------------------------------------------------------- #

def test_parse_approval_accepts_every_documented_verb():
    for verb in ("approve", "resume", "authorise", "authorize", "deny", "reject",
                 "drop", "cancel"):
        assert ay.parse_approval(f"{verb} task ab12cd34") == (verb, "ab12cd34")


def test_parse_approval_ignores_everything_else():
    for text in ("", "approve", "task ab12", "approve the task ab12cd34",
                 "what's in my latest file", "approve task zzz"):
        assert ay.parse_approval(text) is None, text


def test_approval_resumes_the_task():
    parked, queue, _ = park()
    said = run(ay.apply_approval("approve", parked.short, queue=queue))
    assert queue.tasks[parked.id]["status"] == "pending"
    assert queue.tasks[parked.id]["approved"] == 1
    assert "Authorised" in said and parked.title in said


def test_denial_drops_the_task():
    parked, queue, _ = park()
    said = run(ay.apply_approval("deny", parked.short, queue=queue))
    assert queue.tasks[parked.id]["status"] == "cancelled"
    assert "Dropped" in said


def test_an_unknown_id_is_refused_not_guessed():
    _, queue, _ = park()
    said = run(ay.apply_approval("approve", "deadbeef", queue=queue))
    assert "No task awaiting authorisation" in said


def test_an_ambiguous_prefix_asks_for_more_of_the_id():
    """Approving the wrong write because two ids share a prefix is unacceptable."""
    queue = FakeQueue()
    park(queue=queue)
    park(queue=queue)
    said = run(ay.apply_approval("approve", "0", queue=queue))
    assert "several waiting tasks" in said
    assert all(t["status"] == "needs_confirmation" for t in queue.tasks.values()), \
        "an ambiguous approval must approve NOTHING"


def test_approving_something_already_gone_is_honest():
    parked, queue, _ = park()
    queue.approve_task = lambda tid: False   # cancelled between the ping and the reply
    said = run(ay.apply_approval("approve", parked.short, queue=queue))
    assert "couldn't resume" in said


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
