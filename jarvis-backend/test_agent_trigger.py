r"""test_agent_trigger.py — F-59: the door the 56-tool catalogue sits behind.

Run: venv\Scripts\python.exe test_agent_trigger.py

WHAT WAS MEASURED, AND WHY IT WAS NOT A TEST PROBLEM
----------------------------------------------------
F-59 was recorded as "`should_use_agent` accepts two sentence shapes while A22 has
24 rows written against goals it will not accept — widen the gate or rewrite the
rows". Measured on 2026-08-22, it was worse and simpler than that: the gate
accepts **0 of the 14** A22 phrases. Not some. None.

    turn the TV volume up three notches        -> no
    what have I changed in the project         -> no
    did she message me today                   -> no
    chart my last 5 days of steps              -> no
    show me a picture of a red panda            -> no

The two wired shapes are a file-recency read and a file write with content, so
six waves of tool work — 56 tools, the shelf, `search_tools`, the skills, MCP —
were reachable only by a request about a file.

And the narrowness is CORRECT. The code says why: "a false positive routes a
trivial command through a multi-step loop." Routing "turn the TV up" through the
agent turns a one-second command into six seconds with more failure surface, and
the one-shot path already does it properly today.

So neither of the recorded options was right. The gate is not widened and the rows
are not rewritten away: an EXPLICIT trigger was added, chosen by Kaustav on
2026-08-22. He says "work through this: …" or "figure out …" and the whole tool
layer is reachable; he says anything else and nothing about today changes. No
false positives to tune, because opting in is not a guess.

THE HALF THAT NEARLY GOT MISSED
-------------------------------
Opening the door is not enough — what is behind it has to be the right tools. Two
things had to follow, and both are pinned below:

  * the trigger is STRIPPED from the goal, because the shelf's preload searches
    the goal text and "work through this" dilutes it
  * a triggered goal that fits neither wired shape gets the `open` base — ONE
    tool — so the preload can fill the rest. Handing a TV goal five file tools
    was the entire tier 2.1 finding, and doing it again through a new door would
    have reintroduced it

The alias test at the bottom exists because the first run of this harness found
that "what have I changed in the project" — row 23b.9, verbatim — matched
**nothing**: "changed" was an alias of `github_diff` only and "project" of nothing
at all. The offline retrieval eval scores 40/40 and missed it, because it uses its
own phrasings rather than the checklist's. That is why these assertions quote the
gate rows word for word.
"""

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("JARVIS_AGENT_LOOP", "1")

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


def _ar():
    from modules import agent_runner
    return agent_runner


#: The A22 gate rows, quoted as the checklist words them, with the tool each one
#: needs. This is the list the gate accepted none of.
A22 = [
    ("23b.4", "turn the TV volume up three notches", "tv_volume"),
    ("23b.5", "put Stranger Things on Netflix on the TV", "tv_play_media"),
    ("23b.6", "play moonlight", "play_music"),
    ("23b.9", "what have I changed in the project", "github_status"),
    ("23b.11", "open the wikipedia page and search for red pandas", "web_browse"),
    # `render_image` does not exist -- the expectation in the first version of
    # this list was mine, not the code's. The real tool is web_search_image.
    ("23b.12", "show me a picture of a red panda", "web_search_image"),
    ("23b.13", "did she message me today", "partner_contact_status"),
    ("23b.15", "chart my last 5 days of steps", "render_chart"),
]


# ------------------------------------------------------------------- the gate
def test_the_wired_shapes_still_work_exactly_as_before():
    """The trigger is ADDITIVE. If this fails, the change cost him something."""
    ar = _ar()
    for goal in ("find my most recent file and tell me what's in it",
                 "what's in the newest document in my workspace",
                 "write a note called plan.md saying buy milk"):
        check(ar.should_use_agent(goal) is True,
              f"still routes to the agent: {goal[:46]}")
    for goal in ("what is the time", "turn the TV volume up three notches",
                 "hello", "did she message me today"):
        check(ar.should_use_agent(goal) is False,
              f"still does NOT: {goal[:46]} — the one-shot path keeps it")


def test_every_a22_phrase_is_refused_plain_and_accepted_triggered():
    """Both halves matter. The first is the measurement that produced F-59; the
    second is the fix, phrase by phrase, in the checklist's own words."""
    ar = _ar()
    for rid, phrase, _tool in A22:
        check(ar.should_use_agent(phrase) is False,
              f"{rid} plain is still one-shot ({phrase[:38]})")
    for rid, phrase, _tool in A22:
        check(ar.should_use_agent("work through this: " + phrase) is True,
              f"{rid} triggered reaches the agent")


def test_the_trigger_is_stripped_from_the_goal():
    """The shelf's preload searches the GOAL. Leaving "work through this" in it
    dilutes the query that has to find `tv_volume`."""
    ar = _ar()
    for prefix in ("work through this: ", "work through this ", "figure out ",
                   "sort this out — ", "handle this: "):
        goal = ar.agent_goal(prefix + "turn the TV volume up")
        check(goal == "turn the TV volume up",
              f"{prefix.strip()!r} leaves only the task ({goal!r})")
    mid = ar.agent_goal("could you figure out what changed in the project")
    check("figure out" not in mid,
          f"a trigger mid-sentence is removed too ({mid!r})")
    check(ar.agent_goal("what is the time") == "what is the time",
          "an untriggered request is returned untouched")


