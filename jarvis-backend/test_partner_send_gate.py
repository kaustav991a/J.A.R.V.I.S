r"""test_partner_send_gate.py — the outbound gate, proven by EXECUTION.

`test_partner_messaging.py` already proves the pure logic (resolver, guard,
prompt) and pins the wiring by reading source text. This harness exists because
those are not the same claim as the one that matters:

    "the recipient was refused"   is not   "nothing was sent"

A source grep stays green through a rename that changes behaviour, and no test
anywhere drove `ActionEngine.execute()` end-to-end to count what actually
reached the transport. So everything here asserts on **transport call count**
against the REAL objects: the real governance manager and its CONFIRM slot, the
real registry, the real engine method, and — extracted from `main.py` and
executed, not grepped — the real read-back and denial functions.

The transport is the only fake. `send_text_to_partner` is replaced with a
recorder, so a message that "would have been sent" is a hard, countable failure
instead of an inference.

`main.py` is not imported: it costs ~47 s (TensorFlow, face recognition) and
would roughly double the suite. Instead `_load_main_function` compiles the two
partner functions straight out of main.py's source, so this executes the real
definitions — a drift in main's body fails here rather than passing a substring
check. A new dependency inside either function surfaces as a loud NameError.

No network, no Telegram, no real ids: the chat ids below are fixtures injected
AFTER imports, because importing `action_engine` pulls in `load_dotenv(override=True)`
and would otherwise put the operator's live ids under test.
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# ── Imports first, environment second ────────────────────────────────────────
# action_engine's import chain calls load_dotenv(override=True). Setting the
# fixtures afterwards is what keeps this harness off the real registry.
import action_engine                                          # noqa: E402
from action_engine import (ADMIN_TIER, TIER_BLOCKED_PREFIX,   # noqa: E402
                           VIP_GUEST_TIER, ActionEngine)
from governance_manager import governance_manager             # noqa: E402
from modules import partner_messaging, partner_registry, telegram_bot  # noqa: E402

GF_ID = 700000001
BRO_ID = 700000002
os.environ["TELEGRAM_GF_ID"] = str(GF_ID)
os.environ["TELEGRAM_BROTHER_ID"] = str(BRO_ID)

SEND = "message_partner"

# ── The transport, faked and counted ─────────────────────────────────────────
SENDS: list[tuple[int, str]] = []


async def _fake_send(partner_id, text):
    SENDS.append((partner_id, text))
    return True


telegram_bot.send_text_to_partner = _fake_send
telegram_bot.is_configured = lambda: True

# `_message_partner` and the pre-dispatch half of `execute` touch no instance
# state, so the engine is built without its heavy __init__ (cameras, ADB, app
# index). If that ever stops being true this raises AttributeError, loudly.
ENGINE = ActionEngine.__new__(ActionEngine)


def _reset() -> None:
    SENDS.clear()
    partner_messaging.guard.clear()
    governance_manager.cancel_pending()
    governance_manager._pending_registry.clear()


def _run(coro):
    return asyncio.run(coro)


def _execute(target, *, bypass=False, tier=ADMIN_TIER):
    return _run(ENGINE.execute({"action_type": SEND, "target": target},
                               governance_bypass=bypass, permission_tier=tier))


# ── main.py's real functions, without main.py's 47-second import ─────────────

def _load_main_function(fn_name: str):
    """Compile one top-level function out of main.py and bind it to the real
    modules it closes over. This is main's own source, not a copy of it."""
    src = pathlib.Path(__file__).resolve().parent.joinpath("main.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            ns = {
                "partner_messaging": partner_messaging,
                "partner_registry": partner_registry,
                "governance_manager": governance_manager,
                "__builtins__": __builtins__,
            }
            exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), ns)
            return ns[fn_name]
    raise AssertionError(f"main.py has no top-level {fn_name}() — the gate moved")


partner_confirm_text = _load_main_function("_partner_confirm_text")
partner_note_denial = _load_main_function("_partner_note_denial")


def _stage(target):
    """Drive the real production sequence up to the open CONFIRM prompt.

    Returns (cid, prompt_text) exactly as the desk and phone surfaces get them."""
    out = _execute(target)
    assert out.startswith("GOVERNANCE_CONFIRM:"), out
    cid = out.split(":", 2)[2]
    return cid, partner_confirm_text(SEND, cid, "Sir")


