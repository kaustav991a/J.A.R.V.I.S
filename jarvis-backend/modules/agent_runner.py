"""agent_runner.py — the one wired intent, behind a flag.

Agentic core, phase 4. Everything before this was plumbing with no caller. This
is the glue that lets ONE kind of request run through the tool loop while every
other command keeps taking the existing one-shot path:

    should_use_agent(text)  -> is this the wired intent, and is the flag on?
    run_agent_command(...)  -> run it, narrate it, return an AgentResult

Deliberately narrow. `JARVIS_AGENT_LOOP` defaults to **off**, and even switched
on only a request that matches the demo intent is routed here. On any failure
the caller falls back to the one-shot pipeline, so today's behaviour cannot be
lost by turning the flag on.

**CONFIRM resolves by PRESENCE**, reusing the Track B fused verdict:

  * `at_desk` — the interactive path, and the reason this exists. The HUD is
    asked, the loop parks on a Future (outside the engine lock, so nothing else
    freezes), and on approval it executes with `governance_bypass=True` and
    continues IN PLACE. No serialisation, no Telegram, no exit.
  * anything else — refused with an honest explanation. The AWAY yield (serialise
    to `Session.pending`, ping the phone, resume on "approve task <id>") is the
    next phase; until it exists, saying so beats pretending.

Narration is first-class: every step is pushed to the HUD through
`socket_manager` as it happens, so the ReAct trace is watchable live rather than
a black box that returns one string at the end.
"""

from __future__ import annotations

import os
import re
from typing import Any

from modules import agent_confirm, agent_core
from modules.agent_core import AgentLimits, Decision
from modules.tool_calls import ToolCall

#: Feature flag. OFF by default — the one-shot path stays the default everywhere.
FLAG_ENV = "JARVIS_AGENT_LOOP"

#: WebSocket frame type the HUD listens for. Additive: no existing frame changes.
FRAME = "agent_step"
CONFIRM_FRAME = "agent_confirm"

SYSTEM_PROMPT = (
    "You are JARVIS, working through a task for Kaustav with tools.\n"
    "Rules:\n"
    "- Call ONE tool at a time and read its result before deciding the next step.\n"
    "- Use the tools to find things out. Never invent a filename, a path or a "
    "file's contents — if a tool did not tell you, you do not know it.\n"
    "- When you have the answer, reply in plain prose with no tool call. Be brief "
    "and specific: name the actual file and say what is actually in it.\n"
    "- If a tool fails or is refused, say so plainly and try another route or stop. "
    "Never claim something was done when it was not."
)

# The wired intent: "find my most recent workspace file and tell me what's in it".
# Conservative on purpose, in the spirit of planner.should_plan — a false
# negative just keeps today's path, a false positive routes a trivial command
# through a multi-step loop.
_FIND = ("recent", "latest", "last", "newest")
_THING = ("file", "files", "script", "document", "note")
_READ = ("what's in", "whats in", "what is in", "tell me what", "read it",
         "contents", "summarise", "summarize", "inside")


