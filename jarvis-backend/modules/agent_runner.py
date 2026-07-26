"""agent_runner.py — the one wired intent, behind a flag.

Agentic core, phase 4. Everything before this was plumbing with no caller. This
is the glue that lets ONE kind of request run through the tool loop while every
other command keeps taking the existing one-shot path:

    should_use_agent(text)  -> is this a wired intent, and is the flag on?
    tool_set_for(text)      -> which curated set that intent needs
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
  * anything else (phase 5) — the action is PARKED as a durable queued task and
    the owner is pinged wherever he is; "approve task ab12cd34" from his phone
    runs it later (see `modules/agent_yield.py`). The loop is told the call was
    refused, because parked is not done.

Phase 5 also gives the parent a `delegate_subtask` tool when asked for it: one
sub-question, one read-only helper agent, one sentence back
(`modules/agent_subagents.py`). The transcript budget that makes that worth doing
lives in `agent_core.compact_messages`.

Narration is first-class: every step is pushed to the HUD through
`socket_manager` as it happens, so the ReAct trace is watchable live rather than
a black box that returns one string at the end.
"""

from __future__ import annotations

import os
import re
from typing import Any

from modules import agent_confirm, agent_core, agent_subagents, agent_yield
from modules.agent_core import AgentLimits, Decision
from modules.tool_calls import ToolCall

#: Feature flag. OFF by default — the one-shot path stays the default everywhere.
FLAG_ENV = "JARVIS_AGENT_LOOP"

#: WebSocket frame type the HUD listens for. Additive: no existing frame changes.
FRAME = "agent_step"
CONFIRM_FRAME = "agent_confirm"
PARKED_FRAME = "agent_parked"

#: How many CONFIRM actions one away run may park. One. A model that keeps
#: reaching for a writing tool must not turn into five phone notifications.
MAX_PARKS_PER_RUN = 1

SYSTEM_PROMPT = (
    "You are JARVIS, working through a task for Kaustav with tools.\n"
    "Rules:\n"
    "- Call ONE tool at a time and read its result before deciding the next step.\n"
    "- Pass FULL paths to file tools. If a listing gave you a path, copy it "
    "verbatim — a bare filename is resolved against a different root and fails.\n"
    "- Use the tools to find things out. Never invent a filename, a path or a "
    "file's contents — if a tool did not tell you, you do not know it.\n"
    "- When you have the answer, reply in plain prose with no tool call. Be brief "
    "and specific: name the actual file and say what is actually in it.\n"
    "- If a tool fails or is refused, say so plainly and try another route or stop. "
    "Never claim something was done when it was not."
)

# The wired intents. Conservative on purpose, in the spirit of
# planner.should_plan — a false negative just keeps today's path, a false positive
# routes a trivial command through a multi-step loop.
#
# 1. READ: "find my most recent workspace file and tell me what's in it".
_FIND = ("recent", "latest", "last", "newest")
_THING = ("file", "files", "script", "document", "note")
_READ = ("what's in", "whats in", "what is in", "tell me what", "read it",
         "contents", "summarise", "summarize", "inside")

# 2. WRITE: "write a note called x.md saying y". Narrower still, and the reason it
#    exists is that the read intent can never reach a CONFIRM — the `files` set is
#    read-only by construction, so without this the desk-confirm and away-park
#    paths would be code nobody can actually exercise. The write itself is still
#    CONFIRM-tier: this only decides which loop looks at the request.
_WRITE = ("write", "save", "create", "append")
_CONTENT = ("saying", "that says", "with the text", "containing", "contents:",
            "with content")


def _is_read_intent(t: str) -> bool:
    return (any(w in t for w in _FIND)
            and any(f" {w} " in t or f" {w}," in t for w in _THING)
            and any(w in t for w in _READ))


def _is_write_intent(t: str) -> bool:
    return (any(f" {w} " in t for w in _WRITE)
            and any(f" {w} " in t or f" {w}," in t for w in _THING)
            and any(w in t for w in _CONTENT))


