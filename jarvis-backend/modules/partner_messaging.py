r"""partner_messaging.py — propose-and-approve for a message to a real person.

Sending a private message to someone's girlfriend is not an action to get
almost-right. Three rules live here, all of them provable without a network:

1. **The owner approves the exact artifact.** `confirm_prompt()` reads back the
   RESOLVED NAME and the FULL message text verbatim — no truncation, no
   paraphrase, no "…". `agent_confirm.question_for` clips targets at 120 chars,
   which is fine for a filename and wrong for the thing being said to a person,
   so partner sends build their own prompt.

2. **A refusal is terminal.** `SendGuard` remembers a declined (recipient, text)
   pair for a few minutes and refuses it at the ENGINE, which is the one place
   every route funnels through — voice, HUD command line, Telegram, a second
   action in the same LLM reply. This is the live bug-#4 discipline: declining
   must not get the owner asked again thirty seconds later by another mechanism.
   The guard also refuses a DUPLICATE staging of a send already awaiting
   approval, so one reply emitting the same send twice cannot produce two
   prompts (or two messages from one "yes").

3. **Nothing autonomous.** There is no send path here at all — this module only
   parses, phrases, and remembers. The engine action does the sending, and it
   only ever runs after governance has been satisfied.

Pure logic (no I/O, injectable clock), so the harness proves the refusals.
"""

from __future__ import annotations

import hashlib
import os
import time

ACTION_SEND = "message_partner"
ACTION_SUMMARISE = "summarize_partner_chat"

#: How long a declined send stays refused. Long enough that an LLM retry or a
#: fallback path cannot slip through behind the refusal; short enough that the
#: owner can deliberately change his mind without a restart.
DENY_TTL_S = float(os.getenv("JARVIS_PARTNER_DENY_TTL_S", "300") or 300)

#: How long a staged-but-unanswered send blocks an identical re-staging. Matches
#: governance's own 90 s confirmation TTL.
STAGE_TTL_S = 90.0

REFUSED_DENIED = "already_declined"
REFUSED_DUPLICATE = "already_awaiting_approval"


def parse_target(target) -> tuple[str, str]:
    """Pull (recipient_name, message_text) out of an action target.

    Accepts the shapes an LLM actually produces:

        {"to": "girlfriend", "message": "have you eaten?"}
        {"recipient": "...", "text": "..."} / {"name": ..., "body": ...}
        "girlfriend|have you eaten?"

    Returns ("", "") for anything unusable — the caller refuses rather than
    inventing either half.
    """
    if isinstance(target, dict):
        name = str(target.get("to") or target.get("recipient") or target.get("name")
                   or target.get("partner") or target.get("who") or "").strip()
        body = str(target.get("message") or target.get("body") or target.get("text")
                   or target.get("content") or "").strip()
        return name, body

    raw = str(target or "").strip()
    if not raw:
        return "", ""
    if "|" in raw:
        name, _, body = raw.partition("|")
        return name.strip(), body.strip()
    # A bare string is a recipient with no message, never a message with no
    # recipient — guessing the addressee is the one mistake worth preventing.
    return raw, ""


def normalise_body(body: str) -> str:
    return " ".join(str(body or "").split())


def _key(slot: str, body: str) -> str:
    digest = hashlib.sha256(normalise_body(body).lower().encode("utf-8")).hexdigest()[:32]
    return f"{(slot or '').strip()}:{digest}"


class SendGuard:
    """Remembers declined and in-flight partner sends. Fake-clock testable."""

    def __init__(self, deny_ttl_s: float = DENY_TTL_S,
                 stage_ttl_s: float = STAGE_TTL_S, clock=None):
        self.deny_ttl_s = float(deny_ttl_s)
        self.stage_ttl_s = float(stage_ttl_s)
        self._clock = clock or time.monotonic
        self._denied: dict[str, float] = {}
        self._staged: dict[str, float] = {}

    # -- recording ---------------------------------------------------------- #

    def note_staged(self, slot: str, body: str) -> None:
        """A send is now awaiting the owner's approval."""
        self._staged[_key(slot, body)] = self._clock()

    def note_denied(self, slot: str, body: str) -> None:
        """The owner said no. Nothing may re-attempt this send."""
        k = _key(slot, body)
        self._denied[k] = self._clock()
        self._staged.pop(k, None)

    def note_sent(self, slot: str, body: str) -> None:
        """Approved and delivered — clear the in-flight mark."""
        self._staged.pop(_key(slot, body), None)

    # -- checking ----------------------------------------------------------- #

    def refusal(self, slot: str, body: str, *, approved: bool = False) -> str | None:
        """Why this send must not proceed, or None if it may.

        `approved=True` means the owner has just authorised THIS staging and the
        engine is running the post-confirmation invocation. The in-flight mark
        belongs to the prompt he answered, so it must not refuse its own
        execution — that is one approval being spent, not a second staging.

        The DENIED half is checked in both modes, and deliberately first: a
        refusal outranks an approval sentinel, which is what makes a denial
        terminal on every route including the post-approval one.

        Defaults to False so any caller that does not know about approval gets
        the strict behaviour.
        """
        k = _key(slot, body)
        now = self._clock()

        t = self._denied.get(k)
        if t is not None:
            if (now - t) <= self.deny_ttl_s:
                return REFUSED_DENIED
            self._denied.pop(k, None)

        if approved:
            # The prompt has been answered either way; retire its mark so a
            # failed delivery does not block a fresh proposal for 90 seconds.
            self._staged.pop(k, None)
            return None

        t = self._staged.get(k)
        if t is not None:
            if (now - t) <= self.stage_ttl_s:
                return REFUSED_DUPLICATE
            self._staged.pop(k, None)
        return None

    def clear(self) -> None:
        self._denied.clear()
        self._staged.clear()


#: Process-wide guard. The engine consults it on every partner send, so the
#: refusal holds across every route into the engine.
guard = SendGuard()


def refusal_text(reason: str, display_name: str = "them") -> str:
    if reason == REFUSED_DENIED:
        return (f"You declined that message to {display_name}, Sir. I won't "
                "re-attempt it — tell me afresh if you've changed your mind.")
    if reason == REFUSED_DUPLICATE:
        return (f"That message to {display_name} is already waiting on your "
                "authorisation, Sir — one approval, one send.")
    return f"I won't send that message to {display_name}, Sir."


def confirm_prompt(display_name: str, body: str, honorific: str = "Sir") -> str:
    """The read-back. Names the resolved recipient and quotes the WHOLE message.

    Deliberately unabbreviated: the owner is authorising these exact words
    leaving his account, so a summary of them is not consent.
    """
    text = normalise_body(body)
    return (f"Authorisation required, {honorific}. I am ready to send this to "
            f"{display_name}, verbatim:\n\n“{text}”\n\n"
            "Say 'confirm' to send it, or 'cancel' to drop it.")


def format_history(rows: list, display_name: str, disclosure: str) -> str:
    """Render logged partner messages for the synthesis layer, disclosure first.

    The disclosure leads so it survives summarisation — the owner should be told
    where this knowledge came from even if the model shortens everything else.
    """
    if not rows:
        return ""
    lines = [f"[PARTNER CHAT LOG — {display_name}] {disclosure}"]
    for r in rows:
        stamp = str(r.get("timestamp") or "")[:16].replace("T", " ")
        lines.append(f"- [{stamp}] {r.get('content', '')}")
    return "\n".join(lines)
