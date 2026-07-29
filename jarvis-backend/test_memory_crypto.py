"""Harness for modules/memory_crypto.py — the "I can get my data back" proof.

A harness that only proves data gets encrypted is worthless. The passing bar
here is the reverse direction: every test that matters ends by getting the
original bytes back, including from a simulated dead Windows profile.

Runs entirely in a temp directory — the real jarvis_key.* files are never
touched, read, or overwritten by this harness.
"""

import base64
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from modules import memory_crypto as mc

# ── isolation: point every key path at a throwaway directory ────────────────

_TMP = Path(tempfile.mkdtemp(prefix="jarvis_keytest_"))
_REAL_PATHS = (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE)


def _fingerprint_real_keys():
    """Snapshot the real key files BEFORE anything is redirected.

    Once the ceremony has been run these files legitimately exist, so their
    absence proves nothing. What must hold is that this harness never reads,
    rewrites, or deletes them — a test run that clobbered the live DEK would
    make his real memory unreadable.
    """
    out = {}
    for path in _REAL_PATHS:
        out[path] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        )
    return out


_REAL_KEYS_BEFORE = _fingerprint_real_keys()

mc.DPAPI_KEY_FILE = _TMP / "jarvis_key.dpapi"
mc.RECOVERY_KEY_FILE = _TMP / "jarvis_key.recovery"
mc.X25519_KEY_FILE = _TMP / "jarvis_x25519.enc"
mc.CANARY_FILE = _TMP / "jarvis_key.canary"


def _fresh_keys():
    """A brand-new key set in the temp dir. Returns the recovery code."""
    for p in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE):
        if p.exists():
            p.unlink()
    mc.clear_cache()
    return mc.initialise_keys().recovery_code


# ── DPAPI itself ────────────────────────────────────────────────────────────

def test_dpapi_is_available_on_this_host():
    assert mc.dpapi_available(), "this design assumes Windows DPAPI"


def test_dpapi_round_trips_bytes():
    secret = b"\x00\x01 the quick brown fox \xff\xfe"
    assert mc.dpapi_unprotect(mc.dpapi_protect(secret)) == secret


def test_dpapi_blob_does_not_contain_the_plaintext():
    secret = b"fixture-payload-charlie-42"
    assert secret not in mc.dpapi_protect(secret)


def test_dpapi_rejects_a_blob_wrapped_with_different_entropy():
    """Entropy binding means another program running as him cannot unwrap by luck."""
    blob = mc.dpapi_protect(b"payload")
    original = mc.DPAPI_ENTROPY
    try:
        mc.DPAPI_ENTROPY = b"some-other-program"
        try:
            mc.dpapi_unprotect(blob)
        except mc.MemoryLockedError:
            return
        raise AssertionError("a blob unwrapped under the wrong entropy")
    finally:
        mc.DPAPI_ENTROPY = original


# ── the ceremony ────────────────────────────────────────────────────────────

def test_initialise_creates_all_four_files():
    _fresh_keys()
    for path in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE):
        assert path.exists(), f"{path.name} was not created"


def test_initialise_refuses_to_overwrite_existing_keys():
    """Regenerating over a live DEK orphans every encrypted row. Must be an error."""
    _fresh_keys()
    try:
        mc.initialise_keys()
    except mc.KeysAlreadyExistError as exc:
        assert "unreadable" in str(exc).lower()
        return
    raise AssertionError("initialise_keys overwrote an existing key set")


def test_recovery_code_is_not_stored_anywhere_on_disk():
    code = _fresh_keys()
    stripped = code.replace("-", "")
    for path in _TMP.iterdir():
        blob = path.read_text(encoding="utf-8", errors="ignore")
        assert code not in blob and stripped not in blob, f"{path.name} leaks the recovery code"


# ── the two-lock claim: both wraps open the SAME data key ───────────────────

def test_both_wraps_yield_the_identical_dek():
    code = _fresh_keys()
    from_dpapi = mc.load_dek(use_cache=False)
    from_recovery = mc.load_dek_from_recovery(code)
    assert from_dpapi == from_recovery, "the two wraps disagree — recovery would be useless"
    assert len(from_dpapi) == 32


def test_canary_verifies_under_both_wraps():
    code = _fresh_keys()
    assert mc.verify_keys() is True
    assert mc.verify_keys(mc.load_dek_from_recovery(code)) is True


def test_a_wrong_recovery_code_is_refused_loudly():
    _fresh_keys()
    try:
        mc.load_dek_from_recovery("JARVIS-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF-GG")
    except mc.MemoryLockedError:
        return
    raise AssertionError("a wrong recovery code was accepted")


