"""Harness for modules/agent_core.py — the async decide→act→observe loop.

A scripted fake model, a fake clock and fake tools: no keys, no network, no side
effects. Two things are really being guarded here:

  * the loop is HONEST — it stops when it can't finish and says so, and never
    reports success it didn't earn;
  * the loop never holds the engine lock across an await it doesn't have to,
    because the AT_DESK confirm (a later phase) parks inside `authorize` waiting
    for a human, and that must not freeze the rest of JARVIS.
"""

import asyncio
import sys

from modules import agent_core as ac
from modules.tool_calls import ToolCall, ToolTurn

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "delete_file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
]


def run(coro):
    """Drive one loop run to completion."""
    return asyncio.run(coro)


def call(name="read_file", args=None, cid="c1", err=None, raw=None):
    return ToolCall(id=cid, name=name, arguments=args if args is not None else {},
                    arguments_error=err, raw_arguments=raw)


def turn_with(*calls):
    return ToolTurn(ok=True, tool_calls=list(calls), provider="fake")


def answer(text="All done, Sir."):
    return ToolTurn(ok=True, text=text, provider="fake")


class Script:
    """Returns the next scripted turn per model call, recording what it saw."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen_messages = []

    def __call__(self, messages, tools, **kw):
        self.seen_messages.append(list(messages))
        return self.turns.pop(0) if self.turns else answer("(script exhausted)")


class Clock:
    def __init__(self, t=0.0, step=0.0):
        self.t, self.step = t, step

    def __call__(self):
        self.t += self.step
        return self.t


# ---- the happy path ------------------------------------------------------ #

def test_tool_then_answer():
    ran = []
    script = Script(turn_with(call(args={"path": "a.txt"})), answer("It says hello."))
    res = run(ac.run_agent_loop("read a.txt", TOOLS,
                                lambda c: ran.append(c.name) or "hello",
                                call_model=script))
    assert res.ok and res.stop_reason == ac.ANSWERED
    assert res.answer == "It says hello."
    assert ran == ["read_file"] and res.steps == 2
    assert res.tool_runs[0].ok and res.tool_runs[0].output == "hello"


def test_async_tools_are_awaited():
    async def execute(c):
        await asyncio.sleep(0)
        return "async-result"

    script = Script(turn_with(call(args={"path": "a"})), answer())
    res = run(ac.run_agent_loop("x", TOOLS, execute, call_model=script))
    assert res.tool_runs[0].output == "async-result"


def test_sync_tools_run_off_the_event_loop():
    """A blocking OS tool must not stall the HUD, so it goes to a thread."""
    import threading
    seen = {}

    def blocking(c):
        seen["thread"] = threading.current_thread().name
        return "ok"

    async def main():
        seen["loop_thread"] = threading.current_thread().name
        return await ac.run_agent_loop("x", TOOLS, blocking,
                                       call_model=Script(turn_with(call()), answer()))

    run(main())
    assert seen["thread"] != seen["loop_thread"]


def test_tool_output_is_fed_back_to_the_model():
    script = Script(turn_with(call(args={"path": "a"})), answer())
    run(ac.run_agent_loop("go", TOOLS, lambda c: "FILE-CONTENTS", call_model=script))
    second = script.seen_messages[1]
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": "FILE-CONTENTS"}
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "c1"


def test_answer_without_any_tool_exits_cleanly():
    res = run(ac.run_agent_loop("hi", TOOLS, lambda c: None,
                                call_model=Script(answer("Hello."))))
    assert res.ok and res.steps == 1 and res.tool_runs == []
    assert res.stop_reason == ac.ANSWERED


def test_parallel_tool_calls_all_execute():
    ran = []
    script = Script(turn_with(call(cid="a", args={"path": "1"}),
                              call(cid="b", args={"path": "2"})), answer())
    res = run(ac.run_agent_loop("both", TOOLS, lambda c: ran.append(c.id) or "ok",
                                call_model=script))
    assert ran == ["a", "b"] and len(res.tool_runs) == 2 and res.ok


def test_system_prompt_and_message_list_goals():
    script = Script(answer())
    run(ac.run_agent_loop("do it", TOOLS, lambda c: None, system="You are JARVIS.",
                          call_model=script))
    assert script.seen_messages[0][0] == {"role": "system", "content": "You are JARVIS."}

    script2 = Script(answer())
    run(ac.run_agent_loop([{"role": "user", "content": "prior"},
                           {"role": "assistant", "content": "context"}], TOOLS,
                          lambda c: None, call_model=script2))
    assert len(script2.seen_messages[0]) == 2


def test_anthropic_dialect_tools_are_accepted():
    """The registry authors input_schema; the loop must not care."""
    tools = [{"name": "read_file", "description": "read",
              "input_schema": {"type": "object",
                               "properties": {"path": {"type": "string"}}}}]
    res = run(ac.run_agent_loop("x", tools, lambda c: "ok",
                                call_model=Script(turn_with(call()), answer("done"))))
    assert res.ok and res.tool_runs[0].ok


# ---- the lock: never held across a wait ---------------------------------- #

def test_lock_is_released_between_steps():
    """The loop may not sit on the engine lock while the model is thinking."""
    lock = asyncio.Lock()
    observed = []

    def execute(c):
        observed.append(("tool", lock.locked()))
        return "ok"

    def model(messages, tools, **kw):
        observed.append(("model", lock.locked()))
        return turn_with(call()) if len(observed) < 3 else answer("done")

    run(ac.run_agent_loop("x", TOOLS, execute, call_model=model, lock=lock))
    assert ("model", True) not in observed, "lock held during a model turn"
    assert ("tool", True) in observed, "lock not held during execution"
    assert not lock.locked()


def test_a_loop_waiting_on_a_human_does_not_block_other_commands():
    """The shape the AT_DESK confirm depends on.

    `authorize` parks on a Future exactly as the HUD confirm will. While it is
    parked, an unrelated command must still be able to take the engine lock and
    run. If the loop held the lock across that wait, this deadlocks.
    """
    lock = asyncio.Lock()
    approved = asyncio.Event()
    unrelated_ran = asyncio.Event()

    async def authorize(c):
        approved.set()                 # "HUD prompt shown"
        await asyncio.sleep(0.05)      # human thinking
        return ac.Decision(True, "approved at the desk")

    async def unrelated_command():
        await approved.wait()          # only start once the loop is parked
        async with lock:               # must NOT be blocked by the paused loop
            unrelated_ran.set()

    async def main():
        loop_task = asyncio.create_task(ac.run_agent_loop(
            "write it", TOOLS, lambda c: "written",
            authorize=authorize, lock=lock,
            call_model=Script(turn_with(call(name="delete_file",
                                             args={"path": "x"})), answer("done"))))
        other = asyncio.create_task(unrelated_command())
        res = await asyncio.wait_for(loop_task, timeout=2.0)
        await asyncio.wait_for(other, timeout=2.0)
        return res

    res = run(main())
    assert unrelated_ran.is_set(), "an unrelated command was blocked by the paused loop"
    assert res.ok


def test_the_event_loop_keeps_running_while_a_blocking_tool_executes():
    """A 50ms blocking tool must not stop the HUD heartbeat."""
    ticks = []

    def blocking(c):
        import time as _t
        _t.sleep(0.05)
        return "ok"

    async def heartbeat():
        for _ in range(5):
            await asyncio.sleep(0.01)
            ticks.append(1)

    async def main():
        hb = asyncio.create_task(heartbeat())
        res = await ac.run_agent_loop("x", TOOLS, blocking,
                                      call_model=Script(turn_with(call()), answer()))
        await hb
        return res

    run(main())
    assert len(ticks) == 5


# ---- caps: the run must STOP, and say why -------------------------------- #

def test_step_cap_fails_honestly():
    """A model that keeps calling tools forever must not run forever, and the
    result must not look like success."""
    forever = Script(*[turn_with(call(args={"path": "x"})) for _ in range(20)])
    res = run(ac.run_agent_loop("loop", TOOLS, lambda c: "ok", call_model=forever,
                                limits=ac.AgentLimits(max_steps=3)))
    assert res.ok is False and res.stop_reason == ac.MAX_STEPS
    assert res.steps == 3 and res.answer is None
    assert "3-step" in res.error
    assert "couldn't finish" in res.summary()


def test_wall_clock_cap():
    clock = Clock(step=10.0)      # 10s per check
    script = Script(*[turn_with(call(args={"path": "x"})) for _ in range(20)])
    res = run(ac.run_agent_loop("slow", TOOLS, lambda c: "ok", call_model=script,
                                limits=ac.AgentLimits(max_seconds=25.0), clock=clock))
    assert res.ok is False and res.stop_reason == ac.TIMEOUT


def test_too_many_tools_is_refused_not_trimmed():
    """Silently dropping tools would make the agent fail for an invisible reason."""
    many = [{"type": "function", "function": {"name": f"t{i}"}} for i in range(12)]
    called = []
    res = run(ac.run_agent_loop("x", many, lambda c: None,
                                call_model=lambda *a, **k: called.append(1) or answer()))
    assert res.ok is False and res.stop_reason == ac.BAD_REQUEST
    assert "limit is 8" in res.error and called == []


def test_invalid_tool_defs_never_reach_the_model():
    called = []
    res = run(ac.run_agent_loop("x", [{"type": "function"}], lambda c: None,
                                call_model=lambda *a, **k: called.append(1) or answer()))
    assert res.ok is False and res.stop_reason == ac.BAD_REQUEST and called == []


# ---- repair: one correction, then honest failure ------------------------- #

def test_malformed_arguments_get_one_repair():
    bad = call(err="invalid JSON arguments", raw='{"path": "a')
    script = Script(turn_with(bad), turn_with(call(args={"path": "a.txt"})),
                    answer("Fixed."))
    res = run(ac.run_agent_loop("read", TOOLS, lambda c: "contents", call_model=script))
    assert res.ok and res.answer == "Fixed."
    # The model was TOLD what was wrong rather than left to guess.
    repair_msg = script.seen_messages[1][-1]
    assert repair_msg["role"] == "tool" and "ERROR" in repair_msg["content"]


def test_second_bad_call_ends_the_run():
    bad = turn_with(call(err="invalid JSON arguments"))
    res = run(ac.run_agent_loop("read", TOOLS, lambda c: "x",
                                call_model=Script(bad, bad, bad)))
    assert res.ok is False and res.stop_reason == ac.BAD_REQUEST
    assert "kept producing invalid" in res.error


def test_unknown_tool_name_is_corrected_with_the_real_names():
    script = Script(turn_with(call(name="format_disk")),
                    turn_with(call(name="read_file", args={"path": "a"})), answer())
    res = run(ac.run_agent_loop("go", TOOLS, lambda c: "ok", call_model=script))
    msg = script.seen_messages[1][-1]["content"]
    assert "unknown tool 'format_disk'" in msg
    assert "read_file" in msg and "delete_file" in msg
    assert res.ok


# ---- governance: checked before EVERY execution -------------------------- #

def test_no_tool_call_ever_skips_governance():
    """Every executed call must have been authorised first, in order."""
    authorised, executed = [], []
    script = Script(turn_with(call(cid="1", args={"path": "a"}),
                              call(cid="2", args={"path": "b"})),
                    turn_with(call(cid="3", args={"path": "c"})), answer())

    def authorize(c):
        authorised.append(c.id)
        return ac.Decision(True)

    run(ac.run_agent_loop("x", TOOLS, lambda c: executed.append(c.id) or "ok",
                          authorize=authorize, call_model=script))
    assert authorised == ["1", "2", "3"] == executed


def test_denied_tool_is_never_executed():
    ran = []

    def authorize(c):
        return ac.Decision(c.name != "delete_file", "delete_file requires confirmation")

    script = Script(turn_with(call(name="delete_file", args={"path": "x"})),
                    turn_with(call(name="read_file", args={"path": "x"})),
                    answer("Read instead."))
    res = run(ac.run_agent_loop("delete x", TOOLS, lambda c: ran.append(c.name) or "ok",
                                authorize=authorize, call_model=script))
    assert "delete_file" not in ran
    assert res.ok and res.answer == "Read instead."
    denied = [r for r in res.tool_runs if r.denied]
    assert len(denied) == 1 and "confirmation" in denied[0].error
    assert "DENIED" in script.seen_messages[1][-1]["content"]


def test_repeated_denial_stops_the_run():
    deny = turn_with(call(name="delete_file", args={"path": "x"}))
    res = run(ac.run_agent_loop("delete", TOOLS, lambda c: "ok",
                                authorize=lambda c: ac.Decision(False, "not authorised"),
                                call_model=Script(deny, deny, deny)))
    assert res.ok is False and res.stop_reason == ac.DENIED
    assert "not authorised" in res.summary()


def test_async_authorize_is_supported():
    """The AT_DESK confirm will be async; a sync one must still work."""
    async def authorize(c):
        await asyncio.sleep(0)
        return ac.Decision(True, "ok")

    res = run(ac.run_agent_loop("x", TOOLS, lambda c: "ok", authorize=authorize,
                                call_model=Script(turn_with(call()), answer("done"))))
    assert res.ok


# ---- tool failures ------------------------------------------------------- #

def test_tool_error_is_reported_back_and_the_run_continues():
    """A missing file is information, not a crash — the model should adapt."""
    def execute(c):
        if c.arguments.get("path") == "missing.txt":
            raise FileNotFoundError("missing.txt")
        return "contents"

    script = Script(turn_with(call(args={"path": "missing.txt"})),
                    turn_with(call(cid="c2", args={"path": "real.txt"})),
                    answer("Found it in real.txt."))
    res = run(ac.run_agent_loop("read", TOOLS, execute, call_model=script))
    assert res.ok and res.answer == "Found it in real.txt."
    # Since §6.8.1 gap B the observation is an INSTRUCTION, not a Python
    # exception name — what the model needs is "it isn't there, go find it",
    # and `str(FileNotFoundError("missing.txt"))` is only "missing.txt".
    observation = script.seen_messages[1][-1]["content"]
    assert "File not found" in observation and "missing.txt" in observation, observation
    assert "do not read it again" in observation.lower(), observation
    assert [r.ok for r in res.tool_runs] == [False, True]


def test_honest_tool_failure_keeps_the_engines_own_wording():
    """Phase-2 discipline: when execute_with_retry says FAILED, the model should
    read exactly what the user would have heard, not a Python exception name."""
    def execute(c):
        raise ac.ToolFailure("I couldn't open Notepad, Sir — the window never appeared.")

    script = Script(turn_with(call()), answer("Understood."))
    res = run(ac.run_agent_loop("type", TOOLS, execute, call_model=script))
    observation = script.seen_messages[1][-1]["content"]
    # The engine's wording is kept VERBATIM and leads the message. Since §6.8.1
    # gap B a next-step line follows it, but nothing may rewrite the sentence
    # itself — and the Python class name still must not leak.
    assert observation.startswith("ERROR: I couldn't open Notepad, Sir — "
                                  "the window never appeared."), observation
    assert "ToolFailure" not in observation
    assert res.tool_runs[0].ok is False


def test_repeated_tool_failures_stop_the_run():
    def boom(c):
        raise RuntimeError("device on fire")

    t = turn_with(call(args={"path": "x"}))
    res = run(ac.run_agent_loop("read", TOOLS, boom, call_model=Script(t, t, t, t, t)))
    assert res.ok is False and res.stop_reason == ac.TOOL_ERRORS
    assert "3 tool failures in a row" in res.error


def test_a_success_resets_the_error_streak():
    calls = {"n": 0}

    def flaky(c):
        calls["n"] += 1
        if calls["n"] in (1, 2, 4):
            raise RuntimeError("flaky")
        return "ok"

    t = turn_with(call(args={"path": "x"}))
    res = run(ac.run_agent_loop("x", TOOLS, flaky,
                                call_model=Script(t, t, t, t, answer("Done."))))
    assert res.ok, "two failures, a success, then a failure must not trip the streak"


def test_long_tool_output_is_truncated_visibly():
    """A silently-cut result teaches the model the data ended where it didn't."""
    script = Script(turn_with(call(args={"path": "big"})), answer())
    res = run(ac.run_agent_loop("read", TOOLS, lambda c: "x" * 5000, call_model=script,
                                limits=ac.AgentLimits(max_tool_output_chars=100)))
    out = res.tool_runs[0].output
    assert len(out) < 200 and "truncated 4900 more characters" in out


