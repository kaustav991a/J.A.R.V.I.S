"""Harness for §6.8.2 wave 1 — the email + calendar tools.

A registry entry is not "just a description". Every one of these composes a
`target` STRING that an `action_engine` handler then splits, so a wrong
separator does not fail loudly — it mails the subject line as the body, or
searches for the wrong thing. The target-composition checks below are written
against the handlers' documented formats, quoted at each test.

Also pinned: the tools are FINDABLE (the whole point of wave 1 — they are
reachable through `search_tools`, not wired into every intent), and the writing
half is CONFIRM, since each of those either leaves the machine or wipes a day.
"""

import sys

from agent_tier_fixture import TIERS, tier_lookup
from modules import agent_search as ags
from modules import agent_tools as at
from modules.agent_search import ToolShelf
from modules.tool_calls import ToolCall

WAVE1_READ = ("gmail_read_unread", "gmail_read", "search_email", "read_email",
              "check_calendar", "morning_briefing")
WAVE1_WRITE = ("gmail_send", "gmail_reply", "create_event", "clear_schedule")


def registry():
    return at.build_default_registry(tier_lookup())


def target(name, **args):
    return registry().to_payload(ToolCall(id="c1", name=name, arguments=args))["target"]


# ── target composition: where a wrong string sends the wrong mail ────────────

def _mail_fields(**kw):
    """Build gmail_send's target and parse it the way `_send_email` does.

    Asserting on the PROPERTY (the three fields arrive as written) rather than
    on the wire format. The previous version of these two tests pinned the
    delimited string itself, which is why neither noticed that the handler had
    moved from `split("|")` to `split("|", 2)` underneath them — the docstring
    still described a truncation the code had already stopped doing.
    """
    import json as _json

    got = target("gmail_send", **kw)
    stripped = str(got).strip()
    if stripped.startswith("{"):
        d = _json.loads(stripped)
        return (str(d.get("to", "")).strip(), str(d.get("subject", "")).strip(),
                str(d.get("body", "")).strip())
    parts = [p.strip() for p in stripped.split("|", 2)]
    return tuple((parts + ["", "", ""])[:3])


def test_gmail_send_carries_all_three_fields():
    got = _mail_fields(to="a@b.com", subject="Invoice", body="Please see attached.")
    assert got == ("a@b.com", "Invoice", "Please see attached."), got


def test_a_pipe_in_the_subject_does_not_move_it_into_the_body():
    """The finding. "Re: Q3 | final" is an ordinary subject line.

    Under the delimited format it parsed as subject="Re: Q3",
    body="final | ...", and the email SENT that way with nobody told. The body
    case was already safe (maxsplit=2 keeps the tail); the subject case was not.
    """
    to, subject, body = _mail_fields(
        to="a@b.com", subject="Re: Q3 | final", body="Here is the report.")
    assert subject == "Re: Q3 | final", subject
    assert body == "Here is the report.", body
    assert to == "a@b.com", to


def test_a_body_containing_a_pipe_does_not_shift_the_fields():
    to, subject, body = _mail_fields(to="a@b.com", subject="S", body="one | two")
    assert (to, subject) == ("a@b.com", "S"), (to, subject)
    assert body == "one | two", f"the body lost a pipe: {body!r}"


def test_gmail_reply_composes_thread_then_body():
    """Handler: `target format: "thread_id | reply body"`."""
    got = target("gmail_reply", thread_id="18f0a", body="Thanks, will do.")
    assert [p.strip() for p in got.split("|", 1)] == ["18f0a", "Thanks, will do."], got


def test_gmail_read_sends_a_bare_query_when_no_count_is_given():
    """Handler: `"query"` or `"query|N"`. A trailing bare pipe would make it
    parse an empty string as the limit."""
    assert target("gmail_read", query="is:unread") == "is:unread"
    assert target("gmail_read", query="is:unread", max_results=3) == "is:unread|3"


def test_gmail_read_unread_sends_an_empty_target_for_the_default():
    """Handler: `""` or `"inbox"` -> top 5; `"N"` -> top N."""
    assert target("gmail_read_unread") == ""
    assert target("gmail_read_unread", count=10) == "10"


