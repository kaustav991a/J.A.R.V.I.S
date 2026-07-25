"""Harness for modules/agent_tools.py — the agent's view of action_engine.

A fake engine and a fake governance lookup: nothing launches, nothing is written,
no ruleset file is read. The invariants under test are the dangerous ones:

  * a BLOCK-tier action cannot be REGISTERED (design-time guard) and would also
    be refused at call time (runtime guard) — both proven separately;
  * a curated set stays small;
  * governance is consulted for every call;
  * a refusal never reaches the model dressed up as a successful tool result.
"""

import asyncio
import sys

from modules import agent_core as ac
from modules import agent_tools as at
from modules.agent_core import AgentLimits
from modules.tool_calls import ToolCall, validate_tool_defs

# A stand-in ruleset. Mirrors the real governance.json tiers for these actions.
TIERS = {
    "tavily_search": "AUTO", "web_browse": "AUTO", "search_documents": "AUTO",
    "memory_recall": "AUTO", "workspace_read": "AUTO", "list_directory": "AUTO",
    "find_file": "AUTO", "system_status": "AUTO", "read_screen": "AUTO",
    "workspace_write": "CONFIRM",
    "delete_file": "BLOCK", "run_terminal_command": "BLOCK",
}


def tiers(mapping=None):
    m = TIERS if mapping is None else mapping
    return lambda action_type: m.get(action_type, "BLOCK")


def registry(mapping=None):
    return at.build_default_registry(tiers(mapping))


def call(name, **args):
    return ToolCall(id="c1", name=name, arguments=args)


def run(coro):
    return asyncio.run(coro)


# ---- registration: BLOCK is refused at DESIGN time ----------------------- #

def test_registering_a_block_tier_tool_is_refused():
    """Defense in depth, half one. A BLOCK tool must not merely be denied when
    called — it must be impossible to put in the registry, so its name never
    appears in a schema any model sees."""
    r = at.ToolRegistry(tiers())
    try:
        r.register("delete_file", "Delete a file",
                   {"type": "object", "properties": {"path": {"type": "string"}}})
        raise AssertionError("registering a BLOCK-tier tool should have raised")
    except at.BlockedToolError as e:
        assert "delete_file" in str(e) and "BLOCK" in str(e)
    assert r.names() == [], "nothing may be left behind by a refused registration"


def test_a_block_tool_is_also_refused_at_call_time():
    """Defense in depth, half two. Even if an entry's tier were mutated at
    runtime past the registration guard, the authorizer still refuses it."""
    r = registry()
    entry = r.get("tavily_search")
    r._tools["tavily_search"] = at.replace(entry, tier="BLOCK")
    d = r.authorizer()(call("tavily_search", query="x"))
    assert d.allowed is False and "blocked by governance" in d.reason


def test_an_unknown_action_type_cannot_be_registered():
    """Governance fail-safes unknown types to BLOCK, so a TYPO in an action_type
    is caught here rather than becoming a tool that can never dispatch."""
    r = at.ToolRegistry(tiers())
    try:
        r.register("tavily_serach", "typo",     # note the transposition
                   {"type": "object", "properties": {"query": {"type": "string"}}})
        raise AssertionError("a typo'd action_type should have been refused")
    except at.BlockedToolError:
        pass


def test_no_block_tier_tool_is_in_the_default_registry():
    r = registry()
    for name in r.names():
        assert r.tier_of(name) in ("AUTO", "CONFIRM"), name
    assert "delete_file" not in r.names()
    assert "run_terminal_command" not in r.names()


def test_duplicate_and_malformed_registrations_are_refused():
    r = at.ToolRegistry(tiers())
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    r.register("tavily_search", "d", schema)
    for bad in (lambda: r.register("tavily_search", "d", schema),        # duplicate
                lambda: r.register("", "d", schema),                     # no name
                lambda: r.register("web_browse", "d", {"properties": {}})):  # no type
        try:
            bad()
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_refresh_tiers_drops_a_tool_that_became_blocked():
    """A governance reload that re-tiers an action to BLOCK must REMOVE it, not
    leave a stale AUTO entry the model can still see."""
    mutable = dict(TIERS)
    r = at.build_default_registry(lambda a: mutable.get(a, "BLOCK"))
    assert "read_screen" in r.names()
    mutable["read_screen"] = "BLOCK"
    dropped = r.refresh_tiers()
    assert dropped == ["read_screen"]
    assert "read_screen" not in r.names()


def test_refresh_tiers_updates_a_changed_tier_in_place():
    mutable = dict(TIERS)
    r = at.build_default_registry(lambda a: mutable.get(a, "BLOCK"))
    assert r.tier_of("read_screen") == "AUTO"
    mutable["read_screen"] = "CONFIRM"
    assert r.refresh_tiers() == []
    assert r.tier_of("read_screen") == "CONFIRM"


