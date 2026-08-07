"""Harness for modules/agent_subagents.py — agentic core phase 5, sub-agents.

Fake engine, scripted fake models for BOTH loops. The contract under test is the
one that keeps recursion from becoming a foot-gun:

  * the parent sees an ANSWER, never the helper's tool output (that is the whole
    point — context, not cleverness);
  * a helper that fails, or answers nothing, fails the parent's tool call
    honestly instead of returning an empty success;
  * the delegated set must be read-only, checked when the tool is BUILT;
  * there is no path from a helper to another helper — depth 1 by construction;
  * the helper's events reach the caller tagged `sub:` so a long delegation is
    visible and cannot be mistaken for the parent's own steps.
"""

import asyncio
import sys

from modules import agent_core as ac
from modules import agent_subagents as sa
from modules import agent_tools as at
from modules.tool_calls import ToolCall, ToolTurn

TIERS = {"tavily_search": "AUTO", "web_browse": "AUTO", "search_documents": "AUTO",
         "memory_recall": "AUTO", "workspace_read": "AUTO", "list_directory": "AUTO",
         "find_file": "AUTO", "system_status": "AUTO", "read_screen": "AUTO",
         "workspace_write": "CONFIRM", "workspace_patch": "CONFIRM"}

#: Reads take an ABSOLUTE path since §6.8.1 gap G — a relative one is refused
#: before the engine is reached, so these fixtures must supply a real shape.
ABS_FILE = r"F:\work\a.py"


def registry():
    return at.build_default_registry(lambda a: TIERS.get(a, "BLOCK"))


def run(coro):
    return asyncio.run(coro)


class FakeEngine:
    def __init__(self, result="RESULT", state="COMPLETE"):
        self.result, self.state, self.seen = result, state, []

    async def execute_with_retry(self, payload, return_meta=False, trace_id=None, *,
                                 governance_bypass=False, permission_tier="admin"):
        self.seen.append((payload["action_type"], governance_bypass))
        return {"state": self.state, "result": self.result}


def script(*turns):
    seq = list(turns)
    return lambda m, t, **k: seq.pop(0) if seq else ToolTurn(ok=True, text="done.",
                                                             provider="fake")


def tool_turn(_tool, _cid="t1", **args):
    return ToolTurn(ok=True, provider="fake",
                    tool_calls=[ToolCall(id=_cid, name=_tool, arguments=args)])


def final(text="Done."):
    return ToolTurn(ok=True, text=text, provider="fake")


def call(question="which file is newest?"):
    return ToolCall(id="d1", name=sa.DELEGATE_TOOL, arguments={"question": question})


# ---- the definition the parent sees --------------------------------------- #

def test_the_definition_is_anthropic_shaped_and_demands_a_question():
    d = sa.delegate_definition()
    assert d["name"] == sa.DELEGATE_TOOL
    assert d["input_schema"]["required"] == ["question"]
    assert "self-contained" in d["input_schema"]["properties"]["question"]["description"]


def test_the_description_tells_the_model_the_helper_is_blind():
    assert "cannot see this conversation" in sa.DELEGATE_DESCRIPTION


# ---- read-only, checked at construction ---------------------------------- #

def test_a_writable_set_cannot_be_delegated():
    """A sub-agent is unattended by definition — no owner, no HUD prompt."""
    reg, engine = registry(), FakeEngine()
    try:
        sa.make_delegate(reg, engine, tool_set="authoring")
    except sa.UnsafeSubagentError as e:
        assert "workspace_write" in str(e)
    else:
        raise AssertionError("a CONFIRM-tier tool in the set must refuse to build")


def test_a_read_only_set_builds():
    reg, engine = registry(), FakeEngine()
    definition, runner = sa.make_delegate(reg, engine, tool_set="research")
    assert definition["name"] == sa.DELEGATE_TOOL and callable(runner)


def test_an_unknown_set_is_a_keyerror_not_an_empty_helper():
    reg, engine = registry(), FakeEngine()
    try:
        sa.make_delegate(reg, engine, tool_set="nope")
    except KeyError:
        return
    raise AssertionError("an unknown set must not silently build a toolless helper")


# ---- depth 1, by construction -------------------------------------------- #

