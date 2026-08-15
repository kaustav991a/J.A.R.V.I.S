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

Phase 5 adds the token half of rule 3. Every step re-sends the entire transcript,
so the cost of a run is quadratic in its length and the free tiers meter
tokens-per-minute, not requests. `compact_messages` drops the OLDEST completed
steps once the history outgrows `max_transcript_chars`, in whole
assistant+tool-result groups (an orphaned `tool` message is a 400 from every
provider) and replaced by a note that says the detail is gone rather than
paraphrasing it — a paraphrase would let the model keep quoting a file it can no
longer see. `unlocked_tools` is the other phase-5 seam: a delegate tool is a
nested loop that takes the same engine lock itself, so it must not be wrapped in
it here.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from modules import agent_search as ags
from modules import agent_skills as asx
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
    #: Phase 5: transcript budget. Every step re-sends the WHOLE history, so an
    #: 8-step run costs ~8x its own tokens on a free tier where tokens-per-minute
    #: is the real ceiling. Past this, the oldest completed steps are compacted.
    #: 0 disables compaction entirely.
    max_transcript_chars: int = 20000
    #: How many of the most recent step-groups survive compaction untouched.
    keep_recent_groups: int = 2


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
    #: Out-of-band things the CALLER must tell the owner regardless of ok/answer —
    #: phase 5's away-yield writes the "parked as task ab12cd34" sentence here.
    #: The loop itself never reads these; they exist so a run that legitimately
    #: could not finish still hands the user their next move.
    notes: list[str] = field(default_factory=list)

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


def message_chars(message: Any) -> int:
    """Rough size of one message on the wire — content plus serialised call args."""
    if not isinstance(message, dict):
        return len(str(message))
    total = len(str(message.get("content") or ""))
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) else None
        if isinstance(fn, dict):
            total += len(str(fn.get("name") or "")) + len(str(fn.get("arguments") or ""))
    return total


def transcript_chars(messages: list) -> int:
    return sum(message_chars(m) for m in messages)


def _split_groups(messages: list) -> tuple[list, list[list]]:
    """Split a transcript into (head, step_groups).

    `head` is the system prompt plus the opening user message(s) — the goal, which
    can never be dropped. Each group after it starts at an assistant message and
    carries the `tool` results that answer it. Grouping this way is not cosmetic:
    a `tool` message whose assistant turn has been removed is a 400 from every
    OpenAI-compatible provider ("tool_call_id not found"), so compaction must move
    whole groups or nothing.
    """
    head: list = []
    groups: list[list] = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else None
        if role == "assistant":
            groups.append([m])
        elif groups:
            groups[-1].append(m)
        else:
            head.append(m)
    return head, groups


def compact_messages(messages: list, max_chars: int,
                     keep_recent_groups: int = 2) -> tuple[list, int, list[str]]:
    """Drop the oldest completed steps once the transcript outgrows its budget.

    Returns `(messages, groups_dropped, tool_names_dropped)`. What replaces them
    is one honest note saying the detail is GONE — a summary that merely
    paraphrased the old output would let the model keep quoting a file it can no
    longer see. If dropping everything droppable still doesn't fit, the transcript
    is returned as short as it can be: refusing to run is worse than one oversized
    request, and per-tool truncation already caps the biggest single message.
    """
    if max_chars <= 0 or transcript_chars(messages) <= max_chars:
        return messages, 0, []
    head, groups = _split_groups(messages)
    keep = max(1, int(keep_recent_groups))
    dropped: list[list] = []
    while len(groups) > keep:
        note = [_compaction_note(dropped)] if dropped else []
        if transcript_chars(head + note + _flatten(groups)) <= max_chars:
            break
        dropped.append(groups.pop(0))
    if not dropped:
        return messages, 0, []
    names = _dropped_tool_names(dropped)
    return head + [_compaction_note(dropped)] + _flatten(groups), len(dropped), names


def _flatten(groups: list[list]) -> list:
    return [m for g in groups for m in g]


def _dropped_tool_names(dropped: list[list]) -> list[str]:
    names: list[str] = []
    for group in dropped:
        for m in group:
            for tc in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
                fn = tc.get("function") if isinstance(tc, dict) else None
                name = (fn or {}).get("name")
                if name and name not in names:
                    names.append(str(name))
    return names