def test_a_trigger_with_no_task_is_not_a_task():
    """The first version returned the trigger itself as the goal when nothing
    remained, so "figure out" alone opened a multi-step loop over nothing."""
    ar = _ar()
    for phrase in ("figure out", "work through this", "work through this:",
                   "handle this", "work through this: it"):
        check(ar.should_use_agent(phrase) is False,
              f"{phrase!r} is refused — no task behind the trigger")
    check(ar.agent_goal("figure out") == "",
          "and the empty goal reads as EMPTY, not as the trigger")


def test_the_trigger_is_case_insensitive():
    ar = _ar()
    for text in ("Work Through This: turn the TV volume up",
                 "FIGURE OUT what changed in the project"):
        check(ar.should_use_agent(text) is True, f"accepted: {text[:40]}")


def test_the_flag_still_governs_everything():
    """An explicit trigger must not be a way around the feature flag."""
    ar = _ar()
    off = {"JARVIS_AGENT_LOOP": "0"}
    check(ar.should_use_agent("work through this: turn the TV up", off) is False,
          "flag off refuses a triggered goal as well as a wired one")


# ------------------------------------------------------------- what is behind it
def test_a_triggered_goal_gets_the_open_base_not_five_file_tools():
    """Tier 2.1's finding, which a new door could have reintroduced: the model was
    handed five file tools and asked to book a dentist appointment."""
    ar = _ar()
    check(ar.tool_set_for("work through this: turn the TV volume up") == "open",
          "a triggered non-file goal gets the `open` base")
    check(ar.tool_set_for("work through this: write a note called x.md saying hi")
          == "authoring", "a triggered WRITE goal still gets authoring")
    check(ar.tool_set_for("work through this: what's in my most recent file")
          == "files", "a triggered file READ still gets files")
    check(ar.tool_set_for("turn the TV volume up") == "files",
          "and an untriggered request is classified exactly as before")

    from modules import agent_tools
    reg = agent_tools.default_registry()
    base = reg.set_names("open")
    check(len(base) == 1,
          f"the open base is ONE tool, leaving the slots for the goal ({base})")


def test_every_a22_phrase_puts_its_tool_in_front_of_the_model():
    """The half that opening the door does not solve. Quoted from the checklist on
    purpose: the offline eval is 40/40 on its OWN phrasings and still missed that
    row 23b.9's exact words matched nothing at all."""
    ar = _ar()
    from modules import agent_tools
    from modules.agent_search import ToolShelf

    reg = agent_tools.default_registry()
    missing = []
    for rid, phrase, tool in A22:
        text = "work through this: " + phrase
        goal = ar.agent_goal(text)
        shelf = ToolShelf(reg, base=reg.set_names(ar.tool_set_for(text)),
                          max_tools=8, allow_confirm=True)
        room = shelf.room()
        seed = [h.name for h in shelf.search(goal)[:room]]
        if seed:
            shelf.promote(seed)
        if tool not in set(shelf.resident()):
            missing.append(f"{rid}:{tool}")
    check(not missing,
          f"all {len(A22)} gate phrases surface their tool ({missing})")


def test_row_23b9s_exact_words_reach_github_status():
    """Pinned on its own because it is the one that failed, and because the fix
    was two aliases -- the kind of thing that silently rots back."""
    from modules import agent_tools
    from modules.agent_search import ToolShelf
    reg = agent_tools.default_registry()
    shelf = ToolShelf(reg, base=["memory_recall"], max_tools=8,
                      allow_confirm=True)
    for phrase in ("what have I changed in the project",
                   "what I have changed in the project"):
        names = [h.name for h in shelf.search(phrase)[:5]]
        check("github_status" in names, f"{phrase!r} -> {names}")
    aliases = reg.get("github_status").aliases
    check("project" in aliases and "changed" in aliases,
          f"and the two aliases that fixed it are still there ({aliases})")


# ------------------------------------------------------------------ both doors
def test_both_doors_use_the_stripped_goal_and_one_gate():
    """Root cause #4: the desk and the remote channel each call the gate, so a
    change made at one and not the other is the project's most common bug."""
    src = (HERE / "main.py").read_text(encoding="utf-8")
    check(src.count("agent_runner.should_use_agent(") == 2,
          "both doors ask the same gate function")
    check(src.count("agent_runner.agent_goal(") == 2,
          "and both pass the STRIPPED goal to the loop")
    check(src.count("agent_runner.explicit_trigger(") == 2,
          "and both log which door opened, so the trace says why")
    check("command_text, engine, lock=COMMAND_LOCK, send=_agent_notify" not in src,
          "neither passes the raw text with the trigger still in it")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 70)
    print("The agent trigger — F-59, the door the catalogue sits behind")
    print("=" * 70)
    for t in TESTS:
        t()
    print("-" * 70)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
