"""Harness: one conversation across the desk, Telegram and the phone.

The memory was merged on 2026-08-20 — "make one entity". Until then the phone had
a thread of its own, so a question asked at the desk was invisible from the phone
an hour later and vice versa: three surfaces, three memories, one person talking.

The merge itself was correct and was never run. It was verified against a
STANDALONE COPY of `_memory_key` typed out beside it, on a machine with no
Python, and nothing in the suite touched it. Two holes were open underneath it:

  1. **Desk-answered turns were never filed at all.** `_forward_to_desk` and
     `_ask_desk` hand the question to the desk and return before anything writes
     here, so with the desk LINKED — the normal state at home — the shared
     history filled only from the cloud fallback. The one case the shared memory
     exists for was the one case that skipped it. The turn was not lost; it was
     in the desk's own store, which is not the store all three surfaces read.
  2. **The unprompted voice wrote to the raw `APP_CHAT_ID`.** With
     `TELEGRAM_USER_ID` set that is a different key from the one `think` reads,
     so "he speaks first" landed where nothing reads it — and the line's own
     docstring names the cost: "a message the model cannot remember saying makes
     the next turn incoherent". The commute briefing was the same class and
     wrote nothing at all.

WHAT THIS PINS
--------------
Offline: no provider, no socket, no database. `_memory_ready` is forced false so
persistence is skipped and `_HISTORY` — the in-process cache — is the whole
observable. Both properties are checked by CALLING the writers and reading back
what landed under which key, because a key mismatch is invisible to any
assertion about source text.
"""

import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402

OWNER = 6292286568          # a plausible Telegram user id; the value is arbitrary
VIP = 111222333             # Mousumi, whose conversation is hers

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _setup():
    """No bot, no database, no history. `_memory_ready` false is the important
    one: it is what keeps `_history_add` from reaching for Postgres."""
    cg.BOT_TOKEN = ""
    cg.PUBLIC_URL = ""
    cg.APP_MEMORY_SHARED = True
    cg.APP_CHAT_ID = -90001
    os.environ["TELEGRAM_USER_ID"] = str(OWNER)
    cg._HISTORY.clear()

    async def _not_ready():
        return False

    cg._memory_ready = _not_ready


def _teardown():
    cg._HISTORY.clear()


def _run(coro):
    return asyncio.run(coro)


def _filed(key):
    """What is in the history under one key, as (role, content) pairs."""
    return [(m["role"], m["content"]) for m in cg._HISTORY.get(key, [])]


# ── which key a turn is remembered under ─────────────────────────────────────

def test_the_phone_is_remembered_under_the_operators_own_thread():
    check(cg._memory_key(cg.APP_CHAT_ID) == OWNER,
          "a phone turn is filed under the operator's Telegram chat")


def test_the_operators_own_chat_is_left_exactly_as_it_was():
    check(cg._memory_key(OWNER) == OWNER,
          "the operator's own chat id is unchanged")


def test_a_vip_keeps_their_own_conversation():
    """Merging Mousumi's chat into his would be a privacy failure dressed as a
    feature. This is why the map names one specific pair of ids rather than
    collapsing everything into one."""
    check(cg._memory_key(VIP) == VIP, "a VIP's conversation stays theirs")


def test_a_gateway_with_no_telegram_wiring_falls_through():
    """The app must work on a gateway that has no Telegram at all."""
    os.environ.pop("TELEGRAM_USER_ID", None)
    try:
        check(cg._memory_key(cg.APP_CHAT_ID) == cg.APP_CHAT_ID,
              "with no TELEGRAM_USER_ID the phone keeps its own thread")
    finally:
        os.environ["TELEGRAM_USER_ID"] = str(OWNER)


def test_a_malformed_owner_id_falls_through_instead_of_raising():
    os.environ["TELEGRAM_USER_ID"] = "not-an-id"
    try:
        check(cg._memory_key(cg.APP_CHAT_ID) == cg.APP_CHAT_ID,
              "a malformed owner id falls through rather than raising")
    finally:
        os.environ["TELEGRAM_USER_ID"] = str(OWNER)


def test_a_negative_owner_id_is_accepted():
    """Telegram group and channel ids are negative, and an owner id read from
    the environment is not this module's business to validate beyond parsing."""
    os.environ["TELEGRAM_USER_ID"] = "-100777"
    try:
        check(cg._memory_key(cg.APP_CHAT_ID) == -100777,
              "a negative owner id is parsed, not rejected")
    finally:
        os.environ["TELEGRAM_USER_ID"] = str(OWNER)


def test_the_off_switch_restores_the_old_behaviour():
    cg.APP_MEMORY_SHARED = False
    try:
        check(cg._memory_key(cg.APP_CHAT_ID) == cg.APP_CHAT_ID,
              "APP_MEMORY_SHARED=0 gives the phone its thread back")
    finally:
        cg.APP_MEMORY_SHARED = True


# ── hole 2: what he says unprompted ──────────────────────────────────────────

def test_an_unprompted_line_lands_where_think_will_read_it():
    """The bug, stated as the property it broke. Writing to `APP_CHAT_ID` put
    the line in a conversation nothing reads."""
    _run(cg._remember_said("The desk is awake, Sir."))
    check(_filed(OWNER) == [("assistant", "The desk is awake, Sir.")],
          "an unprompted line is filed under the shared key")
    check(_filed(cg.APP_CHAT_ID) == [],
          "...and NOT under the raw APP_CHAT_ID, which is the bug")


def test_the_line_is_readable_by_the_next_turn():
    """Stated the way the failure was felt: "what did you mean by that" has to
    reach a brain that said it."""
    _run(cg._remember_said("Traffic on the Bypass is bad tonight."))
    hist = _run(cg._history_for(cg._memory_key(cg.APP_CHAT_ID)))
    check(any("Bypass" in m["content"] for m in hist),
          "the history think() reads contains what he was just told")


