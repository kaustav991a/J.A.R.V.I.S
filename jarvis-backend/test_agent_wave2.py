"""Harness for §6.8.2 wave 2 — the television, and music on the desk.

Three things are pinned here, and only the first is about descriptions.

1. **Target composition.** Every entry builds a string that a handler then
   splits — on a pipe (`tv_volume`), on a colon (`tv_play_media`), or on word
   boundaries (`play_music`). A wrong separator does not fail loudly; it plays
   the wrong thing on the wrong screen.

2. **The HUD bridge.** `play_music`'s result is not an answer, it is an
   INSTRUCTION to the desk display. In the agent loop a result goes to the
   model, not to the screen, so without the bridge the run would report music
   that never played. The tests below drive the real executor and assert on
   what reached the sink.

3. **The two screens stay apart.** The failure this wave is most likely to
   cause is "play X" landing on the television when he meant the desk, or the
   reverse, so each description names its screen and points at its opposite.
"""

import asyncio
import sys

from agent_tier_fixture import TIERS, tier_lookup
from modules import agent_tools as at
from modules.agent_search import ToolShelf
from modules.media_query import clean_music_query
from modules.tool_calls import ToolCall

WAVE2 = ("tv_power", "tv_volume", "tv_control", "tv_launch_app",
         "tv_play_media", "tv_type", "play_music")


def registry():
    return at.build_default_registry(tier_lookup())


def call(name, **args):
    return ToolCall(id="c1", name=name, arguments=args)


def target(name, **args):
    return registry().to_payload(call(name, **args))["target"]


def run(coro):
    return asyncio.run(coro)


class FakeEngine:
    """Stands in for ActionEngine: records payloads, returns a meta dict."""

    def __init__(self, result="RESULT", state="COMPLETE"):
        self.result, self.state = result, state
        self.seen = []

    async def execute_with_retry(self, payload, return_meta=False, trace_id=None, *,
                                 governance_bypass=False, permission_tier="admin"):
        self.seen.append(payload)
        return {"trace_id": "t", "state": self.state, "result": self.result}


# ── target composition ──────────────────────────────────────────────────────

def test_tv_volume_sends_a_bare_direction_when_no_count_is_given():
    """Handler: `"up"`, or `"up|5"` — it partitions on the first pipe and would
    read a trailing bare pipe as an empty count."""
    assert target("tv_volume", direction="up") == "up"
    assert target("tv_volume", direction="down", steps=3) == "down|3"


def test_mute_never_carries_a_count():
    """`_tv_volume` returns `execute_action("tv_mute")` before it looks at the
    count, so a count on a mute is noise that only makes the target harder to
    read if the far end ever changes."""
    assert target("tv_volume", direction="mute", steps=5) == "mute"


def test_tv_play_media_composes_app_then_title():
    """Handler: `app, query = target.split(":", 1)`."""
    got = target("tv_play_media", title="Stranger Things", app="netflix")
    app, _, query = got.partition(":")
    assert app.strip() == "netflix" and query.strip() == "Stranger Things", got


def test_tv_play_media_without_an_app_sends_a_bare_title():
    """No colon is the far end's own signal to ask which app to use — it must
    not be faked with an empty app."""
    assert target("tv_play_media", title="Stranger Things") == "Stranger Things"


def test_a_colon_in_the_title_is_refused_when_no_app_is_named():
    """Rule 3, in code. "Mission: Impossible" would split into app "mission",
    and the TV answers "that app isn't wired up yet" — a dead end the model
    cannot diagnose from the message."""
    problem = at._tv_media_precondition({"title": "Mission: Impossible"})
    assert problem and "colon" in problem
    assert at._tv_media_precondition(
        {"title": "Mission: Impossible", "app": "netflix"}) is None


def test_the_precondition_is_wired_to_the_tool_not_just_defined():
    reg = registry()
    decision = reg.authorizer()(call("tv_play_media", title="Mission: Impossible"))
    assert decision.allowed is False and "colon" in decision.reason


