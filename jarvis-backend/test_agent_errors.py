"""Harness for modules/agent_errors.py — §6.8.1 gap B (rule 6).

The property under test is not "the message is nicer". It is:

  * every error names a NEXT ACTION, not just a status;
  * a REFUSAL is never phrased in a way that invites a retry (the 2026-07-26
    live failure: a sandbox refusal read as ordinary data and the model tried
    three more roots until the step cap stopped it);
  * the raw error text SURVIVES, because the audit trail and the owner read it;
  * advice only ever names tools the run actually holds;
  * the loop is really wired to it, and the audit trail was NOT polluted with
    model-facing advice.

Driven against the real `run_agent_loop` with a scripted fake model — no
network, no engine, no files.
"""

import asyncio
import sys

from modules import agent_core as ac
from modules.agent_core import AgentLimits, ToolFailure
from modules.agent_errors import explain
from modules.tool_calls import ToolCall, ToolTurn

TOOLS = [
    {"name": "workspace_read", "description": "Read a file.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "find_file", "description": "Find a file.",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]}},
]


def call(name="workspace_read", **args):
    return ToolCall(id="c1", name=name, arguments=args or {"path": "F:/x.md"})


def run(coro):
    return asyncio.run(coro)


# ── every branch names a next action ─────────────────────────────────────────

def test_a_missing_file_says_where_to_look_instead_of_rereading():
    text = explain(FileNotFoundError("File not found: F:/work/notes.md"),
                   call(path="F:/work/notes.md"), available=TOOLS)
    assert "File not found: F:/work/notes.md" in text, "raw error was lost"
    assert "do not read it again" in text.lower(), text
    assert "find_file" in text, "no concrete next tool was named"


def test_a_refusal_is_never_phrased_as_retryable():
    """The live 2026-07-26 failure. A refusal is terminal for that path, so the
    instruction must say stop — including for a NEARBY path, which is what the
    model actually tried."""
    text = explain(PermissionError("Access denied: 'F:/work' is outside the "
                                   "permitted workspace roots."), call())
    assert "refused" in text.lower(), text
    assert "retrying" in text.lower() or "do not retry" in text.lower(), text
    assert "nearby path" in text.lower(), \
        "the message does not warn against the exact thing the model did live"


def test_governance_sentinels_are_treated_as_refusals_not_faults():
    for sentinel in ("GOVERNANCE_BLOCKED: nope", "TIER_BLOCKED: nope",
                     "Write refused: binary/executable file type '.exe'"):
        text = explain(PermissionError(sentinel), call())
        assert "security boundary" in text, f"{sentinel} -> {text}"


def test_a_directory_is_distinguished_from_a_missing_file():
    text = explain(IsADirectoryError("Path is not a file: F:/work"),
                   call(), available=TOOLS)
    assert "directory, not a file" in text, text
    # `list_directory` is not in TOOLS for this run, so it must NOT be named.
    assert "list_directory" not in text, \
        "advised a tool the run does not hold"


def test_advice_only_names_tools_the_run_actually_holds():
    """Naming an unavailable tool costs one wasted step and an unknown-tool
    repair — the exact budget this module exists to save."""
    with_find = explain(FileNotFoundError("File not found: x"), call(),
                        available=TOOLS)
    without = explain(FileNotFoundError("File not found: x"), call(),
                      available=[{"name": "system_status"}])
    assert "find_file" in with_find
    assert "find_file" not in without, without


def test_no_tool_list_still_gives_generic_advice():
    text = explain(FileNotFoundError("File not found: x"), call())
    assert "Locate the real one first" in text, text


def test_a_bad_argument_points_back_at_the_schema():
    text = explain(TypeError("expected str, got int"), call())
    assert "schema" in text.lower() and "call it once more" in text.lower(), text
    assert "workspace_read" in text, "the failing tool is not named"


def test_a_timeout_forbids_the_identical_retry():
    text = explain(TimeoutError("operation timed out after 30s"), call())
    assert "do not repeat the same call" in text.lower(), text


def test_an_unclassifiable_error_admits_it_rather_than_inventing_a_remedy():
    """A confident wrong instruction is worse than none."""
    text = explain(RuntimeError("segfault in libfoo"), call())
    assert "cannot classify" in text, text
    assert "segfault in libfoo" in text, "raw error was lost"


def test_the_raw_text_always_survives():
    for exc in (FileNotFoundError("File not found: a"), PermissionError("Access denied x"),
                TimeoutError("timed out"), ValueError("bad value"),
                RuntimeError("weird"), ToolFailure("engine said no")):
        assert str(exc) in explain(exc, call()), exc


# ── wired into the real loop ─────────────────────────────────────────────────

def _model_then_answer(script):
    """A fake model: emits scripted turns, then answers."""
    turns = list(script)

    def call_model(messages, tools):
        if turns:
            return turns.pop(0)
        return ToolTurn(ok=True, text="done")
    return call_model


def test_the_loop_sends_the_instruction_to_the_model():
    seen = {}

    def execute(c):
        raise FileNotFoundError("File not found: F:/work/notes.md")

    turn = ToolTurn(ok=True, tool_calls=[call(path="F:/work/notes.md")])
    result = run(ac.run_agent_loop(
        "read notes", TOOLS, execute,
        call_model=_model_then_answer([turn]),
        limits=AgentLimits(max_steps=3)))

    tool_messages = [m for m in result.messages if m.get("role") == "tool"]
    assert tool_messages, "no tool result reached the model"
    body = tool_messages[0]["content"]
    assert "do not read it again" in body.lower(), body
    assert "find_file" in body, body


def test_the_audit_trail_keeps_the_raw_error_not_the_advice():
    """A diagnosis should not have to step around advice written for the model."""
    def execute(c):
        raise FileNotFoundError("File not found: F:/work/notes.md")

    turn = ToolTurn(ok=True, tool_calls=[call(path="F:/work/notes.md")])
    result = run(ac.run_agent_loop(
        "read notes", TOOLS, execute,
        call_model=_model_then_answer([turn]),
        limits=AgentLimits(max_steps=3)))

    failed = [r for r in result.tool_runs if not r.ok]
    assert failed, "the failure was not recorded"
    assert "do not read it again" not in (failed[0].error or "").lower(), \
        "model-facing advice leaked into the audit trail"
    assert "File not found" in failed[0].error


def test_a_tool_failure_keeps_the_engines_own_wording_verbatim():
    """`ToolFailure` carries the sentence the OWNER would hear. Advice may be
    appended; the wording may not be rewritten."""
    def execute(c):
        raise ToolFailure("I couldn't open that file, Sir.")

    turn = ToolTurn(ok=True, tool_calls=[call()])
    result = run(ac.run_agent_loop(
        "read", TOOLS, execute,
        call_model=_model_then_answer([turn]),
        limits=AgentLimits(max_steps=3)))
    body = [m for m in result.messages if m.get("role") == "tool"][0]["content"]
    assert "I couldn't open that file, Sir." in body, body


def test_better_messages_did_not_change_which_failures_are_fatal():
    """The consecutive-error cap is the loop's business. A nicer error must not
    quietly make a run survive longer or die sooner."""
    def execute(c):
        raise RuntimeError("boom")

    turns = [ToolTurn(ok=True, tool_calls=[call()]) for _ in range(5)]
    result = run(ac.run_agent_loop(
        "read", TOOLS, execute,
        call_model=_model_then_answer(turns),
        limits=AgentLimits(max_steps=6, max_consecutive_errors=3)))
    assert result.ok is False
    assert result.stop_reason == ac.TOOL_ERRORS, result.stop_reason
    assert len([r for r in result.tool_runs if not r.ok]) == 3


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
