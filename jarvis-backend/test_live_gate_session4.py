"""Harness for the §7 live-gate session-4 findings that are code, not prose.

Session 4 was run unattended, so it drove the text command door and the HTTP
routes rather than a microphone. That door reaches the same brain, and it found:

  F-45  the two gesture switches an operator sets in the environment were
        honoured internally and published as their opposite — the daemon ran
        with JARVIS_AUTO_LOCK=0 while GET /api/gesture/state answered
        "auto_lock": true, because only the voice-toggle setters ever wrote to
        the published mirror
  F-46  `llama-3.1-8b-instant` was hardcoded in five files and the default in
        two more, and Groq had decommissioned it — every memory extraction,
        every episodic summary and the GUI agent's parser answered 404 on every
        turn, silently, because all three swallow their own errors
  F-48  the streamed reply was cut off mid-value on real turns, so the desk
        spoke the prefix ("It is", "System load is") and the action parser
        correctly refused a truncated write. F-44's root cause on the answer
        path: every leg of the cascade now spends output budget on reasoning
  F-51  "save it to my desktop" was refused as outside the workspace roots.
        `~/Desktop` expands to a folder that does not exist on this machine —
        the Desktop is OneDrive-redirected — so the home-relative form of a
        redirected known folder was outside every root. The FIFTH distinct
        cause of row 4.1

F-45 is the F-19/F-21/F-25 class: a state that reports the default rather than
the truth. F-46 is root cause #4 — the same Groq decommissioning was fixed at
the two doors someone was looking at (`GROQ_TOOL_MODEL`, the cloud gateway) and
left at five that nobody was. F-51 is F-22's absolute twin: F-22 taught that a
leading segment naming a root means THAT root, and fixed only the relative form.
"""

import ast
import os
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


# ── F-51: the desktop the owner means is the desktop the guard allows ────────

def test_a_home_relative_known_folder_maps_into_its_redirected_root():
    """`~/Desktop/add.py` must reach the Desktop ROOT, wherever it really is.

    The live failure: the model emitted `~/Desktop/add.py`, which expands to
    C:\\Users\\<me>\\Desktop — a folder that does not exist here, because the
    shell folder is redirected to OneDrive. The configured root was the
    OneDrive path, so the expanded path was inside no root at all and the answer
    to "save it to my desktop" was "Access denied".
    """
    from modules import workspace_agent as wa

    redirected = (pathlib.Path.home() / "OneDrive_test_s4" / "Desktop")
    roots = [pathlib.Path("F:/work").resolve(), redirected]
    original = wa.WORKSPACE_ROOTS
    wa.WORKSPACE_ROOTS = roots
    try:
        got = wa.WorkspaceAgent._resolve_within_roots("~/Desktop/add.py")
        check(got is not None, "a home-relative desktop path resolves at all")
        check(got == (redirected / "add.py").resolve(),
              f"'~/Desktop/add.py' -> the Desktop ROOT, redirected or not ({got})")

        # The same path spelled the way Windows expands it.
        expanded = str(pathlib.Path.home() / "Desktop" / "add.py")
        check(wa.WorkspaceAgent._resolve_within_roots(expanded)
              == (redirected / "add.py").resolve(),
              "and the already-expanded absolute form resolves identically")

        # Case is not the user's problem, here either.
        check(wa.WorkspaceAgent._resolve_within_roots("~/desktop/add.py")
              == (redirected / "add.py").resolve(),
              "the match is case-insensitive")

        # A deeper path keeps its tail.
        check(wa.WorkspaceAgent._resolve_within_roots("~/Desktop/notes/todo.md")
              == (redirected / "notes" / "todo.md").resolve(),
              "a subdirectory under the named root is preserved")

        # ── and it grants nothing new ───────────────────────────────────────
        check(wa.WorkspaceAgent._resolve_within_roots("~/Desktop/../../etc/passwd") is None,
              "the new branch cannot be used to climb out of the root")
        check(wa.WorkspaceAgent._resolve_within_roots("~/Downloads/x.py") is None,
              "a home folder that names no root is still refused")
        check(wa.WorkspaceAgent._resolve_within_roots(str(pathlib.Path.home())) is None,
              "the home directory itself is not a root")
        check(wa.WorkspaceAgent._resolve_within_roots("C:/Windows/system32/evil.py") is None,
              "an absolute path outside home and outside every root is still refused")
    finally:
        wa.WORKSPACE_ROOTS = original