def test_a_dropped_tool_is_removed_from_its_sets_too():
    mutable = dict(TIERS)
    r = at.build_default_registry(lambda a: mutable.get(a, "BLOCK"))
    assert any(d["name"] == "system_status" for d in r.defs("research"))
    mutable["system_status"] = "BLOCK"
    r.refresh_tiers()
    assert all(d["name"] != "system_status" for d in r.defs("research"))


# ---- the schema dialect -------------------------------------------------- #

def test_definitions_use_the_anthropic_shape():
    d = registry().defs(["tavily_search"])[0]
    assert set(d) == {"name", "description", "input_schema"}
    assert "parameters" not in d and "function" not in d
    assert d["input_schema"]["type"] == "object"
    assert d["input_schema"]["required"] == ["query"]


def test_a_no_argument_tool_declares_an_empty_object_schema():
    d = {x["name"]: x for x in registry().defs("research")}["system_status"]
    assert d["input_schema"] == {"type": "object", "properties": {}, "required": []}


def test_definitions_survive_the_wire_translation():
    """The registry never thinks about OpenAI; the boundary does the work."""
    assert validate_tool_defs(registry().defs("research")) == []


def test_every_tool_carries_a_description():
    r = registry()
    for name in r.names():
        assert len(r.get(name).description) > 20, name


# ---- curated sets -------------------------------------------------------- #

def test_every_set_fits_the_loop_cap():
    cap = AgentLimits().max_tools
    r = registry()
    for s in r.sets():
        assert len(r.defs(s)) <= cap, f"set '{s}' exceeds {cap}"


def test_an_oversized_set_is_refused_at_definition_time():
    r = registry()
    try:
        r.define_set("everything", r.names())          # 10 tools > cap of 8
        raise AssertionError("an oversized set should have been refused")
    except ValueError as e:
        assert "cap is 8" in str(e)


def test_a_set_of_unregistered_tools_is_refused():
    r = registry()
    for bad in (lambda: r.define_set("bad", ["nope"]),
                lambda: r.define_set("empty", [])):
        try:
            bad()
            raise AssertionError("expected an error")
        except (KeyError, ValueError):
            pass


def test_unknown_set_or_tool_raises():
    r = registry()
    for bad in (lambda: r.defs("nope"), lambda: r.defs(["not_a_tool"])):
        try:
            bad()
            raise AssertionError("expected KeyError")
        except KeyError:
            pass


def test_the_research_set_is_read_only():
    """Phase 4 wires this set first, so nothing in it may change the machine."""
    r = registry()
    for d in r.defs("research"):
        assert r.tier_of(d["name"]) == "AUTO", d["name"]


# ---- tier is on the entry (no second lookup) ----------------------------- #

def test_tier_is_resolved_once_at_registration():
    lookups = []

    def counting(action_type):
        lookups.append(action_type)
        return TIERS.get(action_type, "BLOCK")

    r = at.build_default_registry(counting)
    n = len(lookups)
    authorize = r.authorizer()
    for _ in range(5):
        authorize(call("tavily_search", query="x"))
    assert len(lookups) == n, "the authorizer must not re-query governance per call"
    assert r.tier_of("tavily_search") == "AUTO"


# ---- payload mapping ----------------------------------------------------- #

def test_payload_uses_the_action_type_and_target_shape():
    p = registry().to_payload(call("tavily_search", query="who won"))
    assert p["action_type"] == "tavily_search" and p["target"] == "who won"


def test_composite_target_is_built():
    """workspace_write's handler parses "path|content", not a dict."""
    p = registry().to_payload(call("workspace_write", path="a.py", content="print(1)"))
    assert p["target"] == "a.py|print(1)"


def test_no_argument_tool_gets_an_empty_target():
    assert registry().to_payload(call("system_status"))["target"] == ""


def test_missing_required_arguments_are_listed():
    r = registry()
    assert r.missing_required(call("workspace_write", path="a.py")) == ["content"]
    assert r.missing_required(call("tavily_search", query="")) == ["query"]
    assert r.missing_required(call("tavily_search", query="x")) == []


# ---- governance adapter --------------------------------------------------- #

def test_auto_tier_is_allowed():
    d = registry().authorizer()(call("tavily_search", query="x"))
    assert d.allowed and d.reason == "AUTO"


def test_confirm_tier_is_refused_in_an_unattended_run():
    """Nobody is at the keyboard mid-loop; a CONFIRM action that self-approves
    would defeat the entire tier system."""
    d = registry().authorizer()(call("workspace_write", path="a", content="b"))
    assert d.allowed is False
    assert "CONFIRM-tier" in d.reason and "unattended" in d.reason


def test_confirm_can_be_allowed_for_an_attended_run():
    d = registry().authorizer(allow_confirm=True)(
        call("workspace_write", path="a", content="b"))
    assert d.allowed is True and "attended" in d.reason


def test_unregistered_tool_is_refused():
    assert registry().authorizer()(ToolCall(id="1", name="format_drive")).allowed is False


