r"""test_partner_contact.py — the butler answers, and has nothing to leak.

Every behavioural check drives the REAL code against a REAL temp sqlite: a
message goes in through `partner_contact.note_contact`, the answer comes out of
`partner_contact.status_for`, and the assertions are made on the sentence a
human would hear and on the bytes on disk.

That is the lesson of `f84f644`. The previous partner feature shipped with
grep-level tests asserting the source contained the right words; it passed for
weeks while the feature had never once worked, because "refused" and "nothing
happened" look identical from outside. So the leak checks here do not ask
whether the code avoids selecting content — they push a message carrying a rare
marker word through the real write path and then scan the raw database file for
that marker.

Source-level assertions survive in two places only, both about WIRING that has
no observable runtime behaviour from here: that the action is dispatched, and
that it is kept out of the synthesis sets.

If the machine has no key set, the encryption-specific checks skip themselves
and the plaintext-degradation path is asserted instead — matching how every
other store behaves before the ceremony.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from modules import contact_events as ce           # noqa: E402
from modules import memory_crypto as mc            # noqa: E402
from modules import partner_contact as pc          # noqa: E402

KEYS = mc.keys_ready()

#: Rare enough that finding it anywhere is proof of a leak, not a coincidence.
MARKER = "zarquon"
BODY = f"the {MARKER} biopsy result came back today"
URGENT_BODY = f"please call me right now, the {MARKER} result was bad"


class Store:
    """A throwaway contact-event database with recording forced on."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="contact_events_test_")
        self.db = os.path.join(self.tmp, "events.db")

    def __enter__(self):
        self._saved = os.environ.get(ce.ENV_FLAG)
        os.environ[ce.ENV_FLAG] = "1"
        return self

    def note(self, slot, text, *, when=None):
        return pc.note_contact(slot, text, when=when, db_path=self.db)

    def ask(self, name="girlfriend", now=None):
        return pc.status_for(name, now=now, db_path=self.db)

    def raw_bytes(self) -> bytes:
        return pathlib.Path(self.db).read_bytes()

    def rows(self):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            return con.execute(
                f"SELECT partner_key, partner_slot, timestamp, urgency "
                f"FROM {ce.TABLE}").fetchall()
        finally:
            con.close()

    def columns(self):
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            return [r[1] for r in con.execute(
                f"PRAGMA table_info({ce.TABLE})").fetchall()]
        finally:
            con.close()

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop(ce.ENV_FLAG, None)
        else:
            os.environ[ce.ENV_FLAG] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


def _need_keys():
    if not KEYS:
        print("      (skipped — no key set on this machine)")
        return False
    return True


def _no_leak(text: str):
    low = (text or "").lower()
    for phrase in (MARKER, "biopsy", "result was bad", "came back"):
        assert phrase not in low, f"{phrase!r} leaked into: {text!r}"


# ── the store holds no content, and cannot ───────────────────────────────────

def test_a_partner_message_writes_exactly_one_content_free_event():
    with Store() as s:
        assert s.note("gf", BODY) is True
        rows = s.rows()
        assert len(rows) == 1, f"expected one event, got {len(rows)}"

        assert set(s.columns()) == {"id", "partner_key", "partner_slot",
                                    "timestamp", "urgency"}, \
            f"the schema grew a column: {s.columns()}"

        blob = s.raw_bytes()
        for phrase in (MARKER, "biopsy", "came back"):
            assert phrase.encode() not in blob, \
                f"{phrase!r} is readable in the contact-event store"


def test_the_record_api_has_no_parameter_content_could_arrive_through():
    """The guarantee is the signature, not the caller's good manners."""
    import inspect
    params = set(inspect.signature(ce.record).parameters)
    assert params == {"partner_slot", "urgent", "when", "env", "db_path"}, \
        f"contact_events.record grew a parameter: {sorted(params)}"
    for banned in ("content", "message", "text", "body"):
        assert banned not in params


def test_a_second_message_is_a_second_event():
    with Store() as s:
        s.note("gf", "morning")
        s.note("gf", "afternoon")
        s.note("brother", "unrelated")
        assert ce.count("gf", db_path=s.db) == 2
        assert ce.count("brother", db_path=s.db) == 1


# ── encryption at rest ───────────────────────────────────────────────────────