def test_non_string_tool_output_is_serialised():
    script = Script(turn_with(call(args={"path": "a"})), answer())
    res = run(ac.run_agent_loop("x", TOOLS, lambda c: {"lines": 3}, call_model=script))
    assert res.tool_runs[0].output == '{"lines": 3}'


# ---- provider failure ---------------------------------------------------- #

def test_provider_failure_is_not_a_silent_success():
    res = run(ac.run_agent_loop("x", TOOLS, lambda c: "ok",
                                call_model=lambda *a, **k: ToolTurn.failed("all providers failed")))
    assert res.ok is False and res.stop_reason == ac.PROVIDER_FAILED
    assert res.answer is None
    assert "unreachable" in res.summary()


def test_none_turn_is_handled():
    res = run(ac.run_agent_loop("x", TOOLS, lambda c: "ok",
                                call_model=lambda *a, **k: None))
    assert res.ok is False and res.stop_reason == ac.PROVIDER_FAILED


# ---- observability ------------------------------------------------------- #

def test_events_are_emitted_for_the_hud():
    events = []
    script = Script(turn_with(call(args={"path": "a"})), answer("Done."))
    # Authorized on purpose. This asserts the event stream of an ORDINARY run,
    # and an ordinary run is governed — production has an authorizer on every
    # branch. Left ungoverned, it also picked up the `ungoverned` warning event,
    # which is a true statement about the run and a false one about the HUD.
    run(ac.run_agent_loop("x", TOOLS, lambda c: "ok", call_model=script,
                          authorize=lambda c: ac.Decision(True),
                          on_event=lambda k, d: events.append(k)))
    assert events == ["model_turn", "tool_start", "tool_ok", "model_turn", "answer"]


