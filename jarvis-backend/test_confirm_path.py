"""Harness: the CONFIRM gate reads answers, and never acts on a non-answer.

Row `4.1` of the §7 live gate failed four times on four distinct causes. The
fourth told the owner it had succeeded when nothing had been written, and the
three findings behind the fourth attempt are all in this file:

  * **F-42 — substrings.** Every governance door matched with
    `any(w in text for w in WORDS)`. `"no"` is inside "now", "know" and
    "nothing"; `"stop"` is inside "stopwatch". Three doors, one bug, which is
    root cause #4: an injection class fixed one site at a time stays open.
  * **F-40 — the tie.** Approval was tested before denial, so "no, go ahead"
    read as an approval and EXECUTED.
  * **F-43 — the missing else.** While a prompt was open, an utterance that was
    neither an approval nor a denial fell straight through and ran as a command
    with the prompt STILL ARMED. The owner was never told his answer had not
    landed, and the pinned id sat waiting for a stray "yes" minutes later to
    resolve it out of context.

WHAT THIS PINS
--------------
The word matching is tested by CALLING it — `_read_confirmation_answer` needs no
provider, no mic and no socket, so its properties are checked directly rather
than asserted about source text. The door wiring is structural, because driving
the voice loop needs a microphone: what is checked there is that all three doors
reach the one helper, that the third branch exists, and that the two things
which would quietly break it (an unconditional counter reset, a terminal
partner-denial on a non-answer) are absent.
"""

import ast
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("JARVIS_HARNESS", "1")

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _read():
    import main
    return main._read_confirmation_answer


def _src() -> str:
    return (HERE / "main.py").read_text(encoding="utf-8", errors="replace")


# ── F-42: whole words, not substrings ────────────────────────────────────────

def test_a_denial_word_inside_another_word_is_not_a_denial():
    """The bug itself. Each of these contains a denial word as a substring and
    is not remotely a denial."""
    r = _read()
    for said in ("now", "i know", "knows", "nothing", "stopwatch",
                 "another", "nobody", "cancellation policy", "denial"):
        check(r(said) is None, f"{said!r} is not read as a denial")


def test_an_approval_word_inside_another_word_is_not_an_approval():
    r = _read()
    for said in ("yesterday", "eyes", "proceeds", "allowance"):
        check(r(said) is None, f"{said!r} is not read as an approval")


def test_the_plain_answers_still_work():
    """The point of tightening the match is not to break the ordinary case."""
    r = _read()
    for said in ("yes", "confirm", "approve", "proceed", "allow", "granted",
                 "yes please", "Yes!", "yes."):
        check(r(said) == "approve", f"{said!r} approves")
    for said in ("no", "deny", "cancel", "abort", "stop", "decline", "reject",
                 "no thanks", "NO", "nevermind"):
        check(r(said) == "deny", f"{said!r} denies")


def test_a_phrase_survives_a_word_landing_between_its_own():
    """`"go ahead"` has to survive "go right ahead". A transcript is not a
    keyboard, and a butler that only accepts the exact phrase is a butler who
    makes the owner repeat himself."""
    r = _read()
    check(r("go ahead") == "approve", "'go ahead' approves")
    check(r("go right ahead") == "approve", "'go right ahead' approves")
    check(r("never mind") == "deny", "'never mind' denies")
    check(r("mind never") == "deny", "...in either order, since order is not the signal")


def test_an_apostrophe_is_not_load_bearing():
    """A transcriber may or may not produce one, and the list spells it with."""
    r = _read()
    check(r("don't do it") == "deny", "\"don't do it\" denies")
    check(r("dont do it") == "deny", "...and so does the transcribed 'dont'")
    check(r("don’t") == "deny", "...and the typographic apostrophe too")


# ── F-40: denial breaks the tie ──────────────────────────────────────────────

def test_an_utterance_holding_both_is_read_as_a_denial():
    """"no, go ahead" holds one of each. Approval was tested first, so it ran.

    A gate whose entire purpose is to not act by accident must resolve
    ambiguity towards doing nothing — there is no symmetric argument here. A
    wrongly-read denial costs him a repeated sentence; a wrongly-read approval
    costs him whatever the action was.
    """
    r = _read()
    check(r("no, go ahead") == "deny", "'no, go ahead' does NOT execute")
    check(r("go ahead, no") == "deny", "...nor in the other order")
    check(r("do it, don't") == "deny", "...nor when the denial is last")
    check(r("yes, cancel") == "deny", "...nor 'yes, cancel'")


