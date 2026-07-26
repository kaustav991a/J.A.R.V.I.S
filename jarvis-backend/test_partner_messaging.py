"""Harness for partner messaging — these tests ARE the safety features.

Two capabilities, both owner-facing:
  * `message_partner` — propose-and-approve send to a REGISTERED partner.
  * `summarize_partner_chat` — admin-only pull of what a partner told JARVIS.

What is proved here, deliberately weighted toward the refusals:

  unknown / vague / raw-id recipients are refused, never guessed
  a declined send is TERMINAL — no route may re-attempt it (live bug #4)
  a send already awaiting approval is not staged twice
  the confirm prompt carries the resolved NAME and the FULL text verbatim
  nothing sends without approval, and detection alone never sends
  summarize is admin-only; one partner can never read another's history
  partner logging respects its OFF default — with the flag off, no rows exist

No Telegram, no network, no real ids: env and db path are injected, the clock is
fake, and the transport is asserted by source inspection (a send that bypassed
the gates would be a code change these assertions catch).
"""

import os
import pathlib
import sqlite3
import tempfile

from modules import partner_log, partner_messaging, partner_registry

GF_ENV = {"TELEGRAM_GF_ID": "111222333", "TELEGRAM_BROTHER_ID": "444555666"}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _tmpdb() -> str:
    fd, path = tempfile.mkstemp(suffix=".db", prefix="partner_test_")
    os.close(fd)
    os.unlink(path)          # let sqlite create it
    return path


# ── recipient resolution: refuse, never guess ───────────────────────────────

def test_role_words_and_first_names_resolve():
    for name in ("girlfriend", "my girlfriend", "GF", "Mousumi"):
        r = partner_registry.resolve(name, GF_ENV)
        assert r.ok and r.slot == "gf", f"{name!r} -> {r}"
        assert r.partner_id == 111222333
        assert r.display_name == "Mousumi"
    for name in ("brother", "my brother", "Kinshuk"):
        r = partner_registry.resolve(name, GF_ENV)
        assert r.ok and r.slot == "brother" and r.partner_id == 444555666


def test_unknown_recipient_is_refused_not_guessed():
    for name in ("Priya", "the neighbour", "boss", "mum"):
        r = partner_registry.resolve(name, GF_ENV)
        assert not r.ok
        assert r.reason == partner_registry.REASON_UNKNOWN
        assert r.partner_id is None
        assert "won't guess" in r.refusal_text()


def test_vague_recipient_is_refused():
    """A private message to the wrong person is the failure being prevented, so
    'her' / 'someone' resolve to nobody."""
    for name in ("her", "she", "them", "someone", "my partner", "everyone"):
        r = partner_registry.resolve(name, GF_ENV)
        assert not r.ok, f"{name!r} resolved to {r.slot}"
        assert r.reason == partner_registry.REASON_AMBIGUOUS
        assert r.partner_id is None


def test_two_partners_named_at_once_is_ambiguous():
    r = partner_registry.resolve("girlfriend and brother", GF_ENV)
    assert not r.ok and r.reason == partner_registry.REASON_AMBIGUOUS


def test_raw_chat_id_is_rejected_in_every_shape():
    """The model can never supply the address — digits are refused before the
    alias table is consulted."""
    for name in ("111222333", 111222333, "  987654321 ", "mousumi 111222333",
                 "chat_id=111222333", "+91 90000 00000", 0, -1, 12.5):
        r = partner_registry.resolve(name, GF_ENV)
        assert not r.ok, f"{name!r} resolved"
        assert r.reason == partner_registry.REASON_RAW_ID, f"{name!r} -> {r.reason}"
        assert r.partner_id is None


def test_known_name_with_no_registered_id_is_not_reachable():
    r = partner_registry.resolve("girlfriend", {"TELEGRAM_BROTHER_ID": "444555666"})
    assert not r.ok and r.reason == partner_registry.REASON_NOT_REGISTERED
    assert r.partner_id is None
    assert "no registered Telegram account" in r.refusal_text()


def test_empty_recipient_asks_who():
    r = partner_registry.resolve("", GF_ENV)
    assert not r.ok and r.reason == partner_registry.REASON_EMPTY
    assert "Who should I send" in r.refusal_text()


def test_non_numeric_env_id_is_treated_as_unregistered():
    r = partner_registry.resolve("mousumi", {"TELEGRAM_GF_ID": "@mousumi"})
    assert not r.ok and r.reason == partner_registry.REASON_NOT_REGISTERED


def test_inbound_identity_maps_back_to_a_slot():
    assert partner_registry.slot_for_user("MOUSUMI") == "gf"
    assert partner_registry.slot_for_user("kinshuk") == "brother"
    assert partner_registry.slot_for_user("KAUSTAV") is None   # the owner is not a partner
    assert partner_registry.slot_for_user("") is None


