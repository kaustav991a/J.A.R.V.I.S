"""
planner.py — The ReAct Orchestrator (Roadmap §1.2 + §1.1b)
==========================================================

Sits between `brain.process_command` and `action_engine`. For COMPLEX, multi-step
goals, J.A.R.V.I.S. stops emitting a flat one-shot action array and instead runs a
**ReAct (Reason + Act) loop**:

    Think    → the LLM analyses the goal + everything observed so far and decides the
               single immediate next step (or declares the goal complete).
    Act      → that one action runs through action_engine (governance-gated).
    Observe  → the result is appended to a scratchpad.
    Repeat   → loop back to Think until the goal is met, it needs the user, or the
               step budget is exhausted.

DESIGN PRINCIPLES
-----------------
- **Fast-path bypass.** `should_plan()` is conservative: only clearly multi-step
  intents enter the loop. Simple commands ("turn off the TV", "what time is it")
  never pay the planner's latency — they take the existing single-shot path.
- **Transport-agnostic.** The loop is handed an `execute_fn` (the engine's
  `execute_with_retry`) and an optional `notify` callback. It imports neither the
  engine singleton nor main, so there are no circular imports.
- **Governance is never bypassed.** Every planned action runs through
  `execute_with_retry`, which applies the governance gate. CONFIRM/BLOCK signals are
  honoured: the planner will NOT auto-approve a CONFIRM-tier step.
- **Shared-engine safety.** Each Act is taken under `COMMAND_LOCK`, so a planner run
  interleaves safely with HUD/voice/Telegram conversations.
- **Self-correction is intrinsic.** A failed step lands in the scratchpad as a failed
  observation, so the next Think naturally re-plans around it. `replan_after_failure()`
  exposes the same capability to the Overnight Worker Loop (§1.1b).
"""

from __future__ import annotations

import re
import json
import asyncio
from typing import Awaitable, Callable, Optional

from modules.session_manager import COMMAND_LOCK

# Lazy/soft import: the planner degrades to a clear error rather than crashing if the
# router is unavailable.
try:
    from modules.llm_router import universal_llm_call as _default_llm_call
except Exception:  # pragma: no cover
    _default_llm_call = None

# Governance is read-only here; we only use it to clear a pending CONFIRM slot the
# engine may have opened, so a mid-plan confirmation can't dangle.
try:
    from governance_manager import governance_manager
except Exception:  # pragma: no cover
    governance_manager = None


# ════════════════════════════════════════════════════════════════════════════
# Complexity gate — decides whether a goal needs the heavy planner
# ════════════════════════════════════════════════════════════════════════════
# Connectors that signal "do X, then do Y" — a genuinely sequenced/compound goal.
_MULTISTEP_CONNECTORS = (
    " and then ", " then ", " after that", " after you", " once you ", " once you've",
    " followed by ", " and after ", "; ", " and email ", " and send ", " and save ",
    " and summarise", " and summarize", " and tell me", " and report", " and draft ",
    " and then email", " and put ", " and compile", " step 1", " first,", " firstly",
)
# Action-ish verbs; ≥2 distinct ones alongside a connector = compound work.
_ACTION_VERBS = (
    "search", "find", "research", "look up", "browse", "open", "read", "write",
    "create", "email", "send", "summarise", "summarize", "compile", "check",
    "download", "save", "draft", "generate", "fetch", "get", "list", "compare",
    "analyse", "analyze", "gather", "build", "review", "organise", "organize",
)


def should_plan(user_text: str) -> bool:
    """Return True only for clearly multi-step goals (everything else fast-paths).

    Conservative by design: a false negative just keeps the snappy single-shot
    path; a false positive would add latency to a trivial command, so we require
    BOTH a sequencing connector AND at least two distinct action verbs.
    """
    if not user_text:
        return False
    t = f" {user_text.lower().strip()} "
    if len(t) < 25:                       # very short → always fast-path
        return False

    has_connector = any(c in t for c in _MULTISTEP_CONNECTORS)
    verbs_present = {v for v in _ACTION_VERBS if v in t}

    # Numbered/enumerated plans are explicitly multi-step.
    enumerated = bool(re.search(r"\b(step\s*\d|1\.\s|2\.\s|first\b.*\bthen\b)", t))

    if enumerated:
        return True
    return has_connector and len(verbs_present) >= 2


