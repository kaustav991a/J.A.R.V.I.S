"""Harness for memory PROVENANCE — `memories.source`, 2026-08-02.

Until now a fact the Render gateway captured while the PC was off, sealed, and
the desk later drained was stored as `(Fact, ciphertext, WHO)` — byte-identical
to something he said in person. The cloud path carries a weaker guarantee (the
gateway sees that turn in plaintext, and a sealed record's `who` is a claim, not
a credential), so the two have to be tellable apart.

This proves five things, and the last two are the ones that could quietly go
wrong:

  1. a live write lands `source='desk'`; a drained fact lands `source='cloud'`,
     through the REAL fact_drain -> fact_sink -> governance stack;
  2. the two are queryably distinguishable, and the audit count needs no key;
  3. DEDUP IS UNCHANGED — the same fact from both routes is still one row, and
     the FIRST writer's provenance stands in both directions;
  4. the backfill migration preserves every existing row: identical ids, and
     category / user / timestamp / content / content_hash byte-identical, and
     every content still DECRYPTS to what it did before the column existed;
  5. that verification is not vacuous — it is shown FAILING on a tampered copy.

Keys, the memory database, the ledger, the outbox and the dead-letter store are
all redirected into a temp directory. The real jarvis_longterm.db is never
written. The only stub is the extractor's Groq call.
"""

import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import memory_manager as mm
import migrate_memory_source as mig
from modules import fact_drain as fd
from modules import fact_outbox as fo
from modules import fact_seal as fs
from modules import fact_sink as sink
from modules import memory_crypto as mc

# -- isolation ---------------------------------------------------------------

_TMP = Path(tempfile.mkdtemp(prefix="jarvis_memsource_"))
_REAL_DB = Path(mm._DB_PATH)
_REAL_LEDGER, _REAL_OUTBOX = fd.LEDGER_DB, fo.OUTBOX_FILE

mc.DPAPI_KEY_FILE = _TMP / "jarvis_key.dpapi"
mc.RECOVERY_KEY_FILE = _TMP / "jarvis_key.recovery"
mc.X25519_KEY_FILE = _TMP / "jarvis_x25519.enc"
mc.CANARY_FILE = _TMP / "jarvis_key.canary"
fs.QUARANTINE_DIR = _TMP / "fact_quarantine"
fo.OUTBOX_FILE = _TMP / "fact_outbox.jsonl"
fo.DESK_KEY_FILE = _TMP / "fact_desk_key.json"
fd.LEDGER_DB = _TMP / "jarvis_fact_ledger.db"

_DB = _TMP / "test_longterm.db"
mm._DB_PATH = str(_DB)

# The migration script must point at the SAME fixture, never the live store.
mig.DB_PATH = _DB
mig.ASIDE_DIR = _TMP / "aside"


def _fake_extract(user_text, user="KAUSTAV"):
    """Stands in for the Groq call ONLY. add_memory, AES-256-GCM, the blind
    index and the duplicate check below it are all the production ones."""
    return [{"category": "Fact", "content": user_text}]


def _reset():
    for p in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE):
        if p.exists():
            p.unlink()
    mc.clear_cache()
    mc.initialise_keys()

    shutil.rmtree(fs.QUARANTINE_DIR, ignore_errors=True)
    shutil.rmtree(mig.ASIDE_DIR, ignore_errors=True)
    fo.reset_state()
    for p in (fo.OUTBOX_FILE, fo.DESK_KEY_FILE, fd.LEDGER_DB, _DB):
        if p.exists():
            p.unlink()
    fd.init_db()
    mm._init_db()

    mm.extract_memories_from_input = _fake_extract
    sink.install()


def _raw(query, params=()):
    conn = sqlite3.connect(str(_DB))
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def _snapshot():
    """Everything the migration must not move."""
    return _raw("SELECT id, category, user, timestamp, content, content_hash "
                "FROM memories ORDER BY id")


def _strip_source():
    """Make the rows look like they were written before the column existed."""
    conn = sqlite3.connect(str(_DB))
    try:
        conn.execute("UPDATE memories SET source = NULL")
        conn.commit()
    finally:
        conn.close()