# ══ GATE 1 — the send is BLOCKED until it is approved ════════════════════════

def test_an_unapproved_send_returns_the_sentinel_and_sends_nothing():
    out = _execute({"to": "girlfriend", "message": "I'll be late"})
    assert out.startswith(f"GOVERNANCE_CONFIRM:{SEND}:"), out
    assert SENDS == [], f"a send escaped before approval: {SENDS}"
    assert governance_manager.has_pending(), "the payload must be parked, not dropped"


def test_the_parked_payload_is_the_exact_artifact_not_a_summary():
    body = "Stuck at the office, don't wait up for dinner"
    out = _execute({"to": "girlfriend", "message": body})
    cid = out.split(":", 2)[2]
    parked = governance_manager.get_pending_payload(cid)
    assert parked["action_type"] == SEND
    name, parked_body = partner_messaging.parse_target(parked["target"])
    assert name == "girlfriend" and parked_body == body
    assert SENDS == []


def test_governance_json_registers_the_send_as_confirm():
    """The behavioural gate above is only meaningful while the rule says CONFIRM."""
    import json
    rules = json.loads(pathlib.Path(action_engine.__file__).parent
                       .joinpath("governance.json").read_text(encoding="utf-8"))["rules"]
    assert rules[SEND] == "CONFIRM", f"the send tier was weakened to {rules[SEND]!r}"


def test_a_guest_cannot_even_open_the_confirm_prompt():
    """tier_allows runs BEFORE governance: a non-admin gets no pend, no prompt,
    no dispatch — the prompt itself is a capability the owner alone has."""
    out = _execute({"to": "girlfriend", "message": "hi"}, tier=VIP_GUEST_TIER)
    assert out == f"{TIER_BLOCKED_PREFIX}{SEND}", out
    assert SENDS == []
    assert not governance_manager.has_pending(), "a refused tier must not park a payload"


# ══ GATE 2 — approval, and only approval, sends ══════════════════════════════

def test_approval_sends_exactly_once_to_the_registered_id():
    body = "Running late, home by nine"
    cid, _ = _stage({"to": "girlfriend", "message": body})
    assert SENDS == []
    governance_manager.consume_pending(cid)
    out = _execute({"to": "girlfriend", "message": body}, bypass=True)
    assert len(SENDS) == 1, f"expected exactly one send, got {len(SENDS)}"
    assert SENDS[0][0] == GF_ID, f"delivered to {SENDS[0][0]}, not the gf slot"
    assert SENDS[0][1] == body
    assert "Mousumi" in out


def test_the_confirm_prompt_does_not_refuse_the_send_it_authorised():
    """REGRESSION — this is the defect this harness was written to find.

    Building the CONFIRM prompt marks the send in-flight (`note_staged`) so one
    LLM reply cannot raise two prompts. The approval then re-enters the engine
    with the SAME (slot, body), and the duplicate check refused it as a repeat
    of itself: `already_awaiting_approval`, nothing delivered, on 100% of sends.

    Both TTLs are 90 s, so there was no window where an approval was still valid
    and the mark had expired — the feature could never have worked. The unit
    tests passed throughout: `SendGuard` is correct in isolation and every
    wiring test matched source text rather than running the sequence.

    The full production order, end to end:
    """
    body = "I'll be late"
    target = {"to": "girlfriend", "message": body}

    out = _execute(target)                                    # 1. owner asks
    assert out.startswith("GOVERNANCE_CONFIRM:")
    cid = out.split(":", 2)[2]

    prompt = partner_confirm_text(SEND, cid, "Sir")           # 2. prompt is built
    assert "Mousumi" in prompt and body in prompt
    assert SENDS == []

    approved = governance_manager.consume_pending(cid)        # 3. owner confirms
    assert approved is not None

    result = _run(ENGINE.execute(approved, governance_bypass=True))   # 4. send
    assert len(SENDS) == 1, (
        f"the approved send was refused by its own prompt: {result!r}")
    assert SENDS[0] == (GF_ID, body)
    assert "Mousumi" in result


def test_each_registered_partner_reaches_their_own_id():
    for name, expect_id, display in (("girlfriend", GF_ID, "Mousumi"),
                                     ("mousumi", GF_ID, "Mousumi"),
                                     ("brother", BRO_ID, "Kinshuk"),
                                     ("kinshuk", BRO_ID, "Kinshuk")):
        _reset()
        out = _execute({"to": name, "message": f"note for {name}"}, bypass=True)
        assert len(SENDS) == 1 and SENDS[0][0] == expect_id, f"{name} -> {SENDS}"
        assert display in out