def test_async_event_hooks_are_awaited():
    events = []

    async def hook(kind, data):
        await asyncio.sleep(0)
        events.append(kind)

    run(ac.run_agent_loop("x", TOOLS, lambda c: "ok",
                          call_model=Script(answer("Fine.")), on_event=hook))
    assert events == ["model_turn", "answer"]


def test_a_broken_event_hook_cannot_break_the_run():
    def bad_hook(kind, data):
        raise RuntimeError("telemetry exploded")

    res = run(ac.run_agent_loop("x", TOOLS, lambda c: "ok",
                                call_model=Script(answer("Fine.")), on_event=bad_hook))
    assert res.ok and res.answer == "Fine."


# ---- compaction (phase 5) ------------------------------------------------- #

def transcript(steps: int, chars: int = 500) -> list:
    """system + goal, then `steps` complete assistant→tool groups."""
    msgs: list = [{"role": "system", "content": "sys"},
                  {"role": "user", "content": "the goal"}]
    for i in range(steps):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": f"tool_{i}", "arguments": '{"path": "a"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * chars})
    return msgs


def test_a_short_transcript_is_left_alone():
    msgs = transcript(2, 100)
    out, dropped, names = ac.compact_messages(msgs, 20000)
    assert out is msgs and dropped == 0 and names == []