# ── target parsing: never invent the missing half ───────────────────────────

def test_target_shapes_parse():
    assert partner_messaging.parse_target(
        {"to": "girlfriend", "message": "have you eaten?"}) == ("girlfriend", "have you eaten?")
    assert partner_messaging.parse_target(
        {"recipient": "brother", "text": "call me"}) == ("brother", "call me")
    assert partner_messaging.parse_target("girlfriend|have you eaten?") == ("girlfriend", "have you eaten?")


def test_bare_string_is_a_recipient_with_no_message():
    """Never the other way round: guessing the addressee is the one mistake that
    matters, so a lone string is treated as a name and the send stalls for text."""
    assert partner_messaging.parse_target("girlfriend") == ("girlfriend", "")
    assert partner_messaging.parse_target({}) == ("", "")
    assert partner_messaging.parse_target(None) == ("", "")


# ── the confirm prompt: the exact artifact, not a summary ───────────────────

def test_confirm_prompt_names_the_partner_and_quotes_the_whole_message():
    body = ("Hi love, I'm running late from the office tonight — please eat "
            "without me, and I'll bring back those almond croissants you like "
            "from the place near the station. Text me if you want anything else.")
    p = partner_messaging.confirm_prompt("Mousumi", body, "Sir")
    assert "Mousumi" in p
    assert body in p, "the message text must appear verbatim"
    assert "…" not in p and "..." not in p, "the read-back must not be truncated"
    assert "confirm" in p.lower() and "cancel" in p.lower()


def test_confirm_prompt_is_longer_than_the_agent_generic_one():
    """agent_confirm.question_for clips targets at 120 chars — fine for a
    filename, wrong for words being said to a person."""
    from modules import agent_confirm

    body = "x" * 400
    generic = agent_confirm.question_for("message_partner", body)
    ours = partner_messaging.confirm_prompt("Mousumi", body)
    assert "…" in generic and len(generic) < 200      # the generic one truncates
    assert body in ours                               # ours does not


# ── deny is terminal, and one prompt means one send ─────────────────────────

def test_declined_send_is_refused_afterwards():
    c = Clock()
    g = partner_messaging.SendGuard(clock=c)
    g.note_denied("gf", "have you eaten?")
    assert g.refusal("gf", "have you eaten?") == partner_messaging.REFUSED_DENIED
    # ...and whitespace/case games do not slip past the memo
    assert g.refusal("gf", "  Have  You   Eaten? ") == partner_messaging.REFUSED_DENIED
    assert "won't re-attempt" in partner_messaging.refusal_text(
        partner_messaging.REFUSED_DENIED, "Mousumi")


def test_denial_is_scoped_to_that_message_and_that_partner():
    g = partner_messaging.SendGuard(clock=Clock())
    g.note_denied("gf", "have you eaten?")
    assert g.refusal("gf", "are you home?") is None        # different words
    assert g.refusal("brother", "have you eaten?") is None  # different person


def test_denial_expires_so_the_owner_can_change_his_mind():
    c = Clock()
    g = partner_messaging.SendGuard(deny_ttl_s=300.0, clock=c)
    g.note_denied("gf", "have you eaten?")
    c.advance(299.0)
    assert g.refusal("gf", "have you eaten?") == partner_messaging.REFUSED_DENIED
    c.advance(2.0)
    assert g.refusal("gf", "have you eaten?") is None


def test_a_staged_send_is_not_staged_twice():
    """One reply emitting the same send twice must not produce two prompts — or
    one 'yes' could deliver two messages."""
    c = Clock()
    g = partner_messaging.SendGuard(clock=c)
    g.note_staged("gf", "have you eaten?")
    assert g.refusal("gf", "have you eaten?") == partner_messaging.REFUSED_DUPLICATE
    assert "already waiting" in partner_messaging.refusal_text(
        partner_messaging.REFUSED_DUPLICATE, "Mousumi")


def test_stage_mark_clears_on_delivery_and_on_denial():
    g = partner_messaging.SendGuard(clock=Clock())
    g.note_staged("gf", "hello")
    g.note_sent("gf", "hello")
    assert g.refusal("gf", "hello") is None
    g.note_staged("gf", "hello")
    g.note_denied("gf", "hello")
    assert g.refusal("gf", "hello") == partner_messaging.REFUSED_DENIED  # deny wins


def test_stage_mark_expires_with_the_governance_window():
    c = Clock()
    g = partner_messaging.SendGuard(stage_ttl_s=90.0, clock=c)
    g.note_staged("gf", "hello")
    c.advance(91.0)
    assert g.refusal("gf", "hello") is None


