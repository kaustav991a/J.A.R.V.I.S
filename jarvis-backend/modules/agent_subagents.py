"""agent_subagents.py — one tool that is itself an agent.

Agentic core, phase 5 (roadmap §5 Tier C #12). This is the piece the Claude Agent
SDK would have given us for free: recursion on the same loop, so the main run can
hand a self-contained question to a fresh agent and get back an ANSWER instead of
a pile of tool output.

The value is not cleverness, it is context. A sub-question like "which of these
files is the newest" costs three tool calls and a few thousand characters of
directory listings. Run inline, all of that lands in the parent transcript and is
re-sent on every subsequent step — on a free tier where tokens-per-minute is the
real ceiling, that is the difference between finishing and being rate-limited.
Run as a sub-agent, the parent sees one sentence.

Three hard constraints, each closing a way this could go wrong:

1. **Depth 1, by construction.** The sub-agent's tool list is built from a
   read-only registry set and NEVER contains the delegate tool itself, so there is
   no code path from a sub-agent to another sub-agent. Depth is not enforced by a
   counter that a future edit could forget to pass.

2. **Read-only, checked at construction.** Every tool in the delegated set must be
   AUTO tier or `make_delegate` refuses to build. A sub-agent is unattended by
   definition — there is no HUD prompt bound to it and no owner watching its
   trace — so it must not be able to reach a CONFIRM action at all. (`authorize`
   still runs per call underneath; this is the design-time half.)

3. **A failed sub-agent fails honestly.** If the inner loop hits a cap or a
   provider outage it raises `ToolFailure` carrying the inner `summary()`, which
   the parent loop feeds back to the model as an observation. It never returns an
   empty string that would read as "nothing found".
"""

from __future__ import annotations

from typing import Any, Callable

from modules import agent_core
from modules.agent_core import AgentLimits, ToolFailure
from modules.tool_calls import ToolCall

#: The one tool name the parent sees.
DELEGATE_TOOL = "delegate_subtask"

DELEGATE_DESCRIPTION = (
    "Hand ONE self-contained sub-question to a helper who has the same read-only "
    "lookup tools you do, and get back a short answer. Use it when finding "
    "something out would take several searches whose raw output you do not need — "
    "ask for the conclusion, not the material. Give the helper everything it needs "
    "in the question: it cannot see this conversation."
)

SUBAGENT_SYSTEM = (
    "You are a research helper working for JARVIS on ONE narrow question.\n"
    "- You cannot see the wider conversation. Work only from the question given.\n"
    "- Use your tools to find out; never invent a filename, a path or contents.\n"
    "- Answer in at most three sentences, stating the specific facts you found.\n"
    "- If the tools cannot answer it, say exactly what you could not determine. "
    "Never guess to look helpful."
)

#: Tighter than the parent's: a helper that needs eight steps is not a helper.
SUBAGENT_LIMITS = AgentLimits(max_steps=4, max_seconds=60.0, max_tools=8,
                              max_transcript_chars=12000)


class UnsafeSubagentError(ValueError):
    """Raised when a delegated tool set contains anything that is not read-only."""


def delegate_definition(name: str = DELEGATE_TOOL,
                        description: str = DELEGATE_DESCRIPTION) -> dict:
    """The Anthropic-dialect definition the parent's tool list carries."""
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": ("The complete sub-question, self-contained. "
                                    "Include any path, name or context the helper "
                                    "needs — it starts with no history."),
                },
            },
            "required": ["question"],
        },
    }


def _assert_read_only(registry, tool_set: str | list) -> list[str]:
    names = registry.set_names(tool_set) if isinstance(tool_set, str) else list(tool_set)
    writable = [n for n in names if (registry.tier_of(n) or "BLOCK") != "AUTO"]
    if writable:
        raise UnsafeSubagentError(
            f"sub-agents run unattended and must be read-only, but {writable} "
            f"in '{tool_set}' are not AUTO tier")
    return list(names)


def make_delegate(registry, engine, *, tool_set: str = "research",
                  name: str = DELEGATE_TOOL,
                  limits: AgentLimits | None = None,
                  lock: Any | None = None,
                  call_model: Callable[..., Any] | None = None,
                  on_event: Callable[[str, dict], Any] | None = None,
                  system: str = SUBAGENT_SYSTEM):
    """Build `(definition, executor)` for the delegate tool.

    `on_event` receives the sub-agent's own loop events with their kind prefixed
    `sub:` — so the HUD can show what the helper is doing without those steps
    being mistaken for the main run's.
    """
    names = _assert_read_only(registry, tool_set)
    tools = registry.defs(names)
    limits = limits or SUBAGENT_LIMITS
    # allow_confirm=False is belt-and-braces: nothing in `names` is CONFIRM, and
    # if a governance reload made one CONFIRM the run refuses it rather than
    # self-approving on a channel with no owner attached.
    authorize = registry.authorizer(allow_confirm=False)
    execute = registry.executor(engine)

    async def relay(kind: str, data: dict):
        if on_event is None:
            return
        await agent_core._maybe_await(on_event, f"sub:{kind}", data)

    async def run(call: ToolCall) -> str:
        question = str((call.arguments or {}).get("question") or "").strip()
        if not question:
            raise ToolFailure("the sub-question was empty — nothing to delegate")
        result = await agent_core.run_agent_loop(
            question, tools, execute,
            system=system,
            authorize=authorize,
            call_model=call_model,
            limits=limits,
            lock=lock,
            on_event=relay if on_event is not None else None,
        )
        if not result.ok or not (result.answer or "").strip():
            # The parent must learn the sub-question is UNANSWERED, in the inner
            # loop's own words, so it can try another route or stop.
            raise ToolFailure(f"the helper could not answer that: {result.summary()}")
        return result.answer.strip()

    return delegate_definition(name), run