def test_read_email_defaults_to_latest():
    """Handler: `index = target if target else "latest"` — but an explicit
    default is clearer than relying on the handler's fallback, and it survives
    a change at the far end."""
    assert target("read_email") == "latest"
    assert target("read_email", which="2") == "2"


def test_create_event_passes_the_phrase_through_unchanged():
    assert target("create_event", description="dentist Thursday 4pm") == \
        "dentist Thursday 4pm"


def test_the_no_argument_tools_send_an_empty_target():
    for name in ("check_calendar", "morning_briefing", "clear_schedule"):
        assert target(name) == "", name


# ── tiers: everything that leaves the machine needs a human ──────────────────

def test_every_writing_tool_is_confirm():
    reg = registry()
    for name in WAVE1_WRITE:
        assert reg.tier_of(name) == "CONFIRM", f"{name} is {reg.tier_of(name)}"


def test_every_reading_tool_is_auto():
    reg = registry()
    for name in WAVE1_READ:
        assert reg.tier_of(name) == "AUTO", f"{name} is {reg.tier_of(name)}"


def test_a_writing_tool_is_refused_in_an_unattended_run():
    reg = registry()
    decision = reg.authorizer()(ToolCall(id="c1", name="gmail_send", arguments={
        "to": "a@b.com", "subject": "S", "body": "B"}))
    assert decision.allowed is False and "unattended" in decision.reason


def test_a_writing_tool_is_not_even_findable_in_an_unattended_run():
    """Offering mail-sending to a run with nobody to approve it only teaches the
    model to ask for refusals."""
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=False)
    assert "gmail_send" not in [h.name for h in s.search("send an email")]


# ── findability: the point of the wave ───────────────────────────────────────

def test_the_new_tools_are_findable_by_plain_words():
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=True)
    for query, expected in (
        ("check my email for anything new", "gmail_read_unread"),
        ("search my email from a person", "gmail_read"),
        ("what is on my calendar", "check_calendar"),
        ("send an email", "gmail_send"),
        ("reply to an email thread", "gmail_reply"),
        ("create a calendar event", "create_event"),
    ):
        names = [h.name for h in s.search(query)]
        assert expected in names, f"{query!r} did not surface {expected}: {names}"


def test_the_two_superseded_actions_were_deliberately_not_registered():
    """`check_email` duplicates `gmail_read_unread` (its own handler says so) and
    `send_email` duplicates `gmail_send`. Two tools for one job make the model
    guess, and a guess costs a step and sometimes the wrong result."""
    names = registry().names()
    assert "check_email" not in names
    assert "send_email" not in names


def test_the_unread_and_search_tools_tell_the_model_which_to_use():
    """Rule 1 — the description carries when NOT to call it. These two are the
    likeliest pair to be confused, so each points at the other."""
    reg = registry()
    assert "gmail_read" in reg.get("gmail_read_unread").description
    assert "gmail_read_unread" in reg.get("gmail_read").description


def test_the_reply_tool_says_where_the_thread_id_comes_from():
    """A thread id cannot be invented; without this the model fabricates one."""
    assert "gmail_read" in registry().get("gmail_reply").description


def test_the_destructive_calendar_tool_says_what_it_destroys():
    description = registry().get("clear_schedule").description
    assert "every event" in description and "today" in description.lower()


def test_wave_one_did_not_change_what_the_wired_intents_offer():
    """The wave is reachable through search, NOT bolted onto existing sets — an
    intent that was curated to 6 tools must still send 6."""
    reg = registry()
    assert len(reg.set_names("research")) == 6
    assert not set(reg.set_names("research")) & set(WAVE1_READ + WAVE1_WRITE)


def test_every_wave_one_action_is_in_the_shared_tier_fixture():
    """The fixture is what stops each wave breaking five harnesses; a tool added
    to the registry but not to the fixture fails every agent harness at import,
    which is a confusing way to learn it."""
    reg = registry()
    for name in WAVE1_READ + WAVE1_WRITE:
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
