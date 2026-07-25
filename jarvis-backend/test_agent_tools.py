"""Harness for modules/agent_tools.py — the agent's view of action_engine.

A fake engine and a fake governance lookup: nothing launches, nothing is written,
no ruleset file is read. The invariants under test are the dangerous ones — that
a curated set stays small, that governance is consulted for every call, and that
a refusal never reaches the model dressed up as a successful tool result.
"""

import sys

from modules import agent_tools as at
from modules.agent_core import AgentLimits
from modules.tool_calls import ToolCall, validate_tool_defs


def call(name, **args):
    return ToolCall(id="c1", name=name, arguments=args)


# ---- the registry -------------------------------------------------------- #

def test_every_set_fits_the_loop_cap():
    """Rule 1: small models degrade sharply past ~8 tools, so a set that would be
    refused by agent_core is a registry bug, not a runtime surprise."""
    cap = AgentLimits().max_tools
    for name, tools in at.TOOL_SETS.items():
        assert len(tools) <= cap, f"set '{name}' has {len(tools)} tools (cap {cap})"


def test_every_set_references_real_tools():
    for name, tools in at.TOOL_SETS.items():
        for t in tools:
            assert t in at.TOOL_SPECS, f"set '{name}' references unknown tool '{t}'"


def test_tool_defs_are_valid_openai_schemas():
    for set_name in at.TOOL_SETS:
        assert validate_tool_defs(at.tool_defs(set_name)) == []


def test_tool_defs_carry_descriptions_and_required_args():
    defs = {d["function"]["name"]: d["function"] for d in at.tool_defs("research")}
    assert defs["tavily_search"]["description"]
    assert defs["tavily_search"]["parameters"]["required"] == ["query"]
    # A no-argument tool must say so rather than inventing a parameter.
    assert defs["system_status"]["parameters"]["required"] == []


def test_unknown_set_or_tool_raises():
    for bad in ("nope", ):
        try:
            at.tool_defs(bad)
            raise AssertionError("expected KeyError")
        except KeyError:
            pass
    try:
        at.tool_defs(["not_a_tool"])
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_every_registered_action_type_exists_in_the_governance_ruleset():
    """A typo'd action_type would fail-safe to BLOCK — the agent would simply
    refuse the tool forever, with a reason that reads like policy, not a bug."""
    import json
    import pathlib

    raw = json.loads(pathlib.Path(__file__).with_name("governance.json")
                     .read_text(encoding="utf-8"))
    rules = raw.get("rules", raw)
    missing = [s["action_type"] for s in at.TOOL_SPECS.values()
               if s["action_type"] not in rules]
    assert not missing, f"not in governance.json: {missing}"


def test_every_registered_action_type_is_dispatched_by_action_engine():
    """Same class of bug at the other end: a name the routing table never
    matches falls through and the model is told nothing happened."""
    import pathlib

    src = pathlib.Path(__file__).with_name("action_engine.py").read_text(encoding="utf-8")
    missing = [s["action_type"] for s in at.TOOL_SPECS.values()
               if f'action == "{s["action_type"]}"' not in src]
    assert not missing, f"no dispatch branch in action_engine.py: {missing}"


def test_the_research_set_is_read_only():
    """Phase 4 wires this set first, so nothing in it may change the machine."""
    writers = {"workspace_write", "workspace_patch", "delete_file", "ghost_save_file",
               "run_terminal_command", "os_control"}
    assert not (set(at.TOOL_SETS["research"]) & writers)


# ---- payload mapping ----------------------------------------------------- #

def test_payload_uses_the_action_type_and_target_shape():
    p = at.to_payload(call("tavily_search", query="who won"))
    assert p["action_type"] == "tavily_search" and p["target"] == "who won"


def test_composite_target_is_built():
    """workspace_write's handler parses "path|content", not a dict."""
    p = at.to_payload(call("workspace_write", path="a.py", content="print(1)"))
    assert p["target"] == "a.py|print(1)"


def test_no_argument_tool_gets_an_empty_target():
    assert at.to_payload(call("system_status"))["target"] == ""


def test_missing_required_arguments_are_listed():
    assert at.missing_required(call("workspace_write", path="a.py")) == ["content"]
    assert at.missing_required(call("tavily_search", query="")) == ["query"]
    assert at.missing_required(call("tavily_search", query="x")) == []


# ---- governance ---------------------------------------------------------- #

def tiers(mapping):
    return lambda action_type: mapping.get(action_type, "BLOCK")


def test_auto_tier_is_allowed():
    auth = at.make_authorizer(tiers({"tavily_search": "AUTO"}))
    d = auth(call("tavily_search", query="x"))
    assert d.allowed and d.reason == "AUTO"


def test_confirm_tier_is_refused_in_an_unattended_run():
    """Nobody is at the keyboard mid-loop; a CONFIRM action that self-approves
    would defeat the entire tier system."""
    auth = at.make_authorizer(tiers({"workspace_write": "CONFIRM"}))
    d = auth(call("workspace_write", path="a", content="b"))
    assert d.allowed is False
    assert "CONFIRM-tier" in d.reason and "unattended" in d.reason


def test_confirm_can_be_allowed_for_an_attended_run():
    auth = at.make_authorizer(tiers({"workspace_write": "CONFIRM"}), allow_confirm=True)
    assert auth(call("workspace_write", path="a", content="b")).allowed is True


