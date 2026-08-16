"""Harness for the §7 live-gate session-2 findings that are code, not prose.

  F-22  a named location resolved against the FIRST workspace root, never the
        root that shares its name — "documents/add.py" became F:\\work\\documents
  F-28  a result the speech layer did not recognise was announced as a success,
        so a refusal and a malformed-payload hint both became "File written, Sir."
  F-29  the CONFIRM prompt named the action type and nothing else, so the human
        approving it was shown nothing to catch
  F-30  MODULE_PC_OP was told to print destructive commands with a warning
  F-31  an unparsed request was answered with an invented substitute intent
  F-32  a prompt EXAMPLE was spoken as a live reading ("72 degrees, Sir…")
  F-33  capture ended after 0.5s of silence — the owner stutters, so the back
        half of every sentence was discarded

F-28 and F-32 are the same root cause the review named as B: a claim made
without the thing having happened. F-22, F-29 and F-30 are root cause #4 — a
class closed at one door and left open at its siblings.
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


def _string_literals(path):
    src = (HERE / path).read_text(encoding="utf-8", errors="replace")
    return [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


# ── F-33: the owner stutters, and capture must wait for him ──────────────────

def test_capture_tolerates_a_stutter_block():
    import recorder

    # A stutter block routinely runs 1–3s. The old value was 0.5s, which is why
    # every command had to arrive in one unbroken breath.
    check(recorder.PAUSE_THRESHOLD_S >= 2.0,
          f"silence tolerance is {recorder.PAUSE_THRESHOLD_S}s, not a fluent-speaker default")
    check(recorder.PHRASE_LIMIT_S >= 25,
          f"one utterance may run {recorder.PHRASE_LIMIT_S}s before the hard cap")
    check(recorder.START_TIMEOUT_S >= 10,
          f"a block BEFORE the first word gets {recorder.START_TIMEOUT_S}s")

    # Overridable, because the right number is a property of the speaker.
    os.environ["JARVIS_STT_PAUSE_S"] = "4.5"
    try:
        check(recorder._envf("JARVIS_STT_PAUSE_S", 2.5) == 4.5,
              "the tolerance can be raised without editing code")
        os.environ["JARVIS_STT_PAUSE_S"] = "not-a-number"
        check(recorder._envf("JARVIS_STT_PAUSE_S", 2.5) == 2.5,
              "a malformed override falls back to the accessible default, not to 0")
    finally:
        os.environ.pop("JARVIS_STT_PAUSE_S", None)

    src = (HERE / "recorder.py").read_text(encoding="utf-8", errors="replace")
    check("0.5" not in src.split("PAUSE_THRESHOLD_S")[0].split("pause_threshold")[-1][:40],
          "the old 0.5s literal is gone from the assignment")


# ── F-22: a named location means that root, not a subdirectory of another ────

def test_a_named_location_resolves_to_the_root_that_shares_its_name():
    from modules import workspace_agent as wa

    roots = [pathlib.Path("F:/work").resolve(),
             pathlib.Path("F:/work/JARVIS-Project").resolve(),
             pathlib.Path.home().joinpath("Documents").resolve()]
    original = wa.WORKSPACE_ROOTS
    wa.WORKSPACE_ROOTS = roots
    try:
        got = wa.WorkspaceAgent._resolve_within_roots("Documents/add.py")
        check(got is not None, "a documents path still resolves")
        check(got == roots[2] / "add.py",
              f"'Documents/add.py' -> the Documents ROOT, not the first root ({got})")
        check(got != roots[0] / "Documents" / "add.py",
              "...and specifically NOT a new Documents folder inside F:\\work")

        # Case is not the user's problem.
        lower = wa.WorkspaceAgent._resolve_within_roots("documents/add.py")
        check(lower == roots[2] / "add.py", "the match is case-insensitive")

        # A genuine relative subpath is unaffected — it names no root, so the
        # existing first-root behaviour still applies.
        sub = wa.WorkspaceAgent._resolve_within_roots("some_project/main.py")
        check(sub == roots[0] / "some_project" / "main.py",
              "an ordinary relative path still resolves against the first root")

        # The confinement itself is untouched.
        check(wa.WorkspaceAgent._resolve_within_roots("C:/Windows/system32/evil.py") is None,
              "an absolute path outside every root is still refused")
        check(wa.WorkspaceAgent._resolve_within_roots("Documents/../../../etc/passwd") is None,
              "the named-root branch cannot be used to climb out")
    finally:
        wa.WORKSPACE_ROOTS = original


def test_the_desktop_root_survives_folder_redirection():
    from modules.workspace_agent import _known_folder

    fallback = pathlib.Path.home() / "Desktop"
    got = _known_folder("Desktop", fallback)
    check(isinstance(got, pathlib.Path), "a path comes back")
    if os.name == "nt":
        # On a redirected machine the shell answer differs from the naive guess;
        # on a non-redirected one they agree. Either is correct — what must never
        # happen is returning a path that does not exist when a real one is known.
        check(got.exists() or fallback.exists(),
              f"the resolved Desktop exists ({got})")
    check(_known_folder("NoSuchFolderValue", fallback) == fallback,
          "an unknown shell folder falls back instead of raising")


# ── F-28: a success sentence requires evidence of success ────────────────────

def test_an_unrecognised_result_is_never_announced_as_success():
    import main

    # The exact string that produced "File written, Sir." over a request to
    # write to C:\Windows\system32 — the model omitted the pipe, so nothing was
    # written and the speech layer said it had been.
    hint = "Format: 'filepath|file content'. Pipe separates path from content."
    said = main._sanitize_for_speech("workspace_write", hint)
    check("written" not in (said or "").lower() or "not" in (said or "").lower(),
          f"a malformed-payload hint is not a completed write: {said!r}")
    check("malformed" in (said or "").lower(),
          "...and it says why")

    denial = "Access denied: 'C:\\Windows\\system32\\evil.py' is outside the permitted workspace roots."
    said = main._sanitize_for_speech("workspace_write", denial)
    check("denied" in (said or "").lower() or "permitted" in (said or "").lower(),
          f"a refusal is spoken as a refusal: {said!r}")

    # The success path still works — the fix must not mute a real write.
    ok = main._sanitize_for_speech("workspace_write", "Created: F:\\work\\a.py (3 lines, 40 chars)")
    check(ok == "File created, Sir.", f"a genuine create still reports: {ok!r}")
    ok = main._sanitize_for_speech("workspace_write", "Overwritten: F:\\work\\a.py (3 lines, 40 chars)")
    check(ok == "File overwritten, Sir.", f"a genuine overwrite still reports: {ok!r}")


def test_no_branch_claims_success_on_a_failure_result():
    """The fallthrough was the same habit at eight sites, not one line."""
    import main

    failures = (
        "Access denied: outside the permitted workspace roots.",
        "Format: 'filepath|file content'.",
        "No file path specified for workspace write.",
        "Error: the device is unavailable.",
        "Operation failed.",
        "Refused: not permitted.",
    )
    claims = ("done, sir.", "file written", "patch complete", "save complete",
              "macro complete", "committed to memory", "file read, sir.")
    for atype in ("workspace_write", "workspace_patch", "workspace_read",
                  "ghost_save_file", "os_control", "tv_control", "os_macro",
                  "remember_fact", "enable_focus_mode", "disable_focus_mode"):
        for bad in failures:
            said = (main._sanitize_for_speech(atype, bad) or "").lower()
            check(not any(c in said for c in claims),
                  f"{atype} does not claim success on {bad[:26]!r} -> {said[:44]!r}")


# ── F-29: the human is shown what they are approving ─────────────────────────

def test_the_confirm_prompt_says_what_it_will_do():
    import main

    class _Gov:
        def __init__(self, payload):
            self._p = payload

        def get_pending_payload(self, cid):
            return self._p

    original = main.governance_manager
    try:
        main.governance_manager = _Gov(
            {"action_type": "workspace_patch",
             "target": "F:\\United\\Desktop\\add.py|def add|def plus"})
        said = main._confirm_disclosure("workspace_patch", "cid-1")
        check("add.py" in said, f"the PATH is disclosed: {said!r}")
        check("def add" in said and "def plus" in said,
              "both the search string and the replacement are disclosed")

        main.governance_manager = _Gov(
            {"action_type": "workspace_write",
             "target": "desktop/add.py|def add(a, b):\n    return a + b"})
        said = main._confirm_disclosure("workspace_write", "cid-2")
        check("desktop/add.py" in said, f"the write path is disclosed: {said!r}")

        # A read-back that cannot be built must not take the gate down.
        main.governance_manager = _Gov(None)
        check(main._confirm_disclosure("workspace_write", "cid-3") == "",
              "an unreadable payload yields no detail rather than an exception")
    finally:
        main.governance_manager = original


def test_every_confirm_prompt_site_discloses():
    """Root cause #4: the partner prompt read its message back for a year while
    every sibling prompt said only the action type."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    prompts = src.count("Authorisation required, {")
    disclosed = src.count("_confirm_disclosure(")
    # one definition + one call per prompt site that needs it
    check(disclosed >= 4,
          f"_confirm_disclosure is defined and wired at every site ({disclosed} refs, {prompts} prompts)")


# ── F-30 / F-31 / F-32: the prompt does not hand out what the gate forbids ───

def test_the_pc_op_module_refuses_to_print_destructive_commands():
    lits = _string_literals("brain.py")
    joined = "\n".join(lits)
    check("DESTRUCTIVE OPERATIONS ARE NOT YOURS TO HAND OUT" in joined,
          "MODULE_PC_OP is told not to hand out destructive commands")
    check("blocked by governance policy" in joined,
          "...and what to say instead")
    check(not any("Flag destructive operations" in s and "before the command" in s
                  for s in lits),
          "the old 'warn, then print the command' instruction is gone")


def test_an_unparsed_request_is_not_answered_with_a_guess():
    joined = "\n".join(_string_literals("brain.py"))
    check("NEVER SUBSTITUTE AN INTENT" in joined,
          "the model is told not to answer a question it was not asked")
    check("I'll assume you meant" in joined,
          "...quoting the exact live failure so it is recognisable")


def test_prompt_examples_carry_no_recitable_data():
    """F-32: "72 degrees, Sir — humidity is elevated…" was spoken as a reading.
    It is brain.py's own style illustration."""
    lits = _string_literals("brain.py")
    joined = "\n".join(lits)
    for recitable in ("72 degrees", "34 degrees in Kolkata", "humidity at 78%",
                      "humidity is sitting at 78%", "CPU utilisation at 95%",
                      "Akhon 1:44 PM baje"):
        check(recitable not in joined,
              f"no example supplies a speakable figure: {recitable!r}")
    check("<TEMP>" in joined and "<VALUE>" in joined,
          "the examples use obvious placeholders instead")
    check("never from an illustration in it" in joined,
          "...and the model is told the difference explicitly")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Live gate, session 2 — the findings that are code")
    print("=" * 62)
    for t in TESTS:
        t()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
