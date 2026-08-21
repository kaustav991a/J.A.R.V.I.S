"""Harness: a false camera reject must leave a way back in.

F-23. The owner was refused by the camera against the same 12-sample set that
matched him twice earlier the same session, fell through to the voice challenge,
and was locked out of his own desk by this:

    [VISION] No match
    [JARVIS] Optical scan inconclusive. Please state your name.
    You said: 'my name is'                 <- capture ended before the name
    [JARVIS] I'm afraid I cannot grant you access. Interaction terminated.

Two defects, stacked. The camera was wrong — and with F-19, where the owner was
declared an intruder four minutes after a successful match, identity is
demonstrably unreliable in BOTH directions. Then the recovery path spent his one
attempt on a sentence the transcriber had cut in half.

"My name is …" is the one utterance in the system where a mid-sentence pause is
guaranteed, and the VAD ends the turn inside it. So the single most likely thing
a real owner says to this prompt was the single thing that could not work.

Three properties, and the third is the one that matters most:

  * he is asked more than once;
  * a lead-in with no name is told apart from a name nobody holds — one is a
    stranger, the other is the owner being cut off, and answering both with
    "Interaction terminated" is what caused the lockout;
  * silence is told apart from both, because telling an empty room it has been
    refused access is theatre, and telling someone who spoke that nothing was
    heard is a lie.

WHAT THIS PINS
--------------
`_identify_from_speech` and `_is_only_a_leadin` are lifted out of main.py by AST
and CALLED — the real source, not a copy. The loop itself is structural, because
reaching it needs a camera to fail first and a microphone to answer.
"""

import ast
import io
import re
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
    return (HERE / "main.py").read_text(encoding="utf-8", errors="replace")


def _lift():
    tree = ast.parse(_src())
    keep = []
    for n in tree.body:
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "").startswith(
                ("_NAME_ALIASES", "_IDENTITY_LEADINS")):
            keep.append(n)
        if isinstance(n, ast.Assign) and any(
                getattr(t, "id", "") == "_IDENTITY_ATTEMPTS" for t in n.targets):
            keep.append(n)
        if isinstance(n, ast.FunctionDef) and n.name in (
                "_identify_from_speech", "_is_only_a_leadin"):
            keep.append(n)
    ns = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "lifted", "exec"), ns)
    return ns


def _challenge_block() -> str:
    src = _src()
    start = src.index("FALLBACK: VOICE PROTOCOL")
    end = src.index("BRANCH A: KAUSTAV")
    return src[start:end]


# ── the utterance that caused the lockout ─────────────────────────────────────

def test_a_cut_off_answer_is_recognised_as_cut_off():
    """The exact string from the log. It is not a wrong answer — it is half of a
    right one, and the difference is the whole finding."""
    ns = _lift()
    lead = ns["_is_only_a_leadin"]
    check(lead("my name is") is True, "'my name is' is a lead-in, not an answer")
    check(ns["_identify_from_speech"]("my name is") is None,
          "...and it names nobody")


def test_every_common_lead_in_is_covered():
    """Each of these is a complete utterance a VAD can end on, and each names
    nobody."""
    lead = _lift()["_is_only_a_leadin"]
    for said in ("my name is", "my name's", "name is", "the name is", "i am",
                 "i'm", "im", "it is", "it's", "its", "this is", "call me"):
        check(lead(said) is True, f"{said!r} is a lead-in")


def test_punctuation_and_case_do_not_hide_a_lead_in():
    lead = _lift()["_is_only_a_leadin"]
    for said in ("My name is.", "MY NAME IS", "  my name is  ", "my name is,"):
        check(lead(said) is True, f"{said!r} is still a lead-in")


def test_a_lead_in_with_a_name_is_not_a_lead_in():
    ns = _lift()
    check(ns["_is_only_a_leadin"]("my name is kaustav") is False,
          "'my name is kaustav' is a complete answer")
    check(ns["_identify_from_speech"]("my name is kaustav") == "KAUSTAV",
          "...and it identifies him")


def test_an_unknown_name_is_not_a_lead_in():
    """A stranger and a cut-off owner must not be the same case."""
    ns = _lift()
    check(ns["_is_only_a_leadin"]("bob") is False, "'bob' is a real answer")
    check(ns["_identify_from_speech"]("bob") is None, "...naming nobody held")
    check(ns["_is_only_a_leadin"]("my name is bob") is False,
          "'my name is bob' is an answer, and a refusable one")


# ── who the aliases resolve to ────────────────────────────────────────────────

def test_the_mis_transcriptions_still_resolve():
    """These lists exist because the transcriber already produced every one of
    them. Moving them out of the wake branch must not lose any."""
    ident = _lift()["_identify_from_speech"]
    for said, who in [("kaustav", "KAUSTAV"), ("costav", "KAUSTAV"),
                      ("cow stuff", "KAUSTAV"), ("custard", "KAUSTAV"),
                      ("kinshuk", "KINSHUK"), ("king shook", "KINSHUK"),
                      ("mousumi", "MOUSUMI"), ("mouse me", "MOUSUMI"),
                      ("my sumi", "MOUSUMI")]:
        check(ident(said) == who, f"{said!r} resolves to {who}")


