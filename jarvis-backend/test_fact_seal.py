"""Harness for modules/fact_seal.py — the cloud->desk sealed fact queue, Phase 1.

The passing bar is not "it encrypts". It is the four properties that were
signed off:

  1. a fact sealed on Render opens on the desk, through the real
     DPAPI -> DEK -> X25519-private chain — not a raw key handed to the test;
  2. Render CANNOT open what Render sealed;
  3. a wrong key, a tampered blob, or a malformed record QUARANTINES — it never
     crashes, never silently drops, and never blocks the records behind it;
  4. a LOCKED key store is not a per-record fault: the whole drain aborts and
     the batch is left intact.

Runs entirely in a temp directory — the real jarvis_key.* files are never
touched, read, or overwritten.
"""

import ast
import base64
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from nacl.public import PrivateKey, PublicKey, SealedBox

from modules import fact_seal as fs
from modules import memory_crypto as mc

# ── isolation: point every key path and the dead-letter store at a temp dir ──

_TMP = Path(tempfile.mkdtemp(prefix="jarvis_factseal_"))
_REAL_PATHS = (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE)
_REAL_QUARANTINE = fs.QUARANTINE_DIR


def _fingerprint_real_keys():
    """Snapshot the real key files BEFORE anything is redirected."""
    return {p: (hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None)
            for p in _REAL_PATHS}


_REAL_KEYS_BEFORE = _fingerprint_real_keys()

mc.DPAPI_KEY_FILE = _TMP / "jarvis_key.dpapi"
mc.RECOVERY_KEY_FILE = _TMP / "jarvis_key.recovery"
mc.X25519_KEY_FILE = _TMP / "jarvis_x25519.enc"
mc.CANARY_FILE = _TMP / "jarvis_key.canary"
fs.QUARANTINE_DIR = _TMP / "fact_quarantine"

_MODULE_SRC = Path(__file__).parent.joinpath("modules", "fact_seal.py").read_text(encoding="utf-8")


def _fresh_keys():
    """A brand-new key set in the temp dir."""
    for p in (mc.DPAPI_KEY_FILE, mc.RECOVERY_KEY_FILE, mc.X25519_KEY_FILE, mc.CANARY_FILE):
        if p.exists():
            p.unlink()
    mc.clear_cache()
    mc.initialise_keys()


def _reset_quarantine():
    shutil.rmtree(fs.QUARANTINE_DIR, ignore_errors=True)


def _fresh():
    _fresh_keys()
    _reset_quarantine()


def _a_fact(text="she moved the Goa trip to March"):
    return fs.new_fact(text, who="KAUSTAV", tier=3, reply="Noted, Sir.")


# ── 1. the round trip that matters ──────────────────────────────────────────

def test_a_sealed_fact_opens_through_the_dpapi_wrapped_private_key():
    """No raw key is handed to open_envelope — it walks DPAPI -> DEK -> private."""
    _fresh()
    payload = _a_fact()
    envelope = fs.seal_fact(payload, fs.desk_public_b64())
    assert fs.open_envelope(envelope) == payload


def test_the_drain_opens_a_batch():
    _fresh()
    facts = [_a_fact(f"fact number {i}") for i in range(5)]
    envelopes = [fs.seal_fact(f, fs.desk_public_b64()) for f in facts]
    opened, bad = fs.drain(envelopes)
    assert bad == 0
    assert [p["user_text"] for p in opened] == [f["user_text"] for f in facts]


def test_unicode_survives_the_round_trip():
    """Benglish and emoji go through Telegram constantly — a mangled fact is a wrong fact."""
    _fresh()
    payload = fs.new_fact("Kalke office jabo na — chhuti niyechi 🎉", who="KAUSTAV")
    envelope = fs.seal_fact(payload, fs.desk_public_b64())
    assert fs.open_envelope(envelope)["user_text"] == payload["user_text"]


# ── 2. what the envelope leaks, and what Render can do with it ──────────────

def test_the_envelope_carries_nothing_but_a_uuid_and_a_blob():
    _fresh()
    payload = _a_fact("the mortgage rate is 8.4 percent")
    envelope = fs.seal_fact(payload, fs.desk_public_b64())
    assert set(envelope) == {"v", "id", "sealed"}
    wire = json.dumps(envelope)
    for secret in ("mortgage", "8.4", "KAUSTAV", "Noted, Sir."):
        assert secret not in wire, f"{secret!r} rode outside the seal"


def test_render_cannot_open_what_render_just_sealed():
    """The core property. A SealedBox from a PublicKey has no private half."""
    _fresh()
    pub = fs.desk_public_b64()
    envelope = fs.seal_fact(_a_fact(), pub)
    assert fs.cloud_can_open(envelope, pub) is False
    # ...and directly, so the assertion does not depend on our own wrapper:
    box = SealedBox(PublicKey(base64.b64decode(pub)))
    try:
        box.decrypt(base64.b64decode(envelope["sealed"]))
    except Exception:
        return
    raise AssertionError("a public-key-only SealedBox decrypted a record")


