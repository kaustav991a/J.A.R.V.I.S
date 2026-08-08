"""Harness for §6.8.2 wave 5 — driving a real browser.

Six tools, and what makes this wave different from the others is that five of
them act on STATE the previous tool call produced: `web_browse` returns the page
plus a numbered map of its interactive elements, and clicking or typing means
naming one of those numbers.

The trap is that the numbers are renumbered from 1 on every render, so an id
from two steps ago is not stale-but-close — it is a different element. The
handlers already recover honestly (they hand back a fresh map instead of
clicking blind), so what is pinned here is that every description SAYS the ids
come from the most recent output, and that the tools which cannot see their own
result do not pretend otherwise.
"""

import asyncio
import sys

from agent_tier_fixture import TIERS, tier_lookup
from modules import agent_tools as at
from modules.agent_search import ToolShelf
from modules.tool_calls import ToolCall

WAVE5 = ("web_click", "web_type", "web_scroll", "web_back", "web_close",
         "web_search_image")
#: The five that act on the element map from the previous call.
ON_PAGE = ("web_click", "web_type", "web_scroll", "web_back", "web_close")


def registry():
    return at.build_default_registry(tier_lookup())


def call(name, **args):
    return ToolCall(id="c1", name=name, arguments=args)


def target(name, **args):
    return registry().to_payload(call(name, **args))["target"]


def run(coro):
    return asyncio.run(coro)


class FakeEngine:
    def __init__(self, result="RESULT", state="COMPLETE"):
        self.result, self.state, self.seen = result, state, []

    async def execute_with_retry(self, payload, return_meta=False, trace_id=None, *,
                                 governance_bypass=False, permission_tier="admin"):
        self.seen.append(payload)
        return {"trace_id": "t", "state": self.state, "result": self.result}


# ── target composition ──────────────────────────────────────────────────────

def test_typing_composes_element_then_text():
    """Handler: `parts = str(target).split("|", 1)` — element id, then text."""
    got = target("web_type", element_id="12", text="stranger things")
    assert got.split("|", 1) == ["12", "stranger things"]


def test_a_pipe_in_the_typed_text_survives():
    """`split("|", 1)` keeps everything after the FIRST pipe as the text, so a
    pipe in what he wants typed is safe. Pinned because the same shape is NOT
    safe in `github_commit`, and the difference is one argument to split()."""
    got = target("web_type", element_id="3", text="a | b | c")
    assert got.split("|", 1)[1] == "a | b | c"


def test_the_single_value_tools_pass_it_through():
    assert target("web_click", element_id="7") == "7"
    assert target("web_scroll", direction="down") == "down"
    assert target("web_search_image", query="red panda") == "red panda"


def test_the_no_argument_tools_send_nothing():
    for name in ("web_back", "web_close"):
        assert target(name) == "", name


def test_an_invented_scroll_direction_is_refused():
    """`scroll` treats anything without "up" in it as DOWN, so "left" would
    silently scroll down."""
    problem = registry().schema_problem(call("web_scroll", direction="left"))
    assert problem and "up" in problem


# ── the element ids, which are the whole difficulty of this wave ────────────

def test_both_interacting_tools_say_where_the_ids_come_from():
    reg = registry()
    for name in ("web_click", "web_type"):
        description = reg.get(name).description
        assert "most recent" in description, name


def test_both_interacting_tools_warn_that_ids_are_renumbered():
    """The DOM marker renumbers from 1 on every render — pinned against the real
    source, so if that ever stops being true the warning can be relaxed."""
    from pathlib import Path
    source = Path("modules/web_agent.py").read_text(encoding="utf-8",
                                                    errors="replace")
    assert "let id_counter = 1" in source, \
        "the element ids are no longer renumbered per render"
    reg = registry()
    for name in ("web_click", "web_type"):
        description = reg.get(name).description.lower()
        assert "renumber" in description or "earlier step" in description, name


def test_typing_says_that_enter_is_conditional():
    """`type_text` presses Enter only for a search box. A model that assumes it
    always submits will report a form as sent when it is only filled in."""
    description = registry().get("web_type").description
    assert "search box" in description and "web_click" in description


# ── the image tool: a result the model never sees ───────────────────────────

def test_an_image_result_reaches_the_display():
    sent = []
    engine = FakeEngine({"success": True, "url": "https://x/y.jpg",
                         "title": "Red panda"})
    out = run(registry().executor(engine, payload_sink=sent.append)(
        call("web_search_image", query="red panda")))
    assert sent == [{"status": "search_result_image", "url": "https://x/y.jpg",
                     "title": "Red panda"}]
    assert "NOT seen" in out, out