def test_the_alias_lists_are_read_in_one_place():
    """They were inline in the wake branch. The challenge and its retries both
    need them now, and a second copy of a list like this drifts."""
    src = _src()
    check("kaustav_aliases" not in src, "no second copy of the owner's aliases")
    check("kinshuk_aliases" not in src, "nor of the brother's")
    check("mousumi_aliases" not in src, "nor of the VIP's")
    check(src.count("_NAME_ALIASES") == 2,
          f"the map is defined once and read once ({src.count('_NAME_ALIASES')})")


def test_nobody_is_identified_by_silence():
    ident = _lift()["_identify_from_speech"]
    for said in ("", "   ", None):
        check(ident(said) is None, f"{said!r} identifies nobody")


# ── he is asked more than once ────────────────────────────────────────────────

def test_the_challenge_retries():
    block = _challenge_block()
    check("_IDENTITY_ATTEMPTS" in block, "the challenge has an attempt budget")
    check("for _try in range(1, _IDENTITY_ATTEMPTS + 1)" in block,
          "...and loops over it")
    check(_lift()["_IDENTITY_ATTEMPTS"] >= 3,
          "at least three, since the failure it recovers from is being cut off")


def test_each_reason_gets_its_own_sentence():
    """The old path had one sentence and used it for everything. Being told
    "that name is not one I hold" when you were cut off mid-word sends you
    looking for the wrong problem."""
    block = _challenge_block()
    check("only the beginning" in block, "a cut-off answer says it was cut off")
    check("not one I hold" in block, "an unknown name says that instead")
    check("did not catch that" in block, "silence says that")


def test_every_retry_is_logged_with_which_attempt_it_was():
    block = _challenge_block()
    check("F-23" in block, "the log names the finding")
    check("_IDENTITY_ATTEMPTS}" in block or "/{_IDENTITY_ATTEMPTS}" in block,
          "...and which attempt of how many this was")
    check(block.count("flush=True") >= 1, "...unbuffered")


def test_silence_and_refusal_are_different_endings():
    """Telling an empty room it has been refused access is theatre; telling
    someone who spoke that nothing was heard is a lie."""
    src = _src()
    check("_heard_nothing" in src,
          "the ending distinguishes 'nobody spoke' from 'nobody I know'")
    tail = src[src.index("if not _claimed and _heard_nothing:"):][:400]
    check("Returning to standby" in tail, "silence returns to standby")


def test_the_final_refusal_names_the_way_back():
    """The owner has stood on the wrong side of this. A refusal that does not
    say what to do next is the lockout, restated politely."""
    src = _src()
    denial = src[src.index("BRANCH D: UNKNOWN"):][:900]
    check("cannot grant you access" in denial, "it still refuses")
    check("face the camera" in denial, "...and says how to try again")
    check("Interaction terminated." not in denial,
          "the sentence that ended it is gone")


def test_the_refusal_is_reached_only_after_the_budget():
    src = _src()
    denial_at = src.index("BRANCH D: UNKNOWN")
    loop_at = src.index("for _try in range(1, _IDENTITY_ATTEMPTS + 1)")
    check(loop_at < denial_at, "the loop runs before the refusal can be reached")


# ── the same class at the other two challenges ────────────────────────────────

def test_the_relation_challenge_retries_too():
    """Root cause #4: the same defect at another site. The word this asks for is
    one the transcriber already renders as "bother" and "rather" often enough for
    both to be in the alias list — which argues for a retry, not against one."""
    src = _src()
    check("for _rtry in range(1, 3)" in src, "the relation challenge loops")
    rel = src[src.index("AWAITING RELATION"):][:1200]
    check("Relation mismatch" in src, "a genuine mismatch is still refused")
    check("did not catch that" in rel, "...but a miss is asked again first")


def test_the_passkey_challenge_retries_too():
    src = _src()
    check("for _ptry in range(1, 3)" in src, "the passkey challenge loops")
    check("Invalid passkey" in src, "a wrong passkey is still refused")


def test_no_challenge_spends_its_only_attempt_on_one_utterance():
    """Stated once, as the property. Every `listen_to_mic` on the identification
    path sits inside a loop."""
    src = _src()
    block = src[src.index("FALLBACK: VOICE PROTOCOL"):src.index("BRANCH D: UNKNOWN")]
    listens = block.count("listen_to_mic")
    loops = (block.count("for _try in range") + block.count("for _rtry in range")
             + block.count("for _ptry in range"))
    check(listens == loops,
          f"every identification listen is inside a retry loop "
          f"({listens} listens, {loops} loops)")


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
    print("Identity challenge — F-23")
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
