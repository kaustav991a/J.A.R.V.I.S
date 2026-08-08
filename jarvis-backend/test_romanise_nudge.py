"""
test_romanise_nudge.py — a Benglish answer must not be unspeakable (F-13)
=========================================================================

Live gate, 2026-08-08. Asked how he was, JARVIS replied

    [JARVIS] আমি ভালো, মিঃ কাউষ্টব. আপনার কি হচ্ছে?
    [SPEAKER WARNING] Segment skipped (unsynthesizable): ', আমি ভালো, ...'

and said nothing at all. The TTS cannot synthesise Bengali script, and
`speak_text` swallows what it cannot speak — correct behaviour meeting a bug it
has no way to distinguish from having nothing to say. So the failure mode is not
"wrong script", it is **a lost answer**.

The persona has carried "reply in Latin letters" for a long time. It is not
enough: that turn measured ~3,282 tokens, so the rule sat ~3,200 tokens above
the message it governed. `cloud_gateway.py` solved this in 4fb0821 by injecting
a SCRIPT OVERRIDE system turn adjacent to the user message, but that commit
touched one file and the desk path never got it.

These checks pin the ported mechanism WITHOUT a live model — a model call would
prove a sample, not a property. What is provable here: the detector's boundaries,
and that the override is placed immediately before the user turn on every
message-building path in brain.py.
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


# brain.py imports the whole model stack, so lift the pure helper out of its
# SOURCE and run the real function rather than importing the module.
_SOURCE = BRAIN.read_text(encoding="utf-8", errors="replace")
_TREE = ast.parse(_SOURCE)

_NS: dict = {}
_detector = [n for n in _TREE.body
             if isinstance(n, ast.FunctionDef) and n.name == "_has_indic_script"]
_nudge = [n for n in _TREE.body
          if isinstance(n, ast.Assign)
          and any(isinstance(t, ast.Name) and t.id == "_ROMANISE_NUDGE"
                  for t in n.targets)]
if _detector and _nudge:
    mod = ast.Module(body=_nudge + _detector, type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), str(BRAIN), "exec"), _NS)


# ── the detector ─────────────────────────────────────────────────────────────

def test_the_pieces_exist_on_the_desk_path():
    check(bool(_detector), "_has_indic_script exists in brain.py, not only in cloud_gateway.py")
    check(bool(_nudge), "_ROMANISE_NUDGE exists in brain.py")


def test_bengali_script_is_detected():
    f = _NS["_has_indic_script"]
    check(f("আমি ভালো, মিঃ কাউষ্টব"), "the exact live-gate reply is detected as Bengali")
    check(f("এখন কর্টা বাজে?"), "the 4fb0821 sample is detected")


def test_devanagari_is_detected_because_whisper_mishears_bengali_as_hindi():
    # Kaustav never speaks Hindi; Devanagari in a transcript is mis-transcribed
    # Bengali, and it is equally unspeakable by the TTS.
    check(_NS["_has_indic_script"]("मैं ठीक हूँ"), "Devanagari is detected too")


def test_plain_latin_is_left_alone():
    f = _NS["_has_indic_script"]
    check(not f("how are you"), "plain English does not trigger the override")
    check(not f("ami bhalo achi, tumi kemon acho?"),
          "romanised Benglish does NOT trigger it — it is already in Latin letters")
    check(not f(""), "empty string is not Indic")
    check(not f("CPU 42%, 3 emails, 28°C"), "digits, punctuation and symbols do not trigger it")


def test_the_nudge_says_the_thing_that_actually_matters():
    text = _NS["_ROMANISE_NUDGE"]
    check("highest priority" in text.lower(), "the override announces its priority")
    check("Latin" in text, "it names the required script")
    check("NOT Bengali script" in text or "not bengali script" in text.lower(),
          "it says explicitly what NOT to do")


# ── the wiring: adjacency is the whole mechanism ─────────────────────────────

def _message_build_sites():
    """Every `messages.append({"role": "user", ...})` in brain.py, with the
    statement that precedes it."""
    # Keyed by line so a statement reachable from more than one parent body
    # (function body, enclosing try, ...) is not counted twice.
    sites = {}
    for node in ast.walk(_TREE):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            # Expr only: dumping a compound statement (a FunctionDef, an If)
            # includes its whole body, so `def process_command(...)` would match
            # on the append nested inside it.
            if not isinstance(stmt, ast.Expr):
                continue
            src = ast.dump(stmt)
            if ("value='user'" in src and "messages" in src
                    and "append" in src):
                sites[stmt.lineno] = (body[i - 1] if i else None, stmt)
    return [sites[k] for k in sorted(sites)]


def test_every_user_turn_is_preceded_by_the_override_check():
    sites = _message_build_sites()
    check(len(sites) >= 2,
          f"found the message-building sites, got {len(sites)} (expected >= 2: "
          f"process_command and process_stream)")
    guarded = 0
    for prev, _user_append in sites:
        if prev is None:
            continue
        d = ast.dump(prev)
        if "_has_indic_script" in d and "_ROMANISE_NUDGE" in d:
            guarded += 1
    check(guarded == len(sites),
          f"the override is injected immediately before EVERY user turn, "
          f"got {guarded}/{len(sites)}. Adjacency is the mechanism — a nudge "
          f"anywhere else is the persona rule again, which already failed.")


def test_the_override_is_a_system_turn_not_appended_to_the_user_text():
    # Folding it into the user message would make the operator appear to have
    # written it, and would put it inside what gets stored to memory.
    for prev, _ in _message_build_sites():
        if prev is None:
            continue
        d = ast.dump(prev)
        if "_ROMANISE_NUDGE" in d:
            check("value='system'" in d,
                  "the override is injected as a SYSTEM turn, not merged into the user text")
            return
    check(False, "no override injection found to inspect")


TESTS = [
    test_the_pieces_exist_on_the_desk_path,
    test_bengali_script_is_detected,
    test_devanagari_is_detected_because_whisper_mishears_bengali_as_hindi,
    test_plain_latin_is_left_alone,
    test_the_nudge_says_the_thing_that_actually_matters,
    test_every_user_turn_is_preceded_by_the_override_check,
    test_the_override_is_a_system_turn_not_appended_to_the_user_text,
]


def main():
    print("=" * 60)
    print("romanised-Benglish override harness (F-13)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
