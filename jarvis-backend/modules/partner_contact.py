r"""partner_contact.py — the butler's answer to "did she talk to you?"

A good butler says *"Madam rang, around three — nothing pressing."* He does not
recite what was discussed. He would, instantly, if she had said it was urgent.
That is the whole model (roadmap §6.7), and this module is the sentence he says.

WHAT MAKES THE DISCRETION REAL, RATHER THAN POLITE
--------------------------------------------------
The answer is built from `contact_events`, a store that holds **who, when, and
one urgency bit, and has no content column at all**. So there is no code path —
not a bug, not a prompt-injection, not a future edit by someone who missed the
point — by which her words can reach the owner through this action. The data is
not withheld; it was never put there.

"Discreet" enforced by a careful `format()` string is one refactor away from
leaking. "Discreet" enforced by the absence of a column is a property of the
schema, and the harness asserts it by writing a message with a rare marker word
and proving the marker exists nowhere in the store.

The one place her text is touched is `assess_urgency()` below — in memory, at
the moment the message arrives, producing a single boolean. `note_contact()`
passes only that boolean onward; `contact_events.record()` has no parameter
through which text could arrive even if a caller tried.

Contrast `summarize_partner_chat`, which reads and returns her words in full.
That action still exists and is the **explicit override**: the owner asking
"what did she say" is a different, more deliberate request than "did she call".
This module answers the second question only, and answering it must never
partially answer the first.

URGENCY, AND WHY IT IS ASSESSED AT LOG TIME
-------------------------------------------
Assessing at READ time would mean decrypting and re-reading her messages every
time the owner asks — the exact thing this feature exists not to do. So the
judgement is made ONCE, when the message arrives and JARVIS is already reading
it to reply to her, and only the resulting bit is stored. Roadmap §6.7 calls
that record "the durable artefact".

The scan is a deterministic keyword match, not an LLM call: no new dependency,
no token cost on her every message, and — the point — it is harnessable, so
"does this phrase raise the flag" has an answer that does not drift between
model versions.

It is tuned to **fail toward surfacing the flag**, per §6.7: a false alarm costs
the owner a phone call, a missed emergency costs more. So "important" alone is
enough, even though it is a common word. The cost of that asymmetry is paid in
false positives, and a false positive still reveals NO content — it says only
"she flagged it as important", which is the same shape as the true answer.

Rows written before this module existed have no bit, which reads as not-urgent.
Backfilling would mean reading her old messages, so it is deliberately not done.
"""

from __future__ import annotations

import re
from datetime import datetime

__all__ = [
    "assess_urgency", "answer", "note_contact", "status_for", "URGENT_TERMS",
    "NO_RECORD", "no_record_text", "locked_text", "ACTION_TYPE",
]

#: The engine action name. Deliberately not on `VIP_GUEST_ALLOWED_ACTIONS`, so
#: `action_engine.tier_allows` refuses every non-admin caller before dispatch —
#: one partner can never ask about another.
ACTION_TYPE = "partner_contact_status"

#: Phrases that raise the flag. Matched on word boundaries against the
#: lower-cased message, so "important" hits but "importantly-sized" does not.
#:
#: Both scripts, because she writes Benglish (roman-script Bengali) as often as
#: English and an English-only list would silently miss the urgent half of how
#: she actually types.
#:
#: ⚠️ KAUSTAV: the Benglish half is my best guess at the words SHE uses. You are
#: the one who knows — correct this list rather than living with it.
URGENT_TERMS: tuple[str, ...] = (
    # explicit escalation
    "urgent", "urgently", "emergency", "asap", "immediately", "right away",
    "right now", "important", "serious", "please call", "call me", "phone me",
    "need you", "need to talk", "need to speak", "come home", "come back",
    "where are you", "are you ok", "are you okay", "help me", "please help",
    # the ones that are an emergency regardless of phrasing
    "hospital", "accident", "ambulance", "police", "fire", "doctor",
    # Benglish — roman-script Bengali
    "joruri", "taratari", "bipod", "dorkar", "khub dorkar", "ekhuni",
    "phone koro", "phone kor", "call koro", "call kor", "bari esho",
)

#: Sentinel meaning "the record does not exist", which is NOT the same claim as
#: "she did not make contact". Kept distinct on purpose — see `no_record_text`.
NO_RECORD = "no_record"

_TERM_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in URGENT_TERMS) + r")(?![a-z0-9])",
    re.IGNORECASE,
)


def assess_urgency(text: str | None) -> bool:
    """Did she flag this as needing him? Deterministic, content stays here.

    The text goes in, one bit comes out, and the text is not retained by this
    function. Called once at log time.
    """
    if not text:
        return False
    return bool(_TERM_RE.search(str(text)))


# ── phrasing ─────────────────────────────────────────────────────────────────

def _clock(dt: datetime) -> str:
    """'3pm' / '3:30pm' — a butler's approximation, never a precise timestamp.

    Deliberately coarse. "She messaged at 15:12:44" is surveillance phrasing;
    "around 3pm" is what a person in the house would tell you.
    """
    hour, minute = dt.hour, dt.minute
    if minute >= 45:
        hour = (hour + 1) % 24
        minute = 0
    elif 15 <= minute < 45:
        minute = 30
    else:
        minute = 0
    label = "am" if hour < 12 else "pm"
    twelve = hour % 12 or 12
    return f"{twelve}:30{label}" if minute == 30 else f"{twelve}{label}"


