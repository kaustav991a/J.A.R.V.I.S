"""
test_shell_safety.py — nothing the model wrote reaches a command line
=====================================================================

Pre-Electron review, 2026-08-15. Three sites built a Windows command line by
interpolating a value that originates in the model's action JSON:

    action_engine.py    os.system(f'start "" "{target}"')
    action_engine.py    os.system(f'taskkill /IM "{target_exe}" /F 2>nul')
    human_gui_agent.py  subprocess.Popen(f'start "" "{app_name}"', shell=True)

The surrounding quotes read as protection and are not. `x" & calc & "` closes
the quote, runs a second command, and reopens one so the tail still parses.

Why it mattered more than it looks: governance approves an action by TYPE.
`close_app` is a harmless type and the tier check never inspects the argument.
And with the §6.8 tool layer the model acts on text it did not write — web
results, indexed documents, an MCP server's reply — so prompt injection in any
of those reached the action JSON, and the action JSON reached a shell.

This harness drives the real predicate and asserts on the real call sites'
source shape, because the payoff property ("no shell line is built") is
structural: a behavioural test would have to actually run calc to prove it.
"""

import ast
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


from modules import shell_safety  # noqa: E402  (path set above)


# ── the predicate ────────────────────────────────────────────────────────────

# Every one of these ends a quoted argument or starts a second command.
ATTACKS = [
    'x" & calc & "',                    # the canonical break-out
    'notepad" & calc & "',
    'a"&calc&"b',                       # no spaces — survives .replace(" ", "")
    'x" | calc',
    'x" && calc',
    'x"\r\ncalc',                       # a second LINE is a second command
    'x"\ncalc',
    'chrome" > C:\\Windows\\evil.bat "',  # redirection writes a file
    'x" ^& calc',                       # cmd escape character
    'x%COMSPEC%',                       # variable expansion
    'x" (calc) "',
    'x"!VAR!',                          # delayed expansion
    'app`whoami`',                      # backtick, in case a shell ever changes
    'x$(calc)',
]


def test_every_break_out_is_refused():
    for payload in ATTACKS:
        check(shell_safety.is_shell_safe(payload) is False,
              f"refused: {payload[:34]!r}")


def test_ordinary_app_names_still_pass():
    # The guard is worthless if it also refuses what the assistant is FOR.
    for name in ("notepad", "chrome", "Visual Studio Code", "vs code",
                 "spotify", "CalculatorApp.exe", "WINWORD.EXE",
                 r"C:\Program Files\Mozilla Firefox\firefox.exe",
                 "Photoshop 2026", "obs64.exe", "Zoom.exe",
                 "Notepad++", "7-Zip File Manager", "µTorrent"):
        check(shell_safety.is_shell_safe(name) is True,
              f"allowed, correctly: {name!r}")


def test_empty_and_non_text_are_unsafe():
    # An empty target is never a real app name, and `start "" ""` is not a
    # launch — returning True here would let a caller build one and report it.
    for bad in ("", "   ", "\t", None, 42, [], {"a": 1}):
        check(shell_safety.is_shell_safe(bad) is False,
              f"unsafe: {bad!r}")


def test_the_refusal_does_not_echo_the_payload_back():
    # The rejected string is model- or web-sourced and may itself be aimed at
    # whoever reads the log. Only the character class is reported.
    reason = shell_safety.reject_reason('x" & calc & "')
    check("calc" not in reason,
          "the refusal names the character class, not the payload")
    check('"' in reason or "'\"'" in reason,
          "...but does say which metacharacter tripped it")
    check("empty" in shell_safety.reject_reason(""),
          "an empty target gets its own honest reason")


# ── the call sites ───────────────────────────────────────────────────────────

def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8", errors="replace")


