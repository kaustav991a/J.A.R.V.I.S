"""Harness: he invented an appointment, in the goal that forbids inventing.

WHY THIS EXISTS
---------------
F-74, measured on the desk on 2026-08-29 **while verifying goal 1's own gate
batch**. Row `10.9`, "good morning", answered:

    "Good morning, Sir — it's actually 2:14 PM, Sir; your next scheduled
     match is at 7 PM, Sir."

There is no match. Checked rather than assumed, four ways:

  * the calendar returns `Your calendar is clear today` and
    `get_upcoming(720) == []`;
  * the desk's whole store — 62 facts, session digests, partner messages — has
    no "7 PM" and no "match" anywhere in it;
  * the commute schedule that really does hold a 7 PM office departure lives in
    the **cloud gateway**, which this machine cannot read: there is no
    `app_commute.json` here and no code that reads a departure;
  * it repeated on three consecutive retries, because a fabrication re-enters
    working memory and is read back as established context — a loop `brain.py`
    already names in a comment beside this very guard.

**The briefing already refused to do this.** `_strip_unsourced_state_claims`
drops a schedule sentence when the calendar was not read. The conversational
path did not, because `_strip_unfounded_conversational_claims` was built for a
different failure — claims about what JARVIS *did*, not claims about how the
operator's world *is*. One door guarded, the other not: root cause #4, again.

WHAT THIS PINS
--------------
Offline and pure. No model, no network — the guard is a function over a string.

  * the reported sentence is dropped, and the greeting beside it SURVIVES;
  * **evidence admits it.** With a real calendar read behind it, "your meeting is
    at 4 PM" is the feature working, not a fabrication;
  * a fact about the WORLD is none of this guard's business: "the match is at
    7 PM" is not a claim about him;
  * an admission is the sentence we want, not the one being removed;
  * ordinary conversation is untouched, and an untouched reply is returned
    **byte-identical** — this runs on every turn, and a guard that reflows every
    reply damages more than it repairs;
  * the evidence is CALENDAR-specific: any action having run is not permission to
    describe his diary;
  * when nothing survives, the fallback says which kind of claim went — answering
    a question about the diary with "I have no completed actions to report" is
    its own small dishonesty.

Run standalone: `python test_schedule_claims.py`
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import brain as b  # noqa: E402

_fails: list = []
_checks = 0


def check(ok: bool, why: str) -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"PASS  {why}")
    else:
        print(f"FAIL  {why}")
        _fails.append(why)


def guard(text, calendar_read=False, actions_ran=False):
    return b._strip_unfounded_conversational_claims(
        text, actions_ran=actions_ran, calendar_read=calendar_read)


# (sentence, calendar_read, should it be dropped, why this case is here)
CASES = [
    ("Your next scheduled match is at 7 PM, Sir.", False, True,
     "🛑 THE REPORTED SENTENCE — no calendar read, no such match"),
    ("Your meeting is at 4 PM, Sir.", False, True,
     "same shape, ordinary wording"),
    ("Your meeting is at 4 PM, Sir.", True, False,
     "with a real calendar read behind it this is the FEATURE, not a claim"),
    ("You have a flight at 6 AM tomorrow, Sir.", False, True,
     "'you have' is the same assertion as 'your'"),
    ("Your appointment is at noon, Sir.", False, True,
     "'noon' is a clock time"),
    ("The match is at 7 PM, Sir.", False, False,
     "a fact about the WORLD, not about him — none of this guard's business"),
    ("Your umbrella is by the door, Sir.", False, False,
     "possessive, but no scheduled commitment and no clock"),
    ("I could not reach your calendar, Sir, so I cannot say what is at 7 PM.",
     False, False,
     "an admission is the sentence we WANT — it names the topic and is true"),
    ("I don't have your schedule, Sir.", False, False,
     "the same, shorter"),
    ("Good afternoon, Sir. Anything I can do?", False, False,
     "ordinary conversation is untouched"),
    ("See you later, Sir.", False, False,
     "'later' is not a clock time"),
    ("Your meeting went well, I hope, Sir.", False, False,
     "no clock time: a pleasantry about a meeting is not a schedule claim"),

    # Found by watching the SAME defect walk around the first noun list, within
    # the hour. Three consecutive "good morning" turns produced, in order: a
    # question, a hedged guess, then a flat assertion with a clock on it.
    ("Your coding marathon until 4 AM remains on the agenda, Sir.", False, True,
     "🛑 the second live fabrication — nothing here mentions late-night coding, "
     "and 'agenda' was not in the first noun list"),
    ("Your coding marathon until 4 AM remains on the agenda, Sir.", True, False,
     "...and a real calendar read still admits it"),
    ("Another marathon coding session on the horizon, I presume?", False, False,
     "the HEDGED form two turns earlier is not a claim and must survive — it is "
     "how the persona works, and the turn after it is where the damage was"),
    ("Still set on coding until the early hours, Sir?", False, False,
     "a question about his evening is not an assertion about it"),
    ("Your train is due at 6:40, Sir.", False, True,
     "'due at' with a clock is the same assertion in fewer words"),
]


def test_every_case():
    for text, cal, should_drop, why in CASES:
        out = guard(text, calendar_read=cal)
        dropped = out.strip() != text.strip()
        check(dropped == should_drop,
              f"{'dropped' if dropped else 'kept':7} | cal_read={cal!s:5} | "
              f"{text[:52]!r} — {why}")


def test_the_greeting_beside_it_survives():
    """Dropping the fabrication must not cost him the rest of the reply."""
    out = guard("Good morning, Sir. Your next match is at 7 PM, Sir.")
    check("Good morning" in out, f"the greeting survives: {out!r}")
    check("7 PM" not in out, "...and the invented appointment does not")


def test_an_untouched_reply_is_byte_identical():
    """This runs on every turn. A guard that reflows every reply damages more
    than it repairs — the existing F-16 comment says so and it still holds."""
    for text in ("Good afternoon, Sir.\n\n  Two spaces and a newline.  ",
                 "```python\nprint('hi')\n```\nThat is the file, Sir."):
        check(guard(text) == text, f"unchanged, byte for byte: {text[:34]!r}")


def test_the_fallback_says_which_kind_of_claim_went():
    out = guard("Your next match is at 7 PM, Sir.")
    check("schedule" in out.lower(),
          f"it names the diary rather than actions: {out!r}")
    check("completed actions" not in out.lower(),
          "...so a question about the diary is not answered about actions")
    check("calendar" in out.lower(),
          "...and it says what would make the answer available")


def test_action_claims_still_get_their_own_fallback():
    """The other half must not have been overwritten by the new one."""
    out = guard("I have sent that email for you, Sir.")
    check(out.strip() != "I have sent that email for you, Sir.",
          "an unfounded action claim is still dropped")
    check("completed actions" in out.lower(),
          f"...with its own wording: {out!r}")


# ── the evidence, which is what makes a kept sentence honest ────────────────

def test_calendar_evidence_is_calendar_specific():
    """Any action having run is not permission to describe his diary."""
    stub_cal = [{"role": "assistant", "content": "[Executed: check_calendar. Done.]"}]
    stub_other = [{"role": "assistant", "content": "[Executed: tavily_search. Done.]"}]
    check(b._calendar_read_recently(stub_cal) is True,
          "a calendar read counts as evidence")
    check(b._calendar_read_recently(stub_other) is False,
          "a web search does not")
    check(b._calendar_read_recently([]) is False, "an empty buffer is not evidence")
    check(b._actions_ran_recently(stub_other) is True,
          "...while the broader action evidence still sees it, unchanged")


def test_evidence_is_read_from_the_dispatch_stub_not_the_model():
    """The stub is written from a PARSE of what was dispatched. A model claiming
    to have read the calendar must not thereby license itself to describe it."""
    said_so = [{"role": "assistant",
                "content": "I checked your calendar and your meeting is at 4."}]
    check(b._calendar_read_recently(said_so) is False,
          "the model saying it read the calendar is not evidence that it did")


def test_the_guard_is_wired_with_the_calendar_evidence():
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    check("calendar_read=_calendar_read_recently(" in src,
          "the call site passes real evidence rather than a default")
    check(src.count("_strip_unfounded_conversational_claims(") >= 2,
          "the guard is actually called from brain, not only defined")


# ── F-74b: a refusal that misstated its own grounds ────────────────────
#
# Same session, same class. "what time is my next thing" made the model emit
# `calendar_next_event`; the next attempt emitted `get_calendar`. Neither exists.
# Governance refused both by its documented fail-safe - correct - and the desk
# said they were "classified as high-risk", which is a claim about a
# classification nobody ever made. F-69's lesson, paid for a second time.


def test_governance_can_tell_an_unwritten_rule_from_a_strict_one():
    from governance_manager import governance_manager as g
    check(g.is_known("check_calendar") is True, "a real action is known")
    check(g.is_known("get_calendar") is False,
          "an invented action is NOT known - and get_tier cannot say so, "
          f"since it returns {g.get_tier('get_calendar')!r} for both")
    check(g.get_tier("get_calendar") == "BLOCK",
          "...while the DECISION is still a refusal, which is right")
    check(g.is_known("  CHECK_CALENDAR  ") is True,
          "case and whitespace are the caller's business, not the answer's")


def test_the_refusal_names_the_right_reason():
    from modules.answer_provenance import refusal_sentence
    unknown = refusal_sentence("get_calendar", known=False)
    check("no such capability" in unknown,
          f"an unknown action is refused as unknown: {unknown!r}")
    check("high-risk" not in unknown,
          "...and is NOT described as high-risk, which was never decided")
    check("get_calendar" in unknown, "...and it names what was asked for")

    ruled = refusal_sentence("send_email", known=True)
    check("high-risk" in ruled,
          f"a genuinely blocked action keeps its wording: {ruled!r}")


def test_all_three_doors_use_the_one_sentence():
    """Three call sites said the same invented thing. That is root cause #4, and
    it is why the sentence lives in one function now."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    check("classified as high-risk" not in src,
          "no door hardcodes the claim any more")
    check(src.count("refusal_sentence(") == 3,
          f"all three refusal doors call the shared sentence "
          f"({src.count('refusal_sentence(')})")
    check("governance_manager.is_known(" in src,
          "...with governance's own answer, not a guess")


