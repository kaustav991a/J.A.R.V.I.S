"""Harness for the medium-severity findings of the pre-Electron review, batch 1.

  R4   the lockdown protocol said the ports were secured and secured nothing
  R6   /api/autopilot wrote into a caller-chosen absolute directory
  R7   the body-less POST routes were callable by any web page
  R8   patch_file round-tripped a file through a lossy decode
  R9   create_note silently destroyed an existing note and reported success
  R10  web_click/web_type interpolated a model id into a CSS selector
  R12  sleep_protocol returned a UI sentinel no caller consumed

Half of these are root cause B — a claim without the thing having happened. That
is treated as top severity in this project regardless of what the label says,
because an assistant that misreports itself is worse than one that fails.
"""

import ast
import pathlib
import sys
import tempfile

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


# ── R10: an element id is one of the integers we handed out ──────────────────

def test_a_css_selector_cannot_be_escaped_through_the_element_id():
    from modules.web_agent import _element_id_problem

    check(_element_id_problem("12") is None, "an ordinary id is accepted")
    check(_element_id_problem(" 7 ") is None, "whitespace is tolerated")
    for bad in ("1'], a[href^='http", "1' or '1'='1", "*", "", None,
                "1;drop", "../1", "1 2"):
        check(_element_id_problem(bad) is not None,
              f"refused: {str(bad)[:28]!r}")


def test_both_browser_tools_check_the_id_before_building_a_selector():
    """One of two call sites guarded is the shape of finding 17."""
    src = (HERE / "modules" / "web_agent.py").read_text(encoding="utf-8",
                                                        errors="replace")
    tree = ast.parse(src)
    for name in ("click", "type_text"):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == name), None)
        check(fn is not None, f"{name} exists")
        if fn is None:
            continue
        body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
        check("_element_id_problem" in body, f"{name} validates the id first")


# ── R9: a note is not silently destroyed ─────────────────────────────────────

def test_create_note_refuses_to_clobber_and_says_so():
    from modules.file_agent import FileAgent

    with tempfile.TemporaryDirectory() as tmp:
        agent = FileAgent.__new__(FileAgent)
        agent.notes_dir = pathlib.Path(tmp)

        first = agent.create_note("Budget: Q3 figures are final")
        check("created" in first.lower(), "the first note is created")

        note = pathlib.Path(tmp) / "Budget.txt"
        check(note.exists() and "Q3" in note.read_text(encoding="utf-8"),
              "...and it holds the Q3 content")

        second = agent.create_note("Budget: Q4 figures are provisional")
        check("already exists" in second.lower(),
              "a colliding note is refused, not silently overwritten")
        check("created" not in second.lower(),
              "...and it does NOT claim to have created anything")
        check("Q3" in note.read_text(encoding="utf-8"),
              "...and the original content survived")


# ── R8: a patch does not quietly rewrite the rest of the file ────────────────

def test_a_patch_refuses_a_file_it_would_mangle():
    from modules.workspace_agent import WorkspaceAgent

    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "legacy.txt"
        # cp1252: a curly quote and an e-acute that are NOT valid UTF-8.
        target.write_bytes(b"timeout = 30\nCaf\xe9 \x93quoted\x94\n")

        agent = WorkspaceAgent.__new__(WorkspaceAgent)
        agent._resolve_safe_for_write = lambda raw: pathlib.Path(raw)
        agent._fuzzy_hint = staticmethod(lambda *a, **k: None)

        out = agent.patch_file(str(target), "timeout = 30", "timeout = 60")
        check("refused" in out.lower(), f"the patch is refused: {out[:60]}")
        check("UTF-8" in out or "utf-8" in out, "...and says why")
        check(target.read_bytes() == b"timeout = 30\nCaf\xe9 \x93quoted\x94\n",
              "...and the file is byte-for-byte untouched")


def test_a_patch_of_a_utf8_file_preserves_line_endings():
    """`write_text` translates "\\n" to the platform ending, so on Windows a
    one-line patch used to rewrite every line ending in the file."""
    from modules.workspace_agent import WorkspaceAgent

    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "unix.txt"
        target.write_bytes(b"alpha\ntimeout = 30\nomega\n")

        agent = WorkspaceAgent.__new__(WorkspaceAgent)
        agent._resolve_safe_for_write = lambda raw: pathlib.Path(raw)
        agent._fuzzy_hint = staticmethod(lambda *a, **k: None)

        out = agent.patch_file(str(target), "timeout = 30", "timeout = 60")
        raw = target.read_bytes()
        check("Patched" in out, f"the patch applied: {out[:40]}")
        check(b"\r\n" not in raw, "LF line endings survived the round trip")
        check(b"timeout = 60" in raw, "...and the change landed")