def test_the_relative_form_that_f22_fixed_still_works():
    """F-51's fix must not disturb F-22's. Both forms, one behaviour."""
    from modules import workspace_agent as wa

    desktop = (pathlib.Path.home() / "OneDrive_test_s4" / "Desktop")
    roots = [pathlib.Path("F:/work").resolve(), desktop]
    original = wa.WORKSPACE_ROOTS
    wa.WORKSPACE_ROOTS = roots
    try:
        check(wa.WorkspaceAgent._resolve_within_roots("Desktop/add.py")
              == (desktop / "add.py").resolve(),
              "F-22's relative named-root branch is unchanged")
        check(wa.WorkspaceAgent._resolve_within_roots("some_project/main.py")
              == (roots[0] / "some_project" / "main.py").resolve(),
              "an ordinary relative path still resolves against the first root")
    finally:
        wa.WORKSPACE_ROOTS = original


# ── F-45: the switch that was set is the switch that is published ───────────

def test_the_switches_the_operator_set_are_the_ones_published():
    """Constructing the daemon with both switches OFF must publish both as OFF.

    The live evidence: launched with JARVIS_AUTO_LOCK=0, the daemon honoured it
    (the away-lock never armed) and `/api/gesture/state` reported
    `"auto_lock": true` for the whole session — the dict literal's default. The
    HUD and the phone read that endpoint.
    """
    import gesture_daemon as gd

    saved_env = {k: os.environ.get(k) for k in ("JARVIS_GESTURE", "JARVIS_AUTO_LOCK")}
    saved_state = dict(gd.gesture_state)
    try:
        os.environ["JARVIS_GESTURE"] = "0"
        os.environ["JARVIS_AUTO_LOCK"] = "0"
        d = gd.GestureDaemon()
        check(d.gestures_enabled is False, "the daemon itself reads JARVIS_GESTURE=0")
        check(d.auto_lock is False, "the daemon itself reads JARVIS_AUTO_LOCK=0")
        check(gd.gesture_state["enabled"] is False,
              "and the PUBLISHED state says gestures are off")
        check(gd.gesture_state["auto_lock"] is False,
              "and the PUBLISHED state says auto-lock is off")

        # The default direction must still hold — an unset switch is on.
        os.environ.pop("JARVIS_GESTURE")
        os.environ.pop("JARVIS_AUTO_LOCK")
        d2 = gd.GestureDaemon()
        check(d2.auto_lock is True and gd.gesture_state["auto_lock"] is True,
              "unset still means on, in the object and in the mirror")
    finally:
        gd.gesture_state.clear()
        gd.gesture_state.update(saved_state)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── F-48: the reply budget has to fit the thinking as well as the answer ────

