"""Harness for modules/agent_core.py — the decide→act→observe loop.

A scripted fake model, a fake clock and fake tools: no keys, no network, no side
effects. The point of these checks is that the loop is HONEST — it must stop when
it can't finish and say so, and it must never report success it didn't earn.
"""

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
    res = ac.run_agent("read a.txt", TOOLS, lambda c: ran.append(c.name) or "hello",
                       call_model=script)
    assert res.ok and res.stop_reason == ac.ANSWERED
    assert res.answer == "It says hello."
    assert ran == ["read_file"] and res.steps == 2
    assert res.tool_runs[0].ok and res.tool_runs[0].output == "hello"


def test_tool_output_is_fed_back_to_the_model():
    script = Script(turn_with(call(args={"path": "a"})), answer())
    ac.run_agent("go", TOOLS, lambda c: "FILE-CONTENTS", call_model=script)
    second = script.seen_messages[1]
    assert second[-1] == {"role": "tool", "tool_call_id": "c1", "content": "FILE-CONTENTS"}
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "c1"


def test_answer_without_any_tool_is_fine():
    res = ac.run_agent("hi", TOOLS, lambda c: None, call_model=Script(answer("Hello.")))
    assert res.ok and res.steps == 1 and res.tool_runs == []


def test_parallel_tool_calls_all_execute():
    ran = []
    script = Script(turn_with(call(cid="a", args={"path": "1"}),
                              call(cid="b", args={"path": "2"})), answer())
    res = ac.run_agent("both", TOOLS, lambda c: ran.append(c.id) or "ok",
                       call_model=script)
    assert ran == ["a", "b"] and len(res.tool_runs) == 2 and res.ok


def test_system_prompt_and_message_list_goals():
    script = Script(answer())
    ac.run_agent("do it", TOOLS, lambda c: None, system="You are JARVIS.",
                 call_model=script)
    assert script.seen_messages[0][0] == {"role": "system", "content": "You are JARVIS."}

    script2 = Script(answer())
    ac.run_agent([{"role": "user", "content": "prior"},
                  {"role": "assistant", "content": "context"}], TOOLS,
                 lambda c: None, call_model=script2)
    assert len(script2.seen_messages[0]) == 2


# ---- caps: the run must STOP, and say why -------------------------------- #

def test_step_cap_fails_honestly():
    """A model that keeps calling tools forever must not run forever, and the
    result must not look like success."""
    forever = Script(*[turn_with(call(args={"path": "x"})) for _ in range(20)])
    res = ac.run_agent("loop", TOOLS, lambda c: "ok", call_model=forever,
                       limits=ac.AgentLimits(max_steps=3))
    assert res.ok is False and res.stop_reason == ac.MAX_STEPS
    assert res.steps == 3 and res.answer is None
    assert "3-step" in res.error
    assert "couldn't finish" in res.summary()


def test_wall_clock_cap():
    clock = Clock(step=10.0)      # 10s per check
    script = Script(*[turn_with(call(args={"path": "x"})) for _ in range(20)])
    res = ac.run_agent("slow", TOOLS, lambda c: "ok", call_model=script,
                       limits=ac.AgentLimits(max_seconds=25.0), clock=clock)
    assert res.ok is False and res.stop_reason == ac.TIMEOUT


def test_too_many_tools_is_refused_not_trimmed():
    """Silently dropping tools would make the agent fail for an invisible reason."""
    many = [{"type": "function", "function": {"name": f"t{i}"}} for i in range(12)]
    called = []
    res = ac.run_agent("x", many, lambda c: None,
                       call_model=lambda *a, **k: called.append(1) or answer())
    assert res.ok is False and res.stop_reason == ac.BAD_REQUEST
    assert "limit is 8" in res.error and called == []


def test_invalid_tool_defs_never_reach_the_model():
    called = []
    res = ac.run_agent("x", [{"type": "function"}], lambda c: None,
                       call_model=lambda *a, **k: called.append(1) or answer())
    assert res.ok is False and res.stop_reason == ac.BAD_REQUEST and called == []


# ---- repair: one correction, then honest failure ------------------------- #

def test_malformed_arguments_get_one_repair():
    bad = call(err="invalid JSON arguments", raw='{"path": "a')
    script = Script(turn_with(bad), turn_with(call(args={"path": "a.txt"})), answer("Fixed."))
    res = ac.run_agent("read", TOOLS, lambda c: "contents", call_model=script)
    assert res.ok and res.answer == "Fixed."
    # The model was TOLD what was wrong rather than left to guess.
    repair_msg = script.seen_messages[1][-1]
    assert repair_msg["role"] == "tool" and "ERROR" in repair_msg["content"]


def test_second_bad_call_ends_the_run():
    bad = turn_with(call(err="invalid JSON arguments"))
    res = ac.run_agent("read", TOOLS, lambda c: "x",
                       call_model=Script(bad, bad, bad))
    assert res.ok is False and res.stop_reason == ac.BAD_REQUEST
    assert "kept producing invalid" in res.error