def _day_phrase(then: datetime, now: datetime) -> str:
    days = (now.date() - then.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"on {then.strftime('%A')}"
    return f"on {then.strftime('%d %B').lstrip('0')}"


def _parse(ts: str | None) -> datetime | None:
    """UTC ISO string → local-aware datetime. Unparseable rows are skipped
    rather than guessed at — a wrong time is worse than no time."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return dt.astimezone() if dt.tzinfo else dt.astimezone()


def no_record_text(display: str, flag: str, honor: str = "Sir") -> str:
    """The honest answer when no record could exist.

    NOT "no, she didn't" — that would be a confident claim built on an empty
    table, and the owner cannot tell the two apart. Same discipline as the
    locked-keystore rule: "I can't see" must never be rendered as "there is
    nothing there".
    """
    return (f"I can't tell you either way, {honor} — I keep no record of when "
            f"{display} gets in touch, so I've nothing to check. "
            f"({flag} is switched off.)")


def locked_text(display: str, honor: str = "Sir") -> str:
    """A sealed store that will not open is not the same as a quiet partner.

    Same rule as `no_record_text`, one layer down: the rows exist and cannot be
    read. Saying "no, she didn't call" here would be a confident answer produced
    by a failure, which is the exact shape of bug C#11a's silent-empty-read rule
    was written to prevent.
    """
    return (f"I can't reach the record of {display}'s messages, {honor} — the "
            f"store is sealed and the key isn't available, so I can't tell you "
            f"either way.")


def answer(display: str, rows, *, now: datetime | None = None,
           honor: str = "Sir") -> str:
    """The butler's line, built from metadata alone.

    `rows` is `partner_log.contact_metadata()` output — dicts of `timestamp`
    and `urgent`, newest first, and **nothing else**. There is no content
    parameter, which is the guarantee.
    """
    now = now.astimezone() if now else datetime.now().astimezone()

    stamped = []
    for r in rows or []:
        dt = _parse(r.get("timestamp"))
        if dt is not None:
            stamped.append((dt, bool(r.get("urgent"))))
    stamped.sort(key=lambda p: p[0], reverse=True)

    today = [p for p in stamped if p[0].date() == now.date()]

    if not today:
        if not stamped:
            return f"No, {honor} — nothing from {display} at all."
        last, _ = stamped[0]
        return (f"No, {honor} — nothing from {display} today. "
                f"Last I heard from her was {_day_phrase(last, now)}, "
                f"around {_clock(last)}.")

    first_dt = today[-1][0]
    urgent = any(flag for _, flag in today)

    when = f"around {_clock(first_dt)}"
    extra = len(today) - 1
    if extra:
        more = {1: "once", 2: "twice"}.get(extra, f"{extra} times")
        when += f" and {more} more since"

    if urgent:
        # Semicolon, not a third "and" — the urgency is the part he needs to
        # hear, and it should not arrive at the end of a list.
        return (f"Yes, {honor} — {display} messaged {when}; she flagged it as "
                f"important. You may want to call her.")
    return f"Yes, {honor} — {display} messaged {when}. Nothing urgent."


def note_contact(partner_slot: str, message_text: str | None, *,
                 when: datetime | None = None, env=None,
                 db_path: str | None = None) -> bool:
    """One partner message arrived ⇒ record ONE content-free contact event.

    This is the only place her text and the store meet, and they do not: the
    text is scanned here, in memory, and only the resulting boolean is handed to
    `contact_events.record()`. Nothing downstream of this line has the content.

    Called from `run_remote_command`. Returns False rather than raising when
    recording is off or the write fails — a bookkeeping fault must never break
    her conversation.
    """
    from modules import contact_events

    return contact_events.record(
        partner_slot, urgent=assess_urgency(message_text),
        when=when, env=env, db_path=db_path)


def status_for(target, *, honor: str = "Sir", now: datetime | None = None,
               db_path: str | None = None) -> str:
    """Resolve a name and answer for that ONE partner. The whole action.

    Lives here rather than in `action_engine` so the harness can drive the real
    behaviour against a temp database instead of asserting on source text. That
    distinction is not academic: `f84f644` shipped a partner feature whose
    grep-level tests could not tell "refused" from "nothing happened", and it
    never once worked in production.
    """
    from modules import contact_events, partner_registry

    name = str(target or "").strip()
    res = partner_registry.resolve(name)
    if not res.ok and res.reason != partner_registry.REASON_NOT_REGISTERED:
        # Ambiguous ("her", "them") or unknown: refuse rather than guess WHICH
        # partner is being asked about. Guessing here would answer a question
        # about one person using another person's record.
        #
        # NOT_REGISTERED is deliberately allowed through, exactly as
        # `_summarize_partner_chat` allows it: a missing TELEGRAM_*_ID means
        # JARVIS cannot WRITE to her, but her past messages are already filed
        # under the slot and reading them needs no address.
        return res.refusal_text()

    slot = res.slot
    display = res.display_name or partner_registry.display_for(slot or "")

    if not contact_events.enabled():
        return no_record_text(display, contact_events.ENV_FLAG, honor)

    try:
        rows = contact_events.recent(slot, db_path=db_path)
    except contact_events.MemoryLockedError:
        # Deliberately caught HERE and turned into an honest sentence rather
        # than left to raise: the owner asked a question and deserves an answer
        # that distinguishes "she didn't" from "I can't look".
        return locked_text(display, honor)

    return answer(display, rows, now=now, honor=honor)
