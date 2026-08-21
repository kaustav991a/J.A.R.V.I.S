"""Harness: a classification that did not happen must not be spent as one.

F-24 was filed 🔵 LOW — "recovered, no harm on this turn". F-44 upgraded it to
🔴 when it had harm. The chain, from the live log of 2026-08-16:

    [BRAIN] Intent classification JSON decode error: Expecting property name
            enclosed in double quotes: line 1 column 2 (char 1)
    [BRAIN] Persona Matrix -> MODULE: GENERAL | ...

`line 1 column 2` is a bare `{` and nothing after it — the model spent its whole
output budget thinking. The fallback then returned an ordinary GENERAL/CASUAL
dict, the log printed `MODULE: GENERAL` in the *identical* format a real reading
prints, and the first `4.1` attempt of the evening was dropped in full and
answered with an unrelated night-mode nudge. Nothing anywhere said the
classification had not happened.

Two separate defects, and this pins both:

  * **the budget.** `max_tokens=140` was right when the cloud leg was a
    non-thinking flash. Measured against the live API on 2026-08-22, one
    classify-shaped call per model: `gemini-3.5-flash`, `gemini-3.6-flash` and
    `gemini-3.7-flash` ALL return `finish_reason=2` and unparseable output at
    140, and all three return valid JSON at 700. So this was never the evergreen
    alias moving to one bad model — pinning would not have fixed it. Pinning
    also has its own rot: `gemini-2.5-flash` is still listed in the catalogue and
    404s on use, which is the exact failure the alias exists to avoid.
  * **the silence.** A fallback that is indistinguishable from a reading is a
    fallback that gets believed.

WHAT THIS PINS
--------------
`_unclassified` is called directly — no provider, no network. The two places the
marker has to be honoured are checked structurally, because reaching them needs
a live classification call.
"""

import ast
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

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


def _src() -> str:
    return (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")


# ── the fallback says it is one ───────────────────────────────────────────────

def test_the_fallback_is_marked_as_a_fallback():
    import brain
    d = brain._unclassified("harness")
    check(d.get("classified") is False,
          "an unclassified turn is marked classified=False")


def test_a_real_classification_is_marked_too():
    """Absence of the marker cannot be the signal — a caller reading
    `.get("classified", True)` on a dict that never carries it would read every
    fallback as a success."""
    src = _src()
    ok = src[src.index("def classify_intent"):]
    ok = ok[:ok.index("def _unclassified")] if "def _unclassified" in ok else ok
    body = src.split("def classify_intent", 1)[1]
    body = body[:body.index("\ndef ", 1)]
    check('"classified":      True' in body,
          "the success path carries classified=True")


def test_the_fallback_values_are_the_same_safe_neutrals_as_before():
    """The fix is the marker, not the values. Changing what the fallback SAYS
    would be a behaviour change smuggled in with a visibility fix."""
    import brain
    d = brain._unclassified("harness")
    check(d["intent"] == "GENERAL", "intent is still GENERAL")
    check(d["emotion"] == "CASUAL", "emotion is still CASUAL")
    check(d["sarcasm_allowed"] is True, "sarcasm is still allowed")
    check(d["brevity_mode"] is False, "brevity is still off")
    check(d["response_mode"] == "CINEMATIC", "response mode is unchanged")
    check(d["sass_index"] == 50, "sass is still the neutral 50")


def test_the_fallback_resets_the_exposed_sass_index():
    """`get_last_sass_index()` is read by main.py after the fact, so a stale
    value from the previous turn would drive this turn's prosody."""
    import brain
    brain._last_sass_index = 100
    brain._unclassified("harness")
    check(brain.get_last_sass_index() == 50,
          "the exposed sass index is reset, not left on the last turn's value")


def test_the_fallback_is_loud():
    src = _src()
    body = src[src.index("def _unclassified"):]
    body = body[:body.index("\ndef ", 1)]
    check("INTENT NOT CLASSIFIED" in body,
          "the fallback prints that it IS a fallback")
    check("flush=True" in body,
          "...unbuffered, so a crash after it does not eat the line")


def test_both_exception_paths_go_through_it():
    """A JSON decode error and any other exception are the same event as far as
    the caller is concerned: no classification happened."""
    src = _src()
    body = src.split("def classify_intent", 1)[1]
    body = body[:body.index("\ndef ", 1)]
    check(body.count("_unclassified(") == 2,
          f"both except branches return the marked fallback "
          f"({body.count('_unclassified(')})")
    check("_last_sass_index = 50" not in body,
          "neither branch keeps its own copy of the reset")


# ── the marker is honoured ────────────────────────────────────────────────────

def test_an_unknown_intent_still_carries_the_action_catalogue():
    """The harm, stated as the property. `include_actions` was
    `deterministic_action or intent in (CODER, PC_OP) or _action_likely(...)`,
    and a fallback GENERAL satisfies none of them — so an instruction whose
    wording missed the hint list became chat. Paying 5.4k tokens on a turn that
    turns out to be chitchat costs a slower reply; getting it wrong the other way
    costs the instruction."""
    src = _src()
    n = src.count('or not classification.get("classified", True)')
    check(n == 2,
          f"both prompt-building paths carry the catalogue when intent is "
          f"unknown ({n} of 2)")


def test_the_persona_line_says_when_it_is_not_a_reading():
    """This line printed `MODULE: GENERAL` identically whether the classifier had
    answered or died, which is how the silent fallback read as a reading."""
    src = _src()
    block = src[src.index("Persona Matrix -> MODULE") - 400:]
    block = block[:800]
    check("NOT CLASSIFIED" in block,
          "the persona line marks an unclassified turn")


# ── the budget ────────────────────────────────────────────────────────────────

def test_the_classifier_budget_survives_a_thinking_model():
    """140 is the number that lost the answer. The measurement is in the comment
    beside it; this pins the conclusion."""
    src = _src()
    body = src.split("def classify_intent", 1)[1]
    body = body[:body.index("\ndef ", 1)]
    check("max_tokens=140," not in body,
          "the budget that could not survive thinking is gone")
    import re
    m = re.search(r"max_tokens=(\d+),", body)
    check(m is not None and int(m.group(1)) >= 700,
          f"the budget clears the measured thinking overhead "
          f"({m.group(1) if m else 'absent'})")


def test_the_measurement_is_recorded_next_to_the_number():
    """A budget with no stated reason is a budget the next person lowers."""
    src = _src()
    body = src.split("def classify_intent", 1)[1]
    body = body[:body.index("\ndef ", 1)]
    check("finish_reason 2" in body,
          "the failure mode that set this number is written down")
    check("gemini-2.5-flash" in body,
          "...and so is why pinning was not the fix")


def test_the_number_is_not_confused_with_the_vision_budget():
    """`test_reasoning_leak.py` forbids `max_tokens=700,` in cloud_gateway.py for
    an unrelated reason. Two different calls, two different 700s — and the one
    forbidden there must not be reintroduced here by someone reading that rule
    as global."""
    cg = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    check("max_tokens=700," not in cg,
          "the vision budget is still not back in cloud_gateway.py")
    src = _src()
    body = src.split("def classify_intent", 1)[1]
    body = body[:body.index("\ndef ", 1)]
    check("cloud_gateway" in body,
          "the comment here names the other 700 so they are not merged")


def test_brain_still_parses():
    ast.parse(_src())
    check(True, "brain.py parses after the edits")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Intent classification — F-24 and F-44")
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
