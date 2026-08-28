"""Harness: gate row 4.3, the patch that staged, was approved, and never applied.

Run: venv\\Scripts\\python.exe test_gate_row_43.py

WHY THIS EXISTS
---------------
`LIVE_GATE_CHECKLIST.md` row 4.3 is *"In add.py change the function name add to
plus"*, and the tracker recorded it as the one gate row that failed for a reason
still unexplained:

    still owed — the patch was staged correctly but never applied across two
    attempts; the second turn re-read the file instead. Not the same defect as
    before — needs its own look.

**The cause, reproduced offline and mechanically.** Row 4.1 writes `add.py` from
*"a simple add function"*, and what an LLM writes for that contains `add` at least
twice — the function, and the `__main__` block that calls it. Row 4.3's search
string is `add`. `WorkspaceAgent.patch_file` therefore refuses the patch as
AMBIGUOUS and writes nothing, which is **correct and deliberate**: until
2026-08-08 the default replaced every match silently, and that is the bug the
refusal exists to prevent.

So the staging was right, the approval was right, the refusal was right, and the
model re-reading the file was the designed recovery. What was broken was that
none of the refusal reached the operator in a form he could act on, at three
layers:

1. **`_sanitize_for_speech`** had no branch for the ambiguity refusal — added
   2026-08-08, after the `aborted` branch beside it — so it fell to the generic
   `_unevidenced` net and he heard *"The patch did not apply, Sir. The file is
   unchanged."* Honest, and stripped of the only part he could use. The RARER
   failure (too many matches) had carried actionable advice all along. He retried
   the identical phrasing, it failed identically, and the row was recorded as
   "cause not yet found".

2. **`brain.py` never told the planner that `*all*` exists.** The applier's own
   refusal text says *"or say explicitly that all 2 should change"* — advice the
   planner could not act on, because neither `workspace_patch` description
   mentioned the prefix. A refusal that recommends an unreachable path is a
   promise the system cannot keep, which is this project's top severity.

3. **`_confirm_disclosure` stripped the `*all*` prefix and never said "every
   occurrence".** A one-line edit and a rewrite of every match in the file read
   back as the *same sentence*. Latent only because of (2) — so fixing (2)
   without this would have created an under-disclosed authorisation, and F-29's
   rule is that the human is shown what they are approving.

WHAT THIS PINS
--------------
Offline. A temp workspace root, no model, no network, no governance daemon.

  * the applier's three outcomes, **checked against the bytes on disk** and not
    against what it said — row 4.1 already produced a "saved" over a file that
    was never written;
  * the spoken line for an ambiguous refusal names the count and both ways
    forward, and the other patch outcomes are unchanged;
  * a refusal that is *not* about ambiguity does not get the ambiguity text;
  * the confirmation read-back states SCOPE when `*all*` is present;
  * **and the two halves are pinned together**: the spoken advice says "tell me
    to change all of them", which is only honest while the planner knows the
    prefix. Removing either one fails this harness.
"""

import io
import os
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="row43-"))
# set BEFORE any import that builds WORKSPACE_ROOTS, so a harness run can never
# touch a real file of his
os.environ["JARVIS_WORKSPACE_ROOTS"] = str(_TMP)
os.environ.setdefault("JARVIS_SKIP_TTS", "1")

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


# what an LLM writes for "a simple add function", and the reason row 4.3 fails:
# `add` appears twice, so the search string is ambiguous
ADD_PY = (
    'def add(a, b):\n'
    '    """Return the sum of a and b."""\n'
    '    return a + b\n'
    '\n'
    '\n'
    'if __name__ == "__main__":\n'
    '    print(add(2, 3))\n'
)


def test_the_applier_refuses_the_row_as_written_and_writes_nothing():
    from modules import workspace_agent as wa

    agent = wa.WorkspaceAgent()
    p = _TMP / "add.py"

    check(ADD_PY.count("add") >= 2,
          f"the file row 4.1 writes contains 'add' more than once "
          f"({ADD_PY.count('add')}x) — this is the whole cause")

    p.write_bytes(ADD_PY.encode("utf-8"))
    said = agent.patch_file("add.py", "add", "plus")
    on_disk = p.read_bytes().decode("utf-8")
    check(on_disk == ADD_PY,
          "row 4.3 as written changes NOTHING on disk — verified in the bytes, "
          "not in the reply")
    check("refused" in said.lower() and "ambiguous" in said.lower(),
          f"and it says why: {said.splitlines()[0][:64]!r}")
    check("2" in said, "naming how many places it matched")

    # the row IS achievable — both ways
    p.write_bytes(ADD_PY.encode("utf-8"))
    said = agent.patch_file("add.py", "def add(", "def plus(")
    after = p.read_bytes().decode("utf-8")
    check("def plus(" in after and "print(add(" in after,
          "a search string identifying ONE place applies, and touches only it")
    check("replacement" in said.lower(), f"and reports it: {said.splitlines()[0]!r}")

    p.write_bytes(ADD_PY.encode("utf-8"))
    said = agent.patch_file("add.py", "add", "plus", replace_all=True)
    after = p.read_bytes().decode("utf-8")
    check("def plus(" in after and "print(plus(" in after and "add" not in after,
          "replace_all changes every occurrence, which is what `*all*` reaches")

    # a one-match file was never the problem
    p.write_bytes(b"def add(a, b):\n    return a + b\n")
    agent.patch_file("add.py", "add", "plus")
    check("def plus(" in p.read_bytes().decode("utf-8"),
          "an unambiguous single match still applies — the guard is not a wall")