def test_the_full_text_survives_to_the_transport_uncut():
    """Whitespace is collapsed by normalise_body; CONTENT is never truncated."""
    body = ("Listen — I'm going to be late tonight, the deploy broke and I have to "
            "stay until it's green. Don't wait up for dinner, I'll eat here. "
            "Love you. ❤")
    _execute({"to": "girlfriend", "message": body}, bypass=True)
    assert len(SENDS) == 1
    sent = SENDS[0][1]
    assert sent == partner_messaging.normalise_body(body)
    # 120 is agent_confirm.question_for's clip point — the bar this must clear.
    assert len(sent) > 120, "the long-body case stopped being long"
    assert not sent.endswith(("...", "…")), "the message was truncated"
    for word in ("deploy", "dinner", "Love", "❤"):
        assert word in sent, f"{word!r} was lost on the way to the transport"


# ══ GATE 3 — a denial is terminal, on every route ════════════════════════════

def test_a_denied_send_is_refused_even_on_the_post_approval_path():
    """The hardest case: deny, then re-enter through the branch that carries
    governance_bypass=True. An approval sentinel must not outrank a refusal."""
    body = "I'll be late"
    cid, _ = _stage({"to": "girlfriend", "message": body})
    partner_note_denial(cid)
    governance_manager.cancel_pending(cid)

    out = _execute({"to": "girlfriend", "message": body}, bypass=True)
    assert SENDS == [], f"a declined message was sent anyway: {SENDS}"
    assert "declined" in out.lower()


def test_a_denied_send_is_refused_on_a_fresh_unapproved_attempt_too():
    body = "I'll be late"
    cid, _ = _stage({"to": "girlfriend", "message": body})
    partner_note_denial(cid)
    governance_manager.cancel_pending(cid)

    # A second route re-proposes it: it must not even reach a new prompt.
    out = _execute({"to": "girlfriend", "message": body}, bypass=True)
    assert SENDS == []
    assert "declined" in out.lower()


def test_the_refusal_survives_rewording_of_whitespace_and_case():
    body = "I'll be late"
    cid, _ = _stage({"to": "girlfriend", "message": body})
    partner_note_denial(cid)
    governance_manager.cancel_pending(cid)

    for variant in ("  I'll   be late  ", "i'll be late", "I'LL BE LATE"):
        out = _execute({"to": "girlfriend", "message": variant}, bypass=True)
        assert SENDS == [], f"{variant!r} slipped past the refusal"
        assert "declined" in out.lower()


def test_the_refusal_is_scoped_and_does_not_gag_the_channel():
    """Terminal means terminal for THAT message to THAT person — not a mute."""
    cid, _ = _stage({"to": "girlfriend", "message": "I'll be late"})
    partner_note_denial(cid)
    governance_manager.cancel_pending(cid)

    _execute({"to": "girlfriend", "message": "on my way now"}, bypass=True)
    assert len(SENDS) == 1, "a different message to the same partner must still send"
    SENDS.clear()
    _execute({"to": "brother", "message": "I'll be late"}, bypass=True)
    assert len(SENDS) == 1 and SENDS[0][0] == BRO_ID, \
        "the same words to a different partner must still send"


def test_denial_is_recorded_before_the_payload_is_discarded():
    """_partner_note_denial reads the parked payload to learn WHAT was refused,
    so cancelling first makes every denial a silent no-op. Asserted against the
    guard directly: the send-count is the same either way once the message is
    gone, which is exactly why this ordering bug would hide."""
    body = "cancel this one"

    # WRONG order — the payload is gone before the denial is read.
    cid, _ = _stage({"to": "girlfriend", "message": body})
    governance_manager.cancel_pending(cid)
    partner_note_denial(cid)
    assert partner_messaging.guard.refusal("gf", body) != partner_messaging.REFUSED_DENIED, \
        "this test no longer distinguishes the orderings"

    # RIGHT order — what main.py:1399/1782/2812 actually do.
    _reset()
    cid, _ = _stage({"to": "girlfriend", "message": body})
    partner_note_denial(cid)
    governance_manager.cancel_pending(cid)
    assert partner_messaging.guard.refusal("gf", body) == partner_messaging.REFUSED_DENIED, \
        "note-then-cancel must record a terminal refusal"


