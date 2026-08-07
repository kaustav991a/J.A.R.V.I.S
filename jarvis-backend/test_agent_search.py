"""Harness for modules/agent_search.py — §6.8.2 (rule 13).

The unlock: the loop could reach 11 of JARVIS's 72 reachable actions because a
curated set caps at 8. Curation is right; the other 61 being *unaddressable*
was not.

What is proven here:

  * a tool the run does NOT hold can be found by plain words and used;
  * governance survives the shortcut — a CONFIRM tool is not offered to a run
    with nobody to approve it, and nothing outside the registry is findable;
  * the 8-tool resident cap is never exceeded, and the BASE set is never
    evicted to make room (losing a wired tool mid-run is silent damage);
  * `search_tools` never reaches the authorizer or the engine, and what it
    reveals is still gated on the call that uses it;
  * a fruitless search does not count against the error streak, and does not
    invite an identical retry.

Real registry, real loop, scripted fake model. No network, no engine.
"""

import asyncio
import sys

from modules import agent_core as ac
from modules import agent_search as ags
from modules import agent_tools as at
from modules.agent_core import AgentLimits
from modules.agent_search import ToolShelf
from modules.tool_calls import ToolCall, ToolTurn

from agent_tier_fixture import TIERS


def registry():
    return at.build_default_registry(lambda a: TIERS.get(a, "BLOCK"))


def shelf(base=None, **kw):
    return ToolShelf(registry(), base=list(base or ["system_status"]), **kw)


def run(coro):
    return asyncio.run(coro)


