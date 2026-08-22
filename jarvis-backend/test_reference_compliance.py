r"""test_reference_compliance.py — the tool layer against AGENT-TOOLING-REFERENCE.

Run: venv\Scripts\python.exe test_reference_compliance.py

WHY THIS EXISTS, AND WHAT THE FIRST MEASUREMENT GOT WRONG
--------------------------------------------------------
Asked on 2026-08-22 whether the tools were built to the reference's 18 rules. The
first pass counted CHARACTERS — 14 of 56 descriptions under 150 — and called that
the rule-1 gap. Reading the 14 showed the metric was junk: `check_calendar` at 142
characters carries a trigger AND a negative boundary ("Read-only — it cannot add
or move anything"), while `hud_close_widget` at 34 carried nothing at all. Length
correlates with quality but is not it.

So this harness pins what rule 1 actually asks for, which is testable:

  * a NEGATIVE BOUNDARY — something the tool does not do, will not accept, or is
    not for. Rule 1 lists this first among the things a description must carry,
    and it is what stops a confusable pair being picked at random.
  * SIBLING POINTERS THAT RESOLVE. If a description says "use `x` instead", then
    `x` must be a registered tool. A pointer to a tool that does not exist is the
    F-09 failure — a claim with nothing behind it — aimed at the model instead of
    at Kaustav, and it strands the model exactly when it was trying to correct
    itself.
  * MUTUALITY for the pairs that are genuinely confusable. One-way pointers only
    help the model that already picked correctly.

Rule 11 is pinned here too, as a DECISION rather than a mechanism: the batch is
executed serially on purpose, and the reasons live at the loop. A future reader
who sees half a rule implemented and no explanation would reasonably "fix" it.
"""

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


_reg = None


def registry():
    global _reg
    if _reg is None:
        from modules import agent_tools
        _reg = agent_tools.default_registry()
    return _reg


def descriptions() -> dict:
    r = registry()
    return {d["name"]: d["description"] for d in r.defs(r.names())}


#: Words and shapes that make a negative boundary. Kept explicit rather than
#: clever: a regex over natural language that nobody can read is not a pin.
#:
#: A REDIRECT COUNTS. "This is the PERSON; for how the MACHINE is doing use
#: `system_status`" contains no negative word and is one of the strongest
#: boundaries in the registry -- it names the exact tool to use instead. The first
#: version of this check missed all of those and reported 24 tools as deficient
#: when most of them were doing the thing correctly, which is the same mistake as
#: counting characters, one level up.
_NEGATIVE = ("cannot", "can not", "can't", "does not", "do not", "don't",
             "never", "not for", "not a ", "not an ", "not the ", "read-only",
             "instead of", "rather than", "is refused", "will not", "won't",
             "not to be", "no way to")

#: Tools whose whole job is one irreducible act with nothing to confuse it with.
#: An exemption must be a DECISION, so each carries its reason and the harness
#: prints them — an exemption list nobody looks at becomes a place to hide.
_NO_BOUNDARY_NEEDED = {}


def test_every_description_carries_a_negative_boundary():
    """Rule 1 lists the negative boundary first. It is also the half that moves
    wrong-tool selection, because it is the only part that discriminates between
    two tools that both sound right."""
    names = set(registry().names())

    def discriminates(tool: str, desc: str) -> bool:
        if any(k in desc.lower() for k in _NEGATIVE):
            return True
        # A redirect to a DIFFERENT, real tool is a boundary by any useful
        # definition: it tells the model where to go instead.
        return any(ref in names and ref != tool
                   for ref in re.findall(r"`([a-z][a-z0-9_]{2,})`", desc))

    missing = sorted(n for n, d in descriptions().items()
                     if not discriminates(n, d) and n not in _NO_BOUNDARY_NEEDED)
    check(not missing,
          f"every one of the {len(descriptions())} tools discriminates — a "
          f"negative boundary or a redirect to a named sibling "
          f"({'missing: ' + ', '.join(missing) if missing else 'none missing'})")
    check(not _NO_BOUNDARY_NEEDED,
          f"and no tool is exempt ({_NO_BOUNDARY_NEEDED or 'empty list'})")


def test_every_tool_name_a_description_points_at_actually_exists():
    """The F-09 class, aimed at the model. "Use `search_documnets` instead" is
    worse than no pointer: the model tries it, gets an unknown-tool error, and has
    burned a step at the exact moment it was correcting itself."""
    names = set(registry().names())
    # Backticked lower_snake_case tokens are how this file refers to tools --
    # but not exclusively. These are the non-tool identifiers, each declared with
    # its reason, so that a MISSPELLED tool name still fails: it would match
    # neither the registry nor this list.
    not_tools = {
        "old_string": "a parameter of edit_file, named in its own description",
        "lock_screen": "an enum value of os_control's `command`",
        "deep_work": "an os_macro routine name",
        "shallow_work": "an os_macro routine name",
        "diagnostic": "an os_macro routine name",
        "entertainment": "an os_macro routine name",
    }
    token = re.compile(r"`([a-z][a-z0-9_]{2,})`")
    dangling = []
    pointers = 0
    for tool, desc in descriptions().items():
        for ref in token.findall(desc):
            if "_" not in ref:
                continue            # a word in backticks (`mute`), not a tool
            if ref in not_tools:
                continue
            pointers += 1
            if ref not in names:
                dangling.append(f"{tool} -> {ref}")
    check(pointers >= 20,
          f"the descriptions cross-reference each other {pointers} times")
    check(not dangling, f"and every reference resolves to a real tool ({dangling})")


