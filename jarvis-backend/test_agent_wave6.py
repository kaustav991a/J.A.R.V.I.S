"""Harness for §6.8.2 wave 6 — the people, the house, and what is left.

Eleven tools, and this is the wave where a wrong call reaches another human
being. So the rows here are weighted towards the two partner tools: that the
message one needs approval and cannot be handed a raw chat id, and that the
discreet answer and the content answer stay separate — the §6.7 decision, which
is a decision about privacy and not about wording.

The rest is the remainder of the catalogue, and the exclusions matter as much as
the entries: four reachable actions were left out with reasons, and this harness
pins the reasons so a later wave does not "finish the job" by registering
something that cannot work.
"""

import asyncio
import json
import sys

from agent_tier_fixture import TIERS, tier_lookup
from modules import agent_tools as at
from modules.agent_search import ToolShelf
from modules.tool_calls import ToolCall

WAVE6_AUTO = ("partner_contact_status", "summarize_partner_chat",
              "telegram_send_file", "remember_fact", "check_vitals",
              "movie_protocol", "sleep_protocol", "render_chart")
WAVE6_CONFIRM = ("create_note", "organize_downloads")


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


# ── the message that reaches a person, and why it is NOT here ───────────────

def test_the_loop_still_cannot_message_a_person():
    """A standing decision from 2026-07-26, not a gap this wave forgot to fill.
    `test_partner_messaging` asserts the action name is absent from
    `agent_tools` entirely; this asserts the same thing from the registry's
    side, so removing one guard leaves the other standing."""
    assert "message_partner" not in registry().names()
    for hit in ToolShelf(registry(), base=["system_status"],
                         allow_confirm=True).search("text my girlfriend"):
        assert hit.name != "message_partner"


def test_the_reason_is_recorded_where_the_next_wave_will_look():
    """CONFIRM is not the answer to it: away, a CONFIRM parks and pings his
    phone, so an approval tapped at a bus stop would send a private message
    whose words the loop chose. The file has to say so, or a later wave
    "finishes the catalogue" and quietly crosses that line."""
    from pathlib import Path
    source = Path("modules/agent_tools.py").read_text(encoding="utf-8",
                                                      errors="replace")
    assert "must not be able to message a person on its own" in source
    assert "PARKS" in source


# ── the two ways of asking about her, which are not interchangeable ─────────

def test_the_discreet_answer_and_the_content_answer_name_each_other():
    """Roadmap §6.7: "did she message" is answered without repeating a word;
    "what did she say" is a different and more explicit request. A model that
    treats them as synonyms leaks content he did not ask for."""
    reg = registry()
    assert "summarize_partner_chat" in reg.get("partner_contact_status").description
    assert "partner_contact_status" in reg.get("summarize_partner_chat").description


def test_the_discreet_answer_says_it_carries_no_content():
    description = registry().get("partner_contact_status").description
    assert "NO content" in description


def test_the_content_tool_says_it_depends_on_a_switch_he_controls():
    """It only works when transcript logging is on, and it says so — otherwise
    the model reports "she said nothing" for a store that was never written."""
    assert "logging" in registry().get("summarize_partner_chat").description


def test_asking_about_a_partner_defaults_to_no_name_rather_than_a_guess():
    assert target("partner_contact_status") == ""
    assert target("summarize_partner_chat", who="girlfriend") == "girlfriend"


# ── target composition for the rest ─────────────────────────────────────────

def test_a_remembered_fact_always_carries_its_category():
    """`_remember_fact` splits on the FIRST colon and defaults an unknown
    category to "Fact". Emitting the colon always means a fact containing one
    cannot be read as a category."""
    assert target("remember_fact", fact="he prefers tea") == "Fact: he prefers tea"
    assert target("remember_fact", fact="he prefers tea", category="Preference") \
        == "Preference: he prefers tea"
    got = target("remember_fact", fact="the ratio is 3:1")
    assert got.split(":", 1) == ["Fact", " the ratio is 3:1"]


def test_a_note_composes_title_then_body():
    assert target("create_note", title="Sprint Plan", content="ship the exe") \
        == "Sprint Plan: ship the exe"
    assert target("create_note", title="Groceries") == "Groceries"


def test_a_colon_in_a_note_title_is_refused():
    """The title is also what the FILENAME is made from, so a mis-split is
    visible on disk as well as in the note."""
    problem = at._note_precondition({"title": "Meeting: Tuesday"})
    assert problem and "colon" in problem
    assert at._note_precondition({"title": "Meeting", "content": "Tuesday"}) is None


def test_the_file_send_requires_an_absolute_path():
    """Same sandbox lesson as `workspace_read` (§6.8.1 gap G): a relative path
    resolves against whichever root the far end happens to use."""
    reg = registry()
    decision = reg.authorizer()(call("telegram_send_file", path="report.pdf"))
    assert decision.allowed is False
    assert "absolute" in decision.reason.lower()