def _seal(**over) -> dict:
    payload = {
        "v": fs.RECORD_VERSION,
        "id": uuid.uuid4().hex,
        "ts": "2026-08-02T09:00:00+00:00",
        "who": "KAUSTAV",
        "tier": "admin",
        "user_text": "the spare key lives in the hall drawer",
        "reply": "Noted, Sir.",
    }
    payload.update(over)
    return fs.seal_fact(payload, fs.desk_public_b64())


# -- 1. the two routes tag themselves ----------------------------------------

def test_a_live_write_is_tagged_desk():
    _reset()
    assert mm.add_memory("he takes the 8am train", "Fact", "KAUSTAV") is True
    assert _raw("SELECT source FROM memories")[0][0] == mm.SOURCE_DESK


def test_every_default_caller_still_writes_desk():
    """The whole point of the default: nothing existing had to change."""
    _reset()
    mm.add_memory("positional, like action_engine calls it", "Fact", "KAUSTAV")
    mm.add_memory("keyword, like memory.remember_fact calls it", "Preference")
    mm.extract_and_persist("through the extraction path", "KAUSTAV")
    sources = {r[0] for r in _raw("SELECT source FROM memories")}
    assert sources == {mm.SOURCE_DESK}, sources


def test_a_drained_fact_is_tagged_cloud():
    """Through the real drain, the real governed sink and the real gate."""
    _reset()
    result = fd.drain_records([_seal(user_text="the boiler is serviced in October")])
    assert result["stored"] == 1, result
    rows = _raw("SELECT content, source FROM memories")
    assert len(rows) == 1
    assert rows[0][1] == mm.SOURCE_CLOUD
    assert rows[0][0].startswith(mc.FIELD_PREFIX), "content stopped being encrypted"


def test_the_drain_is_the_only_thing_in_the_tree_that_says_cloud():
    """A second caller passing SOURCE_CLOUD would make the label meaningless."""
    here = Path(__file__).resolve().parent
    callers = []
    for path in sorted(here.rglob("*.py")):
        if path.name.startswith(("test_", "migrate_")) or "venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "SOURCE_CLOUD" not in line:
                continue
            callers.append((path.name, lineno, stripped))
    names = {c[0] for c in callers}
    assert names <= {"memory_manager.py", "fact_sink.py"}, callers
    assert "fact_sink.py" in names, "the drain no longer tags its writes"


# -- 2. queryably distinguishable --------------------------------------------

def test_the_two_routes_are_queryably_separable():
    _reset()
    mm.add_memory("said in person over coffee", "Fact", "KAUSTAV")
    fd.drain_records([_seal(user_text="told the phone while the desk was dark")])

    desk = mm.get_memories_by_source(mm.SOURCE_DESK)
    cloud = mm.get_memories_by_source(mm.SOURCE_CLOUD)
    assert [m["content"] for m in desk] == ["said in person over coffee"]
    assert [m["content"] for m in cloud] == ["told the phone while the desk was dark"]
    assert {m["source"] for m in desk} == {mm.SOURCE_DESK}
    assert {m["source"] for m in cloud} == {mm.SOURCE_CLOUD}


def test_the_read_paths_report_the_source():
    _reset()
    mm.add_memory("a desk fact", "Fact", "KAUSTAV")
    fd.drain_records([_seal(user_text="a cloud fact")])
    by_content = {m["content"]: m["source"] for m in mm.get_full_profile("KAUSTAV")}
    assert by_content == {"a desk fact": mm.SOURCE_DESK,
                          "a cloud fact": mm.SOURCE_CLOUD}
    recent = mm.get_relevant_memories(user="KAUSTAV", limit=5)
    assert all("source" in m for m in recent)


def test_the_audit_count_needs_no_key_at_all():
    """`source` is plaintext precisely so provenance can be surveyed without
    unlocking anything. That is the trade the ruling made."""
    _reset()
    mm.add_memory("desk one", "Fact", "KAUSTAV")
    mm.add_memory("desk two", "Fact", "KAUSTAV")
    fd.drain_records([_seal(user_text="cloud one")])

    saved = mc.DPAPI_KEY_FILE
    mc.DPAPI_KEY_FILE = _TMP / "nonexistent.dpapi"
    mc.clear_cache()
    try:
        counts = mm.source_counts()
    finally:
        mc.DPAPI_KEY_FILE = saved
        mc.clear_cache()
    assert counts[mm.SOURCE_DESK] == 2 and counts[mm.SOURCE_CLOUD] == 1
    assert counts["untagged"] == 0