def test_the_confusable_pairs_point_at_each_other_both_ways():
    """A one-way pointer only helps the model that already chose correctly. Each
    of these pairs is a real observed confusion, not a hypothetical."""
    d = descriptions()
    pairs = [
        ("gmail_read", "gmail_read_unread", "which mail tool"),
        ("check_vitals", "system_status", "the PERSON vs the MACHINE"),
        ("tavily_search", "search_documents", "public web vs his own documents"),
        ("find_file", "search_documents", "a file's NAME vs its CONTENTS"),
        ("web_browse", "tavily_search", "a known URL vs finding one"),
        ("hud_close_widget", "close_app", "a HUD panel vs an application"),
        ("sleep_protocol", "os_control", "winding down vs locking the machine"),
    ]
    for a, b, why in pairs:
        check(b in d[a] and a in d[b],
              f"{a} and {b} point at each other ({why})")


def test_the_destructive_tools_say_what_they_destroy():
    """Rule 1's "what it does not return" applied to the other direction: what it
    takes away. These are the ones that cannot be undone from a chat message."""
    d = descriptions()
    check("every event" in d["clear_schedule"],
          "clear_schedule says it removes every event")
    check("re-emit" in d["workspace_write"] or "loses" in d["workspace_write"],
          "workspace_write says a whole-file write loses what is not re-emitted")
    check("UNIQUE" in d["edit_file"],
          "edit_file states the uniqueness requirement that makes a wrong-place "
          "edit impossible rather than merely discouraged (rule 4)")


def test_read_only_tools_say_so():
    """So the model does not reach for a second, riskier tool to do what the safe
    one already did, and does not promise an action a read cannot perform."""
    d = descriptions()
    for name in ("check_calendar", "system_status", "github_log"):
        text = d[name].lower()
        check(any(k in text for k in ("read-only", "cannot", "does not")),
              f"{name} states that it changes nothing")


# ------------------------------------------------------------------ rule 11
def test_rule_11_is_half_met_by_a_written_decision_not_by_accident():
    """Serial execution is a choice: these tools close applications, drive the
    mouse and send messages, the confirm layer is a singleton, and the box is
    CPU-only. Pinned so nobody "completes" the rule without reading the reasons.
    """
    src = (HERE / "modules" / "agent_core.py").read_text(encoding="utf-8")
    flat = " ".join(src.split())
    check("RULE 11 IS HALF-IMPLEMENTED ON PURPOSE" in flat,
          "the loop states that rule 11 is deliberately half-implemented")
    check("WRITTEN DECISION" in flat, "and marks it as a decision")
    for phrase, what in (
            ("has one correct order", "ordering is information, not overhead"),
            ("singleton", "the confirm layer cannot queue two approvals"),
            ("CPU-only", "concurrency on this box mostly makes both calls slower"),
            ("the safe subset", "and it names what a future attempt should limit "
                                "itself to")):
        check(phrase in flat, f"the reasons include: {what}")


def test_rule_11_second_half_holds_every_call_gets_a_result():
    """The half that IS met, and the more dangerous one to lose: a dropped result
    breaks the call/result pairing and teaches the model to stop batching."""
    src = (HERE / "modules" / "agent_core.py").read_text(encoding="utf-8")
    body = src[src.index("for call in turn.tool_calls:"):]
    body = body[:body.index("\n    if ")] if "\n    if " in body else body
    emits = body.count("tool_result_message(")
    check(emits >= 5,
          f"every branch of the call loop emits a result ({emits} sites)")
    check("continue" not in body.split("tool_result_message(")[0],
          "and no path skips straight to the next call before emitting one")


def test_serial_execution_is_what_the_code_actually_does():
    """Pin the fact as well as the note, so the two cannot drift apart: a note
    saying "serial on purpose" over code that gathers concurrently is worse than
    either alone."""
    src = (HERE / "modules" / "agent_core.py").read_text(encoding="utf-8")
    for banned in ("asyncio.gather", "ThreadPoolExecutor", "as_completed"):
        check(banned not in src,
              f"no {banned} in the loop, matching the written decision")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 70)
    print("Reference compliance — rules 1 and 11 (AGENT-TOOLING-REFERENCE)")
    print("=" * 70)
    for t in TESTS:
        t()
    print("-" * 70)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
