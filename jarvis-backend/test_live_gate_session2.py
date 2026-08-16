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
  F-34  one utterance staged THREE confirmations for payloads that could not
        execute, each overwriting the desk's pinned id, and the prompt read
        back a path the sandbox was going to refuse
  F-35  "yes" did not transcribe, the failure printed nothing, and the session
        went to standby with the authorisation still open
  F-17  the Gemini leg was dead on four call sites (system-only prompts), and
  F-36  key rotation hid it: a payload bug read as five key failures, a revoked
        key was retried forever, and the "separate project per key" premise is
        false — the live keys share one quota bucket

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
        # F-34 sharpened this: the prompt names the path the write will
        # actually take, not the string the model produced. "desktop/add.py"
        # is disclosed as the real Desktop, which is the whole point — the
        # owner can see WHERE before he says yes.
        check(said.strip().endswith("add.py") and ":" in said,
              f"the write path is disclosed, resolved: {said!r}")
        check("desktop/add.py" not in said,
              "...not the unresolved string the model produced")

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


# ── F-34: a question the human cannot act on is not a gate ───────────────────

def _engine():
    import action_engine
    return action_engine.ActionEngine()


def _inside_root(name="f34_probe.txt"):
    from modules.workspace_agent import WORKSPACE_ROOTS
    return str(WORKSPACE_ROOTS[0] / name)


def test_a_write_with_no_content_is_refused_before_it_is_staged():
    eng = _engine()
    # The live payload: a path, and nothing on the other side of the pipe.
    for target in ("C:/some/where/add.py",            # no pipe at all
                   _inside_root() + "|",              # pipe, empty content
                   _inside_root() + "|   "):          # pipe, whitespace
        out = eng._preflight_refusal({"action_type": "workspace_write",
                                      "target": target})
        check(out is not None and out.startswith("Format:"),
              f"a write that carries no content is refused up front: {target[:40]!r}")


def test_a_write_outside_the_roots_is_refused_before_it_is_staged():
    eng = _engine()
    # The live path, invented from the SPEAKER's name on a machine whose
    # Windows profile is someone else entirely.
    out = eng._preflight_refusal({
        "action_type": "workspace_write",
        "target": r"C:\Users\KAUSTAV\Desktop\add.py|print('hi')",
    })
    check(out is not None and "outside the permitted" in out,
          "a path outside every workspace root never becomes a question")
    check(eng._preflight_refusal({"action_type": "workspace_write",
                                  "target": _inside_root() + "|print('hi')"}) is None,
          "...and a legitimate write is not touched by the pre-flight")


def test_a_malformed_patch_is_refused_before_it_is_staged():
    eng = _engine()
    out = eng._preflight_refusal({"action_type": "workspace_patch",
                                  "target": _inside_root() + "|only_two_fields"})
    check(out == "Format: 'filepath|search_string|replace_string'",
          "a patch missing its replacement is refused up front")
    check(eng._preflight_refusal({"action_type": "launch_app",
                                  "target": "notepad"}) is None,
          "...and an action with no declared contract is left alone")


def test_a_refused_payload_stages_no_confirmation():
    """The finding itself: governance staged three prompts for three payloads
    that could not have done anything."""
    import asyncio
    from governance_manager import governance_manager

    eng = _engine()
    governance_manager.cancel_pending()
    before = governance_manager.has_pending()
    result = asyncio.run(eng.execute({"action_type": "workspace_write",
                                      "target": r"C:\Users\NOBODY\Desktop\add.py"}))
    check(before is False, "no confirmation was pending before the call")
    check(governance_manager.has_pending() is False,
          "a malformed write leaves NOTHING pending — the owner is never asked")
    check(result.startswith("Format:"),
          "...and the caller gets the same refusal the writer would have given")


