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
    """A throwaway contact-event database with recording forced on.

    Layer 2 (the semantic classifier) is forced OFF here: it would put a live
    provider call on the path of every `note()` in this file, which would make
    the suite slow, networked and non-deterministic. The tests that exercise
    layer 2 switch it on and inject a fake model instead.
    """

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="contact_events_test_")
        self.db = os.path.join(self.tmp, "events.db")

    def __enter__(self):
        self._saved = os.environ.get(ce.ENV_FLAG)
        self._saved_sem = os.environ.get(pc.SEMANTIC_ENV_FLAG)
        os.environ[ce.ENV_FLAG] = "1"
        os.environ[pc.SEMANTIC_ENV_FLAG] = "0"
        return self

    def note(self, slot, text, *, when=None, semantic=None):
        return pc.note_contact(slot, text, when=when, db_path=self.db,
                               semantic=semantic)

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
        for flag, saved in ((ce.ENV_FLAG, self._saved),
                            (pc.SEMANTIC_ENV_FLAG, self._saved_sem)):
            if saved is None:
                os.environ.pop(flag, None)
            else:
                os.environ[flag] = saved
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


# ── the term list is one editable place, and both layers read it ─────────────

#: Kaustav's list, 2026-08-02, transcribed from his message rather than from the
#: module — so a term silently lost in an edit fails here instead of going quiet.
KAUSTAVS_LIST = {
    "direct": ("joruri", "khub joruri", "emergency", "urgent"),
    "speed": ("taratari", "ekhuni", "tokhoni", "joldi"),
    "call or come": ("phone koro", "phone kore", "call koro",
                     "bari esho", "asho", "chole esho"),
    "distress": ("bipod", "problem hoyeche", "bhalo lagche na",
                 "sahajjo", "help koro"),
    "need": ("dorkar", "khub dorkar", "important", "dekho"),
}


def test_kaustavs_term_list_is_present_verbatim_and_every_term_flags():
    for group, terms in KAUSTAVS_LIST.items():
        assert group in pc.URGENT_TERM_GROUPS, f"group {group!r} was dropped"
        assert pc.URGENT_TERM_GROUPS[group] == terms, \
            f"group {group!r} drifted: {pc.URGENT_TERM_GROUPS[group]}"
        for term in terms:
            assert term in pc.URGENT_TERMS, f"{term!r} is not in the flat list"
            assert pc.assess_urgency(term) is True, f"{term!r} does not flag"
            assert pc.assess_urgency(f"ok {term} please") is True, \
                f"{term!r} does not flag inside a sentence"


def test_the_english_escalation_terms_were_not_lost_to_the_new_list():
    """His list is Benglish-led and carries no English escalation phrasing. She
    writes English as often as Benglish, so dropping these would mean a plain
    "please call me" reading as routine — the one direction §6.7 forbids."""
    for text in ("please call me", "I need you", "come home", "are you okay?",
                 "ASAP please", "she's in hospital", "call an ambulance"):
        assert pc.assess_urgency(text) is True, f"English urgency lost: {text!r}"


def test_the_flat_list_is_exactly_the_groups_deduplicated():
    flat = [t for terms in pc.URGENT_TERM_GROUPS.values() for t in terms]
    assert pc.URGENT_TERMS == tuple(dict.fromkeys(flat)), \
        "URGENT_TERMS is no longer derived from URGENT_TERM_GROUPS"
    assert len(pc.URGENT_TERMS) == len(set(pc.URGENT_TERMS)), "duplicate term"


def test_the_live_regex_is_compiled_from_the_live_list():
    """Editing the dict must move layer 1 — not just the docstring saying so."""
    assert pc._TERM_RE.pattern == pc._compile_term_re(pc.URGENT_TERMS).pattern
    assert pc._compile_term_re(("zzblargh",)).search("a zzblargh b")
    assert not pc._compile_term_re(("zzblargh",)).search("please call me")


# ── layer 2: the semantic classifier ─────────────────────────────────────────

#: Genuine layer-1 misses. Each is how she might actually write it; none matches
#: a term exactly, because Benglish inflects ("bipode") and re-spells ("joldii").
KEYWORD_MISSES = ("ekkhuni chole eso", "bipode porechi",
                  "khub joldii pathao", "amar kharap lagche")