def test_denial_is_tested_before_approval_in_the_source():
    """The behaviour above is a consequence of the ORDER of two branches, which
    is the kind of thing a later edit reverses without noticing."""
    src = _src()
    body = src.split("def _read_confirmation_answer", 1)[1].split("\ndef ", 1)[0]
    d = body.find("_DENIAL_WORDS")
    a = body.find("_APPROVAL_WORDS")
    check(d != -1 and a != -1 and d < a,
          "the denial branch is evaluated first, so a tie cannot execute")


# ── not an answer at all ─────────────────────────────────────────────────────

def test_a_command_is_never_an_answer():
    r = _read()
    for said in ("open the browser", "check my email", "clear the display",
                 "yes, open the browser", "search for a flight"):
        check(r(said) is None, f"{said!r} is a command, not an answer")


def test_nothing_and_a_speech_is_neither():
    r = _read()
    check(r("") is None, "an empty utterance answers nothing")
    check(r("   ") is None, "...and so does whitespace")
    check(r(None) is None, "...and None does not raise")
    check(r("yes " + "a" * 80) is None,
          "a long utterance is not a yes/no answer however it starts")


# ── F-42 / root cause #4: all three doors, one helper ────────────────────────

def test_no_door_matches_the_word_lists_by_substring_any_more():
    """The three doors — Telegram, /api/backdoor and the voice loop — each had
    their own copy of the substring match. A copy left behind is the finding
    still being open at one site."""
    src = _src()
    for pattern in ("in _gov_lower for w in _APPROVAL_WORDS",
                    "in _gov_lower for w in _DENIAL_WORDS",
                    "in _low for w in _APPROVAL_WORDS",
                    "in _low for w in _DENIAL_WORDS"):
        check(pattern not in src, f"no door still matches with `{pattern}`")


def test_the_word_lists_are_read_in_exactly_one_place():
    """Both lists must be reachable only through the helper. Anything else is a
    second implementation waiting to drift from the first."""
    src = _src()
    check(src.count("_APPROVAL_WORDS") == 2,
          f"_APPROVAL_WORDS is defined once and read once ({src.count('_APPROVAL_WORDS')})")
    check(src.count("_DENIAL_WORDS") == 2,
          f"_DENIAL_WORDS is defined once and read once ({src.count('_DENIAL_WORDS')})")


def test_every_door_calls_the_helper():
    src = _src()
    n = src.count("_read_confirmation_answer(")
    check(n >= 4, f"the helper is defined once and called at all three doors ({n})")


def test_the_word_lists_themselves_were_not_rewritten():
    """The butler's vocabulary is the owner's to refine. This fix changes how
    the list is MATCHED and must not quietly add or drop an entry."""
    import main
    check(main._APPROVAL_WORDS == frozenset({
        "yes", "confirm", "approve", "authorise", "authorize", "proceed",
        "go ahead", "do it", "execute", "allow", "granted",
    }), "the approval list is untouched")
    check(main._DENIAL_WORDS == frozenset({
        "no", "deny", "cancel", "abort", "stop", "decline", "reject",
        "nevermind", "never mind", "don't",
    }), "the denial list is untouched")


# ── F-43: the third branch ───────────────────────────────────────────────────

def _voice_confirm_block(src: str) -> str:
    """The voice loop's governance intercept, from its guard to the sleep
    phrases that follow it."""
    start = src.index("GOVERNANCE CONFIRMATION INTERCEPT (voice / WS path)")
    end = src.index("sleep_phrases = [", start)
    return src[start:end]


def test_a_non_answer_to_a_live_prompt_is_re_asked():
    block = _voice_confirm_block(_src())
    check("That wasn't a yes or a no" in block,
          "an utterance that is not an answer gets the question again")
    check("_confirm_reasks < 2" in block,
          "...at most twice, sharing one budget with the unintelligible case")