def test_every_dispatch_loop_stops_at_a_confirmation():
    """Three doors reach this loop, and the finding was found on one of them."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    check(src.count("enumerate(actions)") == 3,
          "all three dispatch loops know their position in the plan")
    check(src.count("_dropped_plan_note(") == 4,
          "the drop note is defined once and used at all three doors")

    # Each DISPATCH confirmation site must reach a `break` without running
    # another action. Matched on `"action": conf_action` so the F-35 re-ask —
    # which sends the same status and must NOT break — is not counted.
    marker = '"pending_confirmation", "action": conf_action'
    sites = [i for i in range(len(src)) if src.startswith(marker, i)]
    check(len(sites) == 2, f"found {len(sites)} desk/voice dispatch sites (expected 2)")
    for i in sites:
        window = src[i:i + 1800]
        check("break" in window,
              "a confirmation site stops the plan instead of staging the next action")
    # The remote door speaks through the channel instead of the socket.
    remote = src.find("Reply 'confirm' to execute it")
    check(remote != -1 and "break" in src[remote:remote + 1200],
          "the remote door stops the plan at a confirmation too")


def test_the_drop_note_does_not_promise_a_resumption():
    import main
    note = main._dropped_plan_note(2, "Sir")
    check("dropped" in note and "held" not in note,
          "the note says the steps were dropped, because they were")
    check("nothing else ran" in note.lower(),
          "...and states plainly that nothing else happened")
    one = main._dropped_plan_note(1, "Sir")
    check("1 step " in one and " was dropped" in one,
          f"it counts in singular when one step was dropped: {one!r}")


def test_the_prompt_reads_back_the_resolved_path():
    """F-34's other half: the owner was shown the request, not the consequence."""
    import main
    from modules.workspace_agent import WORKSPACE_ROOTS

    root = WORKSPACE_ROOTS[0]
    shown = main._disclosed_path("f34_probe.txt", "workspace_write")
    check(str(root) in shown,
          "a bare filename is read back as the absolute path it will become")
    check(main._disclosed_path(r"C:\Users\NOBODY\x.py", "workspace_write")
          == r"C:\Users\NOBODY\x.py",
          "an unresolvable path falls back to the raw string, never to nothing")
    check(main._disclosed_path("Documents|note.txt", "ghost_save_file")
          == "Documents|note.txt",
          "the Notepad chain's own target format is left alone")


# ── F-37: the spec copied into the payload, and spoken out loud ──────────────

def test_the_spec_placeholder_is_not_a_filename():
    """Live: target="filepath|/Users/KAUSTAV/Desktop/a d d p y" — the model put
    the placeholder where the path goes and the path where the content goes.
    Every real check passed: `filepath` resolves inside a root and the content
    is non-empty. F:\\work\\filepath was created and reported as a success."""
    eng = _engine()
    for path in ("filepath", "FilePath", " file_path ", "<filename>", "path",
                 "path/to/file", "filepath.py", "{file}"):
        out = eng._preflight_refusal({"action_type": "workspace_write",
                                      "target": f"{path}|print('hi')"})
        check(out is not None and out.startswith("Format:"),
              f"the spec placeholder is refused as a path: {path!r}")
    check(eng._preflight_refusal({"action_type": "workspace_write",
                                  "target": "filepath_utils.py|x = 1"}) is None,
          "...but a real filename that merely contains the word is fine")
    check(eng._preflight_refusal({"action_type": "workspace_patch",
                                  "target": "filepath|old|new"}) is not None,
          "the patch door refuses it too")


def test_the_write_itself_refuses_the_placeholder():
    """The pre-flight is skipped on the approval re-entry (governance_bypass),
    so the guard has to exist at the write as well."""
    eng = _engine()
    out = eng._workspace_write("filepath|/Users/KAUSTAV/Desktop/a d d p y")
    check(out.startswith("Format:"),
          "an approved payload naming the placeholder still writes nothing")
    # "somefile" is on the placeholder list and is not a real file anywhere in
    # the tree, so its absence after the call is evidence, not luck.
    probe = pathlib.Path(_inside_root("somefile"))
    existed = probe.exists()
    eng._workspace_write("somefile|x = 1")
    check(probe.exists() == existed,
          "...and nothing appears on disk under a placeholder name")