def _fstring_shell_calls(source: str, path: str):
    """Every os.system(...) or shell=True call whose argument is an f-string.

    Walks the AST rather than grepping, so a reformatted or line-wrapped call
    cannot slip past by not matching a text pattern.
    """
    hits = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        is_system = name == "system"
        is_shell_true = any(
            kw.arg == "shell" and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if not (is_system or is_shell_true):
            continue
        for arg in node.args:
            if isinstance(arg, ast.JoinedStr):      # an f-string
                hits.append(f"{path}:{node.lineno}")
    return hits


def test_no_fstring_ever_reaches_a_shell_in_the_action_path():
    # The property that actually matters, stated structurally: in these files
    # no command line is BUILT from an interpolated string at all. A guard that
    # only filters input is one forgotten call site from being bypassed.
    for rel in ("action_engine.py", "modules/human_gui_agent.py"):
        hits = _fstring_shell_calls(_src(rel), rel)
        check(not hits, f"no f-string shell call in {rel} (found: {hits})")


def test_the_two_action_engine_sites_consult_the_guard():
    src = _src("action_engine.py")
    check("shell_safety.is_shell_safe(target)" in src,
          "launch_app retry validates its target before launching")
    check("shell_safety.is_shell_safe(target_exe)" in src,
          "the taskkill fallback validates each exe name")
    check('["taskkill", "/IM", target_exe, "/F"]' in src,
          "taskkill runs as an argument LIST — it is an exe, not a cmd builtin, "
          "so it needs no shell at all")


def test_human_gui_agent_consults_the_guard():
    src = _src("modules/human_gui_agent.py")
    check("shell_safety.is_shell_safe(app_name)" in src,
          "the internal launcher validates the app name it parsed out of a task")
    check('["cmd", "/c", "start", "", app_name]' in src,
          "...and passes it as an argument, not as syntax")


def test_terminal_agent_is_deliberately_out_of_scope():
    # Running a shell command IS its purpose, and it has its own pattern
    # blocklist and governance tier. Pinned so the exclusion is a decision
    # rather than something that was missed.
    src = _src("modules/terminal_agent.py")
    check("shell=True" in src,
          "terminal_agent still uses a shell on purpose")
    check("shell_safety" not in src,
          "...and is deliberately NOT wrapped in this guard")
    check("_check_blocked" in src,
          "it has its own blocklist, which is a weaker shape and known to be")


# ── the SAME bug, a second shell: ADB ────────────────────────────────────────
# Found in the same review. tv_agent and action_engine build ADB shell commands
# for the television with the model's `query` in them, mitigated only by
# `.replace(" ", "%s")` / `.replace(" ", "%20")`. Swapping spaces does nothing
# about `;`, `$()`, backticks or `&&`, so `netflix:x";reboot;echo"` rebooted the
# TV. The correct pattern was already in the same file at the YouTube branch —
# `shlex.quote(video_url)` — and had simply not been applied to the others.

def _shell_commands_in(line: str) -> int:
    """How many commands a POSIX shell would see in `line`.

    The honest oracle for this bug: not "does the string look escaped" but
    "does the payload create a SECOND command". Tracks quote state so a
    separator inside quotes is data, exactly as /bin/sh treats it.
    """
    count, quote, i = 1, None, 0
    in_backtick = False
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == quote:
                quote = None
            elif quote == '"' and ch == "\\":
                i += 1
        elif ch in "'\"":
            quote = ch
        elif ch == "\\":
            i += 1
        elif ch in ";\n":
            count += 1
        elif ch in "&|" and i + 1 < len(line) and line[i + 1] == ch:
            count += 1
            i += 1
        elif ch == "`":
            # a PAIR of backticks is one substitution, not two commands
            if not in_backtick:
                count += 1
            in_backtick = not in_backtick
        elif ch == "$" and i + 1 < len(line) and line[i + 1] == "(":
            count += 1
        i += 1
    return count


def _drive_tv(target: str) -> list:
    """Run tv_play_media against a stubbed television, return the ADB commands."""
    import time as _time
    from modules import tv_agent as _tv

    sent: list = []
    agent = _tv.TVAgent.__new__(_tv.TVAgent)          # no device, no network
    agent._shell = lambda cmd, **kw: (sent.append(cmd), (True, ""))[1]
    agent._keyevent = lambda code: (True, "")
    real_sleep, _time.sleep = _time.sleep, lambda *_a: None
    try:
        agent.tv_play_media(target)
    finally:
        _time.sleep = real_sleep
    return sent


ADB_PAYLOADS = [
    'netflix:x";reboot;echo"',
    'netflix:x`reboot`',
    'netflix:x$(reboot)',
    'spotify:x";reboot;echo"',
    'hotstar:x;reboot',
    'hotstar:x`id`',
    'prime:x;reboot',
    'prime:x&&reboot',
]


def test_the_oracle_itself_detects_an_unquoted_separator():
    # A test whose oracle is broken proves nothing, so prove the oracle first.
    check(_shell_commands_in('input text x;reboot') == 2,
          "oracle sees an unquoted ';' as a second command")
    check(_shell_commands_in("input text 'x;reboot'") == 1,
          "oracle sees a QUOTED ';' as data")
    check(_shell_commands_in('am start -d "u" && reboot') == 2,
          "oracle sees unquoted '&&'")
    check(_shell_commands_in('input text `id`') == 2,
          "oracle sees a backtick substitution")


def test_no_adb_payload_can_open_a_second_command_on_the_tv():
    for payload in ADB_PAYLOADS:
        cmds = _drive_tv(payload)
        check(bool(cmds), f"drove the TV path for {payload[:28]!r}")
        for cmd in cmds:
            check(_shell_commands_in(cmd) == 1,
                  f"one command only, from {payload[:28]!r}: {cmd[:60]!r}")


def test_the_tv_still_plays_an_ordinary_request():
    # The guard is worthless if it also breaks "play the crown on netflix".
    for target, expect in (("netflix:The Crown", "netflix://search"),
                           ("spotify:Pink Floyd", "spotify://search"),
                           ("hotstar:Kaun Banega", "input text")):
        cmds = _drive_tv(target)
        check(any(expect in c for c in cmds),
              f"{target!r} still issues its {expect!r} command")
        check(all(_shell_commands_in(c) == 1 for c in cmds),
              f"...and does so as a single command ({target!r})")


def test_the_space_swaps_are_gone_from_the_tv_path():
    src = _src("modules/tv_agent.py")
    check('query.replace(" ", "%20")' not in src,
          "the %20 space-swap is replaced by real percent-encoding")
    check("urllib.parse.quote(query" in src,
          "...with urllib.parse.quote, which also encodes & # ?")
    check(src.count("shlex.quote") >= 4,
          f"every ADB interpolation is shlex.quote'd (found {src.count('shlex.quote')})")


def test_action_engine_tv_typing_is_quoted_too():
    src = _src("action_engine.py")
    check('_shlex.quote(text.replace(" ", "%s"))' in src,
          "_tv_type quotes the model-supplied text before it reaches adb shell")


TESTS = [
    test_every_break_out_is_refused,
    test_ordinary_app_names_still_pass,
    test_empty_and_non_text_are_unsafe,
    test_the_refusal_does_not_echo_the_payload_back,
    test_no_fstring_ever_reaches_a_shell_in_the_action_path,
    test_the_two_action_engine_sites_consult_the_guard,
    test_human_gui_agent_consults_the_guard,
    test_terminal_agent_is_deliberately_out_of_scope,
    test_the_oracle_itself_detects_an_unquoted_separator,
    test_no_adb_payload_can_open_a_second_command_on_the_tv,
    test_the_tv_still_plays_an_ordinary_request,
    test_the_space_swaps_are_gone_from_the_tv_path,
    test_action_engine_tv_typing_is_quoted_too,
]


def main():
    print("=" * 60)
    print("shell-safety harness (pre-Electron review)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