def _compaction_note(dropped: list[list]) -> dict:
    """The one message that stands in for everything removed."""
    names = _dropped_tool_names(dropped)
    used = ", ".join(names) if names else "no tools"
    return {"role": "user", "content": (
        f"[Context note: the earliest {len(dropped)} step(s) of this task were "
        f"removed to stay inside the context budget. Tools used there: {used}. "
        "Their output is NO LONGER AVAILABLE to you — do not quote or summarise "
        "it from memory. If you need a detail from one of them, call the tool "
        "again.]")}


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
    unlocked_tools: set | None = None,
    shelf: Any | None = None,
    skills: Any | None = None,
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

    # §6.8.2 (rule 18). The skill loader is one tool for the whole library, so
    # it is added to every list rather than searched for — and de-duplicated,
    # because the runner also hands it to the shelf as an `extra` so the
    # resident cap counts it.
    skill_def = skills.tool_def() if skills is not None else None

    def with_skill_loader(defs: list) -> list:
        if skill_def is None:
            return defs
        if any((d or {}).get("name") == skill_def["name"] for d in defs):
            return defs
        return list(defs) + [skill_def]

    # §6.8.2 (rule 13). With a shelf, the tool list is not fixed for the run:
    # `search_tools` promotes schemas mid-conversation, so the list is rebuilt
    # before every model turn. The shelf owns the resident cap, so the
    # `max_tools` check below still holds on the FIRST list and every later one.
    tools = with_skill_loader(shelf.defs() if shelf is not None else tools)

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
    #: Once per run, not once per call — an ungoverned run says so, loudly, and
    #: then gets on with it rather than shouting on every tool.
    _ungoverned_warned = False

    async def run_tool(call: ToolCall):
        """Execute one tool with the engine lock held for exactly that long.

        Everything slow-but-cooperative (deciding, authorising, waiting on a
        human) happens outside this function, so a loop parked on a confirmation
        never holds the lock that the rest of JARVIS needs.

        `unlocked_tools` is exempt, and phase 5's sub-agent depends on it: a
        delegate tool is not one engine action, it is a whole nested loop that
        takes the SAME lock around each of its own tool calls. Holding it here too
        would deadlock on the first inner call (`asyncio.Lock` is not reentrant),
        and holding it for the entire delegation would freeze every other command
        for the length of the sub-run.
        """
        if lock is None or (unlocked_tools and call.name in unlocked_tools):
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

        # --- compaction (rule: rate limits are the ceiling) -----------------
        # Every step re-sends the whole history, so a long run pays for its own
        # early steps again and again. Trim before the request, never after — the
        # point is the tokens that leave the machine.
        messages, dropped, dropped_tools = compact_messages(
            messages, limits.max_transcript_chars, limits.keep_recent_groups)
        if dropped:
            await emit("compacted", groups=dropped, tools=dropped_tools,
                       chars=transcript_chars(messages))
            print(f"[AGENT] compacted {dropped} step group(s) "
                  f"({', '.join(dropped_tools) or 'no tools'}) — transcript now "
                  f"{transcript_chars(messages)} chars", flush=True)

        if shelf is not None:
            # Rebuilt every turn: a promotion from the previous step only
            # reaches the model if the list it is sent is regenerated here.
            tools = with_skill_loader(shelf.defs())
            tool_names = {t["function"]["name"] if "function" in t else t.get("name")
                          for t in tools}

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

            # --- tool search: answered HERE, never dispatched ---------------
            # `search_tools` changes only what the model can see. It touches
            # nothing, so it has no governance decision to make and must not
            # reach the authorizer (which would refuse it — it is not a
            # registered action) or the engine (which has no handler). What it
            # REVEALS stays gated: every promoted tool is still authorised on
            # the call that uses it.
            # --- skill loading: answered HERE, for the same reason -----------
            # A skill is INSTRUCTIONS. It grants no capability, so there is
            # nothing for governance to decide and nothing for the engine to
            # dispatch; a playbook that names a blocked tool still cannot reach
            # one, because tools are gated where they are called.
            if skills is not None and call.name == asx.LOAD_TOOL_NAME:
                observation = skills.handle(call.arguments or {})
                runs.append(ToolRun(call.name, call.arguments, ok=True,
                                    output=observation))
                await emit("skill_loaded",
                           name=(call.arguments or {}).get("name"),
                           chars=len(observation))
                messages.append(tool_result_message(call, observation))
                # Like a search: not a step towards the goal, and not a failure
                # either. Opening the wrong playbook must not count against the
                # error streak.
                continue

            if shelf is not None and call.name == ags.SEARCH_TOOL_NAME:
                observation = shelf.handle(call.arguments or {})
                runs.append(ToolRun(call.name, call.arguments, ok=True,
                                    output=observation))
                await emit("tool_search", query=(call.arguments or {}).get("query"),
                           resident=shelf.resident())
                messages.append(tool_result_message(call, observation))
                # A search is not a step towards the goal, but it is also not a
                # failure — an unproductive one must not count against the
                # error streak, or three bad guesses would kill a healthy run.
                continue

            # --- governance, before EVERY execution (rule 4) ----------------
            # Deliberately outside `lock`: this is where a later phase awaits a
            # human at the HUD, and holding the engine lock through that would
            # freeze every other command in the process.
            if authorize is None:
                # UNGOVERNED, AND IT SAYS SO. This default is fail-OPEN by
                # construction: no authorizer means every tool runs, including
                # CONFIRM-tier ones. No production caller does it —
                # `run_agent_command` assigns a desk or away authorizer on both
                # branches of its only if/else, and `test_agent_governed.py`
                # pins that — and the harnesses that do it are testing the loop's
                # mechanics rather than its governance.
                #
                # Left permissive rather than flipped to deny-by-default because
                # flipping it changes the contract every existing harness relies
                # on. But it is not left SILENT: this project has already been
                # bitten once by a gate that was never wired ("the shelf had never
                # been wired in production", §6.8), and the thing that made that
                # expensive was that nothing said so at the time.
                if not _ungoverned_warned:
                    _ungoverned_warned = True
                    print("[AGENT] ⚠ running with NO authorizer — governance is "
                          "not being consulted for any tool call. This is only "
                          "correct in a harness.", flush=True)
                    await emit("ungoverned", tool=call.name)
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
                # The AUDIT trail keeps the raw error — `summary()` and the owner
                # read it, and a diagnosis should not have to step around advice
                # written for the model.
                runs.append(ToolRun(call.name, call.arguments, ok=False, error=err))
                await emit("tool_error", tool=call.name, error=err)
                if consecutive_errors >= limits.max_consecutive_errors:
                    return AgentResult(
                        False, None, TOOL_ERRORS, steps, runs,
                        error=f"{consecutive_errors} tool failures in a row: {err}",
                        messages=messages)
                # Hand the error back as an INSTRUCTION (§6.8.1 gap B, rule 6).
                # The raw text is still the first line; what is appended is the
                # next move. A free-tier model given only "FileNotFoundError"
                # retries the identical call until the cap above kills the run —
                # which made a better message a reliability fix, not a nicety.
                from modules.agent_errors import explain
                messages.append(tool_result_message(
                    call, f"ERROR: {explain(e, call, available=tools)}"))