# ══ GATE 4 — the recipient can only come from the allowlist ══════════════════

HOSTILE = [
    {"to": "123456789", "message": "leak"},
    {"to": 123456789, "message": "leak"},
    {"to": "-1001234567890", "message": "leak"},
    {"to": "+919876543210", "message": "leak"},
    {"to": "٢٣٤", "message": "leak"},
    {"to": {"chat_id": "55512345"}, "message": "leak"},
    {"to": "Rahul", "message": "leak"},
    {"to": "the pizza guy", "message": "leak"},
    {"to": "boss", "message": "leak"},
    {"to": "her", "message": "leak"},
    {"to": "someone", "message": "leak"},
    {"to": "my partner", "message": "leak"},
    {"to": "girlfriend and brother", "message": "leak"},
    {"to": "", "message": "leak"},
    "123456789|leak",
    {"recipient": "-1001234567890", "text": "leak"},
]


def test_no_hostile_recipient_shape_reaches_the_transport():
    for target in HOSTILE:
        _reset()
        out = _execute(target, bypass=True)     # bypass = the strongest position
        assert SENDS == [], f"{target!r} reached the transport: {SENDS}"
        assert "Sir" in out, f"{target!r} produced no spoken refusal: {out!r}"


def test_a_hostile_recipient_cannot_even_park_a_confirmation():
    """A refused recipient must not leave a pending slot behind — the next
    unrelated 'confirm' would otherwise have something to land on."""
    for target in HOSTILE[:6]:
        _reset()
        _execute(target)                        # no bypass: the real first call
        governance_manager.cancel_pending()
        _execute(target, bypass=True)
        assert SENDS == [], f"{target!r} reached the transport"


def test_the_id_is_never_read_from_the_payload():
    """A registered NAME alongside a planted id must use the registry's id."""
    _execute({"to": "girlfriend", "message": "hi", "chat_id": 99999999,
              "partner_id": 99999999, "id": 99999999}, bypass=True)
    assert len(SENDS) == 1
    assert SENDS[0][0] == GF_ID, f"the planted id won: {SENDS[0][0]}"


def test_an_unregistered_slot_is_unreachable_even_by_its_real_name():
    saved = os.environ.pop("TELEGRAM_BROTHER_ID")
    try:
        out = _execute({"to": "kinshuk", "message": "hello"}, bypass=True)
        assert SENDS == [], "an unconfigured slot was still reachable"
        assert "no registered telegram" in out.lower()
    finally:
        os.environ["TELEGRAM_BROTHER_ID"] = saved


def test_resolve_has_no_callers_outside_the_partner_path():
    """The boundary widens the moment a fifth caller appears. Pin it.

    `partner_contact.py` was added 2026-08-02 (the butler answer, roadmap §6.7).
    It is a READ-ONLY caller: it resolves a name to decide whose contact record
    to look at, and has no path to the send transport at all. The next check
    enforces that distinction, so this list staying short keeps meaning what it
    was written to mean — resolve() picks a RECIPIENT, and every new caller has
    to be looked at.

    `worker_loop.py` was added 2026-08-16 (review finding C1). It is a DISPLAY
    caller: it resolves a name only to say it back to the owner in the
    authorisation ping — "I am ready to send this to Mousumi, verbatim: …" — for
    a send the ENGINE will perform from the target, not from this resolution.
    The check below pins that it never grew a transport of its own.
    """
    root = pathlib.Path(__file__).resolve().parent
    callers = set()
    for p in list(root.glob("*.py")) + list((root / "modules").glob("*.py")):
        if p.name.startswith("test_"):
            continue
        if "partner_registry.resolve(" in p.read_text(encoding="utf-8"):
            callers.add(p.name)
    assert callers == {"action_engine.py", "main.py", "partner_contact.py",
                       "worker_loop.py"}, \
        f"partner_registry.resolve() gained a caller: {sorted(callers)}"