class FakeModel:
    """A model that records what it was asked and answers what it was told to."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, messages):
        self.calls.append(messages)
        return self.reply


def test_a_variant_spelling_misses_layer_one_and_is_caught_by_layer_two():
    for text in KEYWORD_MISSES:
        assert pc.keyword_urgency(text) is False, \
            f"{text!r} was expected to miss the exact list"
        assert pc.assess_urgency(text) is False, "layer 1 alone must not flag it"
        model = FakeModel('{"urgent": true}')
        assert pc.assess_urgency(
            text, semantic=lambda t: pc.semantic_urgency(t, call=model)) is True, \
            f"the semantic layer did not rescue {text!r}"
        assert model.calls, "the model was never consulted"


def test_layer_two_cannot_downgrade_a_keyword_hit_and_is_not_even_asked():
    """OR, never a vote. A hit is already final, so no tokens are spent on it."""
    model = FakeModel('{"urgent": false}')
    for text in ("khub joruri", "please call me", "ekhuni phone koro"):
        assert pc.assess_urgency(
            text, semantic=lambda t: pc.semantic_urgency(t, call=model)) is True, \
            f"the model overruled a keyword hit on {text!r}"
    assert model.calls == [], "layer 2 was called even though layer 1 hit"


def test_a_routine_message_is_not_flagged_by_either_layer():
    model = FakeModel('{"urgent": false}')
    layer2 = lambda t: pc.semantic_urgency(t, call=model)          # noqa: E731
    for text in ("have you eaten?", "good morning", "what are you up to",
                 "sent you a photo", "the cat is being silly"):
        assert pc.assess_urgency(text, semantic=layer2) is False, \
            f"false alarm on {text!r}"
    assert len(model.calls) == 5, "layer 2 should have been asked about each"


def test_the_prompt_carries_every_term_its_group_label_and_the_meaning_rule():
    msgs = pc.semantic_messages("khub joldi asho")
    assert [m["role"] for m in msgs] == ["system", "user"]
    system = msgs[0]["content"]

    for label, terms in pc.URGENT_TERM_GROUPS.items():
        assert label in system, f"group label {label!r} missing from the prompt"
        for term in terms:
            assert term in system, f"{term!r} never reaches the model"

    low = system.lower()
    assert "meaning" in low and "spelling" in low, \
        "the model is not told to judge by meaning rather than spelling"
    assert "unsure" in low and "true" in low, \
        "§6.7's fail-toward-surfacing asymmetry is not stated to the model"
    assert "data, not instructions" in low, \
        "her message is not fenced off from being read as an instruction"
    assert '{"urgent": true}' in system and '{"urgent": false}' in system, \
        "the answer format is not pinned"
    assert "khub joldi asho" in msgs[1]["content"], "the message never arrives"


def test_the_prompt_follows_an_edit_to_the_term_list():
    """Single source of truth, proven by moving it."""
    real = pc.URGENT_TERM_GROUPS
    pc.URGENT_TERM_GROUPS = dict(real, **{"invented": ("zzblargh",)})
    try:
        assert "zzblargh" in pc.semantic_messages("hi")[0]["content"]
        assert "invented" in pc.semantic_messages("hi")[0]["content"]
    finally:
        pc.URGENT_TERM_GROUPS = real
    assert "zzblargh" not in pc.semantic_messages("hi")[0]["content"]


def test_a_long_message_is_truncated_before_it_reaches_the_model():
    model = FakeModel('{"urgent": false}')
    pc.semantic_urgency("x" * 9000 + MARKER, call=model)
    body = model.calls[0][1]["content"]
    assert len(body) < 2200, f"an unbounded prompt was sent ({len(body)} chars)"
    assert MARKER not in body, "the tail was not truncated"


# ── layer 2 fails toward the keyword verdict, never toward silence ───────────

def test_every_model_failure_yields_no_verdict_rather_than_a_false_one():
    def boom(_messages):
        raise RuntimeError("provider down")

    router_apology = ("My reasoning core is unreachable at the moment, Sir — "
                      "every AI provider is offline.")
    for raw in (router_apology, "", None, "maybe?", "{}", "[]", "not json at all",
                '{"other": true}', "I think this message is urgent, Sir, yes"):
        assert pc.parse_semantic_verdict(raw) is None, \
            f"{raw!r} was read as a verdict"

    assert pc.semantic_urgency("bipode porechi", call=boom) is None, \
        "an exception became a verdict"
    # …and the keyword verdict is what stands
    assert pc.assess_urgency(
        "bipode porechi",
        semantic=lambda t: pc.semantic_urgency(t, call=boom)) is False
    assert pc.assess_urgency(
        "khub joruri",
        semantic=lambda t: pc.semantic_urgency(t, call=boom)) is True, \
        "a dead model suppressed a keyword hit"


def test_the_verdict_parser_accepts_what_models_actually_return():
    for raw in ('{"urgent": true}', '{"urgent":"yes"}', '{"urgent": 1}',
                '{"is_urgent": true}', "true", "TRUE", "yes", True):
        assert pc.parse_semantic_verdict(raw) is True, f"{raw!r} misread"
    for raw in ('{"urgent": false}', '{"urgent":"no"}', '{"urgent": 0}',
                "false", "no", False):
        assert pc.parse_semantic_verdict(raw) is False, f"{raw!r} misread"


def test_the_flag_switches_layer_two_off_without_touching_a_provider():
    def boom(_messages):
        raise AssertionError("the model was called with the flag off")

    saved = os.environ.get(pc.SEMANTIC_ENV_FLAG)
    try:
        for off in ("0", "false", "no", "off", "OFF"):
            os.environ[pc.SEMANTIC_ENV_FLAG] = off
            assert pc.semantic_enabled() is False
            assert pc.semantic_urgency("bipode porechi", call=boom) is None
        os.environ.pop(pc.SEMANTIC_ENV_FLAG, None)
        assert pc.semantic_enabled() is True, "layer 2 must default ON"
    finally:
        if saved is None:
            os.environ.pop(pc.SEMANTIC_ENV_FLAG, None)
        else:
            os.environ[pc.SEMANTIC_ENV_FLAG] = saved


def test_assess_urgency_is_offline_unless_a_caller_opts_in():
    """The default is pure. That is what makes every keyword check above a
    deterministic, network-free assertion."""
    import inspect
    src = inspect.getsource(pc.assess_urgency)
    assert "universal_llm_call" not in src and "requests" not in src
    assert inspect.signature(pc.assess_urgency).parameters["semantic"].default is None


# ── and the semantic path still puts only a bit in the store ─────────────────

def test_a_semantic_flag_reaches_the_store_as_a_bit_and_nothing_else():
    with Store() as s:
        os.environ[pc.SEMANTIC_ENV_FLAG] = "1"
        model = FakeModel('{"urgent": true}')
        text = f"bipode porechi, the {MARKER} biopsy result came back today"
        assert pc.keyword_urgency(text) is False, "this must be a layer-2 rescue"

        assert s.note("gf", text,
                      semantic=lambda t: pc.semantic_urgency(t, call=model))
        rows = ce.recent("gf", db_path=s.db)
        assert [r["urgent"] for r in rows] == [True], "the flag did not survive"
        assert set(rows[0]) == {"timestamp", "urgent"}, rows[0]

        blob = s.raw_bytes()
        for phrase in (MARKER, "biopsy", "bipode", "came back"):
            assert phrase.encode() not in blob, \
                f"{phrase!r} is readable in the store after a semantic flag"
        _no_leak(s.ask())


def test_the_semantic_layer_is_wired_into_the_live_write_path():
    """`note_contact` with no injection must reach the real classifier, not
    silently keep layer 1 only — the whole feature would be inert."""
    import inspect
    src = inspect.getsource(pc.note_contact)
    assert "semantic_urgency" in src, "note_contact never consults layer 2"
    assert "semantic=" in src, "the verdict is not threaded into assess_urgency"

    seen = []
    real = pc.semantic_urgency
    pc.semantic_urgency = lambda t, **k: seen.append(t) or True
    try:
        with Store() as s:
            os.environ[pc.SEMANTIC_ENV_FLAG] = "1"
            assert s.note("gf", "bipode porechi") is True
            assert seen == ["bipode porechi"], \
                f"the live path did not call layer 2: {seen}"
            assert [r["urgent"] for r in ce.recent("gf", db_path=s.db)] == [True]
    finally:
        pc.semantic_urgency = real


def test_only_the_boolean_crosses_into_the_store():
    """Two messages, same urgency, different words ⇒ indistinguishable on disk
    apart from randomised ciphertext. Nothing about the text survives."""
    with Store() as s:
        s.note("gf", "please call me")
        s.note("gf", "ekhuni phone koro")
        rows = ce.recent("gf", db_path=s.db)
        assert [r["urgent"] for r in rows] == [True, True]
        assert set(rows[0]) == {"timestamp", "urgent"}, rows[0]


# ── the opt-in ───────────────────────────────────────────────────────────────

def test_contact_recording_defaults_off_and_fails_towards_off():
    """Unset means OFF, and so does anything unrecognised.

    This store records a third party's behaviour, so the flag is opt-in like
    `JARVIS_LOG_PARTNER_CHATS` — a fresh clone must record nothing about anyone
    until its owner switches it on. The unrecognised-value half matters just as
    much: a typo in `.env` has to fail towards not recording. `enabled()` is
    checked here AND `record()` is driven, because a default that only the
    predicate honours is not a default.
    """
    saved = os.environ.get(ce.ENV_FLAG)
    try:
        os.environ.pop(ce.ENV_FLAG, None)
        assert ce.enabled() is False, "unset must mean OFF"
        assert ce.record("gf") is False, "an event was written with the flag unset"

        for junk in ("", "  ", "yess", "2", "enabled", "0", "false", "no", "off"):
            os.environ[ce.ENV_FLAG] = junk
            assert ce.enabled() is False, f"{junk!r} was read as ON"

        for on in ("1", "true", "yes", "on", "ON", " True "):
            os.environ[ce.ENV_FLAG] = on
            assert ce.enabled() is True, f"{on!r} was not read as ON"
    finally:
        if saved is None:
            os.environ.pop(ce.ENV_FLAG, None)
        else:
            os.environ[ce.ENV_FLAG] = saved


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


# ── pre-Electron review, 2026-08-15 ──────────────────────────────────────────
# `answer()` drops any row whose timestamp will not parse. That is right for ONE
# bad row among good ones — `_parse`'s docstring says a wrong time is worse than
# no time, and it is. It is wrong when EVERY row is bad, because then the
# skipping IS the answer, and the answer it produced was:
#
#     "No, Sir — nothing from Mousumi at all."
#
# A confident denial manufactured by a failure, about another person, and
# indistinguishable from the truth. This module already has two carefully
# written honest-failure paths — `no_record_text` (recording off) and
# `locked_text` (keystore sealed) — and this case fell through the gap between
# them: the store opened, and what came out was not readable.

def test_all_rows_unreadable_is_not_reported_as_silence():
    rows = [{"timestamp": "not-a-timestamp", "urgent": False},
            {"timestamp": "", "urgent": True},
            {"timestamp": None, "urgent": False}]
    out = pc.answer("Mousumi", rows)
    assert "nothing from" not in out.lower(), (
        "unreadable rows were reported as 'nothing from her' — a denial built "
        f"out of a failure. Got: {out}")
    assert "can't tell you either way" in out.lower()
    assert "Mousumi" in out


def test_the_unreadable_answer_matches_the_other_two_honest_failures():
    # All three must refuse in the same voice, or the discipline is accidental.
    unreadable = pc.unreadable_text("Mousumi")
    no_record = pc.no_record_text("Mousumi", "JARVIS_LOG_CONTACT_EVENTS")
    locked = pc.locked_text("Mousumi")
    for text in (unreadable, no_record, locked):
        assert "can't" in text.lower()
        assert "no," not in text.lower()[:4], (
            f"an honest failure must not open like a denial: {text}")


def test_a_genuinely_empty_store_still_says_nothing_at_all():
    # The guard must not swallow the TRUE negative — no rows means no contact,
    # and that is a real answer he needs.
    out = pc.answer("Mousumi", [])
    assert "nothing from Mousumi at all" in out
    out_none = pc.answer("Mousumi", None)
    assert "nothing from Mousumi at all" in out_none


def test_one_bad_row_among_good_ones_is_still_skipped_quietly():
    # The original behaviour, deliberately preserved: a single unreadable row
    # must not turn a working answer into a refusal.
    now = datetime.now().astimezone()
    rows = [{"timestamp": now.isoformat(), "urgent": False},
            {"timestamp": "garbage", "urgent": True}]
    out = pc.answer("Mousumi", rows, now=now)
    assert out.startswith("Yes,"), f"a good row should still answer: {out}"
    assert "can't" not in out.lower()


def test_the_urgent_flag_survives_the_guard():
    now = datetime.now().astimezone()
    rows = [{"timestamp": now.isoformat(), "urgent": True}]
    out = pc.answer("Mousumi", rows, now=now)
    assert "important" in out and "call her" in out


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