# ── partner log: OFF means nothing exists ──────────────────────────────────

def test_logging_flag_defaults_off():
    assert partner_log.logging_enabled({}) is False
    for raw in ("0", "", "false", "no", "off"):
        assert partner_log.logging_enabled({partner_log.ENV_FLAG: raw}) is False
    for raw in ("1", "true", "YES", " on "):
        assert partner_log.logging_enabled({partner_log.ENV_FLAG: raw}) is True


def test_nothing_is_written_when_logging_is_off():
    """Not 'written but hidden' — the rows must not exist, so no later change of
    mind can read a conversation that happened while the flag was off."""
    db = _tmpdb()
    wrote = partner_log.log_inbound("gf", "hi, have you eaten?", env={}, db_path=db)
    assert wrote is False
    assert partner_log.recent("gf", db_path=db) == []
    # the table itself was never created
    if pathlib.Path(db).exists():
        conn = sqlite3.connect(db)
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        finally:
            conn.close()
        assert partner_log.TABLE not in names, f"table created with logging off: {names}"
    os.path.exists(db) and os.unlink(db)


def test_logging_on_persists_and_reads_back_per_partner():
    db = _tmpdb()
    on = {partner_log.ENV_FLAG: "1"}
    assert partner_log.log_inbound("gf", "have you eaten?", partner_id=111222333,
                                   partner_name="Mousumi", env=on, db_path=db) is True
    assert partner_log.log_inbound("gf", "call me when free", env=on, db_path=db) is True
    assert partner_log.log_inbound("brother", "need the car keys", env=on, db_path=db) is True

    gf = partner_log.recent("gf", db_path=db)
    assert [r["content"] for r in gf] == ["have you eaten?", "call me when free"]
    bro = partner_log.recent("brother", db_path=db)
    assert [r["content"] for r in bro] == ["need the car keys"]
    # one partner's history can never appear in another's
    assert all("keys" not in r["content"] for r in gf)
    assert partner_log.count("gf", db_path=db) == 2
    os.unlink(db)


def test_blank_and_unslotted_messages_are_not_logged():
    db = _tmpdb()
    on = {partner_log.ENV_FLAG: "1"}
    assert partner_log.log_inbound("gf", "   ", env=on, db_path=db) is False
    assert partner_log.log_inbound("", "hello", env=on, db_path=db) is False
    assert partner_log.recent("gf", db_path=db) == []
    os.path.exists(db) and os.unlink(db)


def test_summary_payload_discloses_that_it_is_logged_data():
    rows = [{"content": "have you eaten?", "timestamp": "2026-07-26T18:04:00+00:00",
             "partner_name": "Mousumi"}]
    out = partner_messaging.format_history(rows, "Mousumi", partner_log.DISCLOSURE)
    assert partner_log.DISCLOSURE in out
    assert out.index(partner_log.DISCLOSURE) < out.index("have you eaten?"), \
        "the disclosure must lead so it survives summarisation"
    assert partner_messaging.format_history([], "Mousumi", partner_log.DISCLOSURE) == ""


def test_the_store_reuses_the_existing_database():
    """Constraint: no new store. These rows live in memory_manager's db file."""
    assert partner_log.DB_PATH.endswith("jarvis_longterm.db")


# ── wiring: the gates are where they have to be ────────────────────────────

def _src(*parts) -> str:
    return pathlib.Path(__file__).parent.joinpath(*parts).read_text(encoding="utf-8")


def test_governance_registers_both_actions():
    import json

    rules = json.loads(_src("governance.json"))["rules"]
    assert rules["message_partner"] == "CONFIRM", "a send must be authorised"
    assert rules["summarize_partner_chat"] == "AUTO"


def test_summarize_is_admin_only_and_guests_gain_nothing():
    """tier_allows is untouched: neither action is on the VIP allowlist, so a
    guest is refused before dispatch, logging, or a governance pend."""
    from action_engine import (ADMIN_TIER, VIP_GUEST_ALLOWED_ACTIONS,
                               VIP_GUEST_TIER, tier_allows)

    for atype in (partner_messaging.ACTION_SEND, partner_messaging.ACTION_SUMMARISE):
        assert atype not in VIP_GUEST_ALLOWED_ACTIONS
        assert tier_allows(ADMIN_TIER, atype) is True
        assert tier_allows(VIP_GUEST_TIER, atype) is False
        # unknown/empty tiers fail closed too
        assert tier_allows("", atype) is False
        assert tier_allows("mousumi", atype) is False
    assert VIP_GUEST_ALLOWED_ACTIONS == frozenset({"tavily_search", "web_search"}), \
        "the guest allowlist must not have grown"


