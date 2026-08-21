"""Harness: the briefing describes what it read, not what it assumes (F-09).

`test_briefing_truthfulness.py` covers the first half of F-09 — the briefing
claiming to have DONE things. This is the reopened half, and the finding calls it
"far wider than the row implies": the briefing narrated four separate data
sources it had not read. Each checked at the desk, against the real world:

    "you've marked today's date in your calendar"   he had not — nothing marked
    "your inbox contains 201 unread messages"       "totally wrong"
    "vitals stable, with a heart rate of zero"      0 is a no-data sentinel,
                                                    sold as reassurance about
                                                    his body
    "the room's reduced volume and the TV's muted   the TV was not powered on,
     status", and the lights dimmed                 and no such state was set

Not one bad sentence — a whole briefing of confident, unsourced assertion. The
existing guard was kept deliberately narrow to protect the persona, and it only
catches claims about actions it did not RUN. It says nothing about claims about
state it never READ, so the entire class was open.

The cause is in the same log: `[GMAIL AGENT]` printed a fresh OAuth
authorization URL at boot, the Google token being invalid that session, and
Calendar and Fitness initialised after it. An auth failure degrades to
empty-or-zero data and the model narrates that as fact — which is why the fix has
to be in two places at once. Absence must reach the model AS absence, and a claim
about a source that did not answer must not survive whatever the model does with
the instruction.

WHAT THIS PINS
--------------
The guard is CALLED, on the exact sentences from the live briefing. Offline: no
provider, no Google token, no camera.
"""

import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_passed = 0
_failed = 0

# The briefing as it was actually spoken, on 2026-08-08. Kept verbatim: a
# paraphrase would lose the thing that makes each sentence dangerous, which is
# how ordinary and well-formed it sounds.
LIVE = ("Good evening, Sir. I see you've marked today's date in your calendar. "
        "Your email inbox currently contains 201 unread messages. "
        "Your vital signs appear to be stable, with a heart rate of zero. "
        "I have also noted the room's reduced volume and the TV's muted status, "
        "and dimmed the lights accordingly. "
        "The time is 10:41 PM.")


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _src() -> str:
    return io.open(HERE / "brain.py", encoding="utf-8", errors="replace").read()


def _strip(text, sourced):
    import brain
    return brain._strip_unsourced_state_claims(text, sourced)


# ── the four sentences ────────────────────────────────────────────────────────

def test_the_whole_live_briefing_loses_exactly_its_four_fabrications():
    """With nothing sourced — the live state, Gmail auth having failed — the
    greeting and the clock are true and everything between them was invented."""
    out = _strip(LIVE, set())
    check("marked today's date" not in out, "the calendar claim is gone")
    check("201 unread" not in out, "the inbox figure is gone")
    check("heart rate of zero" not in out, "the heart rate is gone")
    check("TV's muted status" not in out, "the television is gone")
    check("Good evening, Sir." in out, "the greeting survives")
    check("The time is 10:41 PM." in out, "the clock survives")


def test_a_source_that_answered_is_allowed_to_be_described():
    """The guard must not be a gag. A briefing that cannot report a calendar it
    successfully read is a different bug of the same size."""
    out = _strip(LIVE, {"calendar", "email"})
    check("marked today's date" in out, "a sourced calendar claim is kept")
    check("201 unread" in out, "a sourced inbox claim is kept")
    check("heart rate of zero" not in out, "the unsourced vitals claim still goes")


def test_each_topic_is_gated_on_its_own_source():
    for topic, needle in (("calendar", "marked today's date"),
                          ("email", "201 unread"),
                          ("vitals", "heart rate of zero")):
        kept = _strip(LIVE, {topic})
        check(needle in kept, f"{topic} sourced -> its sentence is kept")
        gone = _strip(LIVE, set())
        check(needle not in gone, f"{topic} unsourced -> its sentence is dropped")


def test_the_physical_room_is_never_sourced():
    """There is no sensor, no integration and no cache that could ever report the
    television or the lights. A briefing sentence about them is fabricated by
    construction, whatever else answered."""
    out = _strip(LIVE, {"calendar", "email", "vitals"})
    check("TV's muted status" not in out,
          "the television claim goes even with every integration up")
    check("dimmed the lights" not in out, "so does the lighting claim")


# ── it must not silence the truth ────────────────────────────────────────────

def test_a_sentence_that_admits_the_absence_is_kept():
    """The failure being fixed is confident assertion. "I could not reach your
    calendar" is the opposite of that, and replacing it with silence would be a
    worse briefing, not a safer one."""
    for said in ("I could not reach your calendar this morning, Sir.",
                 "Your inbox is not available — the token has expired.",
                 "I have no heart rate reading for you today.",
                 "Health integration is offline, so I cannot say."):
        check(said in _strip(said, set()), f"kept: {said[:44]}…")