def test_compaction_is_disabled_by_a_zero_budget():
    msgs = transcript(20, 500)
    out, dropped, _ = ac.compact_messages(msgs, 0)
    assert out is msgs and dropped == 0


def test_the_oldest_steps_go_first():
    msgs = transcript(6, 500)
    out, dropped, names = ac.compact_messages(msgs, 1600, keep_recent_groups=2)
    assert dropped >= 1
    assert names[0] == "tool_0", "compaction must drop the OLDEST step, not the newest"
    assert ac.transcript_chars(out) <= 1600


def test_the_goal_and_system_prompt_are_never_dropped():
    """Losing the goal turns a struggling run into a confidently wrong one."""
    msgs = transcript(8, 800)
    out, dropped, _ = ac.compact_messages(msgs, 900, keep_recent_groups=1)
    assert dropped == 7
    assert out[0]["role"] == "system" and out[1]["content"] == "the goal"


def test_no_tool_result_is_ever_orphaned():
    """A `tool` message whose assistant turn was dropped is a 400 from every
    OpenAI-compatible provider."""
    msgs = transcript(6, 700)
    out, _, _ = ac.compact_messages(msgs, 1500, keep_recent_groups=1)
    live_ids = {tc["id"] for m in out if m.get("role") == "assistant"
                for tc in (m.get("tool_calls") or [])}
    for m in out:
        if m.get("role") == "tool":
            assert m["tool_call_id"] in live_ids, m