def test_the_event_fields_are_ciphertext_at_rest():
    if not _need_keys():
        return
    with Store() as s:
        s.note("gf", URGENT_BODY)
        key, slot, stamp, urgency = s.rows()[0]
        for name, value in (("partner_slot", slot), ("timestamp", stamp),
                            ("urgency", urgency)):
            assert mc.is_encrypted(value), \
                f"{name} stored in plaintext: {value[:40]!r}"
        # the lookup handle is a keyed blind index — deterministic, but it is
        # not the slot and it does not reveal it
        assert key != "gf" and "gf" not in key
        assert key == ce._partner_key("gf"), "the lookup handle is not stable"

        blob = s.raw_bytes()
        assert b"gf" not in blob or key.encode() in blob
        # the calendar date must not be sitting in the file in the clear
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert today.encode() not in blob, \
            "the contact date is readable — the timestamp did not encrypt"


def test_the_aad_is_namespaced_so_a_blob_cannot_move_between_stores():
    if not _need_keys():
        return
    sealed = mc.encrypt_field("gf", ce.TABLE, "partner_slot")
    assert mc.decrypt_field(sealed, ce.TABLE, "partner_slot") == "gf"
    for table, column in (("partner_messages", "content"), ("memories", "content"),
                          (ce.TABLE, "timestamp")):
        try:
            mc.decrypt_field(sealed, table, column)
            raise AssertionError(f"a contact-event blob opened as {table}.{column}")
        except mc.MemoryCryptoError:
            pass


def test_without_keys_it_degrades_to_plaintext_like_every_other_store():
    real = ce._encryption_on
    ce._encryption_on = lambda: False
    try:
        with Store() as s:
            s.note("gf", BODY)
            key, slot, stamp, urgency = s.rows()[0]
            assert key == "gf" and slot == "gf"
            assert not mc.is_encrypted(stamp) and urgency in ("0", "1")
            # and it still answers correctly
            assert s.ask().lower().startswith("yes")
            # …and STILL holds no content
            _no_leak(s.raw_bytes().decode("utf-8", "replace"))
    finally:
        ce._encryption_on = real


def test_rows_written_before_the_ceremony_still_read_afterwards():
    """The two-predicate lookup: plain-slot rows and blind-index rows coexist."""
    if not _need_keys():
        return
    with Store() as s:
        real = ce._encryption_on
        ce._encryption_on = lambda: False
        try:
            s.note("gf", "written before the key existed")
        finally:
            ce._encryption_on = real
        s.note("gf", "written after")
        rows = ce.recent("gf", db_path=s.db)
        assert len(rows) == 2, f"a pre-ceremony row was stranded: {rows}"


# ── the answers ──────────────────────────────────────────────────────────────

def test_contact_today_answers_yes_with_timing_and_no_content():
    with Store() as s:
        now = datetime.now().astimezone().replace(hour=17, minute=0, second=0,
                                                  microsecond=0)
        assert s.note("gf", BODY, when=now.replace(hour=15, minute=4))
        out = s.ask(now=now)
        assert out.lower().startswith("yes"), out
        assert "Mousumi" in out and "3pm" in out, f"timing wrong: {out!r}"
        assert "nothing urgent" in out.lower()
        _no_leak(out)


def test_no_contact_answers_no():
    with Store() as s:
        s.note("brother", "unrelated")
        out = s.ask("girlfriend")
        assert out.lower().startswith("no"), out
        assert "Mousumi" in out
        assert "kinshuk" not in out.lower(), "another partner's traffic answered"


def test_urgent_message_surfaces_the_flag_but_still_no_content():
    with Store() as s:
        now = datetime.now().astimezone().replace(hour=17, minute=0, second=0,
                                                  microsecond=0)
        assert s.note("gf", URGENT_BODY, when=now.replace(hour=14, minute=50))
        out = s.ask(now=now)
        assert out.lower().startswith("yes"), out
        assert "important" in out.lower(), f"urgency not surfaced: {out!r}"
        assert "call her" in out.lower()
        _no_leak(out)


def test_one_urgent_among_routine_still_surfaces():
    """Fail toward surfacing — a single flagged message in a chatty day must not
    be averaged away into 'nothing urgent'."""
    with Store() as s:
        now = datetime.now().astimezone().replace(hour=18, minute=0, second=0,
                                                  microsecond=0)
        s.note("gf", "morning!", when=now.replace(hour=9))
        s.note("gf", "what's for dinner", when=now.replace(hour=12))
        s.note("gf", URGENT_BODY, when=now.replace(hour=16))
        out = s.ask(now=now)
        assert "important" in out.lower(), f"the urgent one was lost: {out!r}"
        _no_leak(out)