# ════════════════════════════════════════════════════════════════════════════
# Tool catalogue the planner may compose (curated, multi-step-relevant subset)
# ════════════════════════════════════════════════════════════════════════════
_TOOL_CATALOG = """Available actions (emit EXACTLY one per step, keys "action_type" and "target"):
INFORMATION
- tavily_search: fast factual lookup. target="query"
- web_search: deeper multi-result research. target="query"
- web_search_image: target="query"
- web_browse: open and read a page. target="url or what to find"
- search_documents: search the USER's own indexed notes/files. target="query"
- memory_recall: recall stored facts about the user. target="topic"
WEB INTERACTION (a live browser session persists across steps)
- web_click: target="element_id"   - web_type: target="element_id|text"
- web_scroll: target="down|up"      - web_back: target=""   - web_close: target=""
COMMS & LIFE
- read_email / gmail_read_unread: read mail. target=""
- send_email / gmail_send: target="recipient | subject | body"
- gmail_reply: target="message_id | body"   - search_email: target="query"
- check_calendar: target="today"|"week"   - create_event: target="title | when"
- check_vitals: target=""   - morning_briefing: target=""
- telegram_send_file: send a file to the operator. target="filepath" or {"path":..,"caption":..}
CODE / FILES (headless — never GUI)
- workspace_read: target="filepath"
- workspace_write: target="filepath|file_content"
- workspace_patch: target="filepath|search|replace"
- find_file: target="name"   - create_note: target="text"   - organize_downloads: target=""
- run_autopilot: build a Figma design into code. target="figma_file_key"
GITHUB
- github_status / github_log / github_diff: target=""
- github_commit: target="message"   - github_push: target=""
OS / SYSTEM
- run_terminal_command: OS shell op. target="verb: argument"
- get_telemetry / system_status: machine state. target=""
- native_app_launcher: open an app. target="app name"   - close_app: target="app name"
- os_control: target="mute|unmute|play_pause|next_track|prev_track|lock_screen"
- play_music: target="query"
MEMORY
- remember_fact: target="Category: fact"
"""

_REACT_SYSTEM = (
    "You are J.A.R.V.I.S.'s planning core, running a ReAct loop. You pursue ONE "
    "multi-step goal by choosing a single next action at a time, observing its "
    "result, and continuing until the goal is achieved.\n\n"
    + _TOOL_CATALOG
    + "\nRULES:\n"
    "1. Output STRICT JSON only, no prose around it.\n"
    "2. To take the next step:  "
    '{\"thought\": \"why this step\", \"action\": {\"action_type\": \"...\", \"target\": \"...\"}}\n'
    "3. When the goal is fully achieved (or cannot proceed):  "
    '{\"thought\": \"...\", \"final_answer\": \"a concise, butler-toned summary for the user, Sir\"}\n'
    "4. Use the OBSERVATIONS from prior steps — do not repeat a step that already "
    "succeeded. If a step FAILED, diagnose why and try a different approach.\n"
    "5. Prefer the fewest steps that accomplish the goal. Never invent results — "
    "only state what the observations support.\n"
)


def _extract_json(raw: str) -> Optional[dict]:
    """Pull the first JSON decision object out of a ReAct Think step, via the
    shared parse spine — tolerant of code fences, prose, trailing commas and
    truncation, so a recoverable reply no longer aborts the whole plan. Preserves
    the 'thought' / 'action' / 'final_answer' keys the loop below expects."""
    from modules import action_parser
    return action_parser.extract_react_decision(raw)


async def _call_llm(messages: list, llm_call, *, json_mode: bool = True) -> str:
    """Run the (synchronous) router call off the event loop."""
    fn = llm_call or _default_llm_call
    if fn is None:
        raise RuntimeError("No LLM available to the planner (llm_router import failed).")
    return await asyncio.to_thread(
        fn, messages, 0.4, 400, False, json_mode  # temperature, max_tokens, stream, json_mode
    )