def test_sealing_the_same_fact_twice_produces_different_ciphertext():
    """Ephemeral keypair per record — otherwise the queue leaks which facts repeat."""
    _fresh()
    payload = _a_fact()
    pub = fs.desk_public_b64()
    assert fs.seal_fact(payload, pub)["sealed"] != fs.seal_fact(payload, pub)["sealed"]


def test_a_foreign_desk_key_cannot_open_our_records():
    _fresh()
    foreign = PrivateKey.generate()
    ours = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    try:
        fs.open_envelope(ours, bytes(foreign))
    except fs.SealOpenError:
        return
    raise AssertionError("a record opened under a key it was not sealed to")


# ── 3. every failure quarantines; none of them crash ────────────────────────

def test_a_wrong_key_quarantines_instead_of_crashing():
    _fresh()
    foreign = PrivateKey.generate()
    envelope = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    assert fs.open_or_quarantine(envelope, bytes(foreign)) is None
    assert fs.quarantine_count() == 1


def test_a_tampered_blob_quarantines():
    _fresh()
    envelope = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    raw = bytearray(base64.b64decode(envelope["sealed"]))
    raw[-1] ^= 0x01                      # flip one bit of the Poly1305 tag
    envelope["sealed"] = base64.b64encode(bytes(raw)).decode("ascii")
    assert fs.open_or_quarantine(envelope) is None
    assert fs.quarantine_count() == 1


def test_a_truncated_blob_quarantines():
    _fresh()
    envelope = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    envelope["sealed"] = base64.b64encode(
        base64.b64decode(envelope["sealed"])[:20]).decode("ascii")
    assert fs.open_or_quarantine(envelope) is None
    assert fs.quarantine_count() == 1


def test_malformed_envelopes_all_quarantine():
    _fresh()
    good = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    broken = [
        {},                                                   # empty
        {"v": 1, "id": good["id"]},                           # no blob
        {"v": 1, "sealed": good["sealed"]},                   # no id
        {"v": 99, "id": good["id"], "sealed": good["sealed"]},  # future version
        {"v": 1, "id": good["id"], "sealed": "not base64!!"},   # unparseable
        "a bare string",                                       # not a dict
        None,
    ]
    for envelope in broken:
        assert fs.open_or_quarantine(envelope) is None, f"{envelope!r} was accepted"
    assert fs.quarantine_count() == len(broken)


def test_a_rewritten_envelope_id_quarantines():
    """The id is the only thing outside the seal the drain trusts."""
    _fresh()
    envelope = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    envelope["id"] = "0" * 32
    assert fs.open_or_quarantine(envelope) is None
    assert fs.quarantine_count() == 1


def test_one_poison_record_does_not_block_the_rest():
    """The whole point of dead-lettering, borrowed from task_queue."""
    _fresh()
    pub = fs.desk_public_b64()
    first = fs.seal_fact(_a_fact("first fact"), pub)
    poison = {"v": 1, "id": "f" * 32, "sealed": "@@@ not base64 @@@"}
    last = fs.seal_fact(_a_fact("last fact"), pub)

    opened, bad = fs.drain([first, poison, last])
    assert bad == 1
    assert [p["user_text"] for p in opened] == ["first fact", "last fact"]
    assert fs.quarantine_count() == 1


def test_the_quarantined_record_is_kept_and_inspectable():
    """A queue that quietly empties itself looks exactly like success."""
    _fresh()
    envelope = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    envelope["sealed"] = "not base64!!"
    fs.open_or_quarantine(envelope)

    files = fs.list_quarantined()
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["envelope"] == envelope, "the original record was not preserved"
    assert record["reason"] and record["quarantined_at"]
    assert envelope["id"] in files[0].name, "the file cannot be traced back to the record"


# ── 4. a locked key store is not a per-record fault ─────────────────────────

def test_a_locked_key_store_aborts_the_drain_and_keeps_the_batch():
    _fresh()
    envelopes = [fs.seal_fact(_a_fact(f"fact {i}"), fs.desk_public_b64()) for i in range(3)]
    mc.DPAPI_KEY_FILE.unlink()
    mc.clear_cache()
    try:
        fs.drain(envelopes)
    except mc.MemoryLockedError:
        assert fs.quarantine_count() == 0, \
            "a locked key store quarantined records that were never even opened"
        return
    finally:
        _fresh_keys()
    raise AssertionError("a locked key store drained silently")


# ── 5. re-handshake ─────────────────────────────────────────────────────────

