"""C#11a Step 4, Phase 3 — the governed sink: where a drained fact becomes memory.

Phases 1 and 2 built a queue that opens only on this desk and delivers exactly
once. They stopped one step short on purpose: nothing was written. This module
is that step, and it is the only one.

    drain opens the seal  ->  governed_write(payload)  ->  memory_manager
                                      |
                       shape -> identity -> GOVERNANCE -> unlocked store
                                      |
                              any refusal = dead-letter
                              any fault   = HELD, retried next connect

WHY THE GATE IS NOT OPTIONAL HERE
---------------------------------
`crypto_box_seal` is ANONYMOUS. It proves a record can only be opened by this
desk; it proves nothing about who wrote it. The `who` and `tier` inside a
payload are therefore claims, not credentials — a compromised Render dyno could
seal anything it liked to the public half it already holds. Add to that the fact
that this write is UNATTENDED by definition (it exists because the operator was
away), and you have precisely the category the governance engine was built for.
So governance runs on every drained record, before any write, with no fast path
around it. `fact_drain` itself cannot reach memory: it imports no store, and its
only outbound call is the sink installed here.

THREE OUTCOMES, AND THE DIFFERENCE MATTERS
------------------------------------------
  * accepted        -> True/False out of `extract_and_persist` (False = nothing
                       new; a duplicate under the blind index, or a turn with no
                       fact in it — same as live).
  * REFUSED         -> raise a `fact_seal.FactSealError`. The drain dead-letters
                       the sealed envelope, ledgers it and acks it. The record is
                       KEPT for inspection; it is never written and never
                       silently dropped.
  * FAULTED         -> raise anything else. The drain acks nothing, so the record
                       stays in the cloud outbox and comes back on the next
                       connect. A locked key store must cost a retry, not a fact.

The third outcome only became real on 2026-08-16 (review finding M1). The
extractor reported its own failures — a missing key, a 429 across every rotation
key, a timeout, an unparseable reply — by returning `[]`, which is also how it
says "this turn had no fact in it". So a rate limit took the FIRST road, not the
third: nothing written, ledgered STORED, acked, and the cloud dropped the sealed
original for good. `strict=True` below is what separates them.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
  * It does not call `brain.extract_and_store_memory`. That wrapper swallows
    every exception on purpose — correct for a live turn, fatal here, where a
    locked store would silently eat the whole backlog. It calls the same
    `memory_manager.extract_and_persist` that wrapper delegates to, so the
    extraction and the attribution are unchanged, and faults propagate.
  * It does not run `action_engine.tier_allows`. That gate answers "may this
    CALLER INVOKE this action"; a memory extraction is not invoked by the caller,
    live or drained — `main.py` fires it for every recognised identity,
    partners included, deliberately outside the action pipeline. Applying it here
    would mean the queue stored LESS than the live path, silently, for exactly
    one person. The identity check below is the fail-closed gate that does apply:
    an unrecognised `who` or `tier` is refused outright. `tier_allows` is left
    exactly as it is, and `test_fact_governance.py` pins that it stays that way.
  * It does not write a partner transcript. `JARVIS_LOG_PARTNER_CHATS` governs a
    VERBATIM store, and a drained record's `who` is unauthenticated (see above),
    so the drain writes the extraction and nothing else. Strictly less than the
    live path would, never more — the queue must not become a route around the
    partner policy.
"""

from __future__ import annotations

from modules import fact_seal

# The existing governed action for "store this fact" (governance.json: AUTO).
# Reused rather than invented: a new action_type would need a new rule, and a
# rule written to make a feature fit is how a safety spine rots.
ACTION_TYPE = "remember_fact"

# Telegram's own hard limit is 4096 characters, so a longer `user_text` did not
# come from a chat — it came from something generating records. Refused.
MAX_FACT_CHARS = 4096

# The identity `memory_manager` and `fact_outbox` both default to.
OWNER = "KAUSTAV"

# The tier strings `action_engine` defines. An unknown or missing tier is not a
# smaller problem than a wrong one: fail closed.
KNOWN_TIERS = frozenset({"admin", "vip_guest"})

_installed = False


class GovernanceRefusedError(fact_seal.FactSealError):
    """Governance did not pass this write.

    A FactSealError subclass so the drain dead-letters it down the same path a
    record that would not open takes — refused and unopenable are both "this
    record is not becoming a memory, and we are keeping it to say so".
    """


def recognised_users() -> frozenset:
    """Identities this desk will accept a drained fact for.

    Derived from `partner_registry.SLOTS` rather than restated, so adding a
    person is one edit in the place that already owns the roster.
    """
    from modules import partner_registry
    return frozenset({OWNER} | {
        str(meta.get("user") or "").upper()
        for meta in partner_registry.SLOTS.values()
        if meta.get("user")
    })


# -- the gates, in order -----------------------------------------------------