# ── R12: a HUD-effect tool sends the frame or fails honestly ─────────────────

def test_the_sleep_protocol_sentinel_becomes_real_frames():
    from modules import agent_tools as at

    frames = at.hud_frames("UI_WIDGET_TOGGLE:close_display", "sleep_protocol")
    check(len(frames) == 2, f"two frames, as main.py sends; got {len(frames)}")
    check(frames[0].get("status") == "close_search", "the display is cleared")
    check(frames[1].get("status") == "toggle_browser"
          and frames[1].get("visible") is False, "and the browser panel closed")

    told = at.describe_hud_frames(frames)
    check("desk display" in told, f"the model is told what happened: {told[:50]}")
    check("operating-system level" in told,
          "...and what did NOT happen, so it cannot overclaim")

    # An unknown widget toggle is NOT invented into a frame.
    check(at.hud_frames("UI_WIDGET_TOGGLE:something_else", "sleep_protocol") == [],
          "an unrecognised toggle produces no frame rather than a guess")


# ── R4: the lockdown says what it does ───────────────────────────────────────

def test_the_lockdown_no_longer_claims_to_have_secured_anything():
    # Checked on the parsed string CONSTANTS, not the file text — the comment
    # recording this fix quotes the old sentence, and a substring test would
    # match its own documentation.
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    literals = [n.value for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    check(not any("ports have been secured" in s for s in literals),
          "nothing SAYS the ports were secured any more")
    check(any("Lockdown display engaged" in s for s in literals),
          "...replaced with what actually happens")
    check(any("not changed any firewall" in s for s in literals),
          "...and it is explicit about what it did NOT do")


# ── R6: autopilot writes where it is already allowed to write ────────────────

def test_autopilot_confines_its_output_directory():
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "start_autopilot"), None)
    check(fn is not None, "start_autopilot exists")
    if fn is None:
        return
    body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
    check("_resolve_within_roots" in body,
          "the requested out_dir is resolved against the workspace roots")
    check("req.out_dir" not in body.split("_resolve_within_roots")[-1],
          "...and the RESOLVED path is what reaches the worker, not the raw one")


def test_the_root_check_actually_refuses_an_outside_directory():
    from modules.workspace_agent import WorkspaceAgent

    outside = r"C:\Users\KINGSHUK\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
    check(WorkspaceAgent._resolve_within_roots(outside) is None,
          "the Startup folder is outside every workspace root")


# ── R7: a web page cannot drive the desk ─────────────────────────────────────

class _FakeRequest:
    def __init__(self, **headers):
        self.headers = {k.replace("_", "-"): v for k, v in headers.items()}


def test_a_cross_site_post_is_refused():
    from modules import local_origin

    check(local_origin.cross_site_problem(
        _FakeRequest(host="127.0.0.1:8000", sec_fetch_site="cross-site",
                     origin="https://evil.example")) is not None,
          "a cross-site POST is refused")
    check(local_origin.cross_site_problem(
        _FakeRequest(host="127.0.0.1:8000", sec_fetch_site="same-site")) is not None,
          "a same-site (different port) page is refused too")
    check(local_origin.cross_site_problem(
        _FakeRequest(host="127.0.0.1:8000", origin="https://evil.example")) is not None,
          "an unknown Origin is refused even without Sec-Fetch-Site")


def test_the_hud_and_non_browser_callers_still_work():
    from modules import local_origin

    check(local_origin.cross_site_problem(
        _FakeRequest(host="127.0.0.1:8000", sec_fetch_site="same-origin",
                     origin="http://127.0.0.1:8000")) is None,
          "the packaged HUD (same-origin) is allowed")
    check(local_origin.cross_site_problem(
        _FakeRequest(host="localhost:5173", sec_fetch_site="same-origin",
                     origin="http://localhost:5173")) is None,
          "the dev HUD is allowed")
    check(local_origin.cross_site_problem(_FakeRequest(host="127.0.0.1:8000")) is None,
          "a non-browser caller (no Origin, no Sec-Fetch-Site) is allowed")
    check(local_origin.cross_site_problem(_FakeRequest()) is None,
          "a caller with no headers at all is allowed")


def test_a_rebound_hostname_is_refused():
    """Neither the loopback bind nor an origin list survives DNS rebinding: a
    name resolving to 127.0.0.1 makes the page's request same-origin by the
    browser's own reckoning. The Host header is what that cannot forge."""
    from modules import local_origin

    check(local_origin.cross_site_problem(
        _FakeRequest(host="jarvis.evil.example:8000",
                     sec_fetch_site="same-origin")) is not None,
          "a rebound hostname is refused on the Host header")