def test_the_spoken_refusal_is_actionable():
    import main

    ambiguous = ("Patch refused: the search string matches 2 places in add.py, "
                 "so it is ambiguous which one to change. Include more "
                 "surrounding text so it identifies exactly one location — or "
                 "say explicitly that all 2 should change.")
    said = main._sanitize_for_speech("workspace_patch", ambiguous) or ""
    low = said.lower()
    check("2 places" in low, f"the spoken line names the count: {said!r}")
    check("longer piece" in low or "more" in low,
          "and offers a longer search string")
    check("all of them" in low, "and offers changing all of them")
    check("applied" not in low and "complete" not in low,
          "and never reads as success — the file was not touched")

    # the neighbours must not change
    said = main._sanitize_for_speech(
        "workspace_patch",
        "Patch aborted: search string matches 25 locations (max 20). Be more specific.")
    check("aborted" in (said or "").lower(),
          f"the too-many-matches line is untouched: {said!r}")
    said = main._sanitize_for_speech("workspace_patch", "Patched add.py: 2 replacement(s).")
    check("applied" in (said or "").lower(), f"a real patch still reports: {said!r}")

    # a refusal that is NOT about ambiguity must not borrow the advice
    said = main._sanitize_for_speech(
        "workspace_patch", "Patch refused: binary/executable file type '.exe'.") or ""
    check("all of them" not in said.lower(),
          f"a binary refusal gets no ambiguity advice: {said!r}")
    check("applied" not in said.lower() and "complete" not in said.lower(),
          "and still never claims success")


def test_the_readback_states_scope():
    """F-29 at the `*all*` door: two very different authorisations, one sentence."""
    import main

    class _Gov:
        def __init__(self, payload):
            self._p = payload

        def get_pending_payload(self, cid):
            return self._p

    original = main.governance_manager
    try:
        main.governance_manager = _Gov(
            {"action_type": "workspace_patch", "target": "add.py|add|plus"})
        one = main._confirm_disclosure("workspace_patch", "cid-1")
        check("add.py" in one, f"the path is disclosed: {one!r}")
        check("every occurrence" not in one.lower(),
              "a single-place patch does not claim to change every occurrence")

        main.governance_manager = _Gov(
            {"action_type": "workspace_patch", "target": "*all*add.py|add|plus"})
        every = main._confirm_disclosure("workspace_patch", "cid-2")
        check("every occurrence" in every.lower(),
              f"an *all* patch says so BEFORE he authorises it: {every!r}")
        check("add.py" in every,
              "and the path still resolves with the prefix stripped")
        check("*all*" not in every,
              "the prefix itself is machinery and stays out of the sentence")
        check(one != every,
              "the two authorisations no longer read identically — which is the "
              "whole finding")
    finally:
        main.governance_manager = original


def test_the_planner_knows_the_prefix_exists():
    """The half that makes the spoken advice honest rather than a promise.

    Pinned in BOTH directions and in one test on purpose: the speech offers "tell
    me to change all of them", and that is a capability claim. If a later edit
    drops the prefix from the prompt, the claim becomes something JARVIS cannot
    do — the top severity in this project — and this is the check that says so.
    """
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    descs = [ln for ln in src.splitlines()
             if ln.lstrip().startswith('- "workspace_patch"')]
    check(len(descs) >= 2,
          f"brain.py describes workspace_patch to the planner ({len(descs)} places)")
    without = [i for i, ln in enumerate(descs) if "*all*" not in ln]
    check(not without,
          f"and EVERY description names the *all* prefix (missing in {without}) "
          f"— root cause #4: one door taught, its sibling left ignorant")

    main_src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    offers = "change all of them" in main_src
    check(offers,
          "main.py's spoken refusal offers changing all of them")
    check(not offers or all("*all*" in ln for ln in descs),
          "the offer and the planner's knowledge of it are the same change — "
          "neither half may land alone")

    # and the applier really does honour the prefix the prompt now promises
    import action_engine
    check(getattr(action_engine.ActionEngine, "PATCH_ALL_PREFIX", None) == "*all*",
          "the prefix the prompt names is the prefix the engine reads")

    # The recovery advice is the fallback. This is what makes the row pass on the
    # FIRST turn: the planner reached for a bare `add`, which is what walks into
    # the guard. Being told to send `def add(` is the difference between "he is
    # told how to recover" and "it works when he asks".
    vague = [i for i, ln in enumerate(descs) if "UNIQUE" not in ln]
    check(not vague,
          f"every description requires a search string long enough to be UNIQUE "
          f"(missing in {vague})")
    check(all("def add(" in ln for ln in descs),
          "and each carries row 4.3's own example, so the rule is concrete "
          "rather than an adjective")


TESTS = [
    test_the_applier_refuses_the_row_as_written_and_writes_nothing,
    test_the_spoken_refusal_is_actionable,
    test_the_readback_states_scope,
    test_the_planner_knows_the_prefix_exists,
]


def main_() -> int:
    print("=" * 64)
    print("gate row 4.3 — the patch that staged, was approved, and did nothing")
    print("=" * 64)
    for t in TESTS:
        t()
    print("-" * 64)
    print(f"{_passed}/{_passed + _failed} passed")
    return 1 if _failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main_())
    finally:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