def test_the_file_send_carries_its_caption():
    got = target("telegram_send_file", path=r"F:\out\report.pdf", caption="Q3")
    assert got == {"path": r"F:\out\report.pdf", "caption": "Q3"}


def test_the_no_argument_tools_send_nothing():
    for name in ("check_vitals", "movie_protocol", "sleep_protocol",
                 "organize_downloads"):
        assert target(name) == "", name


# ── the chart: data the model supplies, a picture only he sees ──────────────

def test_a_chart_spec_goes_over_as_a_dict():
    got = target("render_chart", title="Spend", chart_type="pie",
                 data=[{"label": "rent", "value": 1200}])
    assert got == {"title": "Spend", "type": "pie",
                   "data": [{"label": "rent", "value": 1200}]}


def test_a_chart_defaults_to_bar_rather_than_failing():
    assert target("render_chart", title="X", data=[])["type"] == "bar"


def test_a_drawn_chart_reaches_the_display():
    sent = []
    payload = json.dumps({"ui_action": "render_chart", "title": "Spend",
                          "chart_type": "pie",
                          "data": [{"label": "rent", "value": 1200.0}]})
    out = run(registry().executor(FakeEngine(payload), payload_sink=sent.append)(
        call("render_chart", title="Spend", data=[{"label": "rent", "value": 1200}])))
    assert sent and sent[0]["ui_action"] == "render_chart"
    assert "Spend" in out and "cannot" in out


def test_a_chart_with_no_usable_data_is_a_result_not_a_frame():
    """The handler answers "I don't have structured data to chart, sir." in
    plain text. Forwarding that would draw nothing and report a chart."""
    sent = []
    out = run(registry().executor(
        FakeEngine("I don't have structured data to chart, sir."),
        payload_sink=sent.append)(call("render_chart", title="X", data=[])))
    assert sent == []
    assert "structured data" in out


# ── the exclusions, and why each one stays out ─────────────────────────────

def test_the_duplicate_telemetry_action_is_absent():
    """`get_telemetry` and `system_status` dispatch to the same call."""
    from pathlib import Path
    assert "get_telemetry" not in registry().names()
    source = Path("action_engine.py").read_text(encoding="utf-8", errors="replace")
    assert source.count("self.telemetry_agent.get_summary_string") >= 2, \
        "they no longer share a handler — reconsider registering both"


def test_the_actions_whose_effect_lives_in_main_are_absent():
    """Same category as focus mode in wave 3: the engine returns a sentence and
    main.py does the work."""
    names = registry().names()
    assert "close_display" not in names
    assert "enable_focus_mode" not in names


def test_the_long_running_and_self_editing_actions_are_absent():
    """`run_autopilot` outlives the loop's 120 s wall clock; `self_improve`
    rewrites JARVIS's own source and belongs behind its own guard rails."""
    names = registry().names()
    assert "run_autopilot" not in names and "self_improve" not in names


def test_the_blind_gui_actions_are_absent():
    """They drive the real mouse and keyboard against whatever window has
    focus, with nothing in the loop to verify the target first."""
    names = registry().names()
    for absent in ("gui_action", "agentic_gui_task", "ghost_type",
                   "ghost_save_file"):
        assert absent not in names


# ── tiers, findability, wired intents ───────────────────────────────────────

def test_the_tiers_match_the_ruleset():
    reg = registry()
    for name in WAVE6_AUTO:
        assert reg.tier_of(name) == "AUTO", f"{name} is {reg.tier_of(name)}"
    for name in WAVE6_CONFIRM:
        assert reg.tier_of(name) == "CONFIRM", f"{name} is {reg.tier_of(name)}"


def test_the_wave_is_findable_by_plain_words():
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=True)
    for query, expected in (
        ("did my girlfriend message me today", "partner_contact_status"),
        ("what did she say to you", "summarize_partner_chat"),
        ("send this file to my phone", "telegram_send_file"),
        ("remember that i prefer tea", "remember_fact"),
        ("how is my heart rate and sleep", "check_vitals"),
        ("set the room up for a film", "movie_protocol"),
        ("draw me a chart of this", "render_chart"),
        ("tidy my downloads folder", "organize_downloads"),
    ):
        names = [h.name for h in s.search(query)]
        assert expected in names, f"{query!r} did not surface {expected}: {names}"


def test_wave_six_did_not_change_what_the_wired_intents_offer():
    reg = registry()
    assert len(reg.set_names("research")) == 6
    assert len(reg.set_names("authoring")) == 6
    assert not set(reg.set_names("research")) & set(WAVE6_AUTO + WAVE6_CONFIRM)


def test_every_wave_six_action_is_in_the_shared_tier_fixture():
    reg = registry()
    for name in WAVE6_AUTO + WAVE6_CONFIRM:
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
