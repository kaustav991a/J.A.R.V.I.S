"""agent_core.py — the tool loop JARVIS was missing.

Agentic core, phase 2 (roadmap §5 Tier C #12). JARVIS already had the *pieces* of
an agent — tools (`action_engine`, the GUI agents, the browser), memory, and a
governance system — but no loop wiring them together. Every command was one shot:
ask the model, parse one answer, execute, done. This is the loop:

    decide -> call tool -> observe -> decide again -> ... -> answer

Everything the loop touches is INJECTED (`call_model`, `execute`, `authorize`,
`clock`), so the whole thing is exercised against a scripted fake model in
test_agent_core.py — no keys, no network, no real side effects.

The five rules from the roadmap, and where they live here:

1. **Small tool sets** — `AgentLimits.max_tools` (default 8). Exceeded is an
   explicit failure, not a silent trim: curation is the registry's job, and
   quietly dropping a tool would make the agent fail for an invisible reason.
2. **Strict schema + ONE repair, then honest failure** — a malformed or unknown
   tool call is fed back once as a `tool` message explaining exactly what was
   wrong. A second failure of the same call ends the run.
3. **Hard caps** — steps, wall clock, and consecutive errors. A run that hits a
   cap reports `stop_reason` and `ok=False`. It must never narrate success.
4. **Governance before EVERY execution** — `authorize(call)` is consulted for
   each call, every step. A denial is reported to the model once so it can take
   another route; repeated denial ends the run.
5. **Never lose today's behaviour** — this module is standalone. Nothing calls it
   until an intent is deliberately wired to it (phase 4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from modules.tool_calls import (
    ToolCall,
    ToolTurn,
    assistant_message,
    tool_result_message,
    validate_tool_defs,
)

# Stop reasons. `answered` is the ONLY one that means the goal was met.
ANSWERED = "answered"
MAX_STEPS = "max_steps"
TIMEOUT = "timeout"
PROVIDER_FAILED = "provider_failed"
DENIED = "denied"
TOOL_ERRORS = "tool_errors"
BAD_REQUEST = "bad_request"


@dataclass
class AgentLimits:
    """Hard ceilings. Every one of these exists because a free model will find it."""

    max_steps: int = 8               # model turns, not tool calls
    max_seconds: float = 120.0       # wall clock for the whole run
    max_tools: int = 8               # small models degrade sharply past ~8 tools
    max_repairs: int = 1             # ONE correction per bad call, then stop
    max_consecutive_errors: int = 3  # tool keeps blowing up -> stop, don't grind
    max_tool_output_chars: int = 4000


@dataclass
class Decision:
    """Governance's answer for one tool call."""

    allowed: bool
    reason: str = ""


@dataclass
class ToolRun:
    """One executed (or refused) tool call — the audit trail of a run."""

    name: str
    arguments: dict
    ok: bool
    output: str = ""
    error: str | None = None
    denied: bool = False


@dataclass
class AgentResult:
    ok: bool
    answer: str | None
    stop_reason: str
    steps: int = 0
    tool_runs: list[ToolRun] = field(default_factory=list)
    error: str | None = None
    messages: list = field(default_factory=list)

    def summary(self) -> str:
        """One honest line. Used when the loop has to explain itself out loud."""
        if self.ok:
            return self.answer or "Done."
        if self.stop_reason == MAX_STEPS:
            return (f"I couldn't finish that within {self.steps} steps, Sir — "
                    "stopping rather than guessing.")
        if self.stop_reason == TIMEOUT:
            return "That took too long, Sir — I stopped before it ran away."
        if self.stop_reason == DENIED:
            return f"I wasn't authorised to do that, Sir: {self.error}"
        if self.stop_reason == PROVIDER_FAILED:
            return "My reasoning core is unreachable at the moment, Sir."
        return f"I couldn't complete that, Sir: {self.error or self.stop_reason}"