def test_the_voice_door_speaks_through_the_sanitiser():
    """F-37's other half: the desk socket sanitised its results and the VOICE
    loop — the one actually used — spoke the engine's raw return value, so the
    owner heard "Format: 'filepath|file content'…" out loud."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    check(src.count("asyncio.create_task(speaker.speak_text(str(result)))") == 4,
          "only the four focus-mode fallbacks speak a raw result")
    # Both generic tails must sanitise. They are the last `else` of each batch
    # loop, and they are the ones every unrecognised action lands in.
    tails = [i for i in range(len(src))
             if src.startswith("spoken = _sanitize_for_speech(atype, result_str)", i)]
    check(len(tails) >= 2,
          f"both batch loops run their fall-through through the sanitiser ({len(tails)})")


def test_the_prompt_no_longer_offers_a_copyable_placeholder():
    joined = "\n".join(_string_literals("brain.py"))
    check('target="filepath|file_content"' not in joined,
          "the write spec no longer reads as a literal string to copy")
    check("The angle brackets are a SLOT, not text" in joined,
          "...and says so explicitly")
    check("ask for it instead of inventing one" in joined,
          "a filename that did not survive transcription is asked for, not guessed")


# ── F-35: an answered question that looked unanswered ────────────────────────

def test_an_unintelligible_answer_is_logged():
    src = (HERE / "recorder.py").read_text(encoding="utf-8", errors="replace")
    check("Speech not understood" in src,
          "a failed transcription prints, instead of returning UNKNOWN in silence")


def test_a_pending_confirmation_is_re_asked_then_lapsed():
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    check("I didn't catch that" in src,
          "an unintelligible answer to a live prompt gets the question again")
    check("_confirm_reasks < 2" in src,
          "...at most twice, so a noisy room is not badgered")
    check("_confirm_reasks = 0" in src,
          "...and the counter resets on a turn that landed")
    check("The authorisation request has lapsed" in src,
          "a session going to standby says the request died with it")
    check("governance_manager.cancel_pending(_DESK_PENDING[\"cid\"])" in src,
          "...and actually cancels it, rather than leaving it for the TTL")


# ── F-17 / F-36: the Gemini leg ──────────────────────────────────────────────

def test_a_system_only_prompt_produces_a_sendable_request():
    """F-17: four call sites pass system text only. Gemini rejects empty
    contents, so all four were permanently dead on this provider."""
    from modules.llm_router import _split_messages_for_gemini

    system, contents = _split_messages_for_gemini(
        [{"role": "system", "content": "You are JARVIS"}])
    check(system == "You are JARVIS", "the system text is still hoisted out")
    check(len(contents) == 1 and contents[0]["role"] == "user",
          "a system-only prompt still carries one user turn")
    check(contents[0]["parts"] == ["Proceed."],
          "...and the filler adds no instruction of its own")

    system2, contents2 = _split_messages_for_gemini(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
    check(len(contents2) == 1 and contents2[0]["parts"] == ["hi"],
          "a normal prompt is unchanged — no filler is added")


class _FakeResourceExhausted(Exception):
    pass


_FakeResourceExhausted.__name__ = "ResourceExhausted"


def _fake_router(monkey_keys=("k1", "k2", "k3")):
    """The router module with a stub SDK and a known key list."""
    from modules import llm_router
    llm_router._gemini_dead_keys.clear()
    llm_router._gemini_cooldown_until = 0.0
    llm_router._gemini_key_idx = 0
    llm_router._gemini_keys = lambda: list(monkey_keys)
    llm_router._import_genai = lambda: type("G", (), {"configure": staticmethod(lambda **k: None)})
    return llm_router


def test_a_client_side_error_does_not_burn_the_key_pool():
    """F-17: one payload bug was reported as five key failures."""
    router = _fake_router()
    calls = []

    def boom(genai):
        calls.append(1)
        raise TypeError("contents must not be empty")

    try:
        router._run_with_gemini_rotation(boom)
        check(False, "a deterministic error propagates")
    except TypeError:
        check(True, "a deterministic error propagates")
    check(len(calls) == 1,
          f"it is raised on the FIRST key, not rotated through all of them (tried {len(calls)})")


def test_a_revoked_key_is_dropped_for_the_process():
    """F-36: key #5 of 5 was revoked and was retried on every single call."""
    router = _fake_router()
    seen = []

    def dead_first(genai):
        seen.append(1)
        if len(seen) == 1:
            raise Exception('400 API key not valid [reason: "API_KEY_INVALID"]')
        return "ok"

    check(router._run_with_gemini_rotation(dead_first) == "ok",
          "a revoked key rotates to the next one")
    check(0 in router._gemini_dead_keys, "the revoked key is remembered")

    after = []
    router._run_with_gemini_rotation(lambda g: after.append(1) or "ok")
    check(len(after) == 1, "the next call does not offer the dead key again")


def test_a_shared_quota_bucket_cools_down_instead_of_re_probing():
    """F-36: the four live keys share ONE bucket — their retry-after values
    counted down in step. Rotating through them buys latency, not headroom."""
    router = _fake_router()
    attempts = []

    def quota(genai):
        attempts.append(1)
        raise _FakeResourceExhausted(
            "429 You exceeded your current quota … Please retry in 48.7")

    try:
        router._run_with_gemini_rotation(quota)
    except Exception:
        pass
    check(len(attempts) == 3, "every live key is tried once before giving up")
    check(router._gemini_cooldown_until > 0, "a cooldown is armed")

    second = []
    try:
        router._run_with_gemini_rotation(lambda g: second.append(1) or "ok")
        check(False, "the next call is refused locally")
    except RuntimeError as e:
        check("cooldown" in str(e), "the next call is refused locally")
    check(len(second) == 0,
          "...without touching the network — no key is even configured")

    router._gemini_cooldown_until = 0.0   # leave the module as we found it
    router._gemini_dead_keys.clear()


def test_the_retry_window_is_read_from_the_provider():
    from modules import llm_router
    secs = llm_router._quota_retry_seconds(Exception("… Please retry in 48.7"))
    check(48 < secs <= 50, f"Google's own retry-after is used ({secs}s)")
    check(llm_router._quota_retry_seconds(Exception("no hint here"))
          == llm_router._GEMINI_COOLDOWN_DEFAULT_S,
          "...and a default covers the case where it does not say")
    check(llm_router._quota_retry_seconds(Exception("retry in 9999")) <= 300,
          "an absurd retry-after is capped rather than parking the leg for hours")


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
