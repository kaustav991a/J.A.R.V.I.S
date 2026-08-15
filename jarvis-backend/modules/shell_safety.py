"""
shell_safety.py — nothing an LLM wrote reaches a command line unchecked
======================================================================

Pre-Electron review, 2026-08-15. Three places built a Windows command line
by f-string interpolation of a value that originates in the model's action
JSON:

    action_engine.py     os.system(f'start "" "{target}"')
    action_engine.py     os.system(f'taskkill /IM "{target_exe}" /F 2>nul')
    human_gui_agent.py   subprocess.Popen(f'start "" "{app_name}"', shell=True)

The quoting looks protective and is not. A target of

    x" & calc & "

closes the opening quote, runs `calc`, and reopens one so the rest parses.
Any command runs, with the user's privileges, from an action the governance
layer approved on its *type* — `close_app` is a harmless action type, and
the tier check never looks at the argument.

**This is not theoretical for an agentic build.** The §6.8 tool layer lets
the model act on text it did not write: web results, an indexed document, an
MCP server's reply. Prompt injection in any of those reaches the action JSON,
and the action JSON reached a shell.

Two rules, applied together, because either alone is a single point of
failure:

1. **Refuse the input.** `is_shell_safe` rejects the Windows metacharacters
   that let a string leave its quotes. An allowlist would be tighter still,
   but app names are genuinely open-ended (paths, unicode, publisher names),
   so this is the one place a blocklist is the right shape — the set being
   excluded is closed and defined by cmd.exe, not by what a model might say.
2. **Do not build a command line at all.** Callers pass argument LISTS with
   `shell=False` wherever the semantics allow it, so even a string that got
   past rule 1 is an argument rather than syntax.

Deliberately NOT applied to `terminal_agent.run_command`, whose entire
purpose is running a shell command the user asked for; that path has its own
pattern blocklist and its own governance tier.
"""

import re

# cmd.exe's syntax characters. A value carrying any of these can change the
# STRUCTURE of a command line rather than sit inside it as data.
#   "   closes a quoted argument            &  ; command separator
#   |   pipe                                <> redirection
#   ^   cmd's escape character              %  variable expansion
#   ()  grouping                            !  delayed expansion
#   \r\n  a second line is a second command
_SHELL_META = '"&|<>^%()!\r\n\t`$'
_SHELL_META_RE = re.compile("[" + re.escape(_SHELL_META) + "]")


def is_shell_safe(value: str) -> bool:
    """True if `value` can cross a command line without changing its shape.

    Empty and non-string values are unsafe: an empty target is never a real
    app name, and returning True for one would let a caller build
    `start "" ""` and call it a launch.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    return _SHELL_META_RE.search(value) is None


def reject_reason(value: str) -> str:
    """A refusal that names the problem without echoing the payload back.

    The offending string is model- or web-sourced and may itself be an
    injection attempt aimed at whatever reads the log; only the character
    class is reported.
    """
    if not isinstance(value, str) or not value.strip():
        return "empty or non-text target"
    found = sorted({c for c in value if _SHELL_META_RE.match(c)})
    printable = ", ".join(repr(c) for c in found)
    return f"target contains shell metacharacter(s): {printable}"
