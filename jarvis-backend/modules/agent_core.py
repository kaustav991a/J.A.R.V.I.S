"""agent_core.py — the tool loop JARVIS was missing.

Agentic core, phase 2 (roadmap §5 Tier C #12). JARVIS already had the *pieces* of
an agent — tools (`action_engine`, the GUI agents, the browser), memory, and a
governance system — but no loop wiring them together. Every command was one shot:
ask the model, parse one answer, execute, done. This is the loop:

    decide -> call tool -> observe -> decide again -> ... -> answer

Everything the loop touches is INJECTED (`call_model`, `execute`, `authorize`,
`clock`, `lock`), so the whole thing is exercised against a scripted fake model
in test_agent_core.py — no keys, no network, no real side effects.

ASYNC, and specifically shaped for the human-in-the-loop confirm that lands in a
later phase:

  * **No lock is ever held across an await that isn't the tool itself.** The
    engine lock (`COMMAND_LOCK` in production) is acquired inside `_run_tool`
    and released the moment the tool returns. Model turns, authorisation, and —
    critically — any wait for a human to approve a CONFIRM-tier action happen
    with the lock NOT held. A loop paused on a confirmation must never stop an
    unrelated command from running; `test_agent_core.py` proves that.
  * `authorize` may be async. That is the seam the AT_DESK confirm uses: it
    emits a HUD prompt and awaits a Future keyed by confirmation_id. Because it
    is called outside the lock, awaiting a human there is safe.
  * Sync callables are accepted everywhere and offloaded with
    `asyncio.to_thread`, so an OS-bound tool can never block the event loop.

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

import asyncio
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


class ToolFailure(Exception):
    """A tool ran and honestly failed (as opposed to blowing up unexpectedly).

    The registry raises this when `execute_with_retry`'s meta comes back
    `state == "FAILED"` — i.e. when the Phase-2 `_is_failure` discipline says the
    action did not do what it claimed. Both this and an unexpected exception are
    fed back to the model as observations; neither crashes the loop.
    """


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


async def _maybe_await(fn: Callable, *args):
    """Call `fn`, awaiting it if it is async and off-threading it if it is not.

    A sync tool is OS-bound often enough (launching an app, reading the screen)
    that running it inline would stall the HUD, the voice loop and every other
    socket for its whole duration — so it goes to a thread. An async tool is
    already cooperative and is simply awaited.
    """
    result = fn(*args)
    if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
        return await result
    return result


async def _offload(fn: Callable, *args):
    """Same, but a *blocking* sync callable is pushed to a worker thread."""
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args)
    result = await asyncio.to_thread(fn, *args)
    if asyncio.iscoroutine(result):        # sync fn that returned a coroutine
        return await result
    return result


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


async def run_agent_loop(
    goal: str | list,
    tools: list,
    execute: Callable[[ToolCall], Any],
    *,
    system: str | None = None,
    authorize: Callable[[ToolCall], Any] | None = None,
    call_model: Callable[..., ToolTurn] | None = None,
    limits: AgentLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
    on_event: Callable[[str, dict], Any] | None = None,
    lock: Any | None = None,
) -> AgentResult:
    """Run the decide→act→observe loop until the model answers or a cap trips.

    `goal` is a string (turned into a user message) or a full message list.
    `execute(call)` performs one tool call and returns its result; raising
    `ToolFailure` (honest failure) or anything else (unexpected) is fine — the
    error is reported back to the model rather than killing the run.
    `authorize(call)` is the governance hook, consulted before EVERY execution;
    it may be async and may await a human, because it runs OUTSIDE `lock`.
    `lock` is an `asyncio.Lock` (production: `COMMAND_LOCK`) held ONLY for the
    duration of a tool execution — never across a model turn or a human wait.
    """
    limits = limits or AgentLimits()

    async def emit(kind: str, **data):
        if on_event is None:
            return
        try:
            await _maybe_await(on_event, kind, data)
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

    tool_names = {t["function"]["name"] if "function" in t else t.get("name")
                  for t in tools}
    runs: list[ToolRun] = []
    deadline = clock() + limits.max_seconds
    repairs = 0
    consecutive_errors = 0
    denials = 0
    steps = 0

    async def run_tool(call: ToolCall):
        """Execute one tool with the engine lock held for exactly that long.

        Everything slow-but-cooperative (deciding, authorising, waiting on a
        human) happens outside this function, so a loop parked on a confirmation
        never holds the lock that the rest of JARVIS needs.
        """
        if lock is None:
            return await _offload(execute, call)
        async with lock:
            return await _offload(execute, call)

    while True:
        if steps >= limits.max_steps:
            await emit("cap", reason=MAX_STEPS, steps=steps)
            return AgentResult(False, None, MAX_STEPS, steps, runs,
                               error=f"hit the {limits.max_steps}-step ceiling",
                               messages=messages)
        if clock() >= deadline:
            await emit("cap", reason=TIMEOUT, steps=steps)
            return AgentResult(False, None, TIMEOUT, steps, runs,
                               error=f"exceeded {limits.max_seconds}s", messages=messages)

        steps += 1
        await emit("model_turn", step=steps)
        # The provider call is blocking HTTP; off-thread it so the event loop
        # keeps serving the HUD while the model thinks.
        turn = await _offload(call_model, messages, tools)

        if turn is None or not turn.ok:
            err = (turn.error if turn is not None else "no turn returned")
            await emit("provider_failed", error=err)
            return AgentResult(False, None, PROVIDER_FAILED, steps, runs,
                               error=err, messages=messages)

        # No tool calls => the model is answering. That ends the run.
        if not turn.wants_tools:
            await emit("answer", text=turn.text)
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
                           + ", ".join(sorted(n for n in tool_names if n)))
            if problem:
                repairs += 1
                await emit("repair", problem=problem, attempt=repairs)
                if repairs > limits.max_repairs:
                    return AgentResult(
                        False, None, BAD_REQUEST, steps, runs,
                        error=f"model kept producing invalid tool calls: {problem}",
                        messages=messages)
                messages.append(tool_result_message(
                    call, f"ERROR: {problem}. Correct the call and try once more."))
                continue

            # --- governance, before EVERY execution (rule 4) ----------------
            # Deliberately outside `lock`: this is where a later phase awaits a
            # human at the HUD, and holding the engine lock through that would
            # freeze every other command in the process.
            if authorize is None:
                decision = Decision(True)
            else:
                decision = await _maybe_await(authorize, call)
            if not decision.allowed:
                denials += 1
                runs.append(ToolRun(call.name, call.arguments, ok=False,
                                    denied=True, error=decision.reason))
                await emit("denied", tool=call.name, reason=decision.reason)
                if denials > limits.max_repairs:
                    return AgentResult(False, None, DENIED, steps, runs,
                                       error=decision.reason, messages=messages)
                messages.append(tool_result_message(
                    call, f"DENIED: {decision.reason}. Do not retry this tool; "
                          "either use a permitted tool or explain what you need."))
                continue

            # --- execute ----------------------------------------------------
            await emit("tool_start", tool=call.name, arguments=call.arguments)
            try:
                raw = await run_tool(call)
                output = _truncate(_stringify(raw), limits.max_tool_output_chars)
                runs.append(ToolRun(call.name, call.arguments, ok=True, output=output))
                consecutive_errors = 0
                await emit("tool_ok", tool=call.name, output=output)
                messages.append(tool_result_message(call, output))
            except Exception as e:  # noqa: BLE001
                consecutive_errors += 1
                # An honest ToolFailure carries the engine's own wording; keep it
                # verbatim so the model reads exactly what the user would hear.
                err = str(e) if isinstance(e, ToolFailure) else f"{type(e).__name__}: {e}"
                runs.append(ToolRun(call.name, call.arguments, ok=False, error=err))
                await emit("tool_error", tool=call.name, error=err)
                if consecutive_errors >= limits.max_consecutive_errors:
                    return AgentResult(
                        False, None, TOOL_ERRORS, steps, runs,
                        error=f"{consecutive_errors} tool failures in a row: {err}",
                        messages=messages)
                # Hand the error back: a good model adapts, and this is how it
                # learns the file doesn't exist / the app isn't installed.
                messages.append(tool_result_message(call, f"ERROR: {err}"))