def _truncate(text: str, limit: int) -> str:
    """Cap a tool's output, and SAY that it was capped.

    A silently-cut result teaches the model the file/list ended where it didn't,
    and it will confidently report the wrong thing.
    """
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit} more characters]"


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        import json
        return json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def run_agent(
    goal: str | list,
    tools: list,
    execute: Callable[[ToolCall], Any],
    *,
    system: str | None = None,
    authorize: Callable[[ToolCall], Decision] | None = None,
    call_model: Callable[..., ToolTurn] | None = None,
    limits: AgentLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_event: Callable[[str, dict], None] | None = None,
) -> AgentResult:
    """Run the decide→act→observe loop until the model answers or a cap trips.

    `goal` is a string (turned into a user message) or a full message list.
    `execute(call)` performs one tool call and returns its result; raising is
    fine — the error is reported back to the model rather than killing the run.
    `authorize(call)` is the governance hook: consulted before EVERY execution.
    """
    limits = limits or AgentLimits()

    def emit(kind: str, **data):
        if on_event:
            try:
                on_event(kind, data)
            except Exception:  # noqa: BLE001
                pass   # telemetry must never break a run

    problems = validate_tool_defs(tools)
    if problems:
        return AgentResult(False, None, BAD_REQUEST,
                           error="invalid tool definitions: " + "; ".join(problems))
    if len(tools) > limits.max_tools:
        # Explicit, not a silent trim — see rule 1.
        return AgentResult(
            False, None, BAD_REQUEST,
            error=(f"{len(tools)} tools offered, limit is {limits.max_tools}; "
                   "curate the tool set for this intent"))

    if call_model is None:
        from modules.llm_router import universal_tool_call as call_model  # lazy: keeps
        # agent_core importable (and testable) without the router's heavy imports.

    messages: list = []
    if system:
        messages.append({"role": "system", "content": system})
    if isinstance(goal, str):
        messages.append({"role": "user", "content": goal})
    else:
        messages.extend(goal)

    tool_names = {t["function"]["name"] for t in tools}
    runs: list[ToolRun] = []
    deadline = clock() + limits.max_seconds
    repairs = 0
    consecutive_errors = 0
    denials = 0
    steps = 0

    while True:
        if steps >= limits.max_steps:
            emit("cap", reason=MAX_STEPS, steps=steps)
            return AgentResult(False, None, MAX_STEPS, steps, runs,
                               error=f"hit the {limits.max_steps}-step ceiling",
                               messages=messages)
        if clock() >= deadline:
            emit("cap", reason=TIMEOUT, steps=steps)
            return AgentResult(False, None, TIMEOUT, steps, runs,
                               error=f"exceeded {limits.max_seconds}s", messages=messages)

        steps += 1
        emit("model_turn", step=steps)
        turn = call_model(messages, tools)

        if turn is None or not turn.ok:
            err = (turn.error if turn is not None else "no turn returned")
            emit("provider_failed", error=err)
            return AgentResult(False, None, PROVIDER_FAILED, steps, runs,
                               error=err, messages=messages)

        # No tool calls => the model is answering. That ends the run.
        if not turn.wants_tools:
            emit("answer", text=turn.text)
            messages.append(assistant_message(turn))
            return AgentResult(True, turn.text, ANSWERED, steps, runs,
                               messages=messages)

        messages.append(assistant_message(turn))

        for call in turn.tool_calls:
            # --- schema / name validation, with ONE repair (rule 2) ---------
            problem = None
            if not call.ok:
                problem = call.arguments_error
            elif call.name not in tool_names:
                problem = (f"unknown tool '{call.name}'. Available tools: "
                           + ", ".join(sorted(tool_names)))
            if problem:
                repairs += 1
                emit("repair", problem=problem, attempt=repairs)
                if repairs > limits.max_repairs:
                    return AgentResult(
                        False, None, BAD_REQUEST, steps, runs,
                        error=f"model kept producing invalid tool calls: {problem}",
                        messages=messages)
                messages.append(tool_result_message(
                    call, f"ERROR: {problem}. Correct the call and try once more."))
                continue

            # --- governance, before EVERY execution (rule 4) ----------------
            decision = authorize(call) if authorize else Decision(True)
            if not decision.allowed:
                denials += 1
                runs.append(ToolRun(call.name, call.arguments, ok=False,
                                    denied=True, error=decision.reason))
                emit("denied", tool=call.name, reason=decision.reason)
                if denials > limits.max_repairs:
                    return AgentResult(False, None, DENIED, steps, runs,
                                       error=decision.reason, messages=messages)
                messages.append(tool_result_message(
                    call, f"DENIED: {decision.reason}. Do not retry this tool; "
                          "either use a permitted tool or explain what you need."))
                continue

            # --- execute ----------------------------------------------------
            emit("tool_start", tool=call.name, arguments=call.arguments)
            try:
                raw = execute(call)
                output = _truncate(_stringify(raw), limits.max_tool_output_chars)
                runs.append(ToolRun(call.name, call.arguments, ok=True, output=output))
                consecutive_errors = 0
                emit("tool_ok", tool=call.name, output=output)
                messages.append(tool_result_message(call, output))
            except Exception as e:  # noqa: BLE001
                consecutive_errors += 1
                err = f"{type(e).__name__}: {e}"
                runs.append(ToolRun(call.name, call.arguments, ok=False, error=err))
                emit("tool_error", tool=call.name, error=err)
                if consecutive_errors >= limits.max_consecutive_errors:
                    return AgentResult(
                        False, None, TOOL_ERRORS, steps, runs,
                        error=f"{consecutive_errors} tool failures in a row: {err}",
                        messages=messages)
                # Hand the error back: a good model adapts, and this is how it
                # learns the file doesn't exist / the app isn't installed.
                messages.append(tool_result_message(call, f"ERROR: {err}"))