def test_source_is_plaintext_on_disk_and_leaks_only_the_route():
    _reset()
    mm.add_memory("the alarm code is 4417", "Fact", "KAUSTAV")
    value = _raw("SELECT source FROM memories")[0][0]
    assert value == mm.SOURCE_DESK, "source was stored sealed; it must stay filterable"
    assert not mc.is_encrypted(value)
    # The ruling traded a two-value metadata leak for a queryable column. The
    # payload beside it must still be sealed.
    assert b"4417" not in _DB.read_bytes()


# -- 3. dedup is unchanged, and the first writer's provenance stands ----------

def test_the_same_fact_from_both_routes_does_not_double_store():
    _reset()
    assert mm.add_memory("the mortgage renews in March", "Fact", "KAUSTAV") is True
    result = fd.drain_records([_seal(user_text="the mortgage renews in March")])
    assert result["stored"] == 0 and result["duplicates"] == 1, result
    assert len(_raw("SELECT id FROM memories")) == 1, "adding source broke dedup"


def test_a_cloud_echo_cannot_relabel_a_fact_he_gave_in_person():
    _reset()
    mm.add_memory("his passport expires in June", "Fact", "KAUSTAV")
    fd.drain_records([_seal(user_text="his passport expires in June")])
    assert _raw("SELECT source FROM memories")[0][0] == mm.SOURCE_DESK


def test_a_live_restatement_cannot_launder_a_cloud_drained_fact():
    """The other direction, and the one that actually matters: a fact that
    arrived by the weaker path must not become `desk` just because he later
    repeated it."""
    _reset()
    fd.drain_records([_seal(user_text="the meeting moved to Thursday")])
    assert _raw("SELECT source FROM memories")[0][0] == mm.SOURCE_CLOUD
    assert mm.add_memory("the meeting moved to Thursday", "Fact", "KAUSTAV") is False
    assert _raw("SELECT source FROM memories")[0][0] == mm.SOURCE_CLOUD
    assert len(_raw("SELECT id FROM memories")) == 1


def test_dedup_still_ignores_case_and_spacing_with_a_source_column():
    _reset()
    assert mm.add_memory("Sir works at Fortmindz", "Fact") is True
    assert mm.add_memory("sir   works at fortmindz", "Fact") is False
    assert len(_raw("SELECT id FROM memories")) == 1


def test_different_users_may_still_hold_the_same_fact():
    _reset()
    assert mm.add_memory("prefers tea over coffee", "Preference", "KAUSTAV") is True
    assert mm.add_memory("prefers tea over coffee", "Preference", "MOUSUMI") is True
    assert len(_raw("SELECT id FROM memories")) == 2


# -- 4. refusals and legacy rows ---------------------------------------------

def test_an_unknown_source_is_refused_not_coerced():
    """Coercing it to `desk` would mislabel the write, which defeats the column."""
    _reset()
    assert mm.add_memory("from somewhere unnamed", "Fact", "KAUSTAV",
                         source="smuggled") is False
    assert _raw("SELECT COUNT(*) FROM memories")[0][0] == 0


def test_source_matching_is_forgiving_about_case_and_spacing():
    _reset()
    assert mm.add_memory("normalised", "Fact", "KAUSTAV", source="  CLOUD ") is True
    assert _raw("SELECT source FROM memories")[0][0] == mm.SOURCE_CLOUD


def test_an_untagged_legacy_row_reads_as_desk():
    """Between the column landing at boot and the migration running, rows are
    NULL. They predate cloud drain, so `desk` is correct, not a guess."""
    _reset()
    mm.add_memory("an old row written before the column existed", "Fact", "KAUSTAV")
    _strip_source()
    profile = mm.get_full_profile("KAUSTAV")
    assert [m["source"] for m in profile] == [mm.SOURCE_DESK]
    assert mm.source_counts()["untagged"] == 1, "untagged rows must stay visible"
    assert len(mm.get_memories_by_source(mm.SOURCE_DESK)) == 1
    assert mm.get_memories_by_source(mm.SOURCE_CLOUD) == []