# ── F-75: the briefing quoted a headline from no source at all ─────────
#
# Found on 2026-08-29 by running row 10.9 through its REAL trigger, `wake up`.
# The comprehensive briefing said:
#
#     "today's notable tech headline from TechCrunch reads: 'AI breakthroughs
#      accelerate autonomous development.'"
#
# DuckDuckGo had begun returning ZERO results without raising, so the `except`
# never fired, nothing was logged, and `news_headline` kept its fallback string -
# which went into the prompt looking like a headline. Three of five comprehensive
# briefings invented one, each with a different publisher attached: TechCrunch,
# Reuters, Google News.
#
# **A quoted headline with a named publisher is a harder claim than F-74's 7 PM
# match**: it attributes words to a real organisation. And news was the one input
# to this briefing with a source but no NO DATA marker, so the guard that has
# protected email, calendar and vitals since F-09 had nothing to match on.


def test_news_is_a_sourced_topic_like_the_other_three():
    check("news" in b._NO_DATA,
          "a failed news lookup has a NO DATA marker, like email/calendar/vitals")
    check("NO DATA" in b._NO_DATA["news"],
          "...and it carries the marker string the guard matches on")
    check("news" in b._TOPIC_WORDS,
          "...and news has claim vocabulary, so a sentence about it is findable")