def test_missing_arguments_are_refused_before_execution():
    d = registry().authorizer()(call("workspace_write", path="a.py"))
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
    """Stands in for ActionEngine: records payloads, returns a meta dict."""

    def __init__(self, result="RESULT", state="COMPLETE"):
        self.result, self.state = result, state
        self.seen = []

    async def execute_with_retry(self, payload, return_meta=False, trace_id=None, *,
                                 governance_bypass=False, permission_tier="admin"):
        self.seen.append((payload, permission_tier, governance_bypass))
        return {"trace_id": "t", "state": self.state, "result": self.result}


def test_executor_maps_call_to_payload_and_returns_output():
    engine = FakeEngine("Sunny, 24 degrees.")
    out = run(registry().executor(engine)(call("tavily_search", query="weather")))
    assert out == "Sunny, 24 degrees."
    payload, tier, bypass = engine.seen[0]
    assert payload == {"action_type": "tavily_search", "target": "weather",
                       "query": "weather"}
    assert tier == "admin" and bypass is False


def test_executor_raises_tool_failure_on_a_failed_state():
    """Phase-2 discipline: the engine already decided this failed via
    _is_failure — the loop must not re-derive it, and the model should read the
    engine's own wording."""
    engine = FakeEngine("I couldn't reach that page, Sir.", state="FAILED")
    try:
        run(registry().executor(engine)(call("web_browse", url="https://x")))
        raise AssertionError("expected ToolFailure")
    except ac.ToolFailure as e:
        assert str(e) == "I couldn't reach that page, Sir."


def test_executor_raises_on_a_refusal_sentinel():
    engine = FakeEngine("GOVERNANCE_BLOCKED:delete_file")
    try:
        run(registry().executor(engine)(call("tavily_search", query="x")))
        raise AssertionError("expected PermissionError")
    except PermissionError:
        pass


def test_executor_passes_tier_and_bypass_through():
    engine = FakeEngine()
    run(registry().executor(engine, permission_tier="vip_guest",
                            governance_bypass=True)(call("tavily_search", query="x")))
    _, tier, bypass = engine.seen[0]
    assert tier == "vip_guest" and bypass is True


# ---- end to end against the loop ----------------------------------------- #

def test_loop_runs_a_research_task_with_governance():
    """Registry + authorizer + executor + agent_core, wired as phase 4 will."""
    from modules.tool_calls import ToolTurn

    r = registry()
    turns = [
        ToolTurn(ok=True, provider="fake", tool_calls=[
            ToolCall(id="t1", name="tavily_search", arguments={"query": "score"})]),
        ToolTurn(ok=True, provider="fake", text="City won 3-1, Sir."),
    ]
    engine = FakeEngine("City 3 - 1 United")
    res = run(ac.run_agent_loop(
        "what was the score", r.defs("research"), r.executor(engine),
        authorize=r.authorizer(),
        call_model=lambda m, t, **k: turns.pop(0)))
    assert res.ok and res.answer == "City won 3-1, Sir."
    assert res.tool_runs[0].output == "City 3 - 1 United"


def test_loop_refuses_a_confirm_tool_and_says_so():
    from modules.tool_calls import ToolTurn

    r = registry()
    write = ToolTurn(ok=True, provider="fake", tool_calls=[
        ToolCall(id="t1", name="workspace_write",
                 arguments={"path": "a.py", "content": "x"})])
    engine = FakeEngine()
    res = run(ac.run_agent_loop(
        "write a.py", r.defs("authoring"), r.executor(engine),
        authorize=r.authorizer(), call_model=lambda m, t, **k: write))
    assert res.ok is False and res.stop_reason == ac.DENIED
    assert engine.seen == [], "a CONFIRM-tier tool must never reach the engine"
    assert "unattended" in res.summary()


def test_loop_never_sees_a_block_tool_at_all():
    """The design-time guard, observed from the loop's side: the schemas handed
    to the model contain no BLOCK action, so it cannot even ask."""
    r = registry()
    names = {d["name"] for s in r.sets() for d in r.defs(s)}
    assert not (names & {"delete_file", "run_terminal_command", "format_drive"})


# ---- the real ruleset ----------------------------------------------------- #

def test_the_real_registry_builds_against_governance_json():
    """Cross-check with no fakes: every default tool must survive registration
    against the SHIPPED ruleset. A tool missing from governance.json, or
    re-tiered to BLOCK, fails here rather than at runtime."""
    from governance_manager import governance_manager

    r = at.build_default_registry(governance_manager.get_tier)
    assert len(r.names()) == 10
    assert r.tier_of("workspace_write") == "CONFIRM"
    assert r.tier_of("tavily_search") == "AUTO"


def test_every_registered_action_type_is_dispatched_by_action_engine():
    """The other end of the same class of bug: a name the routing table never
    matches would leave the model told that nothing happened."""
    import pathlib

    src = pathlib.Path(__file__).with_name("action_engine.py").read_text(encoding="utf-8")
    r = registry()
    missing = [r.get(n).action_type for n in r.names()
               if f'action == "{r.get(n).action_type}"' not in src]
    assert not missing, f"no dispatch branch in action_engine.py: {missing}"


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