def test_the_pass_through_tools_send_their_one_argument_unchanged():
    assert target("tv_type", text="stranger things") == "stranger things"
    assert target("tv_launch_app", app="netflix") == "netflix"
    assert target("tv_control", key="play_pause") == "play_pause"


def test_tv_power_takes_no_target():
    assert target("tv_power") == ""


# ── play_music: composition and the parse at the far end ────────────────────

def test_play_music_sends_plain_words_for_youtube():
    assert target("play_music", query="moonlight") == "moonlight"
    assert target("play_music", query="moonlight", service="youtube") == "moonlight"


def test_play_music_names_spotify_in_the_target_because_that_is_the_switch():
    """`_play_music` chooses the service by looking for the WORD spotify in the
    target — there is no separate field to set."""
    assert target("play_music", query="moonlight", service="spotify") == \
        "moonlight spotify"


def test_an_empty_query_still_opens_the_service():
    assert target("play_music") == ""
    assert target("play_music", service="spotify") == "spotify"


def test_the_composed_target_survives_the_handlers_own_parse():
    """Composition and parse are two halves of one contract, so test them
    together: what the registry builds must come back out of
    `clean_music_query` as the song that was asked for."""
    for query, service, expected in (
        ("moonlight", "youtube", ("youtube", "moonlight")),
        ("moonlight", "spotify", ("spotify", "moonlight")),
        ("only girl in the world", "youtube", ("youtube", "only girl in the world")),
    ):
        got = clean_music_query(target("play_music", query=query, service=service))
        assert got == expected, f"{query} on {service} -> {got}"


def test_a_title_containing_on_is_no_longer_eaten():
    """The regression that made this wave fix a handler. The service words used
    to be stripped as SUBSTRINGS, so "Moonlight" searched for "Molight" and
    "Only Girl" searched for "ly Girl" — quietly, because a search engine
    returns something for either."""
    assert clean_music_query("moonlight")[1] == "moonlight"
    assert clean_music_query("only girl")[1] == "only girl"
    assert clean_music_query("con te partiro")[1] == "con te partiro"


def test_the_carrier_word_is_still_stripped_when_it_is_a_word():
    assert clean_music_query("moonlight on youtube") == ("youtube", "moonlight")
    assert clean_music_query("halo on spotify") == ("spotify", "halo")


# ── the HUD bridge: a result that INSTRUCTS the screen ──────────────────────

def test_a_play_frame_reaches_the_display_and_the_model_is_told_it_played():
    sent = []
    engine = FakeEngine({"success": True, "action_type": "play_youtube",
                         "url": "https://youtu.be/x"})
    execute = registry().executor(engine, payload_sink=lambda f: sent.append(f))
    out = run(execute(call("play_music", query="moonlight")))
    assert sent == [{"status": "play_youtube", "url": "https://youtu.be/x"}]
    assert "https://youtu.be/x" in out


def test_an_async_sink_is_awaited():
    """The real sink is `socket_manager.send_ui_update`, a coroutine. A sink
    that is merely CALLED and not awaited sends nothing and raises no error."""
    sent = []

    async def sink(frame):
        sent.append(frame)

    engine = FakeEngine({"action_type": "play_youtube", "url": "https://youtu.be/x"})
    run(registry().executor(engine, payload_sink=sink)(call("play_music", query="q")))
    assert len(sent) == 1


def test_with_no_display_attached_the_tool_fails_instead_of_claiming_success():
    """The whole reason the bridge exists. A run with nowhere to send the frame
    must not tell the model the music is playing."""
    engine = FakeEngine({"action_type": "play_youtube", "url": "https://youtu.be/x"})
    execute = registry().executor(engine)          # no sink
    try:
        run(execute(call("play_music", query="moonlight")))
    except at.ToolFailure as exc:
        assert "nothing would have happened" in str(exc)
        assert "playing" not in str(exc).lower()
    else:
        raise AssertionError("a HUD-effect tool reported success with no display")