def test_yesterdays_contact_is_not_reported_as_today():
    with Store() as s:
        now = datetime.now().astimezone().replace(hour=11, minute=0, second=0,
                                                  microsecond=0)
        s.note("gf", BODY, when=now - timedelta(days=1))
        out = s.ask(now=now)
        assert out.lower().startswith("no"), out
        assert "yesterday" in out.lower(), f"last-seen hint missing: {out!r}"
        _no_leak(out)


# ── the urgency scan ─────────────────────────────────────────────────────────

def test_urgency_scan_flags_escalation_and_ignores_small_talk():
    for text in ("please call me", "this is urgent", "I need you", "come home",
                 "she's in hospital", "ASAP please", "it's important",
                 "are you okay?"):
        assert pc.assess_urgency(text) is True, f"missed urgency in {text!r}"
    for text in ("have you eaten?", "good morning", "what are you up to",
                 "sent you a photo", "the cat is being silly", ""):
        assert pc.assess_urgency(text) is False, f"false alarm on {text!r}"


def test_urgency_scan_reads_benglish_too():
    """She writes roman-script Bengali as often as English; an English-only list
    would miss the urgent half of how she actually types."""
    for text in ("ekhuni phone koro", "khub joruri", "bipod e achi",
                 "taratari esho"):
        assert pc.assess_urgency(text) is True, f"missed Benglish urgency: {text!r}"


def test_urgency_matches_whole_words_only():
    assert pc.assess_urgency("the important-looking parcel came") is True
    for text in ("we went to the firehouse museum", "unimportantly small",
                 "policeman joke", "seriousness aside"):
        assert pc.assess_urgency(text) is False, f"substring false alarm: {text!r}"


def test_only_the_boolean_crosses_into_the_store():
    """Two messages, same urgency, different words ⇒ indistinguishable on disk
    apart from randomised ciphertext. Nothing about the text survives."""
    with Store() as s:
        s.note("gf", "please call me")
        s.note("gf", "ekhuni phone koro")
        rows = ce.recent("gf", db_path=s.db)
        assert [r["urgent"] for r in rows] == [True, True]
        assert set(rows[0]) == {"timestamp", "urgent"}, rows[0]


# ── failure modes ────────────────────────────────────────────────────────────

def test_recording_off_says_it_cannot_tell_rather_than_no():
    """The silent-empty-read rule applied to a person: with recording off there
    are no rows, so "No, she didn't message" would be a confident claim built on
    an empty table and he could not tell the difference."""
    saved = os.environ.get(ce.ENV_FLAG)
    os.environ[ce.ENV_FLAG] = "0"
    try:
        assert ce.record("gf") is False, "an event was written with recording off"
        out = pc.status_for("girlfriend")
        assert not out.lower().startswith("no,"), \
            f"a missing record was reported as an absence of contact: {out!r}"
        assert "can't tell you either way" in out.lower(), out
        assert ce.ENV_FLAG in out, "the operator isn't told what to fix"
    finally:
        if saved is None:
            os.environ.pop(ce.ENV_FLAG, None)
        else:
            os.environ[ce.ENV_FLAG] = saved


def test_a_locked_keystore_says_it_cannot_look_rather_than_no():
    if not _need_keys():
        return
    with Store() as s:
        s.note("gf", BODY)
        assert s.ask().lower().startswith("yes")

        real_load, real_cache = mc.load_dek, mc._cached_dek
        def locked(*a, **k):
            raise mc.MemoryLockedError("key store unavailable (test)")
        mc.load_dek, mc._cached_dek = locked, None
        try:
            out = s.ask()
            assert not out.lower().startswith("no"), \
                f"a locked store was reported as no contact: {out!r}"
            assert "can't reach the record" in out.lower(), out
            _no_leak(out)
        finally:
            mc.load_dek, mc._cached_dek = real_load, real_cache

        assert s.ask().lower().startswith("yes"), "it did not recover"


def test_an_ambiguous_target_is_refused_not_guessed():
    with Store() as s:
        s.note("gf", BODY)
        for vague in ("her", "them", "someone", ""):
            out = s.ask(vague)
            assert "yes" not in out.lower()[:4], f"{vague!r} was guessed: {out!r}"
            _no_leak(out)


def test_a_raw_chat_id_is_refused():
    with Store() as s:
        out = s.ask("123456789")
        assert "raw chat id" in out.lower() or "won't" in out.lower(), out


