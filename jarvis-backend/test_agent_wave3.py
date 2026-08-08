"""Harness for §6.8.2 wave 3 — applications, the desk display, the machine.

What this wave has to get right is not phrasing, it is BOUNDARIES. Three pairs
of tools now sit next to each other and mean different things:

    open a program   vs   open a web page   vs   open an app on the TV
    the desk's audio vs   the television's audio
    show him a panel vs   fetch the data yourself

A model that picks the wrong one of a pair does something visible and wrong, so
each of those is pinned here rather than left to the description alone.

Also pinned: the HUD bridge now carries THREE kinds of frame, one of which is a
bare sentinel string (`close_app` on the HUD's own player), and one result that
produces TWO frames — so the bridge returns a list, and a partial send is worse
than none.
"""

import asyncio
import sys

from agent_tier_fixture import TIERS, tier_lookup
from modules import agent_tools as at
from modules.agent_search import ToolShelf
from modules.tool_calls import ToolCall

WAVE3 = ("native_app_launcher", "close_app", "hud_open_widget",
         "hud_close_widget", "os_control", "os_macro", "open_link")


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

def test_the_single_argument_tools_pass_their_value_through():
    assert target("native_app_launcher", app="vs code") == "vs code"
    assert target("close_app", app="chrome") == "chrome"
    assert target("open_link", url="example.com") == "example.com"
    assert target("hud_open_widget", widget="calendar") == "calendar"
    assert target("os_control", command="lock_screen") == "lock_screen"


def test_a_macro_with_no_override_sends_a_bare_name():
    """Handler: `"deep_work"` or `"deep_work:<url>"`. A trailing bare colon would
    make it treat an empty string as the override."""
    assert target("os_macro", macro="deep_work") == "deep_work"


def test_a_macro_override_keeps_the_urls_own_colon():
    """`MacroAgent` splits on the FIRST colon, so `http://…` survives intact."""
    got = target("os_macro", macro="deep_work", url="http://localhost:5173")
    assert got == "deep_work:http://localhost:5173"
    assert got.split(":", 1)[1] == "http://localhost:5173"


# ── the HUD bridge, now three frame kinds ───────────────────────────────────

def test_opening_a_panel_reaches_the_display():
    sent = []
    engine = FakeEngine({"action_type": "hud_open_widget", "widget": "calendar"})
    out = run(registry().executor(engine, payload_sink=sent.append)(
        call("hud_open_widget", widget="calendar")))
    assert sent == [{"type": "ui_state", "open_widget": "calendar"}]
    assert "calendar" in out and "open" in out


def test_closing_a_panel_reaches_the_display():
    sent = []
    engine = FakeEngine({"action_type": "hud_close_widget", "widget": "camera"})
    run(registry().executor(engine, payload_sink=sent.append)(
        call("hud_close_widget", widget="camera")))
    assert sent == [{"type": "ui_state", "close_widget": "camera"}]


def test_closing_hud_media_sends_both_frames_not_one():
    """`close_app` on the HUD's own player returns a bare sentinel string and
    main.py answers it with TWO frames. Sending one leaves the display half
    changed, which is why the bridge returns a list."""
    sent = []
    engine = FakeEngine(at.HUD_MEDIA_CLOSE)
    out = run(registry().executor(engine, payload_sink=sent.append)(
        call("close_app", app="the music")))
    assert sent == [{"status": "close_search", "message": "Clearing HUD media."},
                    {"status": "toggle_browser", "visible": False}]
    assert "operating-system level" in out, out


def test_the_sentinel_never_reaches_the_model_as_a_result():
    """`HUD_MEDIA_CLOSE_REQUEST` handed back verbatim is unreadable — the model
    would either narrate the sentinel or treat it as an error."""
    sent = []
    engine = FakeEngine(at.HUD_MEDIA_CLOSE)
    out = run(registry().executor(engine, payload_sink=sent.append)(
        call("close_app", app="hud media")))
    assert at.HUD_MEDIA_CLOSE not in out


def test_a_panel_tool_with_no_display_fails_instead_of_claiming_success():
    engine = FakeEngine({"action_type": "hud_open_widget", "widget": "vitals"})
    try:
        run(registry().executor(engine)(call("hud_open_widget", widget="vitals")))
    except at.ToolFailure as exc:
        assert "nothing would have happened" in str(exc)
    else:
        raise AssertionError("a panel was reported as opened with no display")