def test_the_column_add_is_idempotent():
    _reset()
    conn = sqlite3.connect(str(_DB))
    try:
        assert mm._ensure_source_column(conn) is False, "the column was added twice"
    finally:
        conn.close()
    mm._init_db()
    mm._init_db()
    assert "source" in {r[1] for r in _raw("PRAGMA table_info(memories)")}


# -- 5. the migration: nothing lost, nothing moved ---------------------------

def test_the_backfill_preserves_every_existing_row():
    _reset()
    facts = [("Sir prefers dark-mode interfaces", "Preference"),
             ("tumi kemon achho means how are you", "Fact"),
             ("Always call him Sir, never boss", "Correction")]
    for text, category in facts:
        mm.add_memory(text, category, "KAUSTAV")
    _strip_source()

    before = _snapshot()
    readable_before = {m["content"] for m in mm.get_full_profile("KAUSTAV")}
    assert readable_before == {t for t, _ in facts}

    copy_path = _TMP / "copy_preserves.db"
    shutil.copy2(_DB, copy_path)
    assert mig.backfill_copy(copy_path) == 3
    assert mig.verify_copy(_DB, copy_path) == []

    conn = sqlite3.connect(str(copy_path))
    try:
        after = conn.execute(
            "SELECT id, category, user, timestamp, content, content_hash "
            "FROM memories ORDER BY id").fetchall()
        sources = {r[0] for r in conn.execute("SELECT source FROM memories")}
    finally:
        conn.close()
    assert after == before, "the backfill moved a column it had no business touching"
    assert sources == {mm.SOURCE_DESK}


def test_the_migrated_content_still_decrypts_to_the_same_sentences():
    _reset()
    originals = ["the wedding is in December", "he dislikes loud notifications"]
    for text in originals:
        mm.add_memory(text, "Fact", "KAUSTAV")
    _strip_source()

    copy_path = _TMP / "copy_decrypt.db"
    shutil.copy2(_DB, copy_path)
    mig.backfill_copy(copy_path)
    assert mig.verify_copy(_DB, copy_path) == []

    saved = mm._DB_PATH
    mm._DB_PATH = str(copy_path)
    try:
        got = {m["content"] for m in mm.get_full_profile("KAUSTAV")}
        sources = {m["source"] for m in mm.get_full_profile("KAUSTAV")}
    finally:
        mm._DB_PATH = saved
    assert got == set(originals), "a row did not survive the migration readably"
    assert sources == {mm.SOURCE_DESK}


def test_the_verification_actually_fails_on_a_tampered_copy():
    """A verify that always passes is decoration. Three ways to break it."""
    _reset()
    mm.add_memory("row one", "Fact", "KAUSTAV")
    mm.add_memory("row two", "Fact", "KAUSTAV")
    _strip_source()

    # (a) a content value changed
    tampered = _TMP / "tampered_content.db"
    shutil.copy2(_DB, tampered)
    mig.backfill_copy(tampered)
    conn = sqlite3.connect(str(tampered))
    conn.execute("UPDATE memories SET content = ? WHERE id = 1", ("rewritten",))
    conn.commit()
    conn.close()
    problems = mig.verify_copy(_DB, tampered)
    assert problems, "a rewritten content passed verification"

    # (b) a row deleted
    missing = _TMP / "tampered_missing.db"
    shutil.copy2(_DB, missing)
    mig.backfill_copy(missing)
    conn = sqlite3.connect(str(missing))
    conn.execute("DELETE FROM memories WHERE id = 1")
    conn.commit()
    conn.close()
    problems = mig.verify_copy(_DB, missing)
    assert any("row ids differ" in p for p in problems), problems

    # (c) a row left untagged
    partial = _TMP / "tampered_partial.db"
    shutil.copy2(_DB, partial)
    mig.backfill_copy(partial)
    conn = sqlite3.connect(str(partial))
    conn.execute("UPDATE memories SET source = NULL WHERE id = 1")
    conn.commit()
    conn.close()
    problems = mig.verify_copy(_DB, partial)
    assert any("still empty" in p for p in problems), problems