def test_an_unsourced_news_claim_is_dropped():
    sourced = b._sourced_topics("e", "c", "h", b._NO_DATA["news"])
    check("news" not in sourced, f"an empty lookup is not a source: {sourced}")
    for said in (
        "Today's notable tech headline from TechCrunch reads: 'AI accelerates'.",
        "In technology news, Reuters reports that AI advancements continue.",
        "The latest AI headline from Google News notes a breakthrough.",
        "In the tech sphere Google reports a fresh development.",
    ):
        out = b._strip_unsourced_state_claims(said, sourced)
        check(said not in out, f"dropped: {said[:60]!r}")


def test_a_REAL_headline_is_still_reported():
    """The guard must not delete the feature. With a live lookup behind it, a
    news sentence is the briefing working."""
    sourced = b._sourced_topics("e", "c", "h", "Chip maker unveils new GPU line")
    check("news" in sourced, "a real headline IS a source")
    said = "In technology news, a chip maker unveiled a new GPU line."
    check(b._strip_unsourced_state_claims(said, sourced) == said,
          "...and the sentence survives untouched")


def test_an_empty_result_list_is_treated_as_a_failure():
    """The exact shape that bit: zero results, NO exception, nothing logged."""
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    block = src.split("news_headline = _NO_DATA[")[1][:700]
    check("if results and str(results[0].get(" in block,
          "an empty list no longer passes as a headline")
    check("no results" in block,
          "...and it says so in the log, which is how this went unseen")
    check('news_headline = _NO_DATA["news"]' in src,
          "the fallback is the NO DATA marker, not a sentence that reads like news")


def test_the_briefing_passes_news_to_the_guard():
    """A guard told nothing about news cannot judge a news sentence."""
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    call = src.split("_sourced = _sourced_topics(")[1][:200]
    check("news_headline" in call,
          f"the briefing tells the guard whether news had a source: {call[:90]!r}")



if __name__ == "__main__":
    import traceback

    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)),
                   key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        try:
            fn()
        except Exception:
            _fails.append(name)
            print(f"FAIL  {name} raised")
            traceback.print_exc()
    print(f"\n{_checks - len(_fails)}/{_checks} passed.")
    sys.exit(1 if _fails else 0)