def test_the_worker_resolves_a_name_only_to_show_it():
    """C1's read-back must not become a second send path.

    The worker resumes an approved `message_partner` by handing the payload to
    the ENGINE, which resolves the recipient itself. The name it resolves here
    is for the sentence it shows the owner, and nothing else — so a transport
    appearing in this file is a second road to a real person's phone.
    """
    src = (pathlib.Path(__file__).resolve().parent / "modules"
           / "worker_loop.py").read_text(encoding="utf-8")
    for forbidden in ("telegram_bot", "send_text_to_partner", "partner_id"):
        assert forbidden not in src, \
            f"the worker's read-back reached for {forbidden!r} — it must not send"


def test_the_butler_read_path_cannot_reach_the_send_transport():
    """A read-only caller of resolve() must stay read-only.

    Without this, `partner_contact.py` could grow a send over time and the
    caller-list check above would keep passing — it would already be on the
    allowlist.
    """
    src = (pathlib.Path(__file__).resolve().parent / "modules"
           / "partner_contact.py").read_text(encoding="utf-8")
    for forbidden in ("telegram_bot", "send_text_to_partner", "ACTION_SEND",
                      "message_partner"):
        assert forbidden not in src, \
            f"the butler read path reached for {forbidden!r} — it must not send"


# ══ GATE 5 — the read-back is the artifact, not a description of it ══════════

def test_the_readback_carries_the_resolved_name_and_the_whole_message():
    body = ("Listen — I'm going to be late tonight, the deploy broke and I have to "
            "stay until it's green. Don't wait up for dinner, I'll eat here. Love you.")
    _, prompt = _stage({"to": "girlfriend", "message": body})
    assert "Mousumi" in prompt, "the prompt must name the RESOLVED partner"
    assert body in prompt, "the prompt must quote the message in full"
    assert "…" not in prompt and "..." not in prompt, "the read-back was elided"
    assert "confirm" in prompt.lower() and "cancel" in prompt.lower()


def test_the_readback_resolves_the_alias_rather_than_echoing_it():
    """He says 'my girlfriend'; he must be shown WHO that resolved to."""
    _, prompt = _stage({"to": "my girlfriend", "message": "on my way"})
    assert "Mousumi" in prompt, prompt
    _reset()
    _, prompt = _stage({"to": "kinshuk", "message": "on my way"})
    assert "Kinshuk" in prompt, prompt


def test_the_readback_is_not_clipped_like_the_generic_confirm():
    """agent_confirm.question_for clips targets at 120 chars. Fine for a
    filename, wrong for words going to a person."""
    body = "x" * 400
    _, prompt = _stage({"to": "girlfriend", "message": body})
    assert body in prompt, "a 400-char message must survive the read-back intact"


def test_one_approval_spends_exactly_one_send():
    """One 'yes', one message — even when a reply proposes the same send twice.

    The mechanism is the confirmation id, not the duplicate guard: at CONFIRM
    tier the governance gate returns before the engine method is reached, so
    both proposals pend. A single approval consumes a single id and cannot be
    replayed; the other entry expires unspent.
    """
    body = "double staged"
    target = {"to": "girlfriend", "message": body}
    cid1 = _execute(target).split(":", 2)[2]
    cid2 = _execute(target).split(":", 2)[2]
    assert cid1 != cid2, "two proposals must not share one authorisation"
    assert SENDS == []

    approved = governance_manager.consume_pending(cid1)
    _run(ENGINE.execute(approved, governance_bypass=True))
    assert len(SENDS) == 1, f"one approval produced {len(SENDS)} messages"
    assert governance_manager.consume_pending(cid1) is None, \
        "the same 'yes' must not be spendable twice"


def test_the_duplicate_arm_still_guards_an_unapproved_invocation():
    """Defence in depth. With the rule at CONFIRM the governance gate short-
    circuits before this arm can fire, so it only matters if that rule is ever
    loosened to AUTO or the method is called directly. Keep it working."""
    body = "double staged"
    partner_messaging.guard.note_staged("gf", body)
    out = _run(ENGINE._message_partner({"to": "girlfriend", "message": body}))
    assert SENDS == [], "an unapproved duplicate reached the transport"
    assert "already waiting" in out.lower()


def test_an_empty_message_is_never_staged_as_a_send():
    for target in ({"to": "girlfriend", "message": ""},
                   {"to": "girlfriend", "message": "   "},
                   "girlfriend"):
        _reset()
        out = _execute(target, bypass=True)
        assert SENDS == [], f"{target!r} sent an empty message"
        assert "no message" in out.lower() or "what you'd like to say" in out.lower()


# ══ Runner ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        _reset()
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
