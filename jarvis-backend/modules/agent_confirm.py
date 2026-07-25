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
    """The sentence the HUD shows. Short, specific, and names the real target."""
    shown = (target or "").strip()
    if len(shown) > 120:
        shown = shown[:117] + "…"
    if shown:
        return f"JARVIS wants to run {tool} on {shown} — approve?"
    return f"JARVIS wants to run {tool} — approve?"
