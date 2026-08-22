r"""test_claims_guard.py — every claim JARVIS makes needs something behind it.

Run: venv\Scripts\python.exe test_claims_guard.py

WHY THIS EXISTS
---------------
Tier 1.1 of the reliability ladder. Of live-gate session 4's 16 findings, **7 were
one habit**: a claim made with nothing behind it. Not seven unrelated bugs — one
habit, seven times:

  F-48  a three-word prefix spoken as if it were the answer
  F-49  the model's own reasoning read aloud in the room
  F-60  an offer to order a pizza it has no tool to order
  F-61  a Google Sheets window described on a screen that had none
  F-62  an intruder accusation built on a failed match rather than a recognition
  F-63  an intruder flag held up over an empty room
  F-66  the voice loop showing and STORING what the speaker had refused to say

The taxonomy already existed and was already good — `brain.py` owns four claim
strippers, `reasoning_guard` owns the monologue, `screen_reader` owns entity
grounding. What did not exist was an answer to **"is every path covered?"**, and
that is the only question that stops the habit recurring.

WHAT THIS PINS
--------------
1. The **capability rule** (F-60) — a promise about the future has to be true too.
2. That its verb list is **derived from the live catalogue**, not guessed, so it
   cannot quietly become wrong when a tool is added.
3. An **inventory** of every function that asks an LLM for user-visible text, each
   with the guard it carries — or a written decision that it carries none, and
   why. A new reply path fails this suite until somebody decides which it is.

That third pin is the one that prevents rework. Session 4 found the same class at
`clean_response` twice (two of three sites guarded, then three of three) and at
`process_stream` (which turned out to be guarded all along, via a different
helper). Guesswork about coverage is what cost that time.
"""

import ast
import io
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
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


# ── 1. F-60: a promise is a claim about the future ──────────────────────────

def test_a_promise_it_cannot_keep_is_dropped():
    """Row 23b.3 asked for a capability JARVIS has none of, and got an offer.

    The live reply was "I'll need the pizza type, size and any toppings you
    prefer, Sir, before I can place the order." A false completion is a lie about
    the past that he can check; a false promise is one he ACTS on.
    """
    import brain

    must_drop = [
        "I'll need the pizza type, size and any toppings you prefer, Sir, "
        "before I can place the order.",
        "I can order that for you, Sir.",
        "Shall I book the table, Sir?",
        "I will place the order once you confirm the toppings.",
        "I would be happy to buy that for you.",
        "I'd be happy to order it, Sir.",
        "Let me transfer the funds now.",
        "I could reserve a table for two, Sir.",
    ]
    for s in must_drop:
        check(brain._promises_a_capability_it_lacks(s), f"dropped: {s[:56]}")


def test_an_honest_refusal_survives():
    """The outcome we want must not be eaten by the guard that wants it."""
    import brain

    for s in ("I cannot order anything, Sir — I have no tool for it.",
              "I have no way to book a table, Sir.",
              "I'm not able to buy things, Sir.",
              "I won't be able to pay that, Sir.",
              "I do not have a tool to order food, Sir."):
        check(not brain._promises_a_capability_it_lacks(s),
              f"kept (honest refusal): {s[:52]}")


def test_a_real_capability_is_never_touched():
    """A guard that eats true offers is worse than the bug it fixes."""
    import brain

    for s in ("I can open Notepad for you, Sir.",
              "I can check your calendar, Sir.",
              "Shall I read your unread emails, Sir?",
              "I can call up your calendar, Sir.",
              "I would be happy to check your calendar, Sir.",
              "I would be delighted, Sir.",
              "I'll write that to your desktop, Sir.",
              "Your books are on the desk, Sir.",
              "The order of the results is by date, Sir.",
              "I retrieved the data, Sir, but hit a snag presenting it. "
              "Shall I try again?"):
        check(not brain._promises_a_capability_it_lacks(s),
              f"kept (real or ordinary): {s[:52]}")


def test_the_verb_list_is_derived_from_the_catalogue_not_guessed():
    """The pin that keeps the rule honest as JARVIS grows.

    Every verb in `_NO_TOOL_VERBS` must have NO action behind it. The day someone
    adds a `place_order` tool, this fails and the list has to be revisited rather
    than silently telling him JARVIS cannot do something it now can.
    """
    import brain
    from modules import action_router

    names = " ".join(
        (a.get("name", "") if isinstance(a, dict) else str(a[0]))
        for a in action_router.ACTIONS).lower()

    wrong = [v for v in brain._NO_TOOL_VERBS
             if re.search(rf"\b{v}\b", names)]
    check(not wrong,
          f"no verb in the list has an action behind it ({wrong})")
    check(len(brain._NO_TOOL_VERBS) >= 10,
          f"and the list is substantive ({len(brain._NO_TOOL_VERBS)} verbs)")
    check("call" not in brain._NO_TOOL_VERBS,
          "'call' is excluded — 'call up your calendar' is ordinary phrasing")