def test_the_budget_is_keyed_to_the_prompt_and_not_to_the_turn():
    """The reset used to be unconditional on every landed turn. A non-answer IS
    a landed turn, so leaving it unconditional would zero the budget on every
    pass and re-ask forever — the fix and the loop it would cause are one edit
    apart."""
    src = _src()
    check("_confirm_reask_cid" in src, "the budget records which prompt it counts")
    check('if _DESK_PENDING["cid"] != _confirm_reask_cid:' in src,
          "...and resets only when the prompt it was counting changes")
    check("_confirm_reasks = 0   # F-35: a turn landed" not in src,
          "the unconditional per-turn reset is gone")


def test_the_spent_budget_cancels_the_pending_before_acting():
    """The state that must not survive this branch is an armed prompt. Falling
    through with the id still pinned is the original defect."""
    block = _voice_confirm_block(_src())
    tail = block.split("_confirm_reasks < 2", 1)[1]
    check("governance_manager.cancel_pending" in tail,
          "the pending is cancelled once the re-asks are spent")
    check('_DESK_PENDING["cid"] = None' in tail, "...and the pinned id is released")
    check("speak_text" in tail, "...and he is told out loud, not only in the log")


def test_the_spent_budget_then_processes_the_utterance_as_a_command():
    """Every other branch in this intercept ends in `continue`. This one must
    not: after cancelling, what he said is a command, which is the conclusion
    the remote door already reached."""
    block = _voice_confirm_block(_src())
    tail = block.split("_confirm_reasks < 2", 1)[1]
    check("deliberately NO `continue`: fall through" in tail,
          "the branch falls through to command processing, and says why")
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    check(lines and not lines[-1].startswith("continue"),
          "...and does not end in a continue that would swallow the utterance")


def test_a_non_answer_is_not_recorded_as_a_partner_refusal():
    """`_partner_note_denial` is documented for explicit refusals only, because
    a noted denial is TERMINAL and stops the send being re-attempted. He never
    refused here — he said something that was not an answer. Recording it would
    permanently block a message he never declined."""
    src = _src()
    block = _voice_confirm_block(src)
    tail = block.split("_confirm_reasks < 2", 1)[1]
    # The CALL form, with its paren: both branches name the function in a
    # comment explaining why they do not use it, and that comment is the point.
    check("_partner_note_denial(" not in tail,
          "the voice non-answer branch does not note a terminal refusal")
    check("NOT `_partner_note_denial`" in tail,
          "...and says why, so a later edit does not add it back as a tidy-up")
    # ...and the same branch at /api/backdoor
    door2 = src[src.index("F-43 at this door"):]
    door2 = door2[:door2.index("─────")]
    check("_partner_note_denial(" not in door2,
          "nor does the /api/backdoor non-answer branch")
    # the explicit-denial paths still DO note it — three doors, three calls
    check(src.count("_partner_note_denial(") == 4,
          f"the three explicit-denial paths still record a refusal "
          f"({src.count('_partner_note_denial(')} incl. the definition)")


def test_no_door_leaves_a_prompt_armed_when_it_falls_through():
    """The property under all of F-43, stated once. Every governance intercept
    either resolves the prompt or cancels it; none reaches the code past itself
    with `_DESK_PENDING["cid"]` still set."""
    src = _src()
    block = _voice_confirm_block(src)
    check(block.count('_DESK_PENDING["cid"] = None') >= 3,
          "the voice door releases the id on approve, on deny and on a non-answer")
    door2 = src[src.index("An approval must only ever resolve the prompt"):]
    door2 = door2[:door2.index("PENDING NOTEPAD")]
    check(door2.count('_DESK_PENDING["cid"] = None') >= 3,
          "/api/backdoor releases it on all three too")


def test_the_remote_door_says_that_it_cancelled():
    """It already superseded the pending correctly, and did it silently — so
    from his side the question simply stopped existing."""
    src = _src()
    door1 = src[src.index("If THIS channel was asked to authorise"):]
    door1 = door1[:door1.index("queued-task approve/deny")]
    check("Pending confirmation superseded by a new command" in door1,
          "the remote door still cancels a superseded confirmation")
    after = door1.split("Pending confirmation superseded by a new command", 1)[1]
    check("channel.reply" in after, "...and now tells him it did")


# ── the file compiles, which a source-string harness cannot assume ───────────

def test_main_still_parses():
    ast.parse(_src())
    check(True, "main.py parses after the edits")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("The CONFIRM path — F-40, F-42, F-43")
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