# ════════════════════════════════════════════════════════════════════════════
# The ReAct loop
# ════════════════════════════════════════════════════════════════════════════
async def run_react(
    goal: str,
    user: str,
    execute_fn: Callable[..., Awaitable],
    *,
    notify: Optional[Callable[[str, str], Awaitable[None]]] = None,
    max_steps: int = 8,
    llm_call=None,
) -> dict:
    """Pursue a multi-step goal with Think→Act→Observe.

    Args:
        goal:        the user's natural-language goal.
        user:        active user (for tone/scoping).
        execute_fn:  async (payload, return_meta, trace_id) -> meta-dict
                     (i.e. ActionEngine.execute_with_retry).
        notify:      optional async (status, message) for progress pings.
        max_steps:   hard cap on Think→Act iterations (anti-runaway).
        llm_call:    override the LLM (testing); defaults to llm_router.

    Returns: {"final_answer": str, "steps": [...], "success": bool,
              "needs_confirmation": bool}
    """
    scratchpad: list[dict] = []

    async def _ping(status: str, msg: str = "") -> None:
        if notify is not None:
            try:
                await notify(status, msg)
            except Exception:
                pass

    await _ping("planning", f"Working through a multi-step goal, Sir: {goal[:80]}")

    for step in range(max_steps):
        # ── THINK ──────────────────────────────────────────────────────────
        messages = [
            {"role": "system", "content": _REACT_SYSTEM},
            {"role": "user", "content": _render_prompt(goal, scratchpad)},
        ]
        try:
            raw = await _call_llm(messages, llm_call)
        except Exception as e:
            print(f"[PLANNER] Think step failed: {e}", flush=True)
            return _finish(scratchpad, success=False,
                           answer="I lost my train of thought mid-plan, Sir.")

        decision = _extract_json(raw)
        if not decision:
            print(f"[PLANNER] Unparseable Think output, ending plan: {raw[:120]}", flush=True)
            return await _synthesize(goal, scratchpad, user, llm_call)

        # Goal complete?
        if "final_answer" in decision and "action" not in decision:
            return _finish(scratchpad, success=True,
                           answer=str(decision.get("final_answer", "Done, Sir.")))

        action = decision.get("action")
        if not isinstance(action, dict) or not action.get("action_type"):
            # No actionable step → synthesise from what we have.
            return await _synthesize(goal, scratchpad, user, llm_call)

        thought = str(decision.get("thought", "")).strip()
        atype = str(action.get("action_type", "")).strip()
        await _ping("executing_step", f"Step {step + 1}: {atype}")
        print(f"[PLANNER] step {step+1} | thought: {thought[:80]} | act: {atype}", flush=True)

        # ── ACT (governance enforced by execute_with_retry; serialised) ──────
        try:
            async with COMMAND_LOCK:
                meta = await execute_fn(action, True, None)
        except Exception as e:
            observation = f"action raised {type(e).__name__}: {e}"
            scratchpad.append({"thought": thought, "action": action,
                               "observation": observation, "failed": True})
            continue

        result = meta.get("result", meta) if isinstance(meta, dict) else meta
        state = meta.get("state") if isinstance(meta, dict) else None
        result_str = str(result)

        # ── Governance signals ───────────────────────────────────────────────
        if result_str.startswith("GOVERNANCE_CONFIRM:"):
            # A mid-plan CONFIRM-tier step cannot be auto-approved. Clear the
            # pending slot so it can't dangle, and stop — ask the user.
            if governance_manager is not None:
                try:
                    governance_manager.cancel_pending()
                except Exception:
                    pass
            conf = result_str.split(":", 2)[1] if ":" in result_str else atype
            return _finish(
                scratchpad, success=False, needs_confirmation=True,
                answer=(f"To finish that, Sir, I need your authorisation for a "
                        f"protected step ('{conf}'). I won't run it unattended."),
            )
        if result_str.startswith("GOVERNANCE_BLOCKED:"):
            observation = f"BLOCKED by governance policy ({atype}). Cannot use this tool."
        else:
            failed = (state == "FAILED")
            observation = (("FAILED: " if failed else "") + result_str)[:800]

        scratchpad.append({
            "thought": thought, "action": action,
            "observation": observation,
            "failed": result_str.startswith("GOVERNANCE_BLOCKED:") or (state == "FAILED"),
        })

    # Step budget exhausted → synthesise the best answer from observations.
    print(f"[PLANNER] max_steps ({max_steps}) reached — synthesising.", flush=True)
    return await _synthesize(goal, scratchpad, user, llm_call)