def test_both_live_reply_paths_inherit_the_rule():
    """It is in the shared per-sentence predicate, so both doors get it for free.

    `process_command` reaches it through the whole-text wrapper and
    `process_stream` calls the predicate directly — which is why the rule belongs
    in the predicate and not in either caller.
    """
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)

    pred = next((n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == "_sentence_is_unfounded"), None)
    check(pred is not None, "the shared predicate exists")
    body = ast.get_source_segment(src, pred) or ""
    check("_promises_a_capability_it_lacks" in body,
          "and the capability rule lives inside it")

    for caller in ("process_command", "process_stream"):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == caller), None)
        check(fn is not None, f"{caller} exists")
        cbody = ast.get_source_segment(src, fn) or ""
        reaches = ("_sentence_is_unfounded" in cbody
                   or "_strip_unfounded_conversational_claims" in cbody)
        check(reaches, f"{caller} reaches the predicate")


# ── F-60, second half: the request side ─────────────────────────────────────

def test_a_request_for_an_absent_capability_is_detected():
    """The signal the reply-side rule could never see.

    After the promise form was closed, the live desk stopped promising and started
    GATHERING ARGUMENTS instead:

        "order me a pizza from Dominos"
          -> "What size and toppings would you like, Sir - and where should it
              be delivered?"

    No promise, no claim, and the most misleading of the three, because he answers
    it and then waits.
    """
    import brain

    for text, want in (("order me a pizza from Dominos", "order"),
                       ("book me a table at a restaurant tonight", "book"),
                       ("buy the new keyboard for me", "buy"),
                       ("please pay the electricity bill", "pay"),
                       ("reserve me two seats for the show", "reserve"),
                       ("subscribe to that newsletter", "subscribe"),
                       ("withdraw 2000 from my account", "withdraw")):
        got = brain.requested_capability_absent(text)
        check(got == want, f"{want!r} detected in: {text[:46]}")


def test_a_real_capability_request_is_never_flagged():
    """Every one of these is a measured false positive of an earlier draft, or a
    real action in the catalogue. A guard that refuses real work is worse than the
    bug it fixes."""
    import brain

    for text in ("sort the results in order of date",
                 "what is the order of the results",
                 "order the files by size",
                 "deliver the briefing",
                 "transfer the file to my desktop",
                 "check my phone battery",
                 "read my book notes",
                 "open the phone book",
                 "pay attention to the logs",
                 "book a note about the meeting",
                 "reorder the list",
                 "what is on my calendar",
                 "write add.py to my desktop"):
        check(brain.requested_capability_absent(text) is None,
              f"not flagged: {text[:46]}")


def test_the_ambiguous_verbs_need_the_transaction_shape():
    """`order` alone is a sort; `order me a` is a purchase.

    The split exists because the first draft flagged "deliver the briefing" (a
    real action) and "order the files by size" (a sort).
    """
    import brain

    check(set(brain._NO_TOOL_VERBS_STRICT).isdisjoint(brain._NO_TOOL_VERBS_OBJECT),
          "a verb is in exactly one of the two lists")
    for v in brain._NO_TOOL_VERBS_OBJECT:
        check(brain.requested_capability_absent(f"{v} the report") is None,
              f"'{v} the report' alone is not evidence")
        check(brain.requested_capability_absent(f"{v} me a thing") == v,
              f"'{v} me a thing' is")


def test_an_honest_refusal_in_the_reply_is_recognised():
    """If the model already refused, the guard must not refuse twice."""
    import brain

    check(brain.reply_admits_it_cannot("I cannot order anything, Sir."),
          "a refusal is recognised")
    check(brain.reply_admits_it_cannot("I have no way to do that, Sir."),
          "so is the 'no way to' form")
    check(not brain.reply_admits_it_cannot(
        "What size and toppings would you like, Sir?"),
        "and an argument-gathering question is NOT a refusal")


def test_the_stated_boundary_is_still_the_documented_one():
    """A boundary that is written down beats one that fails open.

    "transfer 500 rupees" is NOT flagged: `transfer` needs the indirect-object
    shape, because "transfer the file to my desktop" is a real capability. This is
    a deliberate miss, recorded so the next person does not read it as a bug.
    """
    import brain

    check(brain.requested_capability_absent("transfer 500 rupees to mousumi") is None,
          "the documented miss still behaves as documented")


def test_the_refusal_is_wired_into_the_reply_path():
    """The detector is worthless if nothing calls it."""
    import ast

    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "process_command"), None)
    check(fn is not None, "process_command exists")
    body = ast.get_source_segment(src, fn) or ""
    check("requested_capability_absent(" in body,
          "process_command consults the request-side rule")
    check("reply_admits_it_cannot(" in body,
          "and does not refuse twice when the model already refused")