class Script:
    """A fake model that emits scripted turns and records what it was sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen_tools = []

    def __call__(self, messages, tools, **kw):
        self.seen_tools.append([t.get("name") or t["function"]["name"] for t in tools])
        return self.turns.pop(0) if self.turns else ToolTurn(ok=True, text="done")


def search_turn(query, cid="s1"):
    return ToolTurn(ok=True, tool_calls=[
        ToolCall(id=cid, name=ags.SEARCH_TOOL_NAME, arguments={"query": query})])


def tool_turn(tool, cid="t1", **args):
    # First parameter is `tool`, not `name`: several real tools take a `name`
    # argument, and `tool_turn("find_file", name="x")` would collide.
    return ToolTurn(ok=True, tool_calls=[ToolCall(id=cid, name=tool, arguments=args)])


class FakeEngine:
    def __init__(self, result="ok"):
        self.seen = []
        self.result = result

    async def execute_with_retry(self, payload, *a, **kw):
        self.seen.append(payload["action_type"])
        return {"result": self.result, "state": "COMPLETED"}


# ── finding ──────────────────────────────────────────────────────────────────

def test_a_tool_the_run_does_not_hold_is_findable_by_plain_words():
    s = shelf()
    hits = s.search("find a file by name")
    assert hits, "nothing matched a plainly-worded capability"
    assert "find_file" in [h.name for h in hits], [h.name for h in hits]


def test_a_tool_already_resident_is_not_offered_again():
    """Re-loading something the model holds wastes a slot and a step."""
    s = shelf(base=["find_file"])
    assert "find_file" not in [h.name for h in s.search("find a file")]


def test_the_best_match_ranks_first():
    s = shelf()
    hits = s.search("read what is on the screen")
    assert hits[0].name == "read_screen", [(h.name, h.score) for h in hits]


def test_select_asks_for_exact_names():
    """Mirrors the reference's ToolSearch: the form to use when the name is
    already known, so a model does not have to describe what it can name."""
    s = shelf()
    hits = s.search("select:find_file,read_screen")
    assert [h.name for h in hits] == ["find_file", "read_screen"], hits


def test_select_cannot_reach_a_tool_the_run_may_not_have():
    """The exact-name form must not become a governance bypass."""
    s = shelf(allow_confirm=False)
    assert s.search("select:workspace_write") == []


def test_a_query_of_only_noise_words_matches_nothing():
    assert shelf().search("can you please do the thing for me") == []


def test_an_unknown_capability_matches_nothing():
    assert shelf().search("launch the orbital cannon") == []


# ── governance survives the shortcut ─────────────────────────────────────────

def test_a_confirm_tool_is_not_offered_when_nobody_can_approve_it():
    """Surfacing it would teach the model to ask for something certain to be
    refused — a wasted step every time."""
    s = shelf(allow_confirm=False)
    assert "workspace_write" not in [h.name for h in s.search("write a file")]


def test_a_confirm_tool_is_offered_to_an_attended_run():
    s = shelf(allow_confirm=True)
    assert "workspace_write" in [h.name for h in s.search("write a file")]


def test_a_confirm_tool_is_flagged_as_needing_approval_when_loaded():
    s = shelf(allow_confirm=True)
    out = s.handle({"query": "write a file"})
    assert "confirmation" in out.lower(), out


def test_nothing_outside_the_registry_is_findable():
    """A BLOCK action cannot be registered, so it cannot be found. Asserted
    rather than assumed, because this is the whole containment story."""
    s = shelf()
    for name in ("delete_file", "format_drive", "run_terminal_command"):
        # Other tools may legitimately match the words ("file"); what must never
        # happen is the BLOCK action itself being offered.
        assert name not in [h.name for h in s.search(name)], name
        assert s.search(f"select:{name}") == [], name


# ── the resident cap ─────────────────────────────────────────────────────────

def test_the_resident_cap_is_never_exceeded():
    s = shelf(base=["tavily_search", "web_browse", "search_documents",
                    "memory_recall", "workspace_read", "system_status"],
              max_tools=8)
    for query in ("find a file", "list a directory", "read the screen"):
        s.handle({"query": query})
        assert len(s.defs()) <= 8, (len(s.defs()), s.resident())


def test_the_base_set_is_never_evicted():
    """The intent was wired with those tools; losing one mid-run is silent
    damage the model cannot see or report."""
    base = ["tavily_search", "web_browse", "search_documents",
            "memory_recall", "workspace_read", "system_status"]
    s = shelf(base=base, max_tools=8)
    for query in ("find a file", "list a directory", "read the screen"):
        s.handle({"query": query})
    for name in base:
        assert name in s.resident(), f"{name} was evicted from the base set"


def test_eviction_is_oldest_promoted_first_and_is_announced():
    base = ["tavily_search", "web_browse", "search_documents",
            "memory_recall", "workspace_read", "system_status"]
    s = shelf(base=base, max_tools=8)
    s.handle({"query": "select:find_file"})
    assert "find_file" in s.resident()
    out = s.handle({"query": "select:read_screen"})
    assert "read_screen" in s.resident()
    assert "find_file" not in s.resident(), "the older promotion was not evicted"
    assert "Dropped to make room: find_file" in out, out


def test_a_promotion_that_cannot_fit_is_not_reported_as_loaded():
    """Reporting a tool the model does not actually have is worse than saying
    there was no room: it calls it, gets `unknown tool`, and burns its repair."""
    base = ["tavily_search", "web_browse", "search_documents", "memory_recall",
            "workspace_read", "system_status", "list_directory"]
    s = shelf(base=base, max_tools=8)
    out = s.handle({"query": "select:find_file"})
    assert "find_file" not in s.resident()
    assert "no room" in out.lower(), out


def test_search_tools_itself_always_has_a_slot():
    s = shelf(base=["system_status"], max_tools=8)
    names = [d["name"] for d in s.defs()]
    assert ags.SEARCH_TOOL_NAME in names
    assert names[-1] == ags.SEARCH_TOOL_NAME, \
        "the meta-tool should not push the wired tools down the list"


# ── what the model is told ───────────────────────────────────────────────────

def test_a_fruitless_search_says_not_to_search_again():
    """A bare "no results" sends a model into a second and third identical
    search — the exact loop the step cap then has to stop."""
    out = shelf().handle({"query": "launch the orbital cannon"})
    assert "no tool" in out.lower(), out
    assert "do not search again" in out.lower(), out


def test_a_successful_search_names_what_can_now_be_called():
    out = shelf().handle({"query": "find a file by name"})
    assert "find_file" in out and "call them now" in out, out


def test_searches_are_recorded_for_the_audit_trail():
    s = shelf()
    s.handle({"query": "find a file"})
    assert s.searches and s.searches[0]["query"] == "find a file"
    assert "find_file" in s.searches[0]["promoted"]


# ── wired into the real loop ─────────────────────────────────────────────────

def test_the_model_can_search_then_call_what_it_found():
    """The end-to-end unlock: a tool that was not in the run's set is found,
    loaded, called, and reaches the engine."""
    reg, engine = registry(), FakeEngine("C:/x/notes.md")
    s = ToolShelf(reg, base=["system_status"], max_tools=8)
    script = Script(search_turn("find a file by name"),
                    tool_turn("find_file", name="notes.md"),
                    ToolTurn(ok=True, text="It is at C:/x/notes.md."))
    res = run(ac.run_agent_loop(
        "where is notes.md", [], reg.executor(engine), shelf=s,
        authorize=reg.authorizer(), call_model=script,
        limits=AgentLimits(max_steps=5)))

    assert res.ok, res.error
    assert engine.seen == ["find_file"], engine.seen
    # The first turn could not see it; the third could.
    assert "find_file" not in script.seen_tools[0], script.seen_tools[0]
    assert "find_file" in script.seen_tools[1], script.seen_tools[1]


def test_search_tools_never_reaches_the_engine_or_the_authorizer():
    """It changes only what the model can see. It has no governance decision to
    make, and the registry would refuse it as an unknown action."""
    reg, engine = registry(), FakeEngine()
    asked = []
    s = ToolShelf(reg, base=["system_status"], max_tools=8)

    def authorize(c):
        asked.append(c.name)
        return reg.authorizer()(c)

    script = Script(search_turn("find a file"), ToolTurn(ok=True, text="done"))
    run(ac.run_agent_loop("x", [], reg.executor(engine), shelf=s,
                          authorize=authorize, call_model=script,
                          limits=AgentLimits(max_steps=4)))
    assert engine.seen == [], engine.seen
    assert ags.SEARCH_TOOL_NAME not in asked, asked


def test_a_promoted_confirm_tool_is_still_gated_on_the_call_that_uses_it():
    """Being findable is not being permitted. This is the property that makes
    the whole shortcut safe."""
    reg, engine = registry(), FakeEngine()
    s = ToolShelf(reg, base=["system_status"], max_tools=8, allow_confirm=False)
    # Loaded by an ATTENDED-style direct promotion, then called in an
    # unattended run — the gate must still fire.
    s.promote(["workspace_write"])
    script = Script(tool_turn("workspace_write", path=r"F:\work\new.py", content="x"),
                    ToolTurn(ok=True, text="done"))
    res = run(ac.run_agent_loop("write", [], reg.executor(engine), shelf=s,
                                authorize=reg.authorizer(), call_model=script,
                                limits=AgentLimits(max_steps=4)))
    assert engine.seen == [], "a CONFIRM tool ran because it had been promoted"
    # The first denial is fed back so the model can take another route (that is
    # the loop's existing contract), so the run does not end here — what matters
    # is that nothing executed and the model was told plainly.
    denied = [r for r in res.tool_runs if r.denied]
    assert denied and "unattended" in denied[0].error, res.tool_runs
    assert any("DENIED" in m.get("content", "") for m in res.messages
               if m.get("role") == "tool")


def test_a_fruitless_search_does_not_count_against_the_error_streak():
    """Three bad guesses must not kill a run that is otherwise healthy."""
    reg, engine = registry(), FakeEngine()
    s = ToolShelf(reg, base=["system_status"], max_tools=8)
    script = Script(search_turn("orbital cannon", "s1"),
                    search_turn("plasma rifle", "s2"),
                    search_turn("warp drive", "s3"),
                    ToolTurn(ok=True, text="I cannot do that, Sir."))
    res = run(ac.run_agent_loop("x", [], reg.executor(engine), shelf=s,
                                authorize=reg.authorizer(), call_model=script,
                                limits=AgentLimits(max_steps=6,
                                                   max_consecutive_errors=3)))
    assert res.ok is True, f"{res.stop_reason}: {res.error}"
    assert res.answer == "I cannot do that, Sir."


def test_the_tool_list_sent_each_turn_stays_inside_the_cap():
    reg, engine = registry(), FakeEngine()
    s = ToolShelf(reg, base=["tavily_search", "web_browse", "search_documents",
                             "memory_recall", "workspace_read", "system_status"],
                  max_tools=8)
    script = Script(search_turn("find a file", "s1"),
                    search_turn("read the screen", "s2"),
                    ToolTurn(ok=True, text="done"))
    run(ac.run_agent_loop("x", [], reg.executor(engine), shelf=s,
                          authorize=reg.authorizer(), call_model=script,
                          limits=AgentLimits(max_steps=5)))
    for sent in script.seen_tools:
        assert len(sent) <= 8, (len(sent), sent)


def test_the_run_without_a_shelf_is_completely_unchanged():
    """Phase 2 must be opt-in: every existing wired intent passes a plain tool
    list and must behave exactly as before."""
    reg, engine = registry(), FakeEngine("telemetry")
    script = Script(tool_turn("system_status"), ToolTurn(ok=True, text="ok"))
    res = run(ac.run_agent_loop("status", reg.defs(["system_status"]),
                                reg.executor(engine), authorize=reg.authorizer(),
                                call_model=script, limits=AgentLimits(max_steps=4)))
    assert res.ok and engine.seen == ["system_status"]
    assert ags.SEARCH_TOOL_NAME not in script.seen_tools[0]


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