def test_unknown_tool_name_is_corrected_with_the_real_names():
    script = Script(turn_with(call(name="format_disk")),
                    turn_with(call(name="read_file", args={"path": "a"})), answer())
    res = ac.run_agent("go", TOOLS, lambda c: "ok", call_model=script)
    msg = script.seen_messages[1][-1]["content"]
    assert "unknown tool 'format_disk'" in msg
    assert "read_file" in msg and "delete_file" in msg
    assert res.ok


# ---- governance: checked before EVERY execution -------------------------- #

def test_denied_tool_is_never_executed():
    ran = []

    def authorize(c):
        return ac.Decision(c.name != "delete_file", "delete_file requires confirmation")

    script = Script(turn_with(call(name="delete_file", args={"path": "x"})),
                    turn_with(call(name="read_file", args={"path": "x"})), answer("Read instead."))
    res = ac.run_agent("delete x", TOOLS, lambda c: ran.append(c.name) or "ok",
                       authorize=authorize, call_model=script)
    assert "delete_file" not in ran
    assert res.ok and res.answer == "Read instead."
    denied = [r for r in res.tool_runs if r.denied]
    assert len(denied) == 1 and "confirmation" in denied[0].error
    assert "DENIED" in script.seen_messages[1][-1]["content"]


def test_repeated_denial_stops_the_run():
    deny = turn_with(call(name="delete_file", args={"path": "x"}))
    res = ac.run_agent("delete", TOOLS, lambda c: "ok",
                       authorize=lambda c: ac.Decision(False, "not authorised"),
                       call_model=Script(deny, deny, deny))
    assert res.ok is False and res.stop_reason == ac.DENIED
    assert "not authorised" in res.summary()


def test_authorize_sees_every_call_including_repeats():
    seen = []
    script = Script(turn_with(call(cid="1", args={"path": "a"})),
                    turn_with(call(cid="2", args={"path": "b"})), answer())
    ac.run_agent("twice", TOOLS, lambda c: "ok",
                 authorize=lambda c: seen.append(c.arguments["path"]) or ac.Decision(True),
                 call_model=script)
    assert seen == ["a", "b"]


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
    res = ac.run_agent("read", TOOLS, execute, call_model=script)
    assert res.ok and res.answer == "Found it in real.txt."
    assert "FileNotFoundError" in script.seen_messages[1][-1]["content"]
    assert [r.ok for r in res.tool_runs] == [False, True]


def test_repeated_tool_failures_stop_the_run():
    def boom(c):
        raise RuntimeError("device on fire")

    t = turn_with(call(args={"path": "x"}))
    res = ac.run_agent("read", TOOLS, boom, call_model=Script(t, t, t, t, t))
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
    res = ac.run_agent("x", TOOLS, flaky, call_model=Script(t, t, t, t, answer("Done.")))
    assert res.ok, "two failures, a success, then a failure must not trip the streak"


def test_long_tool_output_is_truncated_visibly():
    """A silently-cut result teaches the model the data ended where it didn't."""
    script = Script(turn_with(call(args={"path": "big"})), answer())
    res = ac.run_agent("read", TOOLS, lambda c: "x" * 5000, call_model=script,
                       limits=ac.AgentLimits(max_tool_output_chars=100))
    out = res.tool_runs[0].output
    assert len(out) < 200 and "truncated 4900 more characters" in out


def test_non_string_tool_output_is_serialised():
    script = Script(turn_with(call(args={"path": "a"})), answer())
    res = ac.run_agent("x", TOOLS, lambda c: {"lines": 3}, call_model=script)
    assert res.tool_runs[0].output == '{"lines": 3}'


# ---- provider failure ---------------------------------------------------- #

def test_provider_failure_is_not_a_silent_success():
    res = ac.run_agent("x", TOOLS, lambda c: "ok",
                       call_model=lambda *a, **k: ToolTurn.failed("all providers failed"))
    assert res.ok is False and res.stop_reason == ac.PROVIDER_FAILED
    assert res.answer is None
    assert "unreachable" in res.summary()


def test_none_turn_is_handled():
    res = ac.run_agent("x", TOOLS, lambda c: "ok", call_model=lambda *a, **k: None)
    assert res.ok is False and res.stop_reason == ac.PROVIDER_FAILED


# ---- observability ------------------------------------------------------- #

def test_events_are_emitted_for_the_hud():
    events = []
    script = Script(turn_with(call(args={"path": "a"})), answer("Done."))
    ac.run_agent("x", TOOLS, lambda c: "ok", call_model=script,
                 on_event=lambda k, d: events.append(k))
    assert events == ["model_turn", "tool_start", "tool_ok", "model_turn", "answer"]


def test_a_broken_event_hook_cannot_break_the_run():
    def bad_hook(kind, data):
        raise RuntimeError("telemetry exploded")

    res = ac.run_agent("x", TOOLS, lambda c: "ok",
                       call_model=Script(answer("Fine.")), on_event=bad_hook)
    assert res.ok and res.answer == "Fine."


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