def test_engine_routes_both_actions_and_never_takes_an_id_from_the_model():
    src = _src("action_engine.py")
    assert 'elif action == "message_partner"' in src
    assert 'elif action == "summarize_partner_chat"' in src
    body = src.split("async def _message_partner", 1)[1].split("def _summarize_partner_chat", 1)[0]
    assert "partner_registry.resolve(" in body, "recipient must come from the registry"
    assert "guard.refusal(" in body, "a declined send must be refused at the engine"
    assert "res.partner_id" in body and "target.get" not in body, \
        "the id must come from the resolver, never from the payload"


def test_transport_rechecks_the_recipient_against_the_registry():
    src = _src("modules", "telegram_bot.py")
    body = src.split("async def send_text_to_partner", 1)[1].split("def is_configured", 1)[0]
    assert "_IDENTITIES.get(pid)" in body, "unknown ids must be refused at the transport too"
    assert "_ADMIN_TIER" in body, "a partner send must not be redirectable at the owner"


def test_main_reads_back_verbatim_and_records_denials():
    src = _src("main.py")
    assert "_partner_confirm_text(" in src and "_partner_note_denial(" in src
    # every governance CONFIRM surface offers the verbatim read-back...
    assert src.count("_partner_confirm_text(") >= 4      # 1 def + 3 call sites
    # ...and every explicit denial surface records the refusal
    assert src.count("_partner_note_denial(") >= 4       # 1 def + 3 call sites
    # the note must be taken BEFORE the payload is discarded
    for chunk in src.split("_partner_note_denial(")[1:]:
        head = chunk[:400]
        if "cancel_pending" in head:
            assert head.index("cancel_pending") >= 0     # ordering asserted below
    denial_at = src.index("_partner_note_denial(_DESK_PENDING")
    cancel_at = src.index("governance_manager.cancel_pending(_DESK_PENDING", denial_at)
    assert denial_at < cancel_at, "record the denial before the payload is cancelled"


def test_it_works_from_telegram_too():
    """The owner asks from his phone: same engine, same CONFIRM, same terminal
    deny — approval is session-scoped by confirmation_id so a phone 'yes' can
    only ever answer the question that phone was asked."""
    src = _src("main.py")
    remote = src.split("async def run_remote_command", 1)[1]
    confirm = remote.split('startswith("GOVERNANCE_CONFIRM:")', 1)[1][:1200]
    assert "_partner_confirm_text(conf_action, conf_id, honor)" in confirm, \
        "the phone must get the verbatim read-back, not a bare action name"
    assert 'sess.pending["governance"] = {"cid": conf_id' in confirm, \
        "a remote approval must be pinned to this channel's confirmation id"
    # the remote deny branch records the refusal before cancelling
    deny = remote.split("_partner_note_denial(cid)", 1)
    assert len(deny) == 2, "the remote deny path must record the refusal"
    assert "cancel_pending(cid)" in deny[1][:200]
    # and the summary comes back synthesised on the phone as well as the desk
    assert "summarize_partner_chat" in src.split("_REMOTE_DATA_ACTIONS", 1)[1][:400]


def test_inbound_logging_is_flag_gated_and_partner_only():
    src = _src("main.py")
    hook = src.split("Partner-chat log (opt-in", 1)[1][:1200]
    assert "tier != ADMIN_TIER" in hook, "the owner's own messages are not partner logs"
    assert "slot_for_user(" in hook, "an unrecognised identity is never filed"
    assert "partner_log.log_inbound" in hook
    # the flag check lives inside log_inbound — the only way rows get written
    log_src = _src("modules", "partner_log.py")
    write = log_src.split("def log_inbound", 1)[1].split("def recent", 1)[0]
    assert "if not logging_enabled(env):" in write
    assert write.index("logging_enabled") < write.index("ensure_table"), \
        "the flag must be checked before the table is created"


def test_no_autonomous_send_path_exists():
    """Nothing may call the transport except the approved engine action."""
    callers = []
    for name in ("main.py", "background_monitor.py", "modules/owner_notify.py",
                 "modules/agent_tools.py", "modules/worker_loop.py",
                 "modules/routines.py", "modules/telegram_bot.py",
                 "action_engine.py"):
        p = pathlib.Path(__file__).parent.joinpath(*name.split("/"))
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        if "send_text_to_partner" in text:
            callers.append(name)
    assert set(callers) == {"modules/telegram_bot.py", "action_engine.py"}, \
        f"unexpected caller of the partner transport: {callers}"


def test_partner_send_is_not_an_agent_tool():
    """The agentic loop must not be able to message a person on its own."""
    src = _src("modules", "agent_tools.py")
    assert partner_messaging.ACTION_SEND not in src


if __name__ == "__main__":
    import sys
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