def tool_set_for(text: str) -> str:
    """Which curated set this request needs. Read-only unless it asks to write."""
    return "authoring" if _is_write_intent(f" {(text or '').lower().strip()} ") \
        else "files"


def workspace_note() -> str:
    """One line naming the directories the file tools can actually reach.

    Live 2026-07-26 the loop listed the user's HOME (that is `list_directory`'s
    sandbox), picked the newest file there, and handed it to `workspace_read`,
    whose roots are entirely different — so the read failed and the run ended with
    a file it could name but not open. The model cannot infer either sandbox; the
    honest fix is to tell it where its territory is.
    """
    try:
        from pathlib import Path

        from modules.workspace_agent import WORKSPACE_ROOTS
        roots = [Path(p) for p in WORKSPACE_ROOTS]
    except Exception:  # noqa: BLE001 — a missing root list must not break a run
        return ""
    if not roots:
        return ""
    # The two file tools do NOT share a sandbox: `list_directory` (terminal_agent)
    # refuses anything outside the user's home, while `workspace_read` allows the
    # workspace roots — which mostly sit on another drive. Only their INTERSECTION
    # can be browsed and then read, and that is what the model must be told to
    # start from. Live 2026-07-26 it was told "list the workspace roots", dutifully
    # tried F:\work, got "Access denied" as an ordinary result, and thrashed
    # between roots until the step cap stopped it.
    home = Path.home()
    listable = [r for r in roots if r == home or home in r.parents]
    note = "- Files you can READ live under: " + "; ".join(str(r) for r in roots) + ".\n"
    note += (f"- But you can only LIST directories inside {home}"
             + (f" — start from: {'; '.join(str(r) for r in listable)}"
                if listable else "")
             + ". Listing anything outside it is refused, so do not retry it.\n")
    return note


def system_prompt() -> str:
    """The system prompt with the live workspace roots spliced in."""
    note = workspace_note()
    if not note:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT.replace("Rules:\n", "Rules:\n" + note, 1)


def flag_enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(FLAG_ENV, "0")).strip().lower() in ("1", "true", "yes", "on")


