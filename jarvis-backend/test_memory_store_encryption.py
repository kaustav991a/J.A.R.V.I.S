"""Harness for the encrypted store — memory_manager + partner_log end to end.

test_memory_crypto.py proves the primitives. This proves the *wiring*: that a
fact written through the real `add_memory()` lands on disk as ciphertext, comes
back out of the real read path as the exact original string, and that a locked
key produces a loud failure rather than an empty profile.

Runs against a temp database. The real jarvis_longterm.db is never opened.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["JARVIS_LOG_PARTNER_CHATS"] = "1"     # partner_log writes nothing when off

import memory_manager as mm                       # noqa: E402
from modules import memory_crypto as mc           # noqa: E402
from modules import partner_log                   # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="jarvis_storetest_"))
_DB = _TMP / "test_longterm.db"
_REAL_DB = Path(mm._DB_PATH)

mm._DB_PATH = str(_DB)


def _reset_db():
    if _DB.exists():
        _DB.unlink()
    mm._init_db()


def _raw(query, params=()):
    conn = sqlite3.connect(str(_DB))
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


# ── the store is actually encrypted on disk ─────────────────────────────────

def test_keys_are_ready_so_this_harness_is_testing_the_real_thing():
    assert mc.keys_ready(), "no key set — run manage_keys.py init first"


def test_written_content_is_ciphertext_on_disk():
    _reset_db()
    assert mm.add_memory("Sir takes his coffee black", "Preference", "KAUSTAV") is True
    raw_value = _raw("SELECT content FROM memories")[0][0]
    assert mc.is_encrypted(raw_value), "content was written in plaintext"
    assert "coffee" not in raw_value


def test_the_read_path_returns_the_exact_original_string():
    _reset_db()
    original = "the user prefers aisle seats on flights"
    mm.add_memory(original, "Fact", "KAUSTAV")
    rows = mm.get_relevant_memories(user="KAUSTAV", limit=5)
    assert [r["content"] for r in rows] == [original]


def test_full_profile_decrypts_every_row():
    _reset_db()
    facts = [
        ("Sir prefers dark-mode interfaces", "Preference"),
        ("tumi kemon achho means how are you", "Fact"),
        ("Always call him Sir, never boss", "Correction"),
    ]
    for text, category in facts:
        mm.add_memory(text, category, "KAUSTAV")
    got = {r["content"] for r in mm.get_full_profile("KAUSTAV")}
    assert got == {t for t, _ in facts}


def test_a_raw_disk_read_reveals_nothing_readable():
    """What a thief with the file, and without the key, actually sees."""
    _reset_db()
    # Deliberately synthetic tokens: distinctive enough that finding them in the
    # raw file would be unambiguous, and meaningless outside this test.
    mm.add_memory("fixture fact alpha-quebec-77", "Fact", "KAUSTAV")
    blob = _DB.read_bytes()
    for secret in (b"alpha-quebec", b"fixture fact"):
        assert secret not in blob, f"{secret!r} is readable in the raw database file"


# ── duplicate detection survives randomised encryption ──────────────────────

def test_duplicates_are_still_rejected_when_encrypted():
    """Randomised ciphertext means UNIQUE(user, content) can never fire again —
    the blind index is what keeps this working."""
    _reset_db()
    assert mm.add_memory("Sir dislikes loud notifications", "Preference") is True
    assert mm.add_memory("Sir dislikes loud notifications", "Preference") is False
    assert len(_raw("SELECT id FROM memories")) == 1


def test_duplicate_detection_ignores_case_and_spacing():
    _reset_db()
    assert mm.add_memory("Sir works at Fortmindz", "Fact") is True
    assert mm.add_memory("sir   works at fortmindz", "Fact") is False
    assert len(_raw("SELECT id FROM memories")) == 1


def test_different_users_may_hold_the_same_fact():
    _reset_db()
    assert mm.add_memory("prefers tea over coffee", "Preference", "KAUSTAV") is True
    assert mm.add_memory("prefers tea over coffee", "Preference", "MOUSUMI") is True
    assert len(_raw("SELECT id FROM memories")) == 2


def test_the_fingerprint_does_not_leak_the_fact():
    _reset_db()
    mm.add_memory("the wedding is in December", "Fact")
    value = _raw("SELECT content_hash FROM memories")[0][0]
    assert value and "wedding" not in value and "December" not in value


# ── a half-migrated table still reads ───────────────────────────────────────

def test_legacy_plaintext_rows_are_still_readable():
    """During the migration the table legitimately holds both kinds of row."""
    _reset_db()
    conn = sqlite3.connect(str(_DB))
    conn.execute(
        "INSERT INTO memories (category, content, user, timestamp) VALUES (?,?,?,?)",
        ("Fact", "an old row written before encryption", "KAUSTAV", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()
    mm.add_memory("a new encrypted row", "Fact", "KAUSTAV")

    contents = {r["content"] for r in mm.get_full_profile("KAUSTAV")}
    assert contents == {"an old row written before encryption", "a new encrypted row"}


# ── the failure mode that matters most ──────────────────────────────────────

def test_a_locked_key_raises_instead_of_reporting_an_empty_memory():
    """The worst possible outcome is a silent [] — it is indistinguishable from
    having forgotten him, and he would believe the data was lost."""
    _reset_db()
    mm.add_memory("something worth keeping", "Fact", "KAUSTAV")

    saved = mc.DPAPI_KEY_FILE
    mc.DPAPI_KEY_FILE = _TMP / "nonexistent.dpapi"
    mc.clear_cache()
    try:
        for call in (lambda: mm.get_full_profile("KAUSTAV"),
                     lambda: mm.get_relevant_memories(user="KAUSTAV")):
            try:
                result = call()
            except mc.MemoryLockedError:
                continue
            raise AssertionError(f"a locked key returned {result!r} instead of raising")
    finally:
        mc.DPAPI_KEY_FILE = saved
        mc.clear_cache()


def test_the_spoken_lock_message_says_locked_not_empty():
    assert "LOCKED" in mc.MemoryLockedError.SPOKEN
    assert "unavailable" in mc.MemoryLockedError.SPOKEN


# ── partner_log: where her data will actually land ──────────────────────────

def test_partner_messages_are_encrypted_on_disk():
    db = _TMP / "partner.db"
    if db.exists():
        db.unlink()
    assert partner_log.log_inbound("gf", "fixture inbound message bravo-31",
                                   partner_name="TestPartner", db_path=str(db)) is True
    conn = sqlite3.connect(str(db))
    try:
        content, name = conn.execute(
            "SELECT content, partner_name FROM partner_messages"
        ).fetchone()
    finally:
        conn.close()
    assert mc.is_encrypted(content), "the message was stored in plaintext"
    assert mc.is_encrypted(name), "the sender name was stored in plaintext"
    assert "bravo-31" not in content and "TestPartner" not in name
    assert b"bravo-31" not in db.read_bytes()


def test_partner_messages_read_back_exactly():
    db = _TMP / "partner_roundtrip.db"
    if db.exists():
        db.unlink()
    # Romanised Bengali stays in the fixture on purpose — it is the script his
    # partner actually writes in, and it is what proves non-ASCII round-trips.
    messages = ["kal office jabo na", "tumi kemon achho?", "🌙 good night"]
    for text in messages:
        partner_log.log_inbound("gf", text, partner_name="TestPartner", db_path=str(db))
    got = partner_log.recent("gf", db_path=str(db))
    assert [m["content"] for m in got] == messages
    assert {m["partner_name"] for m in got} == {"TestPartner"}


def test_partner_slot_stays_queryable_in_the_clear():
    """The slot must NOT be encrypted — every read filters on it."""
    db = _TMP / "partner_slots.db"
    if db.exists():
        db.unlink()
    partner_log.log_inbound("gf", "hers", partner_name="M", db_path=str(db))
    partner_log.log_inbound("brother", "his", partner_name="B", db_path=str(db))
    assert [m["content"] for m in partner_log.recent("gf", db_path=str(db))] == ["hers"]
    assert [m["content"] for m in partner_log.recent("brother", db_path=str(db))] == ["his"]
    assert partner_log.count("gf", db_path=str(db)) == 1


def test_partner_logging_still_writes_nothing_when_the_flag_is_off():
    """Encryption must not have quietly loosened the opt-in gate."""
    db = _TMP / "partner_off.db"
    if db.exists():
        db.unlink()
    assert partner_log.log_inbound("gf", "should not be stored", env={},
                                   db_path=str(db)) is False
    assert not db.exists(), "a row (or even a table) was created with the flag off"


# ── boundaries ──────────────────────────────────────────────────────────────

def test_the_harness_never_pointed_at_the_real_database():
    assert mm._DB_PATH == str(_DB)
    assert Path(mm._DB_PATH) != _REAL_DB


if __name__ == "__main__":
    import shutil
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