def test_block_tier_is_refused():
    auth = at.make_authorizer(tiers({"tavily_search": "BLOCK"}))
    assert auth(call("tavily_search", query="x")).allowed is False


def test_unknown_action_defaults_to_refused():
    """Governance fails safe (unknown => BLOCK); the adapter must not soften it."""
    auth = at.make_authorizer(tiers({}))
    assert auth(call("tavily_search", query="x")).allowed is False


def test_unregistered_tool_is_refused():
    auth = at.make_authorizer(tiers({"anything": "AUTO"}))
    assert auth(ToolCall(id="1", name="format_drive")).allowed is False


def test_missing_arguments_are_refused_before_execution():
    auth = at.make_authorizer(tiers({"workspace_write": "AUTO"}))
    d = auth(call("workspace_write", path="a.py"))
    assert d.allowed is False and "content" in d.reason


# ---- sentinels ----------------------------------------------------------- #

def test_governance_sentinels_raise_instead_of_being_returned():
    """The whole point: a refusal handed back as a tool RESULT reads to the model
    as success, and the run narrates something that never happened."""
    for sentinel in ("GOVERNANCE_BLOCKED:delete_file",
                     "GOVERNANCE_CONFIRM:workspace_write:abc123",
                     "TIER_BLOCKED:read_screen"):
        try:
            at.interpret_result(sentinel)
            raise AssertionError(f"{sentinel} should have raised")
        except PermissionError as e:
            assert sentinel in str(e)


def test_validation_error_raises_too():
    try:
        at.interpret_result("Validation Error: bad structure")
        raise AssertionError("should have raised")
    except RuntimeError:
        pass


def test_normal_output_passes_through():
    assert at.interpret_result("Chelsea won 2-0.") == "Chelsea won 2-0."
    assert at.interpret_result({"ok": True}) == {"ok": True}


# ---- executor ------------------------------------------------------------ #

class FakeEngine:
    def __init__(self, result="RESULT"):
        self.result = result
        self.seen = []

    async def execute(self, payload, *, permission_tier="admin", **kw):
        self.seen.append((payload, permission_tier))
        return self.result


def sync_runner(coro):
    """Drive a coroutine to completion without an event loop."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    raise AssertionError("fake engine should not await anything")


def test_executor_maps_call_to_payload_and_returns_output():
    engine = FakeEngine("Sunny, 24 degrees.")
    ex = at.make_executor(engine, runner=sync_runner)
    out = ex(call("tavily_search", query="weather"))
    assert out == "Sunny, 24 degrees."
    payload, tier = engine.seen[0]
    assert payload == {"action_type": "tavily_search", "target": "weather",
                       "query": "weather"}
    assert tier == "admin"


def test_executor_raises_on_a_refusal_sentinel():
    ex = at.make_executor(FakeEngine("GOVERNANCE_BLOCKED:delete_file"),
                          runner=sync_runner)
    try:
        ex(call("tavily_search", query="x"))
        raise AssertionError("expected PermissionError")
    except PermissionError:
        pass


def test_executor_passes_the_permission_tier_through():
    engine = FakeEngine()
    ex = at.make_executor(engine, runner=sync_runner, permission_tier="vip_guest")
    ex(call("tavily_search", query="x"))
    assert engine.seen[0][1] == "vip_guest"


# ---- end to end against the loop ----------------------------------------- #

def test_loop_runs_a_research_task_with_governance():
    """Registry + authorizer + executor + agent_core, wired as phase 4 will."""
    from modules import agent_core as ac
    from modules.tool_calls import ToolTurn

    turns = [
        ToolTurn(ok=True, provider="fake", tool_calls=[
            ToolCall(id="t1", name="tavily_search", arguments={"query": "score"})]),
        ToolTurn(ok=True, provider="fake", text="City won 3-1, Sir."),
    ]
    engine = FakeEngine("City 3 - 1 United")
    res = ac.run_agent(
        "what was the score", at.tool_defs("research"),
        at.make_executor(engine, runner=sync_runner),
        authorize=at.make_authorizer(tiers({"tavily_search": "AUTO"})),
        call_model=lambda m, t, **k: turns.pop(0))
    assert res.ok and res.answer == "City won 3-1, Sir."
    assert res.tool_runs[0].output == "City 3 - 1 United"


def test_loop_refuses_a_confirm_tool_and_says_so():
    from modules import agent_core as ac
    from modules.tool_calls import ToolTurn

    write = ToolTurn(ok=True, provider="fake", tool_calls=[
        ToolCall(id="t1", name="workspace_write",
                 arguments={"path": "a.py", "content": "x"})])
    engine = FakeEngine()
    res = ac.run_agent(
        "write a.py", at.tool_defs("authoring"),
        at.make_executor(engine, runner=sync_runner),
        authorize=at.make_authorizer(tiers({"workspace_write": "CONFIRM",
                                            "workspace_read": "AUTO"})),
        call_model=lambda m, t, **k: write)
    assert res.ok is False and res.stop_reason == ac.DENIED
    assert engine.seen == [], "a CONFIRM-tier tool must never reach the engine"
    assert "unattended" in res.summary()


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