def _render_prompt(goal: str, scratchpad: list[dict]) -> str:
    lines = [f"GOAL: {goal}", "", "OBSERVATIONS SO FAR:"]
    if not scratchpad:
        lines.append("(none yet — this is the first step)")
    else:
        for i, s in enumerate(scratchpad, 1):
            act = s.get("action", {})
            lines.append(
                f"{i}. action={act.get('action_type')}({str(act.get('target',''))[:60]}) "
                f"→ {'[FAILED] ' if s.get('failed') else ''}{str(s.get('observation',''))[:300]}"
            )
    lines.append("")
    lines.append("Decide the single next step, or declare the goal complete.")
    return "\n".join(lines)


def _finish(scratchpad: list[dict], *, success: bool, answer: str,
            needs_confirmation: bool = False) -> dict:
    return {
        "final_answer": answer,
        "steps": scratchpad,
        "success": success,
        "needs_confirmation": needs_confirmation,
    }


async def _synthesize(goal: str, scratchpad: list[dict], user: str, llm_call) -> dict:
    """Produce a final answer from the accumulated observations."""
    if not scratchpad:
        return _finish(scratchpad, success=False,
                       answer="I wasn't able to make progress on that, Sir.")
    digest = "\n".join(
        f"- {s.get('action',{}).get('action_type')}: "
        f"{'[FAILED] ' if s.get('failed') else ''}{str(s.get('observation',''))[:300]}"
        for s in scratchpad
    )
    messages = [
        {"role": "system", "content":
            "You are J.A.R.V.I.S. Summarise the outcome of a multi-step task for the "
            "user in a concise, butler-toned reply. Only state what the steps support; "
            "do not invent. Address the user as 'Sir'."},
        {"role": "user", "content": f"GOAL: {goal}\n\nSTEPS & RESULTS:\n{digest}\n\nSummary:"},
    ]
    try:
        answer = await _call_llm(messages, llm_call, json_mode=False)
    except Exception:
        answer = "I've done what I could on that, Sir."
    any_success = any(not s.get("failed") for s in scratchpad)
    return _finish(scratchpad, success=any_success, answer=answer.strip() or "Done, Sir.")


# ════════════════════════════════════════════════════════════════════════════
# §1.1b — Self-correction for the Overnight Worker Loop
# ════════════════════════════════════════════════════════════════════════════
async def replan_after_failure(
    goal: str,
    failed_step: dict,
    error: str,
    *,
    llm_call=None,
) -> list[dict]:
    """Given a failed step, ask the LLM for a NEW action plan to overcome it.

    Returns a list of {action_type, target} dicts (possibly empty if the LLM
    can't find a path). Used by the worker loop's bounded self-heal retries.
    """
    messages = [
        {"role": "system", "content":
            "You are J.A.R.V.I.S.'s recovery planner. A step toward a goal failed. "
            "Propose a NEW plan (1-4 actions) to overcome the obstacle and still reach "
            "the goal. Try a genuinely different approach than the one that failed.\n\n"
            + _TOOL_CATALOG
            + '\nOutput STRICT JSON: {"actions": [{"action_type": "...", "target": "..."}]}. '
            "If recovery is impossible, output {\"actions\": []}."},
        {"role": "user", "content":
            f"GOAL: {goal}\n"
            f"FAILED STEP: {json.dumps(failed_step)[:400]}\n"
            f"ERROR/RESULT: {str(error)[:600]}\n\n"
            "New recovery plan:"},
    ]
    try:
        raw = await _call_llm(messages, llm_call)
    except Exception as e:
        print(f"[PLANNER] replan_after_failure LLM error: {e}", flush=True)
        return []
    parsed = _extract_json(raw) or {}
    actions = parsed.get("actions", [])
    if not isinstance(actions, list):
        return []
    # Keep only well-formed action dicts.
    return [a for a in actions if isinstance(a, dict) and a.get("action_type")]