def should_use_agent(text: str, env: dict | None = None) -> bool:
    """True only for a wired intent, and only when the flag is on."""
    if not text or not flag_enabled(env):
        return False
    t = f" {text.lower().strip()} "
    if len(t) < 15:
        return False
    return _is_read_intent(t) or _is_write_intent(t)


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
        # A sub-agent's events arrive with their kind prefixed `sub:`. They are
        # narrated with the same wording under a "Helper:" label rather than
        # silently, so a long delegation doesn't look like a hang — and the frame
        # keeps the prefixed `event` so the HUD can nest them.
        event = kind
        label, kind = ("Helper: ", kind[4:]) if kind.startswith("sub:") else ("", kind)
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
        elif kind == "compacted":
            text = (f"Trimming context — dropped {data.get('groups')} earlier "
                    f"step(s) to stay inside the token budget")
        await send({
            "type": FRAME, "event": event, "goal": goal, "detail": data,
            # Mirrored into the fields today's HUD already displays. Only the
            # PARENT's answer is "complete" — a helper finishing mid-run must not
            # tell the HUD the whole task is done.
            "status": "complete" if event == "answer" else "processing_llm",
            "message": (label + text) if text else event,
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


def make_away_authorizer(registry, send, goal: str, *, parked: list,
                         notes: list, queue=None, notify=None,
                         max_parks: int = MAX_PARKS_PER_RUN):
    """AWAY governance: AUTO runs, CONFIRM is PARKED for the owner's phone.

    Phase 5. The refusal handed back to the loop names the task id and says NOT
    DONE, so the model can neither retry it nor narrate it as finished. `parked`
    and `notes` are the caller's lists — `run_agent_command` copies the notes onto
    the result so the reply tells the owner his next move ("approve task ab12cd34")
    even though the run itself failed.
    """
    base = registry.authorizer(allow_confirm=False)

    async def authorize(call: ToolCall) -> Decision:
        entry = registry.get(call.name)
        decision = base(call)
        if decision.allowed or entry is None or entry.tier != "CONFIRM":
            return decision
        # A CONFIRM tool called with arguments missing is the model's mistake, not
        # something to wake the owner about — let the base refusal stand so the
        # loop spends its repair on the real problem.
        if registry.missing_required(call):
            return decision
        if len(parked) >= max_parks:
            return Decision(False, (
                f"an action is already waiting for the owner's authorisation "
                f"(task {parked[-1].short}); do not queue another — finish with "
                f"what you have or stop"))

        payload = registry.to_payload(call)
        target = str(payload.get("target", ""))
        question = agent_confirm.question_for(entry.action_type, target)
        park = await agent_yield.park_for_approval(
            payload, goal=goal, question=question, queue=queue, notify=notify)
        parked.append(park)
        notes.append(park.message)
        await send({
            "type": PARKED_FRAME, "task_id": park.id, "short": park.short,
            "tool": entry.action_type, "target": target,
            "status": "awaiting_confirmation", "message": park.message,
        })
        return Decision(False, agent_yield.refusal_reason(park, entry.action_type))

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
    delegate: bool = False,
    delegate_set: str = "research",
    queue=None,
    notify=None,
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
    parked: list = []
    notes: list[str] = []

    if at_desk:
        authorize = make_desk_authorizer(registry, send, confirms, confirm_timeout)
    else:
        # Nobody to ask in the moment — park it durably and ping the phone
        # (phase 5). The loop still gets a refusal: parked is not done.
        authorize = make_away_authorizer(registry, send, goal, parked=parked,
                                         notes=notes, queue=queue, notify=notify)

    # Two-tier CONFIRM execution: an approved tool must run with
    # governance_bypass=True, or the engine would re-pend it and hand back a
    # GOVERNANCE_CONFIRM sentinel that the loop (correctly) treats as a refusal.
    auto_exec = registry.executor(engine)
    approved_exec = registry.executor(engine, governance_bypass=True)
    narrate = make_narrator(send, goal)

    tools = registry.defs(tool_set)
    delegate_run = None
    delegate_name = None
    if delegate:
        # One extra tool that is itself a read-only agent run. Counted against the
        # same max_tools cap, so a set of 8 + delegate is refused by agent_core
        # rather than silently degrading the model.
        delegate_def, delegate_run = agent_subagents.make_delegate(
            registry, engine, tool_set=delegate_set, lock=lock,
            call_model=call_model, on_event=narrate)
        delegate_name = delegate_def["name"]
        tools = tools + [delegate_def]

        inner_authorize = authorize

        async def authorize(call: ToolCall) -> Decision:  # noqa: F811 — wraps it
            if call.name == delegate_name:
                # The delegate touches no action_type of its own; its governance
                # is the sub-agent's per-call check over a read-only set.
                if not str((call.arguments or {}).get("question") or "").strip():
                    return Decision(False, "missing required argument(s): question")
                return Decision(True, "delegation to a read-only helper")
            return await agent_core._maybe_await(inner_authorize, call)

    async def execute(call: ToolCall):
        if delegate_run is not None and call.name == delegate_name:
            return await delegate_run(call)
        entry = registry.get(call.name)
        runner = approved_exec if (entry and entry.tier == "CONFIRM") else auto_exec
        return await runner(call)

    result = await agent_core.run_agent_loop(
        goal,
        tools,
        execute,
        system=system_prompt(),
        authorize=authorize,
        call_model=call_model,
        limits=limits,
        lock=lock,
        # The delegate is exempt from the engine lock: the sub-agent takes it
        # around each of its OWN tool calls, and asyncio locks aren't reentrant.
        unlocked_tools={delegate_name} if delegate_name else None,
        on_event=narrate,
    )
    # Anything the OWNER has to be told regardless of how the run ended — today
    # that is "I parked it as task ab12cd34". The caller speaks these even when
    # `ok` is False, instead of falling back to a path that would re-stage the
    # same confirmation nobody is there to answer.
    result.notes.extend(notes)
    return result