def test_apply_runs_end_to_end_and_leaves_the_original_aside():
    """The real apply_migration, including the swap — against the fixture, with
    only the backup subprocess stubbed out."""
    _reset()
    mm.add_memory("survives the swap", "Fact", "KAUSTAV")
    mm.add_memory("so does this one", "Preference", "KAUSTAV")
    _strip_source()
    before = _snapshot()

    real_backup = mig.take_backup
    mig.take_backup = lambda: True
    try:
        assert mig.apply_migration() == 0
    finally:
        mig.take_backup = real_backup

    assert _snapshot() == before, "the live swap altered a row"
    assert {r[0] for r in _raw("SELECT source FROM memories")} == {mm.SOURCE_DESK}
    assert mm.source_counts() == {mm.SOURCE_DESK: 2, mm.SOURCE_CLOUD: 0, "untagged": 0}
    assert {m["content"] for m in mm.get_full_profile("KAUSTAV")} == \
        {"survives the swap", "so does this one"}

    aside = list(mig.ASIDE_DIR.glob("jarvis_longterm.db.pre-source-*"))
    assert len(aside) == 1, f"the original was not kept aside: {aside}"
    assert not list(_TMP.glob("*.db.sourcing-*")), "a working copy was left behind"


def test_a_second_apply_is_a_no_op():
    _reset()
    mm.add_memory("already tagged", "Fact", "KAUSTAV")
    real_backup = mig.take_backup
    mig.take_backup = lambda: True
    try:
        assert mig.apply_migration() == 0        # nothing untagged
    finally:
        mig.take_backup = real_backup
    aside = list(mig.ASIDE_DIR.glob("*")) if mig.ASIDE_DIR.exists() else []
    assert aside == [], f"a no-op run swapped the database anyway: {aside}"
    assert len(_raw("SELECT id FROM memories")) == 1


def test_the_report_is_read_only_and_counts_honestly():
    _reset()
    mm.add_memory("one", "Fact", "KAUSTAV")
    fd.drain_records([_seal(user_text="two")])
    mm.add_memory("three", "Fact", "KAUSTAV")
    _strip_source()
    before = _snapshot()

    summary = mig.report(_DB)
    assert summary["rows"] == 3
    assert summary["untagged"] == 3
    assert summary["has_column"] is True
    assert summary["blocked"] is False
    assert summary["encrypted_rows"] == 3
    assert _snapshot() == before, "--report modified the database"


def test_the_report_blocks_on_a_source_this_store_does_not_issue():
    _reset()
    mm.add_memory("legitimate", "Fact", "KAUSTAV")
    conn = sqlite3.connect(str(_DB))
    conn.execute("UPDATE memories SET source = 'smuggled'")
    conn.commit()
    conn.close()
    assert mig.report(_DB)["blocked"] is True

    real_backup = mig.take_backup
    mig.take_backup = lambda: True
    try:
        assert mig.apply_migration() == 2, "a blocked survey did not stop the migration"
    finally:
        mig.take_backup = real_backup
    assert _raw("SELECT source FROM memories")[0][0] == "smuggled", \
        "the blocked run changed data anyway"


# -- 6. boundaries -----------------------------------------------------------

def test_the_harness_never_pointed_at_the_real_database():
    assert Path(mm._DB_PATH) == _DB and _DB.parent == _TMP
    assert mig.DB_PATH == _DB
    assert Path(mm._DB_PATH) != _REAL_DB
    assert mig.ASIDE_DIR.parent == _TMP, "the migration would write outside the sandbox"


def test_the_harness_never_touched_the_real_ledger_or_outbox():
    assert fd.LEDGER_DB.parent == _TMP and fo.OUTBOX_FILE.parent == _TMP
    assert not _REAL_LEDGER.exists()
    assert not _REAL_OUTBOX.exists()


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"PASS  {name}")
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
        print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)