def test_recovery_code_survives_how_a_human_types_it():
    code = _fresh_keys()
    expected = mc.load_dek(use_cache=False)
    variants = [
        code,
        code.lower(),
        code.replace("-", " "),
        code.replace("-", ""),
        "  " + code + "  ",
        code[len("JARVIS-"):],            # without the prefix
    ]
    for variant in variants:
        assert mc.load_dek_from_recovery(variant) == expected, f"failed on {variant!r}"


# ── disaster: the Windows profile is gone ───────────────────────────────────

def test_restore_rebuilds_a_working_dpapi_wrap_after_the_profile_dies():
    """The scenario the two-lock design exists for.

    Delete the DPAPI wrap entirely — the state after a profile rebuild or a
    move to another machine — then get everything back from the printed code.
    """
    code = _fresh_keys()
    original_dek = mc.load_dek(use_cache=False)
    secret = mc.encrypt_field("the user prefers window seats", "memories", "content",
                              dek=original_dek)

    mc.DPAPI_KEY_FILE.unlink()
    mc.clear_cache()
    try:
        mc.load_dek(use_cache=False)
        raise AssertionError("load_dek succeeded with no wrap file")
    except mc.MemoryLockedError:
        pass

    mc.restore_dpapi_wrap(code)

    assert mc.load_dek(use_cache=False) == original_dek
    assert mc.verify_keys() is True
    assert mc.decrypt_field(secret, "memories", "content") == "the user prefers window seats"


def test_rotate_issues_a_new_code_without_changing_the_dek():
    old_code = _fresh_keys()
    dek_before = mc.load_dek(use_cache=False)
    ciphertext = mc.encrypt_field("written under the old code", "memories", "content")

    new_code = mc.rotate_recovery_code()

    assert new_code != old_code
    assert mc.load_dek_from_recovery(new_code) == dek_before, "rotation changed the DEK"
    assert mc.decrypt_field(ciphertext, "memories", "content") == "written under the old code"
    try:
        mc.load_dek_from_recovery(old_code)
    except mc.MemoryLockedError:
        return
    raise AssertionError("the old recovery code still works after rotation")


def test_missing_key_file_locks_loudly_instead_of_returning_nothing():
    """The single worst outcome is a silent empty read that looks like forgetting."""
    _fresh_keys()
    mc.DPAPI_KEY_FILE.unlink()
    mc.clear_cache()
    try:
        mc.load_dek(use_cache=False)
    except mc.MemoryLockedError as exc:
        assert "missing" in str(exc).lower()
        assert "LOCKED" in mc.MemoryLockedError.SPOKEN
        return
    raise AssertionError("a missing key file did not raise MemoryLockedError")


# ── field encryption: the round trip that matters ───────────────────────────

def test_field_round_trip_is_byte_identical():
    _fresh_keys()
    samples = [
        "the user prefers aisle seats on flights",
        "tumi kemon achho",                      # romanised Bengali, per his preference
        "emoji survive too 🌙☕",
        "line one\nline two\ttabbed",
        "x" * 5000,
        "'; DROP TABLE memories; --",
        "  leading and trailing  ",
    ]
    for original in samples:
        blob = mc.encrypt_field(original, "memories", "content")
        assert mc.decrypt_field(blob, "memories", "content") == original, f"lost: {original[:40]!r}"


def test_ciphertext_does_not_contain_the_plaintext():
    _fresh_keys()
    plain = "fixture secret delta-19"
    blob = mc.encrypt_field(plain, "memories", "content")
    assert plain not in blob
    assert plain.encode() not in base64.b64decode(blob[len(mc.FIELD_PREFIX):])


def test_same_text_encrypts_differently_every_time():
    """A deterministic ciphertext would leak which rows repeat a fact."""
    _fresh_keys()
    a = mc.encrypt_field("same fact", "memories", "content")
    b = mc.encrypt_field("same fact", "memories", "content")
    assert a != b
    assert mc.decrypt_field(a, "memories", "content") == mc.decrypt_field(b, "memories", "content")


def test_plaintext_passes_through_so_a_half_migrated_table_still_reads():
    """Migration resumability: mid-conversion the table holds both kinds of row."""
    _fresh_keys()
    assert mc.decrypt_field("an old plaintext row", "memories", "content") == "an old plaintext row"
    assert mc.is_encrypted("an old plaintext row") is False
    assert mc.is_encrypted(mc.encrypt_field("x", "memories", "content")) is True