def test_the_escape_hatch_defaults_off_and_fails_towards_off():
    import os
    from modules import local_origin

    hostile = _FakeRequest(host="127.0.0.1:8000", sec_fetch_site="cross-site")
    for value in ("", "0", "no", "off", "banana", "  "):
        os.environ["JARVIS_ALLOW_CROSS_SITE_POST"] = value
        check(local_origin.cross_site_problem(hostile) is not None,
              f"{value!r} reads as OFF")
    os.environ["JARVIS_ALLOW_CROSS_SITE_POST"] = "1"
    check(local_origin.cross_site_problem(hostile) is None,
          "an explicit 1 opens it")
    os.environ.pop("JARVIS_ALLOW_CROSS_SITE_POST", None)
    check(local_origin.cross_site_problem(hostile) is not None,
          "unset reads as OFF")


def test_all_three_body_less_routes_are_guarded():
    """The routes that take no body are the exposed ones — a JSON body forces a
    preflight the origin list already rejects."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    guarded = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in ("request_listen", "governance_cancel", "cancel_task"):
            continue
        body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
        guarded[fn.name] = "cross_site_problem" in body
    for name in ("request_listen", "governance_cancel", "cancel_task"):
        check(guarded.get(name) is True, f"{name} checks the caller")


# ── R14/R15/R16: the sinks that were trusting a caller-side rule ─────────────

def test_close_app_cannot_kill_a_protected_process_or_jarvis_itself():
    """`terminal_agent.kill_process` keeps the real protected list and then
    DELEGATES here, documenting "no bypass possible". It did not: this method's
    only guard was a one-entry explorer.exe check, so the direct `close_app`
    ACTION never passed that list.

    And the process that matters most was on nobody's list — `exe_targets`
    falls back to "<name>.exe", so close_app("python") killed the backend.
    """
    import action_engine as ae
    from modules.terminal_agent import _PROTECTED_PROCESSES

    engine = ae.ActionEngine.__new__(ae.ActionEngine)
    killed = []
    for name in ("lsass", "csrss", "winlogon", "services", "svchost"):
        out = engine._close_app(name)
        check("protected" in out.lower() or "refus" in out.lower(),
              f"close_app('{name}') is refused")
    for name in ("python", "pythonw"):
        out = engine._close_app(name)
        check("terminate me" in out.lower(),
              f"close_app('{name}') refuses to kill JARVIS itself")
    check("explorer.exe" not in str(_PROTECTED_PROCESSES),
          "explorer.exe stays on its own separate guard (unchanged)")


def test_an_email_body_survives_its_own_pipes():
    """The owner approves the whole string at the CONFIRM prompt, so what was
    approved and what leaves the machine must be the same text."""
    import action_engine as ae

    engine = ae.ActionEngine.__new__(ae.ActionEngine)
    sent = {}

    class FakeGmail:
        def send_email(self, to, subject, body):
            sent.update(to=to, subject=subject, body=body)
            return "sent"

    original = ae.GmailAgent
    ae.GmailAgent = FakeGmail
    try:
        engine._send_email(
            "boss@x.com | Q3 invoice | Please pay by Friday | thanks, Kaustav")
    finally:
        ae.GmailAgent = original

    check(sent.get("to") == "boss@x.com", "the recipient is parsed")
    check(sent.get("subject") == "Q3 invoice", "the subject is parsed")
    check(sent.get("body") == "Please pay by Friday | thanks, Kaustav",
          f"the whole body survives its pipes; got {sent.get('body')!r}")


def test_a_fact_containing_a_colon_is_stored_whole():
    """The category fallback already knows the split was wrong. Undo the split
    too, rather than committing half of what he said."""
    import action_engine as ae
    import memory_manager

    engine = ae.ActionEngine.__new__(ae.ActionEngine)
    stored = {}
    original = memory_manager.add_memory
    def _spy(content, category, user, strict=False):
        # `strict` is the keyword M4 made _remember_fact pass so it can tell a
        # write FAULT from a duplicate. A stub without it fails the CALL rather
        # than the assertion, which is a far more confusing way to find out.
        stored.update(content=content, category=category, strict=strict)
        return True

    memory_manager.add_memory = _spy
    try:
        engine._remember_fact("router login: admin/hunter2")
        check(stored.get("content") == "router login: admin/hunter2",
              f"the whole fact is stored; got {stored.get('content')!r}")
        check(stored.get("category") == "Fact", "with the fallback category")

        stored.clear()
        engine._remember_fact("Preference: he takes tea over coffee")
        check(stored.get("category") == "Preference",
              "a REAL category is still honoured")
        check(stored.get("content") == "he takes tea over coffee",
              "...and its fact is the part after the colon")
    finally:
        memory_manager.add_memory = original


def test_the_close_app_fallback_will_not_shut_your_browser():
    """Review finding R13. `close_app("notion")` finds no notion.exe because it
    is a browser TAB, trips a failure phrase, and the title fallback fires. It
    used to WM_CLOSE every window whose title merely CONTAINED "notion" — i.e.
    the Chrome window titled "Roadmap - Notion - Google Chrome", taking every
    other tab with it. That is exactly what the primary path refuses to do, in
    as many words. And it reported "Retry successful."

    Windows-only API, so this drives the real method with the ctypes surface
    faked: the property under test is WHICH titles are selected, not that
    PostMessage works.
    """
    import types
    import action_engine as ae

    WINDOWS = [
        "Roadmap - Notion - Google Chrome",     # a tab: must survive
        "Notion",                                # the real desktop app
        "Notionally Speaking - Notepad",         # substring, not a token
        "Inbox - Microsoft Edge",
    ]
    closed = []

    class _FakeUser32:
        # 1-BASED handles on purpose: ctypes converts HWND(0) to None, so a
        # zero handle is silently skipped inside the callback — which once made
        # this very test pass for the wrong reason.
        @staticmethod
        def EnumWindows(cb, _l):
            for i, _ in enumerate(WINDOWS, start=1):
                cb(i, 0)
            return True

        @staticmethod
        def GetWindowTextLengthW(hwnd):
            return len(WINDOWS[hwnd - 1])

        @staticmethod
        def GetWindowTextW(hwnd, buff, _n):
            buff.value = WINDOWS[hwnd - 1]
            return len(WINDOWS[hwnd - 1])

        @staticmethod
        def PostMessageW(hwnd, *_a):
            closed.append(WINDOWS[hwnd - 1])
            return True

    import ctypes
    real_windll = ctypes.windll
    ctypes.windll = types.SimpleNamespace(user32=_FakeUser32)
    try:
        engine = ae.ActionEngine.__new__(ae.ActionEngine)
        out = engine._attempt_fallback("close_app", "notion", "not found")
    finally:
        ctypes.windll = real_windll

    check("Roadmap - Notion - Google Chrome" not in closed,
          f"the browser window was NOT closed; closed={closed}")
    check("Notionally Speaking - Notepad" not in closed,
          "a substring match is not enough — whole tokens only")
    check("Notion" in closed, "the real desktop app WAS closed")
    check(out is not None and "Retry successful" in out,
          f"and it reports honestly: {str(out)[:60]}")


def test_a_browser_only_target_says_so_instead_of_claiming_success():
    import types
    import action_engine as ae

    WINDOWS = ["Roadmap - Notion - Google Chrome"]
    closed = []

    class _FakeUser32:
        # 1-based: HWND(0) becomes None in the callback. See the note above.
        @staticmethod
        def EnumWindows(cb, _l):
            cb(1, 0)
            return True

        @staticmethod
        def GetWindowTextLengthW(hwnd):
            return len(WINDOWS[hwnd - 1])

        @staticmethod
        def GetWindowTextW(hwnd, buff, _n):
            buff.value = WINDOWS[hwnd - 1]
            return len(WINDOWS[hwnd - 1])

        @staticmethod
        def PostMessageW(hwnd, *_a):
            closed.append(WINDOWS[hwnd - 1])
            return True

    import ctypes
    real_windll = ctypes.windll
    ctypes.windll = types.SimpleNamespace(user32=_FakeUser32)
    try:
        engine = ae.ActionEngine.__new__(ae.ActionEngine)
        out = engine._attempt_fallback("close_app", "notion", "not found")
    finally:
        ctypes.windll = real_windll

    check(closed == [], f"nothing was closed; got {closed}")
    check(out is not None and "browser" in out.lower(),
          f"and it explains why rather than failing mutely: {str(out)[:70]}")
    check("Retry successful" not in str(out), "...and does not claim success")


def test_a_one_character_target_cannot_sweep_the_desktop():
    import action_engine as ae

    engine = ae.ActionEngine.__new__(ae.ActionEngine)
    check(engine._attempt_fallback("close_app", "x", "not found") is None,
          "a one-character target is refused outright")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Pre-Electron review, batch 1 — the medium findings")
    print("=" * 62)
    for t in TESTS:
        t()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
