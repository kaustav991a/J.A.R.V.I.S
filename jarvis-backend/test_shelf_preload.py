r"""test_shelf_preload.py — the right tool must be in front of the model.

Run: venv\Scripts\python.exe test_shelf_preload.py

TIER 2.1, AND THE DIAGNOSIS MATTERED MORE THAN THE FIX
------------------------------------------------------
`run_evals.py --live` scored **19/34 (56%)** on 2026-08-22, with calendar 0/3,
misc 0/3 and web 1/4. The tracker recorded that as a retrieval problem —
descriptions, aliases, ranking. **It was not.** The offline retrieval eval is
40/40: the catalogue surfaces the right tool for every one of those requests.

The cause was upstream of retrieval. `tool_set_for()` can return only `files` or
`authoring`, and **not one** of the fifteen missed tools is in either set — so the
model was handed five file tools and asked to book a dentist appointment. Its only
route to `check_calendar` was to decide, unprompted, that nothing it could see fit
and to call `search_tools`. Sometimes it did (tv scored 5/5); for calendar and misc
it never did, and reached for `find_file` three times in a row instead.

So the search the model might have made is now made FOR it, once, with the goal as
the query, filling only the slots that were already free. Measured across the
eval's 40 tasks — the expected tool resident before the model's first turn:

    before the preload:   4/40  (10%)
    after  the preload:  39/40  (97%)

WHAT THIS HARNESS IS AND IS NOT
-------------------------------
It measures **what the model is shown**, deterministically and offline, which is
the thing the change actually controls. It does NOT measure whether the model then
chooses correctly — that is `--live`, it costs real actions on a real desk, and
quoting one as the other is how a project ends up believing its agent is better
than it is.

One measurement bug worth recording, because it nearly became a wrong conclusion:
the first version of this measurement hardcoded `allow_confirm=False`, which HIDES
every CONFIRM-tier tool — correctly, since an unattended run cannot get approval.
That made five tasks look unreachable when the eval actually runs them `at_desk`.
The apparent 85% was an artefact of the measurement, not the product. Mirror the
caller's presence handling or measure the wrong thing.
"""

import json
import pathlib
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


def _tasks():
    p = HERE / "evals" / "agent_tasks.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def _shelf(goal: str, allow_confirm: bool, preload: bool):
    """A shelf built the way `agent_runner` builds one, with and without preload."""
    from modules import agent_runner as ar
    from modules import agent_tools
    from modules.agent_search import ToolShelf

    reg = agent_tools.default_registry()
    shelf = ToolShelf(reg, base=reg.set_names(ar.tool_set_for(goal)),
                      max_tools=8, allow_confirm=allow_confirm, extra=[])
    if preload:
        room = shelf.room()
        if room > 0:
            seed = [h.name for h in shelf.search(goal)[:room]]
            if seed:
                shelf.promote(seed)
    return shelf


def test_the_runner_preloads_the_shelf_from_the_goal():
    """The fix must be in the runner, not only in this harness's idea of it."""
    import ast

    src = (HERE / "modules" / "agent_runner.py").read_text(encoding="utf-8",
                                                           errors="replace")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "run_agent_command"), None)
    check(fn is not None, "run_agent_command exists")
    body = ast.get_source_segment(src, fn) or ""
    check("shelf.search(goal)" in body,
          "the runner searches the catalogue with the goal")
    check("shelf.promote(" in body,
          "and promotes what it finds before the loop starts")
    check("shelf.room()" in body,
          "into free slots only — the 8-tool ceiling is not raised")


def test_the_expected_tool_is_in_front_of_the_model():
    """The number this change exists to move.

    Presence mirrors the eval: the five CONFIRM-tier tasks run `at_desk`, because
    an unattended run hides CONFIRM tools by design and scoring them as misses
    would blame the model for governance working.
    """
    tasks = _tasks()
    before = after = 0
    misses = []
    for t in tasks:
        want = set(t["expect"])
        ac = bool(t.get("confirm"))
        r0 = set(_shelf(t["prompt"], ac, preload=False).resident())
        r1 = set(_shelf(t["prompt"], ac, preload=True).resident())
        before += bool(want & r0)
        if want & r1:
            after += 1
        else:
            misses.append(t["id"])

    n = len(tasks)
    check(n >= 40, f"the eval set is intact ({n} tasks)")
    check(before <= 6,
          f"without the preload almost nothing is reachable ({before}/{n}) — "
          f"this is the baseline the 56% came from")
    check(after >= int(n * 0.9),
          f"with it, the expected tool is resident for {after}/{n} "
          f"({after * 100 // n}%) — misses: {misses}")
    check(after > before * 3,
          f"and that is a real improvement, not noise ({before} -> {after})")


def test_the_preload_respects_the_ceiling_and_the_base_set():
    """A fix that quietly raised max_tools would undo a deliberate limit.

    The 8-tool ceiling exists because small models degrade past it, and the base
    set is what the intent was wired with — neither may be sacrificed to make room
    for a guess.
    """
    from modules import agent_runner as ar
    from modules import agent_tools

    reg = agent_tools.default_registry()
    for t in _tasks()[:12]:
        base = set(reg.set_names(ar.tool_set_for(t["prompt"])))
        shelf = _shelf(t["prompt"], bool(t.get("confirm")), preload=True)
        resident = shelf.resident()
        check(len(resident) + 1 <= 8,
              f"{t['id']}: at most 8 tools including search_tools "
              f"({len(resident) + 1})")
        check(base <= set(resident),
              f"{t['id']}: the wired base set is never evicted")


def test_search_tools_is_still_offered_after_preloading():
    """The preload is a HINT, not a replacement.

    One goal can need a second tool the first search did not rank — `tv-04` is
    exactly that case in the current set — so the model must still be able to look.
    """
    shelf = _shelf("put something on the television for me", False, preload=True)
    names = [d.get("name") or d.get("function", {}).get("name")
             for d in shelf.defs()]
    check("search_tools" in names,
          f"search_tools is still in the tool list ({len(names)} tools)")


def test_a_goal_with_no_good_match_preloads_nothing_harmful():
    """An unrelated goal must not push junk in front of the model in place of the
    base set. It may preload something irrelevant — the slots are free — but the
    file tools it was wired with have to survive."""
    from modules import agent_runner as ar
    from modules import agent_tools

    reg = agent_tools.default_registry()
    goal = "zzzq wibble frotz"
    base = set(reg.set_names(ar.tool_set_for(goal)))
    shelf = _shelf(goal, False, preload=True)
    check(base <= set(shelf.resident()),
          "a nonsense goal leaves the wired set intact")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 64)
    print("Shelf preload — is the right tool in front of the model? (Tier 2.1)")
    print("=" * 64)
    for t in TESTS:
        t()
    print("-" * 64)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