def test_encrypting_twice_is_a_no_op():
    """Re-running an interrupted migration must not double-wrap a row."""
    _fresh_keys()
    once = mc.encrypt_field("fact", "memories", "content")
    twice = mc.encrypt_field(once, "memories", "content")
    assert once == twice
    assert mc.decrypt_field(twice, "memories", "content") == "fact"


def test_none_and_empty_pass_through_untouched():
    _fresh_keys()
    for value in (None, ""):
        assert mc.encrypt_field(value, "memories", "content") == value
        assert mc.decrypt_field(value, "memories", "content") == value


def test_a_blob_cannot_be_moved_to_another_column():
    """AAD binds ciphertext to table+column, so a lifted blob will not decrypt."""
    _fresh_keys()
    blob = mc.encrypt_field("a private fact", "memories", "content")
    for table, column in [("memories", "category"), ("partner_messages", "content"),
                          ("long_term_memory", "fact")]:
        try:
            mc.decrypt_field(blob, table, column)
        except mc.MemoryLockedError:
            continue
        raise AssertionError(f"blob decrypted as {table}.{column}")


def test_tampered_ciphertext_is_refused_not_silently_wrong():
    _fresh_keys()
    blob = mc.encrypt_field("trust me", "memories", "content")
    raw = bytearray(base64.b64decode(blob[len(mc.FIELD_PREFIX):]))
    raw[-1] ^= 0x01                                   # flip one bit of the GCM tag
    tampered = mc.FIELD_PREFIX + base64.b64encode(bytes(raw)).decode()
    try:
        mc.decrypt_field(tampered, "memories", "content")
    except mc.MemoryLockedError:
        return
    raise AssertionError("a tampered ciphertext was accepted")


def test_a_row_from_a_different_dek_is_refused_not_lost():
    """He must be told 'wrong key', never handed an empty answer."""
    _fresh_keys()
    stale = mc.encrypt_field("written under the old key", "memories", "content")
    _fresh_keys()                                     # brand-new DEK
    try:
        mc.decrypt_field(stale, "memories", "content")
    except mc.MemoryLockedError as exc:
        assert "wrong key" in str(exc).lower() or "different DEK" in str(exc)
        return
    raise AssertionError("a row from another DEK decrypted")


# ── X25519, for the later cloud->desk sealed queue ──────────────────────────

def test_x25519_private_half_is_sealed_with_the_dek():
    _fresh_keys()
    payload = json.loads(mc.X25519_KEY_FILE.read_text(encoding="utf-8"))
    assert "private_sealed" in payload and "public" in payload
    assert "private" not in payload or payload.get("private") is None


def test_x25519_public_matches_the_sealed_private():
    _fresh_keys()
    priv = mc.load_x25519_private()
    assert priv.public_key().public_bytes_raw() == mc.load_x25519_public().public_bytes_raw()
    assert base64.b64decode(mc.x25519_public_b64()) == mc.load_x25519_public().public_bytes_raw()


def test_x25519_seals_and_opens_a_message_end_to_end():
    """Proves the cloud could write a fact the desk can read back, and only the desk."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes

    _fresh_keys()
    desk_public = mc.load_x25519_public()

    ephemeral = X25519PrivateKey.generate()           # the cloud side
    shared = ephemeral.exchange(desk_public)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b"jarvis-fact-queue").derive(shared)
    nonce = b"\x00" * 12
    sealed = AESGCM(key).encrypt(nonce, b"they talked about the trip", None)

    desk_shared = mc.load_x25519_private().exchange(ephemeral.public_key())
    desk_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=b"jarvis-fact-queue").derive(desk_shared)
    assert AESGCM(desk_key).decrypt(nonce, sealed, None) == b"they talked about the trip"


# ── the module's own boundaries ─────────────────────────────────────────────

def test_this_module_never_opens_a_database():
    """Step 1 is keys only. A bug here must not be able to reach his rows."""
    src = Path(__file__).parent.joinpath("modules", "memory_crypto.py").read_text(encoding="utf-8")
    for forbidden in ("import sqlite3", "sqlite3.connect", "chromadb", ".db\"", ".db'"):
        assert forbidden not in src, f"memory_crypto touches storage: {forbidden}"


def test_the_harness_never_touched_the_real_key_files():
    """Clobbering the live DEK would make his real memory unreadable."""
    assert _fingerprint_real_keys() == _REAL_KEYS_BEFORE, \
        "a real jarvis_key.* file changed during the run — the harness was not isolated"


def test_the_harness_wrote_only_inside_its_temp_directory():
    for path in _REAL_PATHS:
        assert not str(path).startswith(str(_TMP)) or path.parent == _TMP
    for path in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE):
        assert path.parent == _TMP, f"{path.name} is not redirected into the temp dir"


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