def test_no_source_file_carries_a_stray_control_byte():
    """The F-18 class, made mechanical — and it caught one of mine.

    While building this rule, a heredoc turned `\\b` into a literal 0x08
    BACKSPACE and wrote it into brain.py. The regex became "match a backspace,
    then the verb, then a backspace", which is invisible in every normal view and
    matches nothing. F-18 was the same shape: `\\v` read as a vertical tab inside
    a path, which rendered as a plausible directory name for weeks.
    """
    allowed = {0x09, 0x0A, 0x0D}
    skip_dirs = {"venv", "__pycache__", "node_modules", ".git", "captures",
                 "models", "action_chroma_db", "jarvis_chroma_db", "metrics",
                 "release", "dist", "build"}
    offenders = []
    # DOCUMENTS TOO, and that is not thoroughness for its own sake: the first
    # version of this scan looked only at *.py, and within the hour the same
    # heredoc wrote `\v` into JARVIS_TRACKER.md as a 0x0B vertical tab -- turning
    # `jarvis-backend\venv\Scripts` into `jarvis-backend<VT>env\Scripts`, which is
    # F-18 CHARACTER FOR CHARACTER. F-18 was in a document. A scan that skips
    # documents cannot catch F-18.
    targets = [q for pat in ("*.py", "*.md", "*.json", "*.yaml", "*.yml")
               for q in HERE.parent.rglob(pat)]
    for path in sorted(set(targets)):
        if any(p in skip_dirs for p in path.parts):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for i, b in enumerate(raw):
            if b < 0x20 and b not in allowed:
                offenders.append(f"{path.name}:{raw[:i].count(chr(10).encode())+1} "
                                 f"0x{b:02x}")
                break
    check(not offenders,
          f"no source OR DOCUMENT carries an invisible control byte ({offenders})")


# ── 2. The coverage inventory — the pin that prevents rework ────────────────

#: Every function in brain.py that asks an LLM for text a human may end up
#: reading or hearing, and what guards it. A new one fails this test until it is
#: added here with a decision — which is the whole point: coverage is a decision,
#: not an assumption.
_LLM_TEXT_FUNCTIONS = {
    "process_command": (
        "_strip_unfounded_conversational_claims",
        "the main non-streaming reply. Whole-text wrapper -> shared predicate.",
    ),
    "process_stream": (
        "_sentence_is_unfounded",
        "the streaming reply. Calls the predicate per sentence, because a claim "
        "cannot be withheld once half of it has been spoken. DORMANT today: "
        "nothing starts streaming_daemon and JARVIS_FULL_DUPLEX_PIPELINE=0, so "
        "it never ran in any live session — guarded anyway, before it is enabled.",
    ),
    "generate_briefing": (
        "_strip_unsourced_state_claims",
        "the briefing. Carries BOTH briefing guards (action claims + unsourced "
        "state claims) because it reports on sources that may have returned NO "
        "DATA — a different taxonomy from a conversational reply.",
    ),
    "classify_intent": (
        None,
        "internal routing only. Its output is a dict of labels, never spoken and "
        "never shown, so there is no claim to guard.",
    ),
    "synthesize_info": (
        None,
        "DECISION, session 4: no conversational guard. It reports TOOL DATA, and "
        "the predicate was measured against eight real synthesis answers with "
        "zero false positives, so adding it would be safe but unjustified — no "
        "claim defect has ever been observed on this path. F-32 (a prompt example "
        "recited as a reading) came from here and was fixed at the prompt, not "
        "with a stripper. Revisit the moment one IS observed.",
    ),
    "synthesize_info_gen": (
        None,
        "same decision as synthesize_info, and it is a generator: guarding it "
        "means the per-sentence pattern, which is worth doing only against real "
        "evidence rather than on suspicion.",
    ),
    "synthesize_deep_memory_gen": (
        None,
        "same decision. Reads back stored memories; the risk here is disclosure, "
        "which partner_log and the VIP refusals own, not claim fabrication.",
    ),
    "_iter_briefing_sentences_from_stream": (
        None,
        "streams sentences that generate_briefing has ALREADY guarded; guarding "
        "again would double-drop.",
    ),
}


def test_every_llm_text_function_has_a_coverage_decision():
    """The question session 4 could not answer without a lot of grepping."""
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)

    found = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.get_source_segment(src, fn) or ""
        if "universal_llm_call(" in body:
            found.add(fn.name)

    unknown = sorted(found - set(_LLM_TEXT_FUNCTIONS))
    check(not unknown,
          f"every LLM-text function has a recorded decision "
          f"(undeclared: {unknown})")

    stale = sorted(set(_LLM_TEXT_FUNCTIONS) - found)
    check(not stale,
          f"and the inventory has no entries for functions that are gone ({stale})")

    for name, (guard, why) in _LLM_TEXT_FUNCTIONS.items():
        check(bool(why and len(why) > 40),
              f"{name}: the decision is written down, not implied")
        if guard:
            fn = next((n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == name), None)
            body = ast.get_source_segment(src, fn) or ""
            check(guard in body, f"{name}: actually calls {guard}")


def test_the_guards_the_inventory_names_all_exist():
    """A named guard that does not exist is a coverage claim with nothing behind
    it — which would be this suite committing the very habit it polices."""
    import brain

    for name, (guard, _why) in _LLM_TEXT_FUNCTIONS.items():
        if guard:
            check(callable(getattr(brain, guard, None)),
                  f"{guard} exists and is callable")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 66)
    print("Claims guard — a claim needs something behind it (Tier 1.1)")
    print("=" * 66)
    for t in TESTS:
        t()
    print("-" * 66)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