def test_an_ordinary_app_close_is_not_a_hud_frame():
    sent = []
    engine = FakeEngine("Closed Chrome, sir.")
    out = run(registry().executor(engine, payload_sink=sent.append)(
        call("close_app", app="chrome")))
    assert out == "Closed Chrome, sir." and sent == []


# ── the enums keep the model inside what the handlers accept ────────────────

def test_an_invented_panel_is_refused_rather_than_becoming_vitals():
    """`_normalize_hud_widget_id` defaults anything it does not recognise to
    "vitals", so without the enum "open the stock ticker" would silently open
    his heart rate."""
    problem = registry().schema_problem(call("hud_open_widget", widget="stocks"))
    assert problem and "vitals" in problem


def test_every_panel_id_the_enum_offers_is_one_the_hud_resolves():
    """The enum is a copy of the normaliser's exact-match cases. If the HUD's
    list changes, this is where the copy is caught."""
    from pathlib import Path
    source = Path("action_engine.py").read_text(encoding="utf-8", errors="replace")
    head, _, rest = source.partition("def _normalize_hud_widget_id")
    assert rest, "the HUD normaliser was renamed — the enum has no source of truth"
    body = rest.split("\n    def ", 1)[0]
    for widget in at.HUD_WIDGETS:
        assert f'return "{widget}"' in body, f"the HUD no longer resolves {widget}"


def test_an_unknown_machine_command_is_refused():
    problem = registry().schema_problem(call("os_control", command="shutdown"))
    assert problem and "lock_screen" in problem


def test_an_unknown_macro_is_refused():
    problem = registry().schema_problem(call("os_macro", macro="party_mode"))
    assert problem and "deep_work" in problem


# ── the boundaries: three pairs a model can confuse ─────────────────────────

def test_opening_a_program_a_page_and_a_tv_app_each_name_the_others():
    reg = registry()
    launcher = reg.get("native_app_launcher").description
    assert "open_link" in launcher and "tv_launch_app" in launcher
    assert "web_browse" in reg.get("open_link").description


def test_the_two_audio_tools_say_which_speakers_they_move():
    reg = registry()
    assert "tv_volume" in reg.get("os_control").description
    assert "room" in reg.get("tv_volume").description


def test_showing_a_panel_is_distinguished_from_fetching_the_data():
    """A model that opens the calendar panel and then answers "what's on today"
    from nothing has invented an answer. The description says the panel returns
    nothing and names the tools that do."""
    description = registry().get("hud_open_widget").description
    assert "check_calendar" in description
    assert "does not read anything back" in description


# ── what was deliberately left out ──────────────────────────────────────────

def test_the_weaker_launcher_was_not_registered():
    assert "launch_app" not in registry().names()
    assert "native_app_launcher" in registry().names()


def test_focus_mode_is_not_registered_because_the_engine_cannot_deliver_it():
    """`action_engine` returns "Focus mode enabled. Notifications silenced." and
    does nothing — the real work is in main.py's dispatcher, out of the agent
    layer's reach. Registering it would announce a state change that never
    happened."""
    from pathlib import Path
    names = registry().names()
    assert "enable_focus_mode" not in names and "disable_focus_mode" not in names
    source = Path("action_engine.py").read_text(encoding="utf-8", errors="replace")
    assert 'return "Focus mode enabled. Notifications silenced."' in source, \
        "if the engine learned to really do this, it can be registered"


# ── tiers, findability, and the wired intents ───────────────────────────────

def test_the_whole_wave_is_auto():
    reg = registry()
    for name in WAVE3:
        assert reg.tier_of(name) == "AUTO", f"{name} is {reg.tier_of(name)}"


def test_the_wave_is_findable_by_plain_words():
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=False)
    for query, expected in (
        ("open notepad on the computer", "native_app_launcher"),
        ("close chrome", "close_app"),
        ("show me the calendar panel", "hud_open_widget"),
        ("lock the workstation", "os_control"),
        ("set up deep work mode", "os_macro"),
        ("open this web page in my browser", "open_link"),
    ):
        names = [h.name for h in s.search(query)]
        assert expected in names, f"{query!r} did not surface {expected}: {names}"


def test_wave_three_did_not_change_what_the_wired_intents_offer():
    reg = registry()
    assert len(reg.set_names("research")) == 6
    assert not set(reg.set_names("research")) & set(WAVE3)
    assert not set(reg.set_names("authoring")) & set(WAVE3)


def test_every_wave_three_action_is_in_the_shared_tier_fixture():
    reg = registry()
    for name in WAVE3:
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