def test_an_empty_line_is_not_remembered():
    _run(cg._remember_said("   "))
    _run(cg._remember_said(None))
    check(_filed(OWNER) == [], "nothing is filed for an empty or missing line")


def test_the_briefing_is_remembered_too():
    """It was the same class as the nudge and wrote nothing at all — so "what
    did you say about the rain" reached a brain that had never said it."""
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    brief = src[src.index("async def _commute_tick"):]
    brief = brief[:brief.index("\nasync def ", 1)]
    check("_remember_said(" in brief,
          "the commute briefing files what it just told him")
    check(brief.index("_push_all(") < brief.index("_remember_said("),
          "...after it is sent, so a failed push does not claim it was said")


# ── hole 1: what the desk answered ───────────────────────────────────────────

def test_a_desk_answered_turn_is_filed_as_a_pair():
    _run(cg._remember_desk_turn(cg.APP_CHAT_ID, "what is on my calendar",
                                "Two meetings, Sir."))
    check(_filed(OWNER) == [("user", "what is on my calendar"),
                            ("assistant", "Two meetings, Sir.")],
          "the question and the desk's answer are both filed, in order")


def test_a_desk_answered_turn_is_filed_under_the_shared_key():
    """The whole point: asked on the phone with the desk up, readable from
    Telegram afterwards."""
    _run(cg._remember_desk_turn(cg.APP_CHAT_ID, "lock the machine", "Locked, Sir."))
    check(_filed(cg.APP_CHAT_ID) == [],
          "nothing lands in the phone's own thread")
    check(len(_filed(OWNER)) == 2, "both halves land in the shared one")


def test_a_desk_turn_from_telegram_is_filed_under_the_same_key():
    """Telegram already speaks with the operator's own chat id, so this is the
    identity case — and it must not be mapped somewhere else by accident."""
    _run(cg._remember_desk_turn(OWNER, "any mail", "Three, Sir."))
    check(len(_filed(OWNER)) == 2, "a Telegram desk turn is filed once, in place")


def test_a_vip_desk_turn_stays_in_the_vips_thread():
    _run(cg._remember_desk_turn(VIP, "kemon acho", "Bhalo, Madam."))
    check(len(_filed(VIP)) == 2, "a VIP's desk turn is filed under the VIP")
    check(_filed(OWNER) == [], "...and does not leak into the operator's history")


def test_half_a_turn_is_still_filed():
    """A desk that answered with nothing to say still asked-and-answered, and a
    question with no answer is the wedged-desk case the watchdog covers."""
    _run(cg._remember_desk_turn(cg.APP_CHAT_ID, "run the backup", ""))
    check(_filed(OWNER) == [("user", "run the backup")],
          "a question with no answer files the question")
    cg._HISTORY.clear()
    _run(cg._remember_desk_turn(cg.APP_CHAT_ID, "", "Done, Sir."))
    check(_filed(OWNER) == [("assistant", "Done, Sir.")],
          "an answer with no question files the answer")


def test_both_desk_doors_file_the_turn():
    """Root cause #4: the phone door and the Telegram door are two sites of one
    hole, and a fix at one of them leaves the other open."""
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    check(src.count("_remember_desk_turn(") == 3,
          f"the helper is defined once and called at both desk doors "
          f"({src.count('_remember_desk_turn(')} incl. the definition)")
    # the phone door
    app = src[src.index("answer = await _ask_desk("):]
    check("_remember_desk_turn(" in app[:600], "the phone door files it")
    # the Telegram door, whose reply arrives in the reader minutes later
    reader = src[src.index('if ftype == "reply" and chat_id is not None:'):]
    check("_remember_desk_turn(" in reader[:600], "the Telegram reader files it")


def test_the_question_survives_the_wait_for_the_answer():
    """The Telegram reply comes back in a different coroutine, by which time the
    question exists nowhere else — so it rides on the pending entry."""
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    check('"asked": text' in src, "the question is stashed on the pending request")
    check('_pending_reqs[rid].get("filed")' in src,
          "...and filed once, not once per streamed chunk")


def test_the_question_is_read_before_the_watchdog_can_pop_it():
    """Setting `evt` releases the watchdog, which pops the entry. Reading the
    question after the first await would be a race that loses it."""
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    block = src[src.index('_asked = ""'):src.index('if ftype == "reply" and chat_id is not None:')]
    check("await" not in block,
          "the question is read with no await between the set and the read")


# ── no writer may reach the raw key any more ──────────────────────────────────

def test_nothing_writes_history_under_an_unmapped_key():
    """The hole-2 shape: `_history_add` takes a RAW key, so calling it directly
    is how a writer ends up in the wrong conversation. Every caller must either
    pass a `_memory_key` result or go through one of the two helpers."""
    src = (HERE / "cloud_gateway.py").read_text(encoding="utf-8", errors="replace")
    check("_history_add(APP_CHAT_ID" not in src,
          "no writer files under the raw APP_CHAT_ID")
    bad = [ln.strip() for ln in src.splitlines()
           if "_history_add(" in ln
           and "async def" not in ln
           and not ln.strip().startswith("#")
           and "_history_add(mem," not in ln
           and "_history_add(_memory_key(" not in ln]
    check(not bad, f"every _history_add call passes a mapped key — offenders: {bad}")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Shared memory — one conversation across three surfaces")
    print("=" * 62)
    for t in TESTS:
        _setup()
        try:
            t()
        except Exception as e:  # noqa: BLE001
            global _failed
            _failed += 1
            print(f"FAIL  {t.__name__} raised {type(e).__name__}: {e}")
        finally:
            _teardown()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