def test_rotation_issues_a_new_public_half():
    _fresh()
    before = fs.desk_public_b64()
    after = fs.regenerate_desk_keypair()
    assert after != before
    assert fs.desk_public_b64() == after


def test_rotation_leaves_the_dek_and_every_encrypted_row_alone():
    """Rotating the fact key must not be a memory-loss event."""
    _fresh()
    row = mc.encrypt_field("he prefers the 8am train", "memories", "content")
    fs.regenerate_desk_keypair()
    assert mc.verify_keys() is True
    assert mc.decrypt_field(row, "memories", "content") == "he prefers the 8am train"


def test_facts_sealed_to_the_old_key_quarantine_after_rotation():
    """The accepted cost of a re-handshake, asserted rather than assumed."""
    _fresh()
    stale = fs.seal_fact(_a_fact(), fs.desk_public_b64())
    fs.regenerate_desk_keypair()
    fresh = fs.seal_fact(_a_fact("after the handshake"), fs.desk_public_b64())

    opened, bad = fs.drain([stale, fresh])
    assert bad == 1
    assert [p["user_text"] for p in opened] == ["after the handshake"]


def test_rotation_never_writes_the_private_half_in_plaintext():
    _fresh()
    fs.regenerate_desk_keypair()
    payload = json.loads(mc.X25519_KEY_FILE.read_text(encoding="utf-8"))
    assert "private_sealed" in payload and payload.get("private") is None
    # and the sealed half must not be the raw key sitting in base64
    raw = mc.x25519_private_raw()
    assert base64.b64decode(payload["private_sealed"]) != raw
    assert base64.b64encode(raw).decode("ascii") not in mc.X25519_KEY_FILE.read_text(encoding="utf-8")


# ── 6. record construction ──────────────────────────────────────────────────

def test_an_empty_utterance_is_refused_at_construction():
    for junk in ("", "   ", None, 42):
        try:
            fs.new_fact(junk)
        except fs.MalformedRecordError:
            continue
        raise AssertionError(f"new_fact accepted {junk!r}")


def test_new_fact_carries_who_so_the_desk_can_apply_the_same_policy():
    fact = fs.new_fact("she said the flight lands at 9", who="mousumi")
    assert fact["who"] == "MOUSUMI"


def test_sealing_an_incomplete_payload_fails_loudly_on_the_cloud():
    """A cloud bug must surface on the cloud, not as a quarantine file hours later."""
    _fresh()
    try:
        fs.seal_fact({"id": "x" * 32, "user_text": "hi"}, fs.desk_public_b64())
    except fs.MalformedRecordError:
        return
    raise AssertionError("a payload missing required fields was sealed")


def test_a_bad_public_key_fails_loudly_on_the_cloud():
    _fresh()
    try:
        fs.seal_fact(_a_fact(), "obviously-not-a-key")
    except fs.MalformedRecordError:
        return
    raise AssertionError("a junk public key was accepted")


# ── 7. the import discipline that keeps this runnable on Render ─────────────

def _module_level_imports():
    tree = ast.parse(_MODULE_SRC)
    names = []
    for node in tree.body:                      # top level ONLY
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return names


def test_the_cloud_never_needs_memory_crypto_to_seal():
    """requirements-cloud.txt has no `cryptography` — a top-level import would
    make the sealing side un-importable on Render."""
    for name in _module_level_imports():
        assert "memory_crypto" not in name, \
            f"memory_crypto is imported at module level ({name}) — Render cannot import this"


def test_module_level_imports_are_stdlib_or_pynacl_only():
    allowed = {"__future__", "base64", "binascii", "datetime", "json", "re", "uuid",
               "pathlib", "typing"}
    for name in _module_level_imports():
        root = name.split(".")[0]
        assert root in allowed or root == "nacl", \
            f"{name} is neither stdlib nor pynacl — it would have to ship to Render"


def test_the_seal_is_libsodium_crypto_box_seal():
    """Ruled explicitly: crypto_box_seal, not a hand-rolled equivalent."""
    assert "SealedBox" in _MODULE_SRC
    assert "from nacl.public import" in _MODULE_SRC


# ── 8. the harness's own boundaries ─────────────────────────────────────────

def test_the_harness_never_touched_the_real_key_files():
    assert _fingerprint_real_keys() == _REAL_KEYS_BEFORE, \
        "a real jarvis_key.* file changed during the run — the harness was not isolated"


def test_the_harness_never_wrote_the_real_quarantine_directory():
    assert fs.QUARANTINE_DIR.parent == _TMP
    assert not _REAL_QUARANTINE.exists() or _REAL_QUARANTINE != fs.QUARANTINE_DIR


def test_the_harness_wrote_only_inside_its_temp_directory():
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