def test_a_failed_image_search_is_not_a_frame():
    """`{"success": False, "error": …}` is an honest miss. Forwarding it would
    put an empty image on the display and tell the model it worked."""
    sent = []
    engine = FakeEngine({"success": False, "error": "No valid image URLs found."})
    out = run(registry().executor(engine, payload_sink=sent.append)(
        call("web_search_image", query="nothing at all")))
    assert sent == []
    assert "No valid image URLs" in str(out)


def test_the_image_tool_says_the_model_cannot_see_the_picture():
    description = registry().get("web_search_image").description
    assert "never see it" in description
    assert "tavily_search" in description


def test_only_the_image_tool_produces_an_image_frame():
    """The result has nothing in it naming the action, so the TOOL is the
    discriminator — otherwise any future dict carrying a url would be broadcast
    as a picture."""
    payload = {"success": True, "url": "https://x/y.jpg", "title": "t"}
    assert at.hud_frames(payload, "web_search_image")
    assert at.hud_frames(payload, "tavily_search") == []
    assert at.hud_frames(payload) == []


# ── the search that was NOT registered, and the hole that closed ────────────

def test_web_search_was_not_registered_beside_tavily():
    """`_web_search` is `_tavily_search` with a DuckDuckGo fallback behind it —
    one job, and two spellings of it make the model choose."""
    assert "web_search" not in registry().names()
    assert "tavily_search" in registry().names()


def test_an_unconfigured_search_fails_instead_of_returning_its_sentinel():
    """`_tavily_search` returns the bare string "TAVILY_UNCONFIGURED" with no
    key set. As a RESULT that reads as data, and the model narrates it or
    invents an answer around it."""
    engine = FakeEngine(at.TAVILY_UNCONFIGURED)
    try:
        run(registry().executor(engine)(call("tavily_search", query="news")))
    except at.ToolFailure as exc:
        assert "not configured" in str(exc)
        assert at.TAVILY_UNCONFIGURED not in str(exc)
    else:
        raise AssertionError("the sentinel was handed to the model as an answer")


def test_an_ordinary_search_result_is_untouched():
    engine = FakeEngine("Sunny, 24 degrees.")
    assert run(registry().executor(engine)(call("tavily_search", query="weather"))) \
        == "Sunny, 24 degrees."


def test_a_formatter_that_declares_failure_is_not_swallowed():
    """The executor deliberately swallows formatter EXCEPTIONS so a broken
    adapter cannot lose a real result. A `ToolFailure` is a verdict, not an
    accident, and has to survive that — which is what `_tavily_guard` relies
    on."""
    from dataclasses import replace

    def verdict(_output):
        raise at.ToolFailure("deliberate verdict")

    def accident(_output):
        raise ValueError("a broken adapter")

    reg = registry()
    reg._tools["system_status"] = replace(reg.get("system_status"),
                                          format_output=verdict)
    try:
        run(reg.executor(FakeEngine("cpu 4%"))(call("system_status")))
    except at.ToolFailure as exc:
        assert "deliberate verdict" in str(exc)
    else:
        raise AssertionError("a formatter's ToolFailure was swallowed")

    reg._tools["system_status"] = replace(reg.get("system_status"),
                                          format_output=accident)
    assert run(reg.executor(FakeEngine("cpu 4%"))(call("system_status"))) == \
        "cpu 4%", "a broken formatter lost a real result"


# ── tiers, findability, wired intents ───────────────────────────────────────

def test_the_whole_wave_is_auto():
    reg = registry()
    for name in WAVE5:
        assert reg.tier_of(name) == "AUTO", f"{name} is {reg.tier_of(name)}"


def test_the_wave_is_findable_by_plain_words():
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=False)
    for query, expected in (
        ("click the button on the page", "web_click"),
        ("type into the form field", "web_type"),
        ("scroll further down the page", "web_scroll"),
        ("go back to the previous page", "web_back"),
        ("find a picture of a red panda", "web_search_image"),
    ):
        names = [h.name for h in s.search(query)]
        assert expected in names, f"{query!r} did not surface {expected}: {names}"


def test_wave_five_did_not_change_what_the_wired_intents_offer():
    reg = registry()
    assert len(reg.set_names("research")) == 6
    assert not set(reg.set_names("research")) & set(WAVE5)


def test_every_wave_five_action_is_in_the_shared_tier_fixture():
    reg = registry()
    for name in WAVE5:
        assert reg.get(name).action_type in TIERS, name


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