def _validate(payload: object) -> tuple[str, str, str]:
    """Shape + identity. Returns (who, tier, text). Refuses, never repairs.

    None of these messages quote a payload VALUE, and that is deliberate: a
    refusal is written verbatim into the dead-letter file next to the sealed
    envelope, so a reason that echoed `who` or `user_text` would put in the clear
    exactly what the seal exists to keep out. Lengths, types and field names are
    enough to diagnose from, and the record itself is right there for whoever
    holds the key.
    """
    if not isinstance(payload, dict):
        raise fact_seal.MalformedRecordError(
            f"payload is {type(payload).__name__}, not a record")

    if payload.get("v") != fact_seal.RECORD_VERSION:
        raise fact_seal.MalformedRecordError(
            f"payload version is not the version this desk speaks "
            f"(v{fact_seal.RECORD_VERSION})")

    text = payload.get("user_text")
    if not isinstance(text, str) or not text.strip():
        raise fact_seal.MalformedRecordError("payload carries no user_text")
    text = text.strip()
    if len(text) > MAX_FACT_CHARS:
        raise fact_seal.MalformedRecordError(
            f"user_text is {len(text)} chars, over the {MAX_FACT_CHARS} cap")

    who = payload.get("who")
    if not isinstance(who, str) or who.strip().upper() not in recognised_users():
        raise fact_seal.MalformedRecordError(
            "who is not an identity this desk recognises")
    who = who.strip().upper()

    tier = payload.get("tier")
    if not isinstance(tier, str) or tier not in KNOWN_TIERS:
        # The cloud sets this from its identity roster on every queued turn, so a
        # record without one did not come from that path.
        raise fact_seal.MalformedRecordError(
            "tier is not a permission tier this desk issues")

    return who, tier, text


def _governance_gate(who: str, tier: str, text: str) -> None:
    """Run the real governance engine. Anything but PASS refuses the write."""
    # Lazy so this module stays importable before the ruleset does, and so an
    # import failure fails CLOSED (it propagates, and the drain holds the batch).
    from governance_manager import governance_manager, GovernanceSignal

    verdict = governance_manager.check({
        "action_type": ACTION_TYPE,
        # The shape action_engine._remember_fact parses: "<category>: <fact>".
        "target": f"Fact: {text}",
        "user": who,
        "permission_tier": tier,
        "source": "cloud_fact_drain",
        "unattended": True,
    })
    signal = verdict.get("signal")

    if signal == GovernanceSignal.PASS.value:
        return

    if signal == GovernanceSignal.PENDING_CONFIRMATION.value:
        # There is nobody to ask — that is what "unattended" means. Auto-approving
        # would BE the bypass. Cancelling is not politeness either: check() has
        # already parked this in the single pending slot, and leaving it there
        # would let the operator's next spoken "yes", meant for something else,
        # confirm a write he never saw.
        governance_manager.cancel_pending(verdict.get("confirmation_id"))
        raise GovernanceRefusedError(
            f"'{ACTION_TYPE}' is CONFIRM tier and this write is unattended — "
            f"refused and dead-lettered rather than auto-approved")

    raise GovernanceRefusedError(
        f"governance returned {signal!r} for '{ACTION_TYPE}': "
        f"{verdict.get('reason') or 'no reason given'}")


def _require_unlocked_store() -> None:
    """Fault (not a refusal) if the key store cannot serve this write.

    Two reasons, and the second is the one that would go unnoticed: a locked
    store cannot encrypt the row, AND it cannot compute the `content_hash` blind
    index — so the write would land unencrypted with dedup silently switched off.
    Holding the record costs a reconnect; writing it costs both properties.
    """
    from modules import memory_crypto
    if not memory_crypto.keys_ready():
        raise memory_crypto.MemoryLockedError(
            "the memory key store is locked or unprovisioned — the batch is HELD, "
            "not written (encryption and the dedup blind index both need it)")


# -- the sink ----------------------------------------------------------------

def governed_write(payload: dict) -> bool:
    """The one way a drained fact reaches memory. Installed as `fact_drain`'s sink.

    Returns True when the extraction produced at least one NEW row, False when it
    produced none — a duplicate under the blind index, or a turn with no fact in
    it. Both are the same outcome the live path gives, and both are handled: the
    drain acks and ledgers either way, so the cloud stops offering the record.
    """
    who, tier, text = _validate(payload)
    _governance_gate(who, tier, text)
    _require_unlocked_store()

    # The same call brain.extract_and_store_memory delegates to, minus that
    # wrapper's catch-all — see the module docstring.
    #
    # SOURCE_CLOUD is the ONLY place in the tree that is passed: everything else
    # defaults to `desk`. It is what makes a fact the Render gateway captured
    # while the PC was off distinguishable from one he said in person — the two
    # were byte-identical in the store until 2026-08-02.
    from memory_manager import SOURCE_CLOUD, extract_and_persist

    # strict=True is finding M1, and it is the difference between the three
    # outcomes above being real and being decorative. Without it a rate-limited
    # extractor returns [] — the same value it uses for "this turn had no fact"
    # — this function returns False, and the drain reads False as a VERDICT:
    # ledger STORED, ack, and the cloud destroys the sealed original. Under
    # strict the failure raises, the drain holds the record, and the next
    # connect tries again.
    saved = extract_and_persist(text, who, source=SOURCE_CLOUD, strict=True)
    print(f"[FACT_SINK] governed write for {who} ({tier}), source={SOURCE_CLOUD}: "
          f"{saved} new memory row(s).", flush=True)
    return saved > 0


def install() -> None:
    """Hand the governed sink to the drain. Idempotent, safe to call per connect.

    Must run BEFORE the `fact_key` handshake: accepting the key is what triggers
    the cloud's flush, and a drain with no sink HOLDS the batch. Holding is safe
    but it is not the goal.
    """
    global _installed
    from modules import fact_drain
    fact_drain.set_sink(governed_write)
    if not _installed:
        _installed = True
        print("[FACT_SINK] governed memory sink installed — drained facts now pass "
              f"governance ('{ACTION_TYPE}') before they are written.", flush=True)


def installed() -> bool:
    from modules import fact_drain
    return fact_drain.has_sink()