# ── admin-only, at the real gate ─────────────────────────────────────────────

def test_a_guest_can_never_ask_and_the_allowlist_did_not_grow():
    """The refusal is `tier_allows`, called at the top of ActionEngine.execute()
    before dispatch, logging, or any governance pend."""
    from action_engine import (ADMIN_TIER, VIP_GUEST_TIER,
                               VIP_GUEST_ALLOWED_ACTIONS, tier_allows)

    assert tier_allows(ADMIN_TIER, pc.ACTION_TYPE) is True
    assert tier_allows(VIP_GUEST_TIER, pc.ACTION_TYPE) is False, \
        "a partner could ask about the other partner"
    for tier in ("", None, "friend", "unknown"):
        assert tier_allows(tier, pc.ACTION_TYPE) is False, \
            f"tier {tier!r} let it through — the gate must fail closed"
    assert pc.ACTION_TYPE not in VIP_GUEST_ALLOWED_ACTIONS
    assert VIP_GUEST_ALLOWED_ACTIONS == frozenset({"tavily_search", "web_search"}), \
        f"the VIP allowlist grew: {sorted(VIP_GUEST_ALLOWED_ACTIONS)}"


def test_governance_registers_it_and_the_policy_is_still_fail_safe():
    import json
    rules = json.loads(pathlib.Path(__file__).resolve().parent
                       .joinpath("governance.json").read_text(encoding="utf-8"))
    assert rules["rules"][pc.ACTION_TYPE] == "AUTO", \
        "an unlisted action defaults to BLOCK — it must be registered"
    assert rules["rules"]["message_partner"] == "CONFIRM", \
        "the outbound send must stay CONFIRM"


# ── wiring, and the promises made to the owner ───────────────────────────────

def test_it_is_dispatched_and_kept_out_of_the_synthesis_sets():
    """Not on DATA_ACTIONS / _REMOTE_DATA_ACTIONS on purpose.

    `summarize_partner_chat` is on them because its output is raw transcript
    needing an LLM to become readable. This answer is already a finished
    sentence, and routing a discreet line through a model invites it to
    elaborate from surrounding context — the leak the feature exists to prevent.
    """
    here = pathlib.Path(__file__).resolve().parent
    engine = here.joinpath("action_engine.py").read_text(encoding="utf-8")
    assert f'elif action == "{pc.ACTION_TYPE}"' in engine, "never dispatched"

    main = here.joinpath("main.py").read_text(encoding="utf-8")
    assert pc.ACTION_TYPE not in main.split("_REMOTE_DATA_ACTIONS", 1)[1][:600], \
        "the discreet answer would be re-written by synthesis"

    brain = here.joinpath("brain.py").read_text(encoding="utf-8")
    assert brain.count(f'"{pc.ACTION_TYPE}"') >= 2, \
        "both prompt catalogues (HUD and remote) must list it"


def test_the_contact_event_write_is_wired_and_not_behind_the_transcript_flag():
    """The separate store exists so the discreet answer works on a machine where
    keeping her words is switched OFF. If the write moved inside the
    partner-log flag, that property would be silently gone."""
    here = pathlib.Path(__file__).resolve().parent
    hook = here.joinpath("main.py").read_text(encoding="utf-8") \
        .split("async def run_remote_command", 1)[1][:5000]
    assert "partner_contact.note_contact" in hook, "the event is never recorded"
    assert "logging_enabled" not in hook, \
        "the contact event was put behind the transcript flag"


def test_extraction_and_retention_were_not_touched():
    """The owner's standing ruling: fact extraction runs for every recognised
    caller, partners included, and is NOT behind the partner-log flag."""
    here = pathlib.Path(__file__).resolve().parent
    hook = here.joinpath("main.py").read_text(encoding="utf-8") \
        .split("async def run_remote_command", 1)[1][:5000]
    assert "extract_and_store_memory" in hook, "extraction was removed"
    assert hook.index("extract_and_store_memory") < hook.index("if tier != ADMIN_TIER"), \
        "extraction moved behind the partner gate — retention must be unchanged"
    assert "partner_log.log_inbound" in hook, "the transcript store was unwired"

    log_src = here.joinpath("modules", "partner_log.py").read_text(encoding="utf-8")
    write = log_src.split("def log_inbound", 1)[1].split("def recent", 1)[0]
    assert "logging_enabled" in write, \
        "partner_log stopped honouring its opt-in flag"


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    print(f"key set present: {KEYS}\n")
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