def flag_enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(FLAG_ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def should_use_agent(text: str, env: dict | None = None) -> bool:
    """True only for the ONE wired intent, and only when the flag is on."""
    if not text or not flag_enabled(env):
        return False
    t = f" {text.lower().strip()} "
    if len(t) < 15:
        return False
    return (any(w in t for w in _FIND)
            and any(f" {w} " in t or f" {w}," in t for w in _THING)
            and any(w in t for w in _READ))


def _presence() -> str:
    """Fused Track B verdict, defaulting to 'unknown' if presence is unavailable."""
    try:
        from modules import presence_probe
        return presence_probe.snapshot().get("presence") or "unknown"
    except Exception:  # noqa: BLE001 — presence must never break a run
        return "unknown"


def make_narrator(send, goal: str):
    """Turn loop events into HUD frames.

    `send(payload)` is `socket_manager.send_ui_update` (or a fake in tests). Each
    frame carries BOTH the additive `agent_step` type and the `status`/`message`
    pair the current HUD already renders, so the trace is visible today without
    any frontend change and a dedicated panel can be added later.
    """
    async def on_event(kind: str, data: dict):
        text = None
        if kind == "model_turn":
            text = f"Thinking… (step {data.get('step')})"
        elif kind == "tool_start":
            args = data.get("arguments") or {}
            detail = next((str(v) for v in args.values() if v), "")
            text = f"Using {data.get('tool')}" + (f" → {detail}" if detail else "")
        elif kind == "tool_ok":
            out = (data.get("output") or "").strip().replace("\n", " ")
            text = f"Got {len(out)} chars back" if len(out) > 80 else f"Result: {out}"
        elif kind == "tool_error":
            text = f"{data.get('tool')} failed: {data.get('error')}"
        elif kind == "denied":
            text = f"{data.get('tool')} refused: {data.get('reason')}"
        elif kind == "repair":
            text = f"Correcting a malformed call: {data.get('problem')}"
        elif kind == "cap":
            text = f"Stopping — {data.get('reason')}"
        elif kind == "answer":
            text = data.get("text") or "Done."
        elif kind == "provider_failed":
            text = f"Reasoning core unreachable: {data.get('error')}"
        await send({
            "type": FRAME, "event": kind, "goal": goal, "detail": data,
            # Mirrored into the fields today's HUD already displays.
            "status": "processing_llm" if kind != "answer" else "complete",
            "message": text or kind,
        })

    return on_event


def make_desk_authorizer(registry, send, confirms=None, timeout: float | None = None):
    """AT_DESK governance: AUTO runs, CONFIRM asks the HUD and waits.

    The wait happens here — inside `authorize`, which `agent_core` calls with the
    engine lock NOT held — so a paused run never blocks the rest of JARVIS.
    """
    confirms = confirms or agent_confirm.confirms
    base = registry.authorizer(allow_confirm=False)

    async def authorize(call: ToolCall) -> Decision:
        entry = registry.get(call.name)
        decision = base(call)
        # Only a CONFIRM-tier refusal is worth asking about. A missing argument
        # or an unregistered tool is the model's problem, not the owner's.
        if decision.allowed or entry is None or entry.tier != "CONFIRM":
            return decision

        target = str(registry.to_payload(call).get("target", ""))
        question = agent_confirm.question_for(entry.action_type, target)
        pending = confirms.open(entry.action_type, target, question)
        await send({
            "type": CONFIRM_FRAME, "confirmation_id": pending.id,
            "tool": entry.action_type, "target": target, "question": question,
            "status": "awaiting_confirmation", "message": question,
        })
        outcome = await confirms.wait(pending, timeout)
        await send({
            "type": CONFIRM_FRAME, "confirmation_id": pending.id,
            "resolved": outcome, "status": "processing_llm",
            "message": f"Authorisation {outcome}.",
        })
        if outcome == agent_confirm.APPROVED:
            return Decision(True, "approved at the desk")
        if outcome == agent_confirm.EXPIRED:
            return Decision(False, "the authorisation prompt timed out unanswered")
        return Decision(False, "the owner declined that action")

    return authorize


async def run_agent_command(
    goal: str,
    engine,
    *,
    registry=None,
    tool_set: str = "files",
    lock=None,
    send=None,
    presence: str | None = None,
    limits: AgentLimits | None = None,
    confirms=None,
    call_model=None,
    confirm_timeout: float | None = None,
):
    """Run one goal through the tool loop, narrating to the HUD as it goes.

    Returns an `agent_core.AgentResult`. The caller decides what to do with a
    failure — the wiring in main.py falls back to the one-shot pipeline, so a
    loop that cannot finish costs the user nothing but a little time.
    """
    if registry is None:
        from modules.agent_tools import default_registry
        registry = default_registry()
    if send is None:
        from socket_manager import send_ui_update as send

    presence = presence or _presence()
    at_desk = presence == "at_desk"

    if at_desk:
        authorize = make_desk_authorizer(registry, send, confirms, confirm_timeout)
    else:
        # No interactive channel: CONFIRM is refused with a reason the model can
        # act on. Phase 5 turns this into the Telegram yield + resume.
        authorize = registry.authorizer(allow_confirm=False)

    # Two-tier CONFIRM execution: an approved tool must run with
    # governance_bypass=True, or the engine would re-pend it and hand back a
    # GOVERNANCE_CONFIRM sentinel that the loop (correctly) treats as a refusal.
    auto_exec = registry.executor(engine)
    approved_exec = registry.executor(engine, governance_bypass=True)

    async def execute(call: ToolCall):
        entry = registry.get(call.name)
        runner = approved_exec if (entry and entry.tier == "CONFIRM") else auto_exec
        return await runner(call)

    return await agent_core.run_agent_loop(
        goal,
        registry.defs(tool_set),
        execute,
        system=SYSTEM_PROMPT,
        authorize=authorize,
        call_model=call_model,
        limits=limits,
        lock=lock,
        on_event=make_narrator(send, goal),
    )