def test_the_helper_is_never_given_the_delegate_tool():
    """No counter to forget: the tool list simply cannot contain itself."""
    reg, engine = registry(), FakeEngine("x")
    seen_tools = []

    def spy(messages, tools, **k):
        seen_tools.append([t["name"] for t in tools])
        return final("42")

    _, runner = sa.make_delegate(reg, engine, tool_set="research", call_model=spy)
    run(runner(call()))
    assert seen_tools and sa.DELEGATE_TOOL not in seen_tools[0]
    assert set(seen_tools[0]) == set(reg.set_names("research"))


# ---- what comes back ------------------------------------------------------ #

def test_the_parent_gets_the_answer_not_the_tool_output():
    reg, engine = registry(), FakeEngine("notes.py  main.py  old.py")
    _, runner = sa.make_delegate(
        reg, engine, tool_set="research",
        call_model=script(tool_turn("workspace_read", path=ABS_FILE),
                          final("notes.py is the newest.")))
    out = run(runner(call()))
    assert out == "notes.py is the newest."
    assert "old.py" not in out, "raw listings are exactly what must not reach the parent"
    assert engine.seen == [("workspace_read", False)]


def test_a_helper_that_hits_a_cap_fails_the_parents_tool_call():
    reg, engine = registry(), FakeEngine("more context")
    loop_forever = tool_turn("workspace_read", path=ABS_FILE)
    _, runner = sa.make_delegate(
        reg, engine, tool_set="research",
        limits=ac.AgentLimits(max_steps=2, max_tools=8),
        call_model=lambda m, t, **k: loop_forever)
    try:
        run(runner(call()))
    except ac.ToolFailure as e:
        assert "could not answer" in str(e)
        return
    raise AssertionError("a capped helper must not look like a successful lookup")


def test_a_provider_outage_inside_the_helper_is_reported_up():
    reg, engine = registry(), FakeEngine()
    _, runner = sa.make_delegate(
        reg, engine, tool_set="research",
        call_model=lambda m, t, **k: ToolTurn.failed("all providers down", "fake"))
    try:
        run(runner(call()))
    except ac.ToolFailure as e:
        assert "unreachable" in str(e) or "could not answer" in str(e)
        return
    raise AssertionError("an outage must surface as a tool failure")


def test_an_empty_question_is_refused_before_any_model_call():
    reg, engine = registry(), FakeEngine()
    calls = []
    _, runner = sa.make_delegate(reg, engine, tool_set="research",
                                call_model=lambda m, t, **k: calls.append(1) or final())
    try:
        run(runner(ToolCall(id="d1", name=sa.DELEGATE_TOOL, arguments={"question": "  "})))
    except ac.ToolFailure as e:
        assert "empty" in str(e)
        assert calls == [], "a blank delegation must not burn a provider call"
        return
    raise AssertionError("an empty sub-question must fail")


def test_a_helper_that_answers_nothing_is_a_failure_not_an_empty_success():
    reg, engine = registry(), FakeEngine()
    _, runner = sa.make_delegate(reg, engine, tool_set="research",
                                call_model=script(final("   ")))
    try:
        run(runner(call()))
    except ac.ToolFailure:
        return
    raise AssertionError("an empty answer would be narrated as 'nothing found'")


# ---- narration ------------------------------------------------------------ #

def test_helper_events_are_tagged_so_the_hud_can_nest_them():
    reg, engine = registry(), FakeEngine("x")
    events = []

    async def on_event(kind, data):
        events.append(kind)

    _, runner = sa.make_delegate(
        reg, engine, tool_set="research", on_event=on_event,
        call_model=script(tool_turn("workspace_read", path=ABS_FILE), final("done")))
    run(runner(call()))
    assert events and all(k.startswith("sub:") for k in events), events
    assert "sub:tool_start" in events and "sub:answer" in events


def test_the_helper_system_prompt_forbids_guessing():
    assert "never invent" in sa.SUBAGENT_SYSTEM.lower()
    assert "Never guess" in sa.SUBAGENT_SYSTEM


def test_the_helper_runs_on_tighter_limits_than_the_parent():
    assert sa.SUBAGENT_LIMITS.max_steps < ac.AgentLimits().max_steps
    assert sa.SUBAGENT_LIMITS.max_transcript_chars < ac.AgentLimits().max_transcript_chars


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
