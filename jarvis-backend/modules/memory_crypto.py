"""C#11a Step 1 — the key story for memory-at-rest encryption.

This module holds keys and encrypts fields. It deliberately **never opens a
database**. Migration lives elsewhere, so a bug here cannot corrupt a row.

The shape, as signed off 2026-07-30:

        random 32-byte DEK  (the only thing that decrypts anything)
                   |
        +----------+-----------+
        |                      |
    DPAPI wrap            recovery wrap
    (his Windows user)    (scrypt over a one-time recovery code)
    jarvis_key.dpapi      jarvis_key.recovery
    unattended boot,      printed ONCE, stored offline
    no prompt             survives a rebuilt Windows profile

Either wrap unwraps the same DEK, so a dead keyring is an inconvenience rather
than data loss. That redundancy is the whole point: a single DPAPI blob is one
profile rebuild away from losing the 4-tier memory, and encryption you cannot
reverse is data loss wearing a security costume.

An X25519 keypair is generated in the same ceremony (its private half sealed
with the DEK) so the later cloud->desk fact sync needs no second key event.

What this protects: the databases **leaving the machine** — a copied folder, a
stray backup, a sync client, a repo accident.
What it does NOT protect: anything running as Kaustav on this box while JARVIS
is up. That is the exact cost of having no boot prompt, and it is the tradeoff
that was chosen knowingly.

Dependencies: `cryptography` (already in the venv, no protobuf dep) + stdlib
ctypes for DPAPI. Nothing new is installed, so the protobuf==6.33.6 /
tensorflow / mediapipe balance is untouched.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BACKEND_DIR = Path(__file__).resolve().parent.parent

# ── file layout (all git-ignored) ───────────────────────────────────────────
DPAPI_KEY_FILE = BACKEND_DIR / "jarvis_key.dpapi"
RECOVERY_KEY_FILE = BACKEND_DIR / "jarvis_key.recovery"
X25519_KEY_FILE = BACKEND_DIR / "jarvis_x25519.enc"
CANARY_FILE = BACKEND_DIR / "jarvis_key.canary"

# ── formats ─────────────────────────────────────────────────────────────────
FORMAT_VERSION = 1
FIELD_PREFIX = "enc:v1:"          # marks an encrypted TEXT column
DEK_BYTES = 32                     # AES-256
NONCE_BYTES = 12                   # GCM standard
CANARY_PLAINTEXT = b"JARVIS memory key canary v1"

# Entropy bound into the DPAPI blob: another program running as the same user
# cannot unwrap it by accident without knowing this string.
DPAPI_ENTROPY = b"JARVIS-memory-DEK-v1"

# scrypt cost for the recovery wrap. ~100ms on this CPU-only box — deliberately
# slow, because the recovery code is typed by a human and must resist offline
# guessing if the wrap file ever leaks.
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 16

# The recovery code carries 160 bits before the KDF: enough that scrypt is
# defence in depth, not the only thing standing there.
RECOVERY_ENTROPY_BYTES = 20
RECOVERY_GROUP = 5


class MemoryCryptoError(Exception):
    """Base for every key/crypto failure in this module."""


class MemoryLockedError(MemoryCryptoError):
    """The DEK could not be obtained, so memory must not be read.

    Raised loudly and on purpose. A silent empty result is the single worst
    outcome available here: it is indistinguishable from "you never told me
    that", and he would believe the facts were gone.
    """

    SPOKEN = "Long-term memory is LOCKED — the key store is unavailable."


class KeysAlreadyExistError(MemoryCryptoError):
    """Refusing to generate over an existing key.

    Overwriting a DEK orphans every row encrypted under it. That is data loss,
    so it is an error rather than a prompt.
    """


# ── Windows DPAPI, through stdlib ctypes (zero new dependencies) ────────────

if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def dpapi_available() -> bool:
    return sys.platform == "win32"


def _to_blob(data: bytes) -> "_Blob":
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: "_Blob") -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def dpapi_protect(data: bytes) -> bytes:
    """Wrap bytes so only this Windows user account can unwrap them."""
    if not dpapi_available():
        raise MemoryCryptoError("DPAPI is Windows-only; this host is " + sys.platform)
    out = _Blob()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(_to_blob(data)), "JARVIS memory key",
        ctypes.byref(_to_blob(DPAPI_ENTROPY)), None, None, 0, ctypes.byref(out),
    )
    if not ok:
        raise MemoryCryptoError(f"CryptProtectData failed (err={ctypes.get_last_error()})")
    try:
        return _from_blob(out)
    finally:
        _kernel32.LocalFree(out.pbData)


def dpapi_unprotect(data: bytes) -> bytes:
    """Unwrap. Fails if the Windows profile changed — that is what recovery is for."""
    if not dpapi_available():
        raise MemoryCryptoError("DPAPI is Windows-only; this host is " + sys.platform)
    out = _Blob()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(_to_blob(data)), None,
        ctypes.byref(_to_blob(DPAPI_ENTROPY)), None, None, 0, ctypes.byref(out),
    )
    if not ok:
        raise MemoryLockedError(
            f"CryptUnprotectData failed (err={ctypes.get_last_error()}) — "
            "this key was wrapped by a different Windows profile. "
            "Use: python manage_keys.py restore-key"
        )
    try:
        return _from_blob(out)
    finally:
        _kernel32.LocalFree(out.pbData)


# ── the recovery code ───────────────────────────────────────────────────────

def format_recovery_code(raw: bytes) -> str:
    """Human-transcribable: base32, no padding, grouped, with a visible prefix.

    Base32 avoids the 0/O and 1/l confusions that make a hand-copied code fail
    at the worst possible moment — the moment he actually needs it.
    """
    body = base64.b32encode(raw).decode("ascii").rstrip("=")
    groups = [body[i:i + RECOVERY_GROUP] for i in range(0, len(body), RECOVERY_GROUP)]
    return "JARVIS-" + "-".join(groups)


def normalise_recovery_code(code: str) -> bytes:
    """Accept it however he types it: spaces, dashes, lowercase, prefix or not."""
    cleaned = code.strip().upper().replace(" ", "").replace("-", "")
    if cleaned.startswith("JARVIS"):
        cleaned = cleaned[len("JARVIS"):]
    pad = (-len(cleaned)) % 8
    try:
        return base64.b32decode(cleaned + "=" * pad, casefold=True)
    except Exception as exc:
        raise MemoryCryptoError(f"that does not look like a recovery code: {exc}") from exc


def _derive_recovery_kek(code_bytes: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(
        code_bytes, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )


# ── envelope helpers ────────────────────────────────────────────────────────

def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) <= NONCE_BYTES:
        raise MemoryCryptoError("ciphertext too short to be valid")
    nonce, body = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, body, aad)


def _write_private(path: Path, payload: dict) -> None:
    """Write atomically, so a crash mid-write cannot leave a truncated key file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise MemoryLockedError(f"key file missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── the ceremony ────────────────────────────────────────────────────────────

@dataclass
class InitResult:
    recovery_code: str
    x25519_public_b64: str
    files: list


def key_files_exist() -> bool:
    return DPAPI_KEY_FILE.exists() or RECOVERY_KEY_FILE.exists()


def keys_ready() -> bool:
    """Is there a usable key set on this machine?

    This is the switch that turns encryption on: once the ceremony has been run,
    new writes encrypt. There is no env flag to forget to set, and no way to end
    up with a store that is half-configured because a variable was missing.
    """
    return DPAPI_KEY_FILE.exists() and CANARY_FILE.exists()


def initialise_keys(force_recreate: bool = False) -> InitResult:
    """Generate the DEK, both wraps, the X25519 pair, and the canary.

    Touches no database. The recovery code is returned to the caller **once**
    and never stored anywhere in recoverable form.
    """
    if key_files_exist() and not force_recreate:
        raise KeysAlreadyExistError(
            "keys already exist. Generating new ones would make every row "
            "encrypted under the old DEK permanently unreadable. Use "
            "'verify' to check them, or 'export-key' for a fresh recovery code."
        )

    dek = secrets.token_bytes(DEK_BYTES)

    # wrap 1 — unattended boot
    _write_private(DPAPI_KEY_FILE, {
        "version": FORMAT_VERSION,
        "wrap": "dpapi",
        "blob": base64.b64encode(dpapi_protect(dek)).decode("ascii"),
        "note": "unwrappable only by this Windows user account",
    })

    # wrap 2 — disaster recovery
    raw_code = secrets.token_bytes(RECOVERY_ENTROPY_BYTES)
    recovery_code = format_recovery_code(raw_code)
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    kek = _derive_recovery_kek(raw_code, salt)
    _write_private(RECOVERY_KEY_FILE, {
        "version": FORMAT_VERSION,
        "wrap": "scrypt-recovery",
        "salt": base64.b64encode(salt).decode("ascii"),
        "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "blob": base64.b64encode(_seal(kek, dek, b"recovery-wrap")).decode("ascii"),
        "note": "unwrappable with the printed recovery code, on any machine",
    })

    # X25519 for the future cloud->desk sealed fact queue: the cloud gets the
    # PUBLIC half only, so it can write their life-facts and never read them.
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes_raw()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
    _write_private(X25519_KEY_FILE, {
        "version": FORMAT_VERSION,
        "public": pub_b64,
        "private_sealed": base64.b64encode(_seal(dek, priv_raw, b"x25519-private")).decode("ascii"),
        "note": "private half sealed with the DEK; only the public half goes to Render",
    })

    # canary — lets 'verify' prove the key works without reading real memory
    _write_private(CANARY_FILE, {
        "version": FORMAT_VERSION,
        "blob": base64.b64encode(_seal(dek, CANARY_PLAINTEXT, b"canary")).decode("ascii"),
    })

    return InitResult(
        recovery_code=recovery_code,
        x25519_public_b64=pub_b64,
        files=[DPAPI_KEY_FILE.name, RECOVERY_KEY_FILE.name,
               X25519_KEY_FILE.name, CANARY_FILE.name],
    )


# ── unwrapping ──────────────────────────────────────────────────────────────

_cached_dek: Optional[bytes] = None


def load_dek(use_cache: bool = True) -> bytes:
    """Unwrap the DEK via DPAPI. Raises MemoryLockedError — never returns None."""
    global _cached_dek
    if use_cache and _cached_dek is not None:
        return _cached_dek
    payload = _read_json(DPAPI_KEY_FILE)
    dek = dpapi_unprotect(base64.b64decode(payload["blob"]))
    if len(dek) != DEK_BYTES:
        raise MemoryLockedError(f"unwrapped key is {len(dek)} bytes, expected {DEK_BYTES}")
    if use_cache:
        _cached_dek = dek
    return dek


def load_dek_from_recovery(recovery_code: str) -> bytes:
    """Unwrap the DEK from the recovery code — works on any machine, any profile."""
    payload = _read_json(RECOVERY_KEY_FILE)
    raw = normalise_recovery_code(recovery_code)
    kek = _derive_recovery_kek(
        raw, base64.b64decode(payload["salt"]),
    )
    try:
        return _open(kek, base64.b64decode(payload["blob"]), b"recovery-wrap")
    except Exception as exc:
        raise MemoryLockedError("recovery code rejected — check for a typo") from exc


def clear_cache() -> None:
    global _cached_dek
    _cached_dek = None


def restore_dpapi_wrap(recovery_code: str) -> None:
    """Rebuild jarvis_key.dpapi on a new machine or a rebuilt Windows profile."""
    dek = load_dek_from_recovery(recovery_code)
    _write_private(DPAPI_KEY_FILE, {
        "version": FORMAT_VERSION,
        "wrap": "dpapi",
        "blob": base64.b64encode(dpapi_protect(dek)).decode("ascii"),
        "note": "rebuilt from the recovery code",
    })
    clear_cache()


def rotate_recovery_code() -> str:
    """Issue a fresh recovery code for the SAME DEK.

    Requires the DEK to be unwrappable right now — he can only export while
    healthy, which is the point: it is not a way to read a locked memory.
    The previous code stops working, and the data is untouched either way.
    """
    dek = load_dek()
    raw_code = secrets.token_bytes(RECOVERY_ENTROPY_BYTES)
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    kek = _derive_recovery_kek(raw_code, salt)
    _write_private(RECOVERY_KEY_FILE, {
        "version": FORMAT_VERSION,
        "wrap": "scrypt-recovery",
        "salt": base64.b64encode(salt).decode("ascii"),
        "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
        "blob": base64.b64encode(_seal(kek, dek, b"recovery-wrap")).decode("ascii"),
        "note": "re-issued; any earlier recovery code is now void",
    })
    return format_recovery_code(raw_code)


def rotate_x25519_keypair() -> str:
    """Issue a fresh cloud->desk fact keypair, sealed under the SAME DEK.

    The DEK is not touched, so every encrypted row stays readable — this rotates
    only the key the cloud seals facts *to*. The cost is bounded and known: any
    fact already sealed to the previous public half and not yet drained can no
    longer be opened, which is why the desk re-sends the new public half on its
    next connect (the handshake). Rotating is therefore safe at any time the
    queue is drained, and lossy only across an undrained outage.

    Returns the new public half, base64, ready to hand to the cloud.
    """
    dek = load_dek()
    priv = X25519PrivateKey.generate()
    priv_raw = priv.private_bytes_raw()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
    _write_private(X25519_KEY_FILE, {
        "version": FORMAT_VERSION,
        "public": pub_b64,
        "private_sealed": base64.b64encode(_seal(dek, priv_raw, b"x25519-private")).decode("ascii"),
        "note": "rotated; facts sealed to the previous public half can no longer be opened",
    })
    return pub_b64


def x25519_private_raw() -> bytes:
    """The raw 32-byte private half, unwrapped through DPAPI -> DEK.

    Exists so the sealed-fact queue can hand the key to libsodium without
    re-implementing the unwrap chain. Never write the return value anywhere.
    """
    return load_x25519_private().private_bytes_raw()


def load_x25519_private() -> X25519PrivateKey:
    payload = _read_json(X25519_KEY_FILE)
    raw = _open(load_dek(), base64.b64decode(payload["private_sealed"]), b"x25519-private")
    return X25519PrivateKey.from_private_bytes(raw)


def load_x25519_public() -> X25519PublicKey:
    payload = _read_json(X25519_KEY_FILE)
    return X25519PublicKey.from_public_bytes(base64.b64decode(payload["public"]))


def x25519_public_b64() -> str:
    return _read_json(X25519_KEY_FILE)["public"]


def verify_keys(dek: Optional[bytes] = None) -> bool:
    """Decrypt the canary. Proves the key works without touching real memory."""
    dek = dek if dek is not None else load_dek()
    payload = _read_json(CANARY_FILE)
    try:
        return _open(dek, base64.b64decode(payload["blob"]), b"canary") == CANARY_PLAINTEXT
    except Exception as exc:
        raise MemoryLockedError(f"canary did not decrypt: {exc}") from exc


# ── field encryption (what the migration will call) ─────────────────────────
#
# AAD binds a ciphertext to its table and column, so a blob lifted from one
# column cannot be pasted into another and still decrypt. It does NOT bind the
# row id: add_memory() does not know its id until after the INSERT, and an
# honest limitation beats a scheme that breaks on the first write.

def _field_aad(table: str, column: str) -> bytes:
    return f"{table}:{column}".encode("utf-8")


def blind_index(value: Optional[str], table: str, column: str,
                dek: Optional[bytes] = None) -> Optional[str]:
    """A deterministic, keyed fingerprint of a value — for UNIQUE constraints.

    Encryption is randomised (the same fact must not produce the same
    ciphertext twice, or the file would leak which rows repeat). That silently
    breaks `UNIQUE(user, content)`: duplicate facts would start piling up
    because no two ciphertexts ever match.

    So the uniqueness moves to this column instead. HMAC keyed by a DEK-derived
    subkey, so the fingerprints are useless to anyone without the key — a plain
    SHA-256 would let a thief confirm guesses ("is one of these facts *this*?").

    Case- and whitespace-insensitive, matching how a duplicate actually looks.
    """
    if value is None or value == "":
        return None
    dek = dek if dek is not None else load_dek()
    subkey = hashlib.blake2b(dek, digest_size=32, person=b"jarvis-blind-ix").digest()
    normalised = " ".join(value.split()).casefold()
    return hashlib.blake2b(
        f"{table}:{column}:{normalised}".encode("utf-8"),
        key=subkey, digest_size=16,
    ).hexdigest()


def is_encrypted(value: Optional[str]) -> bool:
    """True for a value this module produced.

    The migration relies on this to be resumable: a half-migrated table is
    simply a table where some rows already say True.
    """
    return isinstance(value, str) and value.startswith(FIELD_PREFIX)


def encrypt_field(plaintext: Optional[str], table: str, column: str,
                  dek: Optional[bytes] = None) -> Optional[str]:
    """Encrypt one TEXT column value. None and '' pass through unchanged."""
    if plaintext is None or plaintext == "":
        return plaintext
    if is_encrypted(plaintext):
        return plaintext                      # already done; re-running is safe
    dek = dek if dek is not None else load_dek()
    blob = _seal(dek, plaintext.encode("utf-8"), _field_aad(table, column))
    return FIELD_PREFIX + base64.b64encode(blob).decode("ascii")


def decrypt_field(value: Optional[str], table: str, column: str,
                  dek: Optional[bytes] = None) -> Optional[str]:
    """Decrypt one TEXT column value. Plaintext passes through unchanged.

    That passthrough is what makes the migration safe to interrupt: during the
    conversion the table legitimately holds both kinds of row, and reads must
    keep working the whole time.
    """
    if value is None or value == "" or not is_encrypted(value):
        return value
    dek = dek if dek is not None else load_dek()
    try:
        raw = _open(dek, base64.b64decode(value[len(FIELD_PREFIX):]),
                    _field_aad(table, column))
    except MemoryLockedError:
        raise
    except Exception as exc:
        raise MemoryLockedError(
            f"could not decrypt {table}.{column} — wrong key, or the row was "
            f"written under a different DEK ({exc})"
        ) from exc
    return raw.decode("utf-8")