def test_the_replacement_note_says_the_detail_is_gone():
    """A paraphrase would let the model keep quoting a file it can no longer see."""
    msgs = transcript(6, 700)
    out, _, _ = ac.compact_messages(msgs, 1500, keep_recent_groups=1)
    note = next(m for m in out if "Context note" in str(m.get("content")))
    assert "NO LONGER AVAILABLE" in note["content"]
    assert "call the tool again" in note["content"]
    assert "tool_0" in note["content"], "name what was dropped, so it can be redone"


def test_the_most_recent_steps_survive():
    msgs = transcript(5, 600)
    out, _, _ = ac.compact_messages(msgs, 1400, keep_recent_groups=2)
    kept = [tc["function"]["name"] for m in out if m.get("role") == "assistant"
            for tc in (m.get("tool_calls") or [])]
    assert kept == ["tool_3", "tool_4"]


def test_one_oversized_step_is_kept_rather_than_refusing_to_run():
    msgs = transcript(1, 50000)
    out, dropped, _ = ac.compact_messages(msgs, 1000, keep_recent_groups=1)
    assert dropped == 0 and out is msgs


def test_the_loop_compacts_before_it_calls_the_model():
    """Trimming after the request would save nothing — the tokens already left."""
    events = []
    big = "y" * 6000
    script = Script(turn_with(call(args={"path": "a"})),
                    turn_with(call(args={"path": "b"}, cid="c2")),
                    turn_with(call(args={"path": "c"}, cid="c3")),
                    answer("Done."))
    res = run(ac.run_agent_loop(
        "goal", TOOLS, lambda c: big, call_model=script,
        limits=ac.AgentLimits(max_transcript_chars=8000, keep_recent_groups=1),
        on_event=lambda k, d: events.append(k)))
    assert res.ok
    assert "compacted" in events
    # Whatever the model was last shown must already be inside the budget.
    assert ac.transcript_chars(script.seen_messages[-1]) <= 8000


def test_a_compacted_run_still_ends_with_a_valid_transcript():
    script = Script(turn_with(call(args={"path": "a"})),
                    turn_with(call(args={"path": "b"}, cid="c2")),
                    answer("Done."))
    res = run(ac.run_agent_loop(
        "goal", TOOLS, lambda c: "z" * 5000, call_model=script, system="sys",
        limits=ac.AgentLimits(max_transcript_chars=4000, keep_recent_groups=1)))
    assert res.ok and res.steps == 3
    roles = [m["role"] for m in res.messages]
    assert roles[0] == "system", roles
    assert res.messages[1]["content"] == "goal", "the goal stays directly after the system prompt"


def test_message_chars_counts_tool_call_arguments():
    m = {"role": "assistant", "content": "hi", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "read_file", "arguments": '{"path":"aaaa"}'}}]}
    assert ac.message_chars(m) == len("hi") + len("read_file") + len('{"path":"aaaa"}')


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