def test_only_named_frames_are_forwarded():
    """Not "forward any dict". `list_directory` returns one too, and its effect
    is information that is already delivered as text — broadcasting it would
    redraw the HUD in the middle of an agent run."""
    assert at.hud_frames({"ui_action": "render_file_list", "data": []}) == []
    assert at.hud_frames("plain text") == []
    assert at.hud_frames({"action_type": "play_youtube"}) == []       # no url


def test_a_tool_that_answers_normally_is_untouched_by_the_bridge():
    sent = []
    engine = FakeEngine("TV volume up by 3, Sir.")
    out = run(registry().executor(engine, payload_sink=lambda f: sent.append(f))(
        call("tv_volume", direction="up", steps=3)))
    assert out == "TV volume up by 3, Sir." and sent == []


# ── schema: the enums are enforced, not merely documented ───────────────────

def test_an_invalid_direction_is_refused_with_the_choices_listed():
    problem = registry().schema_problem(call("tv_volume", direction="louder"))
    assert problem and "'up'" in problem and "'mute'" in problem


def test_an_unwired_tv_app_cannot_be_requested():
    """The far end answers "that TV app isn't wired up yet" for anything outside
    its five deep links — a dead end that the enum prevents ever reaching."""
    problem = registry().schema_problem(
        call("tv_play_media", title="Dune", app="jiocinema"))
    assert problem and "netflix" in problem


# ── tiers and findability ───────────────────────────────────────────────────

def test_the_whole_wave_is_auto():
    """Governance's ruling, mirrored here so a re-tier shows up as a test
    change: a keypress on a TV is undone by another keypress."""
    reg = registry()
    for name in WAVE2:
        assert reg.tier_of(name) == "AUTO", f"{name} is {reg.tier_of(name)}"


def test_the_wave_is_findable_including_in_an_unattended_run():
    """Unlike wave 1's writing half, nothing here needs a human, so nothing here
    is hidden when there is nobody at the desk."""
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=False)
    for query, expected in (
        ("turn on the tv", "tv_power"),
        ("turn the tv volume up", "tv_volume"),
        ("open netflix on the tv", "tv_launch_app"),
        ("play stranger things on the tv", "tv_play_media"),
        ("play some music", "play_music"),
        ("type into the tv search box", "tv_type"),
    ):
        names = [h.name for h in s.search(query)]
        assert expected in names, f"{query!r} did not surface {expected}: {names}"


def test_tv_search_was_deliberately_not_registered():
    """Its handler is one line — `tv_play_media(f"youtube:{query}")` — so it is
    `tv_play_media` with the app pre-chosen. Same rule that kept `check_email`
    out of wave 1."""
    assert "tv_search" not in registry().names()


def test_wave_two_did_not_change_what_the_wired_intents_offer():
    reg = registry()
    assert len(reg.set_names("research")) == 6
    assert not set(reg.set_names("research")) & set(WAVE2)
    assert not set(reg.set_names("files")) & set(WAVE2)


def test_every_wave_two_action_is_in_the_shared_tier_fixture():
    reg = registry()
    for name in WAVE2:
        assert reg.get(name).action_type in TIERS, name


# ── the two screens ─────────────────────────────────────────────────────────

def test_each_playing_tool_points_at_the_other_screen():
    """Rule 1. "Play X" is ambiguous by nature in a room with two screens, so
    neither tool may describe itself without naming the other."""
    reg = registry()
    assert "play_music" in reg.get("tv_play_media").description
    assert "tv_play_media" in reg.get("play_music").description


def test_the_navigation_tool_names_the_tools_it_is_not():
    """`tv_control`'s handler also accepts power, mute and volume keys. Those
    are not in its enum, so the description has to send the model somewhere."""
    description = registry().get("tv_control").description
    assert "tv_power" in description and "tv_volume" in description


def test_the_power_tool_warns_that_it_is_a_toggle():
    """There is no way to read TV power state, so "make sure it is on" turns it
    off. That has to be in the description; nothing else can catch it."""
    description = registry().get("tv_power").description
    assert "TOGGLE" in description or "toggle" in description
    assert "off" in description


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
