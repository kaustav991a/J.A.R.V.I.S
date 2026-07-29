"""Harness for the retirement of jarvis_memory.db (C#11a).

The old file held two tables and BOTH were live, so retiring it meant
redirecting behaviour, not deleting a dead store:

  * `remember_fact` — still a real action type the model can emit — must now
    write through the encrypted Memory OS instead of the old table.
  * `session_digest` — sleep/wake continuity — must survive the move AND be
    encrypted, having been plaintext for its whole life.

The failure this guards against is silent: if `remember_fact` quietly recreated
the old database, JARVIS would keep working while facts drifted into a second,
unencrypted store nobody reads.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import memory
import memory_manager as mm
from modules import memory_crypto as mc

_TMP = Path(tempfile.mkdtemp(prefix="jarvis_retire_"))
_DB = _TMP / "test_longterm.db"

_REAL_MEMORY_DB = Path(memory.__file__).with_name("jarvis_memory.db")

memory.DB_PATH = str(_DB)
mm._DB_PATH = str(_DB)


def _reset():
    if _DB.exists():
        _DB.unlink()
    mm._init_db()
    memory.init_db()


def _raw(query, params=()):
    conn = sqlite3.connect(str(_DB))
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


# ── the old file is gone and must not come back ─────────────────────────────

def test_the_old_database_file_is_retired():
    assert not _REAL_MEMORY_DB.exists(), (
        "jarvis_memory.db is back in the backend directory — something recreated it"
    )


def test_init_db_no_longer_creates_the_long_term_memory_table():
    """Recreating it would resurrect a second store on the next write."""
    _reset()
    tables = {r[0] for r in _raw("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "long_term_memory" not in tables
    assert "session_digest" in tables


def test_remember_fact_writes_to_the_encrypted_store_not_a_new_file():
    _reset()
    memory.remember_fact("Family", "fixture relative echo-55")
    assert not _REAL_MEMORY_DB.exists(), "remember_fact recreated jarvis_memory.db"
    rows = _raw("SELECT content FROM memories")
    assert len(rows) == 1
    assert mc.is_encrypted(rows[0][0]), "the fact was stored in plaintext"


def test_remember_fact_round_trips_through_the_memory_os():
    _reset()
    memory.remember_fact("Location", "The user is located in a fixture city.")
    contents = [m["content"] for m in mm.get_full_profile("KAUSTAV")]
    assert contents == ["The user is located in a fixture city."]


def test_remember_fact_ignores_blank_input():
    _reset()
    for value in ("", "   ", None):
        memory.remember_fact("Fact", value)
    assert _raw("SELECT COUNT(*) FROM memories")[0][0] == 0


def test_old_free_text_categories_map_onto_the_three_real_ones():
    assert memory._map_category("Preferred web development stack") == "Preference"
    assert memory._map_category("General") == "Preference"
    assert memory._map_category("Family") == "Fact"
    assert memory._map_category("Location") == "Fact"
    assert memory._map_category("Category") == "Fact"
    assert memory._map_category("") == "Fact"
    assert memory._map_category("Correction") == "Correction"


def test_recall_all_facts_reads_the_memory_os():
    _reset()
    memory.remember_fact("Preferred web development stack", "React and Node.js")
    block = memory.recall_all_facts()
    assert "React and Node.js" in block
    assert block.startswith("- ")


def test_recall_all_facts_is_honest_when_there_is_nothing():
    _reset()
    assert memory.recall_all_facts() == "No specific user preferences saved yet."


# ── the session digest: live behaviour, now encrypted ───────────────────────

def test_session_digest_round_trips():
    _reset()
    digest = ("The user opened the camera feed and asked if you could see them. "
              "You misidentified the puppy and corrected yourself.")
    memory.save_session_digest("KAUSTAV", digest)
    assert memory.get_last_session_digest("KAUSTAV") == digest


def test_session_digest_is_encrypted_on_disk():
    """It is a condensed account of a real conversation — among the most
    revealing text the system keeps, and it was plaintext until now."""
    _reset()
    memory.save_session_digest("KAUSTAV", "They argued about the Kolkata trip.")
    stored = _raw("SELECT digest FROM session_digest")[0][0]
    assert mc.is_encrypted(stored), "the digest was written in plaintext"
    assert "Kolkata" not in stored
    assert b"Kolkata" not in _DB.read_bytes()


def test_session_digest_replaces_rather_than_accumulates():
    _reset()
    memory.save_session_digest("KAUSTAV", "first")
    memory.save_session_digest("KAUSTAV", "second")
    assert _raw("SELECT COUNT(*) FROM session_digest")[0][0] == 1
    assert memory.get_last_session_digest("KAUSTAV") == "second"


def test_absent_digest_is_none_not_an_error():
    _reset()
    assert memory.get_last_session_digest("NOBODY") is None


def test_a_plaintext_digest_written_before_the_move_still_reads():
    """The row migrated from the old store was plaintext when it was copied."""
    _reset()
    conn = sqlite3.connect(str(_DB))
    conn.execute(
        "INSERT INTO session_digest (user, digest, timestamp) VALUES (?,?,?)",
        ("KAUSTAV", "a digest from before encryption", "2026-06-28T11:23:48"),
    )
    conn.commit()
    conn.close()
    assert memory.get_last_session_digest("KAUSTAV") == "a digest from before encryption"


def test_seed_from_last_digest_uses_the_decrypted_text():
    """The wake path must get readable text, not a ciphertext blob."""
    _reset()
    memory.clear_working_memory()
    memory.save_session_digest("KAUSTAV", "They were debugging the gesture daemon.")
    seeded = memory.seed_from_last_digest("KAUSTAV")
    assert seeded == "They were debugging the gesture daemon."
    buffer = memory.get_working_memory()
    assert any("gesture daemon" in str(m.get("content", "")) for m in buffer), buffer
    memory.clear_working_memory()


# ── boundaries ──────────────────────────────────────────────────────────────

def test_the_harness_never_pointed_at_the_real_database():
    assert memory.DB_PATH == str(_DB)
    assert mm._DB_PATH == str(_DB)


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
