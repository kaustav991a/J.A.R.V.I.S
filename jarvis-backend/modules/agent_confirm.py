"""agent_confirm.py — "JARVIS wants to run X, approve?" across the HUD gap.

Agentic core, phase 4. When the agent loop hits a CONFIRM-tier tool and the
owner is AT THE DESK, the right answer is not to bail out and message his phone —
he is sitting there watching. So the loop pauses, the HUD asks, and the loop
continues in place the moment he answers.

The mechanics have to bridge two directions that do not naturally meet:

  * outbound is easy — `socket_manager` already broadcasts to the HUD;
  * inbound does not exist. Nothing reads client→server WebSocket frames (the
    voice loop owns that handler and blocks inside it), which is why
    click-to-talk shipped as `POST /api/listen`. So the answer arrives as
    `POST /api/agent/confirm` and lands here.

This module is the meeting point: a pending confirmation is an `asyncio.Future`
keyed by `confirmation_id`; the loop awaits it, the endpoint resolves it.

Three rules:

1. **It always ends.** A prompt nobody answers times out and resolves as a
   REFUSAL, never as an approval and never as a hang. An agent run must not be
   able to park forever holding a conversation slot.
2. **One id, one answer.** Resolving twice is a no-op, so a double-clicked HUD
   button (or a POST racing a voice "yes") cannot approve two different actions.
3. **Ids are unguessable and never reused** — `secrets.token_hex`, so an
   approval can only come from something that was actually shown the prompt.

Standard library only (asyncio, secrets, time), no global state beyond one
process-wide registry, and clock-injectable so the whole thing is harnessable
with no HUD and no HTTP.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_TTL_S = 120.0        # generous: he is at the desk, not being timed

APPROVED = "approved"
DENIED = "denied"
EXPIRED = "expired"
CANCELLED = "cancelled"


@dataclass
class Pending:
    """One outstanding question, and the Future the loop is parked on."""

    id: str
    tool: str
    target: str
    question: str
    created: float
    future: Any = field(repr=False, default=None)
    resolution: str | None = None


class ConfirmRegistry:
    """Process-wide pending confirmations, keyed by confirmation_id."""

    def __init__(self, ttl_s: float = DEFAULT_TTL_S,
                 clock: Callable[[], float] = time.monotonic):
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._pending: dict[str, Pending] = {}

    # -- loop side --------------------------------------------------------- #

    def open(self, tool: str, target: str, question: str,
             cid: str | None = None) -> Pending:
        """Register a question and return it (its Future is not yet awaited)."""
        cid = cid or secrets.token_hex(8)
        p = Pending(id=cid, tool=tool, target=target, question=question,
                    created=self._clock(),
                    future=asyncio.get_running_loop().create_future())
        self._pending[cid] = p
        return p

    async def wait(self, pending: Pending, timeout: float | None = None) -> str:
        """Await the answer. Returns APPROVED / DENIED / EXPIRED / CANCELLED.

        Timing out resolves as a REFUSAL: an unanswered prompt is not consent,
        and leaving the run parked would hold the conversation open indefinitely.
        """
        ttl = self.ttl_s if timeout is None else timeout
        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), ttl)
        except asyncio.TimeoutError:
            pending.resolution = EXPIRED
            return EXPIRED
        finally:
            self._pending.pop(pending.id, None)

    # -- answer side (the POST endpoint, or a voice yes/no) ---------------- #

    def resolve(self, cid: str, approved: bool) -> bool:
        """Answer a pending confirmation. False if unknown or already answered."""
        p = self._pending.get(cid)
        if p is None or p.future is None or p.future.done():
            return False
        p.resolution = APPROVED if approved else DENIED
        p.future.set_result(p.resolution)
        return True

    def cancel_all(self, reason: str = CANCELLED) -> int:
        """Drop every outstanding question — e.g. the session ended.

        Cancelling resolves as a refusal for the same reason a timeout does.
        """
        n = 0
        for cid, p in list(self._pending.items()):
            if p.future is not None and not p.future.done():
                p.resolution = reason
                p.future.set_result(reason)
                n += 1
            self._pending.pop(cid, None)
        return n

    # -- inspection --------------------------------------------------------- #

    def get(self, cid: str) -> Pending | None:
        return self._pending.get(cid)

    def outstanding(self) -> list[dict]:
        """What the HUD would need to re-render its prompt after a reload."""
        return [{"confirmation_id": p.id, "tool": p.tool, "target": p.target,
                 "question": p.question, "age": self._clock() - p.created}
                for p in self._pending.values()]

    def sweep(self) -> int:
        """Expire anything past its TTL that nothing is awaiting any more."""
        n = 0
        for cid, p in list(self._pending.items()):
            if (self._clock() - p.created) > self.ttl_s:
                if p.future is not None and not p.future.done():
                    p.resolution = EXPIRED
                    p.future.set_result(EXPIRED)
                self._pending.pop(cid, None)
                n += 1
        return n


#: The process-wide registry the API endpoint and the loop share.
confirms = ConfirmRegistry()


def question_for(tool: str, target: str) -> str:
    """The HEADLINE the HUD shows. Short, specific, and names the real target.

    Deliberately still capped at 120 characters — it is one line in a trace
    panel. What the owner APPROVES on is `describe_arguments` below; this is the
    label above it.
    """
    shown = (target or "").strip()
    if len(shown) > 120:
        shown = shown[:117] + "…"
    if shown:
        return f"JARVIS wants to run {tool} on {shown} — approve?"
    return f"JARVIS wants to run {tool} — approve?"


#: How much of one field the prompt shows. Generous: the whole point is that the
#: owner can read what he is approving, and a mail body or a file write is the
#: case that matters. Still bounded — a 40 000-line file write must not push the
#: buttons off the screen.
FIELD_PREVIEW = 600


def describe_arguments(arguments: dict | None, limit: int = FIELD_PREVIEW) -> list[dict]:
    """The approval prompt's BODY: the model's own arguments, labelled.

    WHY THIS EXISTS — found 2026-08-16, pre-Electron review, finding 15.

    The HUD rendered `question` and nothing else, and `question_for` truncates at
    120 characters. For `gmail_send` the target is
    `{"to": …, "subject": …, "body": …}`, so the prompt read

        JARVIS wants to run gmail_send on {"to": "x@y.com", "subject": "Invoice
        for August", "body": "Hi Rajat, I've attach…  — approve?

    Recipient and subject survived. The BODY did not. The owner was approving a
    recipient, not a message — with APPROVE autofocused and `Y` bound to it. Same
    for `workspace_write` (`path|content`) and `edit_file`: about a hundred
    characters of whatever is being written.

    `gmail_send` was already recorded as owed: "It is CONFIRM tier, so a human
    approves every send and that approval IS the control. Check that the
    confirmation prompt actually reads back the recipient — if it does not, the
    control is theatre." The recipient does read back, so it is not theatre. It
    was half of one.

    Built from the model's ARGUMENTS, never by re-parsing the composed target.
    The target is a pipe- or colon-joined string, and re-splitting it here would
    add a fourth place that has to agree about the separator — this file already
    records what that costs (`_mail_target`, `_git_commit_precondition`).
    """
    rows: list[dict] = []
    for key, value in (arguments or {}).items():
        text = value if isinstance(value, str) else json_ish(value)
        truncated = len(text) > limit
        rows.append({
            "label": str(key),
            "value": (text[:limit] + "…") if truncated else text,
            "truncated": truncated,
            "full_length": len(text),
        })
    return rows


def arguments_text(arguments: dict | None, limit: int = FIELD_PREVIEW) -> str:
    """`describe_arguments`, rendered for a channel that cannot draw rows.

    Finding 15 gave the HUD frame the model's real arguments. The AWAY path —
    the one that exists precisely because the owner is not at the HUD — carried
    them on a frame he is not looking at, while the sentence that reached his
    phone still held only the 120-character headline. So the phone approval was
    still the half-control finding 15 describes, one door over (C1's neighbour,
    2026-08-16).

    Built from `describe_arguments` rather than beside it: one cap, one set of
    labels, one place to change them.
    """
    rows = describe_arguments(arguments, limit)
    if not rows:
        return ""
    lines = []
    for row in rows:
        suffix = (f" (+{row['full_length'] - limit} more characters)"
                  if row["truncated"] else "")
        lines.append(f"{row['label']}: {row['value']}{suffix}")
    return "\n".join(lines)


def json_ish(value: Any) -> str:
    """A non-string argument as something a person can read."""
    import json
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