def _call_kwargs(path, func_name, callee):
    """Every `callee(...)` keyword set inside `func_name` of `path`."""
    tree = ast.parse((HERE / path).read_text(encoding="utf-8", errors="replace"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and getattr(inner.func, "id", getattr(inner.func, "attr", None)) == callee):
                out.append({kw.arg: kw.value for kw in inner.keywords})
    return out


def test_the_streaming_reply_budget_has_headroom_for_reasoning():
    """The answer call must not sit where a reasoning model lands.

    Measured on the desk's own payload shape (17,755 chars, a JSON reply
    carrying a whole file): openrouter's nemotron-3.5-lightning spent 785
    completion tokens, 657 of them reasoning — 77% of the old 1024 ceiling, for
    a turn that succeeded. The live turns that failed were longer.
    """
    calls = _call_kwargs("brain.py", "process_stream", "universal_llm_call")
    check(bool(calls), "process_stream's universal_llm_call is found by AST")
    for kw in calls:
        node = kw.get("max_tokens")
        budget = getattr(node, "value", None)
        check(isinstance(budget, int), f"max_tokens is a literal ({budget})")
        check(isinstance(budget, int) and budget >= 2048,
              f"the streamed reply budget leaves room for reasoning ({budget})")
        stream = kw.get("stream")
        check(getattr(stream, "value", None) is True,
              "this is the streaming answer call, not another one")


def test_the_classifier_budget_that_f44_raised_is_still_raised():
    """F-44's number must not be walked back by a later edit."""
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    check("max_tokens=1024" in src or "max_tokens=3072" in src,
          "brain.py still names explicit budgets rather than provider defaults")
    calls = _call_kwargs("brain.py", "classify_intent", "universal_llm_call")
    for kw in calls:
        budget = getattr(kw.get("max_tokens"), "value", None)
        check(isinstance(budget, int) and budget >= 1024,
              f"the classifier keeps F-44's ceiling ({budget})")


# ── row 4.1, cause six: one act described twice is not a plan ───────────────

def test_a_write_phrased_as_write_and_save_is_not_a_multi_step_goal():
    """Row 4.1's own sentence must reach the single-action path.

    The live failure chain: " and save " + {write, save} made `should_plan` true,
    the ReAct loop took a one-action request, hit a CONFIRM-tier step, cancelled
    the pending confirmation and answered "I need your authorisation... I won't
    run it unattended" -- with nothing left to authorise. The single-action path
    stages a confirmation the desk can answer.
    """
    from modules import planner

    check(planner.should_plan(
        "Write a python script for a simple add function and save it to my "
        "desktop as add.py") is False,
        "row 4.1's exact sentence fast-paths instead of planning")
    check(planner.should_plan(
        "create a note about the meeting and save it as notes.md") is False,
        "create+save is one act too")
    check(planner.should_plan(
        "draft the release text and generate a changelog file") is False,
        "draft+generate likewise")


def test_genuinely_compound_work_still_plans():
    """The fix must not disarm the planner. A write plus a DIFFERENT act plans."""
    from modules import planner

    check(planner.should_plan(
        "write a summary of the quarter and email it to the team") is True,
        "write + email is still two acts")
    check(planner.should_plan(
        "research the pricing of three vendors and then compile a comparison") is True,
        "research + compile still plans")
    check(planner.should_plan(
        "search for the release notes and save them to my desktop") is True,
        "search + save still plans -- the search is a real second act")
    check(planner.should_plan(
        "step 1 write the file. step 2 save a copy in documents") is True,
        "an explicitly enumerated plan is still a plan, whatever its verbs")
    check(planner.should_plan("open notepad") is False,
          "a short simple command is untouched")


# ── F-53: the desk spoke a model's monologue out loud ───────────────────────

LEAKED_UNTAGGED = """Here's a thinking process:

1.  **Analyze User Input:**
   - User says: "confirm"
   - Context: previous turns involved writing add.py to the desktop.
   - The user's stored facts mention a dog named Kitty.
2.  **Decide:** approve the rename."""

LEAKED_TAGGED = ("<think>" + chr(10)
                 + "The user wants a python script. I need to use the "
                 + "workspace_write action." + chr(10)
                 + "</think>" + chr(10)
                 + "Consider it done, Sir.")


def test_a_tagged_think_block_never_reaches_the_room():
    from modules import reasoning_guard as rg

    check(rg.guard_spoken(LEAKED_TAGGED) == "Consider it done, Sir.",
          "a <think> block is removed and the real answer survives")
    check("<think>" not in rg.guard_spoken(LEAKED_TAGGED),
          "no tag text is left behind")
    check(rg.guard_spoken("<think>budget ran out mid-thought and never closed")
          == rg.DEFAULT_FALLBACK,
          "an UNCLOSED think tag means there is no answer, and it is not spoken")


def test_an_untagged_monologue_is_refused_rather_than_spoken():
    """The live leak carried a private fact out with it — a dog's name.

    There is no reliable way to split a monologue from an answer, so the guard
    does not try: an opening that reads as thinking is replaced wholesale.
    """
    from modules import reasoning_guard as rg

    said = rg.guard_spoken(LEAKED_UNTAGGED)
    check(said == rg.DEFAULT_FALLBACK, "the monologue is replaced, not trimmed")
    check("Kitty" not in said, "and the private fact it carried does not reach the room")
    check("Analyze User Input" not in said, "nor does the shape of the prompt")


def test_a_real_answer_is_never_touched():
    """The guard is anchored to the START of the reply, so an answer that merely
    discusses thinking is safe."""
    from modules import reasoning_guard as rg

    for good in (
        "Consider it done, Sir.",
        "I think the calendar is wrong, Sir — it lists a meeting you cancelled.",
        "Operating at peak efficiency, Sir. Shall we get to work?",
        "Step 1 is done, Sir; the file is on your desktop.",
    ):
        check(rg.guard_spoken(good) == good, f"unchanged: {good[:40]!r}")
    check(rg.guard_spoken("") == "", "silence stays silence rather than becoming chatter")
    check(rg.guard_spoken("   ") == "", "so does whitespace")


def test_the_speaker_cannot_be_bypassed_by_a_new_caller():
    """Every audible line funnels through speak_text, so the guard sits there too."""
    import ast

    src = (HERE / "speaker.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "speak_text"), None)
    check(fn is not None, "speak_text is found")
    guarded = any(isinstance(n, ast.Call)
                  and getattr(n.func, "attr", None) == "guard_spoken"
                  for n in ast.walk(fn))
    check(guarded, "speak_text calls guard_spoken before it prints or synthesises")

    # And the print that logs the line must come AFTER the guard, or the console
    # keeps the leak the room was spared.
    body_src = ast.get_source_segment(src, fn) or ""
    check(body_src.index("guard_spoken") < body_src.index("print(f\"[JARVIS]"),
          "the guard runs before the [JARVIS] log line, not after it")


def test_both_providers_are_asked_to_withhold_reasoning():
    """Layer one: the cheapest fix is not generating it at all.

    Measured session 4 — groq gpt-oss-20b with reasoning_format=hidden returns
    identical content in 0.6s instead of 1.0s; openrouter nemotron with
    reasoning:{exclude:true} in 15s instead of 45s.
    """
    from modules import llm_router as lr

    check(lr._groq_reasoning_kwargs("openai/gpt-oss-20b") == {"reasoning_format": "hidden"},
          "a gpt-oss model is asked to hide its reasoning")
    check(lr._groq_reasoning_kwargs("allam-2-7b") == {},
          "a model that 400s on the parameter is not sent it")

    src = (HERE / "modules" / "llm_router.py").read_text(encoding="utf-8", errors="replace")
    check('"reasoning": {"exclude": True}' in src,
          "the OpenRouter payload excludes reasoning")
    check(src.count("_groq_reasoning_kwargs(GROQ_MODEL)") >= 2,
          "both the streaming and non-streaming Groq calls carry it")


def test_no_answer_budget_is_below_the_thinking_floor():
    """Every output ceiling in brain.py must clear what reasoning costs.

    The live evidence is a complete spoken line: "You have 201". The inbox
    synthesis ran at 220 tokens, the model spent them thinking, and three words
    reached the room. A 150-token weather synthesis said "It is".

    Asserted on the AST, so a future edit that types a bare number instead of
    adding the headroom fails here rather than in a room.
    """
    import ast

    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    floor = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "_THINKING_HEADROOM"):
            floor = node.value.value
    check(isinstance(floor, int) and floor >= 512,
          f"there is a declared thinking headroom, and it is real ({floor})")

    bare = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "max_tokens":
                continue
            # A bare literal is only acceptable if it already clears the floor.
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, int):
                if kw.value.value < 1024:
                    bare.append(f"line {kw.value.lineno}: max_tokens={kw.value.value}")
    check(not bare, f"no answer budget sits below 1024: {bare}")

    # And the assignment form (max_tokens_syn / _max_tokens), which the calls read.
    small = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") in ("max_tokens_syn", "_max_tokens")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)
                and node.value.value < 1024):
            small.append(f"line {node.lineno}: = {node.value.value}")
    check(not small,
          f"no synthesis budget is a bare small number any more: {small}")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Live gate, session 4 — the findings that are code")
    print("=" * 62)
    for t in TESTS:
        t()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