def test_an_empty_result_still_says_something_true():
    """If the guard ate everything, silence would read as a fault — and a
    fabricated briefing is the thing being refused, not speech itself."""
    out = _strip("Your inbox holds 40 unread messages.", set())
    check(out.strip() != "", "something is still said")
    check("systems are online" in out.lower(), "...and it is true by construction")


def test_ordinary_persona_prose_survives():
    """The topic words have to be things that do not appear in normal JARVIS
    speech, or the guard eats the persona."""
    prose = ("Good morning, Sir. All primary systems are online. "
             "The weather is twenty-eight degrees and humid. "
             "I have compiled the usual briefing for you.")
    check(_strip(prose, set()) == prose, "a briefing with no state claims is untouched")


# ── absence reaches the model as absence ─────────────────────────────────────

def test_a_zero_is_never_passed_as_a_measurement():
    """`0` is this agent's no-data sentinel, and it was formatted straight into
    the prompt as "Heart Rate: 0 BPM". The model read that and reassured him
    about his body."""
    src = _src()
    check('f"Heart Rate: {health_data[\'heart_rate\']} BPM. '
          'Steps today: {health_data[\'steps\']}."' not in src,
          "the sentinel is no longer formatted in unconditionally")
    at = src.index("_hr = health_data.get")
    block = src[at - 900:at + 700]   # the reason is written ABOVE the code
    check("if _hr:" in block, "a zero heart rate is omitted, not reported")
    check("if _steps:" in block, "and so is a zero step count")
    check("not a measurement" in block,
          "...and the reason is written down beside it")


def test_the_offline_strings_are_the_ones_the_guard_reads_back():
    """A second variable saying "we have email" while the prompt says NO DATA is
    exactly how this class survives a fix. There is one source of truth, and the
    guard reads it off the same strings the model was given."""
    import brain
    for topic in ("email", "calendar", "vitals"):
        check("NO DATA" in brain._NO_DATA[topic],
              f"the {topic} offline string is marked NO DATA")
    check(brain._sourced_topics(brain._NO_DATA["email"],
                               brain._NO_DATA["calendar"],
                               brain._NO_DATA["vitals"]) == set(),
          "all three offline -> nothing is sourced")
    check(brain._sourced_topics("3 unread from Rajat", "Standup at 10",
                                "Heart Rate: 72 BPM.")
          == {"email", "calendar", "vitals"},
          "real data -> all three are sourced")


def test_the_defaults_come_from_that_one_map():
    src = _src()
    check('email_context = _NO_DATA["email"]' in src,
          "the email default is the map's string")
    check('calendar_context = _NO_DATA["calendar"]' in src,
          "so is the calendar's")
    check('health_context = _NO_DATA["vitals"]' in src,
          "so is health's")
    check("Email integration offline.\"" not in src,
          "no hand-typed copy of an offline string is left behind")


# ── both guards run, in the right order ──────────────────────────────────────

def test_both_guards_are_applied_to_the_briefing():
    src = _src()
    check("_strip_unsourced_state_claims(_strip_unfounded_action_claims(" in src,
          "the state guard wraps the action guard on the briefing's return")


def test_the_prompt_says_it_too_and_the_code_does_not_rely_on_that():
    """F-13 is the standing lesson: a rule a model can ignore is not a
    guarantee. The briefing already carried a TRUTHFULNESS block and narrated
    four sources anyway — so the prompt says it AND the code enforces it."""
    src = _src()
    check("SOURCES (absolute)" in src, "the prompt states the rule")
    check("NO DATA means that source did not answer" in src,
          "...and explains what the marker means")
    check("do not mention them at all" in src,
          "...and forbids the physical-room claims outright")
    check("A zero is the absence" in src, "...and names the sentinel trap")


def test_a_dropped_claim_is_logged_with_its_reason():
    """A guard that silently eats sentences is a guard nobody can debug, and this
    one removes speech the owner was about to hear."""
    src = _src()
    fn = src[src.index("def _strip_unsourced_state_claims"):]
    fn = fn[:fn.index("\ndef ", 1)]
    check("dropped" in fn and "flush=True" in fn, "drops are logged, unbuffered")
    check("{why}" in fn, "...with which source was missing")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Briefing sources — F-09 reopened")
    print("=" * 62)
    for t in TESTS:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            global _failed
            _failed += 1
            print(f"FAIL  {t.__name__} raised {type(e).__name__}: {e}")
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
