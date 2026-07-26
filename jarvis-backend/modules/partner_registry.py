r"""partner_registry.py — name → registered Telegram id, allowlist ONLY.

The failure mode this module exists to prevent is social, not technical: a
private message delivered to the wrong person. So resolution is *closed*:

  * The model may only name a partner. It can never supply, construct, or
    influence a chat id — `resolve()` rejects anything containing digits before
    it looks at the alias table, so "send to 123456789" is refused rather than
    dialled.
  * Only two slots exist, each backed by an environment id that the operator
    set himself: `TELEGRAM_GF_ID` and `TELEGRAM_BROTHER_ID` (the same ids
    telegram_bot.py already recognises as VIP guests).
  * Unknown, generic, or ambiguous names are REFUSED, never guessed. "her",
    "my partner", "someone", or a name matching both slots all come back with
    a reason the caller can say out loud. A wrong recipient is a worse outcome
    than a failed send.
  * A slot whose env id is unset resolves as `not_registered` — the capability
    simply does not exist for that person.

Pure and dependency-free (env is injectable), so test_partner_messaging.py can
prove every refusal path with no Telegram, no network, and no real ids.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# ── Slots ────────────────────────────────────────────────────────────────────
# Keyed by slot name; `env` is the ONLY source of a real chat id.
SLOTS: dict[str, dict] = {
    "gf": {
        "env": "TELEGRAM_GF_ID",
        "display_name": "Mousumi",
        "relation": "girlfriend",
        "honorific": "Madam",
        # The identity string telegram_bot.py assigns this id (used to map an
        # inbound message back to a slot).
        "user": "MOUSUMI",
    },
    "brother": {
        "env": "TELEGRAM_BROTHER_ID",
        "display_name": "Kinshuk",
        "relation": "brother",
        "honorific": "Mr. Kinshuk",
        "user": "KINSHUK",
    },
}

# ── Aliases the operator actually says ───────────────────────────────────────
# Role words AND first names (his choice, 2026-07-26). Matched as whole words
# against the normalised name, so "my girlfriend" and "girlfriend" both land on
# the same slot without "girlfriends of the year" style accidents.
ALIASES: dict[str, str] = {
    "girlfriend": "gf",
    "gf": "gf",
    "mousumi": "gf",
    "brother": "brother",
    "kinshuk": "brother",
}

# Words that name *a* person without identifying WHICH — never resolved.
AMBIGUOUS_WORDS = frozenset({
    "her", "him", "them", "they", "she", "he",
    "partner", "someone", "somebody", "everyone", "all", "both",
    "family", "friend", "people",
})

# Refusal reasons (stable strings — logged and asserted, not user-facing text).
REASON_OK = "ok"
REASON_RAW_ID = "raw_id_rejected"
REASON_UNKNOWN = "unknown_recipient"
REASON_AMBIGUOUS = "ambiguous_recipient"
REASON_NOT_REGISTERED = "not_registered"
REASON_EMPTY = "no_recipient"

_WORD_RE = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Resolution:
    ok: bool
    reason: str
    slot: str | None = None
    partner_id: int | None = None
    display_name: str | None = None
    relation: str | None = None

    def refusal_text(self) -> str:
        """What JARVIS says when he will not send. Honest about which failure it
        was — "I don't know who that is" and "she isn't set up" are different
        problems for the operator to fix."""
        known = ", ".join(sorted({v["relation"] for v in SLOTS.values()}))
        if self.reason == REASON_RAW_ID:
            return ("I won't message a raw chat id, Sir. Name the person and I'll "
                    f"use their registered account ({known}).")
        if self.reason == REASON_AMBIGUOUS:
            return ("I'm not certain who you mean, Sir, and I won't guess with a "
                    "private message. Name them — "
                    f"{known}.")
        if self.reason == REASON_NOT_REGISTERED:
            return (f"{self.display_name or 'That person'} has no registered Telegram "
                    "account on file, Sir, so I have no way to reach them.")
        if self.reason == REASON_EMPTY:
            return f"Who should I send that to, Sir? I can reach {known}."
        return ("I don't have that person on file, Sir, and I won't guess with a "
                f"private message. I can reach {known}.")


def _read_id(env_key: str, env) -> int | None:
    raw = str((env or os.environ).get(env_key, "") or "").strip()
    if not raw or not raw.lstrip("-").isdigit():
        return None
    return int(raw)


def slots_for(name: str) -> set[str]:
    """Every slot the given name matches (whole-word alias match)."""
    words = set(_WORD_RE.findall((name or "").lower()))
    return {ALIASES[w] for w in words if w in ALIASES}


def resolve(name, env=None) -> Resolution:
    """Resolve an operator-supplied NAME to a registered partner.

    `name` must be text. An int, or text containing any digit, is treated as an
    attempt to supply a chat id and refused — the model never picks the address.
    """
    if isinstance(name, bool) or isinstance(name, (int, float)):
        return Resolution(False, REASON_RAW_ID)

    raw = str(name or "").strip()
    if not raw:
        return Resolution(False, REASON_EMPTY)
    if any(ch.isdigit() for ch in raw):
        return Resolution(False, REASON_RAW_ID)

    matched = slots_for(raw)
    if len(matched) > 1:
        return Resolution(False, REASON_AMBIGUOUS)
    if not matched:
        words = set(_WORD_RE.findall(raw.lower()))
        if words & AMBIGUOUS_WORDS:
            return Resolution(False, REASON_AMBIGUOUS)
        return Resolution(False, REASON_UNKNOWN)

    slot = next(iter(matched))
    meta = SLOTS[slot]
    pid = _read_id(meta["env"], env)
    if pid is None:
        return Resolution(False, REASON_NOT_REGISTERED, slot=slot,
                          display_name=meta["display_name"],
                          relation=meta["relation"])
    return Resolution(True, REASON_OK, slot=slot, partner_id=pid,
                      display_name=meta["display_name"], relation=meta["relation"])


def slot_for_user(user: str) -> str | None:
    """Map a channel identity ("MOUSUMI") back to a slot — used when an inbound
    partner message arrives and has to be filed under the right person."""
    u = (user or "").strip().upper()
    for slot, meta in SLOTS.items():
        if meta["user"] == u:
            return slot
    return None


def registered_slots(env=None) -> list[str]:
    """Slots that actually have an id configured right now."""
    return [s for s, meta in SLOTS.items() if _read_id(meta["env"], env) is not None]


def display_for(slot: str) -> str:
    meta = SLOTS.get(slot or "", {})
    name = meta.get("display_name") or (slot or "unknown")
    rel = meta.get("relation")
    return f"{name} ({rel})" if rel else name
