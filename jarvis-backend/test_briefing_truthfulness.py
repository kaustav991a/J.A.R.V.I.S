"""
test_briefing_truthfulness.py — the briefing reports, it does not act (F-09)
============================================================================

Live gate, 2026-08-08. The wake briefing said, unprompted:

    "I did note, however, that you had instructed me to delete certain items and
     clear your schedule for the day. I have taken care of this task, as per
     your request."

No such instruction was given. Nothing was deleted either — `action_engine` has
no `calendar_delete` branch at all — so the sentence was narration, which is
worse than a failed action: it is a confident report of destructive work on the
user's data, in the one output that speaks with full authority every morning,
and there is no way for him to tell it from a true one.

The cause is structural. `generate_briefing` is handed `recall_all_facts()` and
a semantic recall of recent events, which are records of what the OPERATOR SAID.
A small model rewrites "he asked me to delete X" into "I have deleted X".

The guard is in code, not in the prompt. The prompt now says it too, but F-13
is the same lesson twice over: a rule a model can ignore is not a guarantee.
`generate_briefing` only ever reports, so a completed-mutation claim in its
output is false BY CONSTRUCTION and can be removed without knowing the truth.
"""

import ast
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
BRAIN = HERE / "brain.py"

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


# brain.py imports the model stack; lift the pure guard out of its SOURCE and
# run the real function instead of importing the module.
_SOURCE = BRAIN.read_text(encoding="utf-8", errors="replace")
_TREE = ast.parse(_SOURCE)

_WANTED_CONSTS = {"_MUTATING_CLAIM_VERBS", "_FABRICATED_MANDATE",
                  "_FIRST_PERSON", "_BARE_COMPLETION"}
_consts = [n for n in _TREE.body
           if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Name) and t.id in _WANTED_CONSTS
                   for t in n.targets)]
_func = [n for n in _TREE.body
         if isinstance(n, ast.FunctionDef)
         and n.name == "_strip_unfounded_action_claims"]

_NS: dict = {}
if _func and _consts:
    import re as _re
    _NS["re"] = _re
    mod = ast.Module(body=_consts + _func, type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), str(BRAIN), "exec"), _NS)

_strip = _NS.get("_strip_unfounded_action_claims")

# The sentences exactly as they were spoken on 2026-08-08.
LIVE_GATE_TEXT = (
    "Good evening, Sir. Your schedule for today is clear, Sir, devoid of any "
    "appointments. I did note, however, that you had instructed me to delete "
    "certain items and clear your schedule for the day. I have taken care of "
    "this task, as per your request. You have 201 unread emails, Sir."
)


def test_the_guard_exists():
    check(bool(_func), "_strip_unfounded_action_claims exists in brain.py")
    check(len(_consts) == len(_WANTED_CONSTS),
          f"all four claim vocabularies are module-level, found {len(_consts)}")


def test_the_exact_live_gate_confabulation_is_removed():
    out = _strip(LIVE_GATE_TEXT)
    check("I have taken care of this task" not in out,
          "the false completion claim is gone")
    check("as per your request" not in out,
          "the invented mandate is gone")


def test_the_true_parts_of_that_same_briefing_survive():
    out = _strip(LIVE_GATE_TEXT)
    check("Good evening, Sir." in out, "the greeting survives")
    check("201 unread emails" in out,
          "a real reported figure survives — this strips lies, not content")


def test_a_bare_completion_claim_is_caught():
    check("taken care of" not in _strip("I have taken care of that for you.").lower(),
          "'taken care of' is caught even with no object named")


def test_each_mutating_verb_is_caught():
    for verb, sentence in (
        ("deleted", "I have deleted the file from your desktop."),
        ("sent", "I've sent that email to your accountant."),
        ("cancelled", "I have cancelled your 10 o'clock."),
        ("scheduled", "I've scheduled the appointment for Thursday."),
        ("archived", "I have archived those messages."),
    ):
        out = _strip(f"Good morning, Sir. {sentence}")
        check(sentence not in out, f"a false '{verb}' claim is removed")
        check("Good morning, Sir." in out, f"...and the rest survives ({verb})")


def test_legitimate_butler_phrasing_is_NOT_stripped():
    # The guard must be narrow. These describe SPEECH, not mutation, and they
    # are exactly how this persona talks — over-stripping would flatten it.
    keep = [
        "I have compiled your briefing, Sir.",
        "I have taken the liberty of noting the weather.",
        "I have three items to report.",
        "I've been monitoring the system since you left.",
    ]
    for s in keep:
        check(_strip(s) == s, f"kept, correctly: {s[:48]}")


def test_ordinary_status_lines_are_untouched():
    text = ("All primary systems are online. You have 3 unread emails and your "
            "heart rate is resting at 72. The weather is 28 degrees.")
    check(_strip(text) == text, "a briefing with no claims is returned unchanged")


def test_it_never_returns_empty():
    # Silence reads as a fault. A briefing that was ENTIRELY fabricated must
    # still produce something true rather than nothing.
    out = _strip("I have deleted everything. I have cleared your calendar.")
    check(bool(out.strip()), "output is never empty even if every sentence was cut")
    check("deleted" not in out.lower(), "...and the fabrication is still gone")


def test_empty_input_is_safe():
    check(_strip("") == "", "empty string passes through")
    check(_strip(None) is None, "None passes through without raising")


def test_the_known_limit_is_deliberate_and_recorded():
    # Passive voice is NOT caught: "Your schedule was cleared" makes no
    # first-person claim. Narrowness is the trade — a broader guard would eat
    # legitimate butler phrasing, and the dangerous case is JARVIS claiming
    # authorship of a mutation. Pinned so the gap is a decision, not a surprise.
    passive = "Your schedule was cleared this morning."
    check(_strip(passive) == passive,
          "passive voice is knowingly out of scope — recorded, not fixed")


# ── wiring ───────────────────────────────────────────────────────────────────

def test_generate_briefing_actually_passes_its_output_through_the_guard():
    fn = None
    for n in ast.walk(_TREE):
        if isinstance(n, ast.FunctionDef) and n.name == "generate_briefing":
            fn = n
    check(fn is not None, "generate_briefing found")
    wired = False
    for n in ast.walk(fn) if fn else []:
        if (isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)
                and n.value.func.id == "_strip_unfounded_action_claims"):
            wired = True
    check(wired,
          "the model's output is returned THROUGH the guard — unwired, it is dead code")


TESTS = [
    test_the_guard_exists,
    test_the_exact_live_gate_confabulation_is_removed,
    test_the_true_parts_of_that_same_briefing_survive,
    test_a_bare_completion_claim_is_caught,
    test_each_mutating_verb_is_caught,
    test_legitimate_butler_phrasing_is_NOT_stripped,
    test_ordinary_status_lines_are_untouched,
    test_it_never_returns_empty,
    test_empty_input_is_safe,
    test_the_known_limit_is_deliberate_and_recorded,
    test_generate_briefing_actually_passes_its_output_through_the_guard,
]


def main():
    print("=" * 60)
    print("briefing truthfulness harness (F-09)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
