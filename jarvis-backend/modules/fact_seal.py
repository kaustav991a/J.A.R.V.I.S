"""C#11a Step 4 — the cloud->desk sealed fact queue: the seal itself.

The gap this closes: when the PC is off, the cloud gateway answers Telegram
turns out of an in-process dict (`cloud_gateway._HISTORY`, 12 turns) that dies
with the dyno. Every fact stated to JARVIS while the desk is down is lost.

The shape, as signed off 2026-08-01:

        desk generates an X25519 keypair (the C#11a ceremony already did)
                   |
        +----------+-----------------------+
        |                                  |
    private half                       public half
    sealed with the DEK,               handed to Render
    DPAPI -> DEK -> private            over the bridge handshake
    (modules/memory_crypto.py)         (seals; can never open)

Sealing is libsodium's `crypto_box_seal`: an ephemeral keypair is generated per
record, so the ciphertext carries no sender identity and **the cloud cannot
re-open what it just sealed** — the ephemeral private half is destroyed at the
end of the call and the desk private half was never there.

Honest limit, stated the same way DPAPI's was: the cloud sees the turn in
plaintext at the moment it answers it — it *is* the brain, and Groq sees it too.
This protects the stored backlog in transit and at rest, not the live turn.
Today those turns are simply lost; after this they are durable and unreadable
by anyone but the desk.

Import discipline: this module is imported by BOTH sides, and `requirements-
cloud.txt` deliberately has no `cryptography` and no pywin32. So the module
level imports stdlib + pynacl ONLY, and every desk-side path lazy-imports
memory_crypto inside the function. Sealing needs the public half and nothing
else, which is exactly why the cloud can run it.

Dependency check (§8 pins): pynacl 1.6.2 resolves to `cffi>=2.0.0` +
`pycparser`, both already present for `cryptography`. Nothing else moves and
`protobuf==6.33.6` is untouched — re-verified after install. It ships a
prebuilt `cp38-abi3-win_amd64` wheel with libsodium vendored, so there is no
compiler step and no shared sqlite/BLAS surface — this is not the sqlcipher
situation that got rejected in Step 1.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import json
import re
import uuid
from pathlib import Path
from typing import Iterable, Optional

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Dead-lettered records land here — one file per poison record, kept for
# inspection. Sealed facts that failed to open are still ciphertext, but the
# directory is gitignored anyway; nothing about a failure should ever be
# published by accident.
QUARANTINE_DIR = BACKEND_DIR / "fact_quarantine"

RECORD_VERSION = 1

# Only these ride outside the seal. Everything that says anything about the
# conversation lives inside it — an envelope is a UUID and a blob, nothing an
# observer can read a life off.
_ENVELOPE_FIELDS = ("v", "id", "sealed")

# Required inside the seal. `reply` and `tier` are optional by design: a fact
# is still worth keeping when the answer was lost.
_PAYLOAD_REQUIRED = ("v", "id", "ts", "who", "user_text")

_UUID_RE = re.compile(r"\A[0-9a-f]{32}\Z")


class FactSealError(Exception):
    """Base for every sealed-fact failure. Always surfaced, never swallowed."""


class SealOpenError(FactSealError):
    """The seal did not open — wrong key, tampering, or a truncated blob.

    Distinct from MalformedRecordError on purpose: this one means the bytes
    were rejected by the AEAD, which is the signature of a forged or
    misaddressed record rather than a formatting bug.
    """


class MalformedRecordError(FactSealError):
    """The envelope or the payload inside it was not the shape we ship."""


# ── the record ──────────────────────────────────────────────────────────────

def new_fact(user_text: str, who: str = "KAUSTAV", tier: Optional[int] = None,
             reply: Optional[str] = None, ts: Optional[str] = None,
             fact_id: Optional[str] = None) -> dict:
    """Build one unsealed fact record.

    `who` is carried so the desk can persist under the right identity and apply
    the same partner policy it would have applied live — the queue must not
    become a way around it.
    """
    if not isinstance(user_text, str) or not user_text.strip():
        raise MalformedRecordError("a fact with no user_text is not worth queueing")
    return {
        "v": RECORD_VERSION,
        "id": fact_id or uuid.uuid4().hex,
        "ts": ts or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "who": (who or "KAUSTAV").upper(),
        "tier": tier,
        "user_text": user_text,
        "reply": reply,
    }


# ── sealing (runs on Render; needs the public half and nothing else) ────────

def seal_fact(payload: dict, desk_public_b64: str) -> dict:
    """Seal one record to the desk's public half. Returns the envelope.

    Raises rather than returning something half-sealed: a cloud-side bug must
    be loud on the cloud, not turn into a quarantine file on the desk hours
    later.
    """
    missing = [f for f in _PAYLOAD_REQUIRED if f not in payload]
    if missing:
        raise MalformedRecordError(f"payload missing {', '.join(missing)}")
    try:
        pub = PublicKey(base64.b64decode(desk_public_b64, validate=True))
    except (binascii.Error, ValueError, TypeError) as exc:
        raise MalformedRecordError(f"desk public key is not usable: {exc}") from exc

    blob = SealedBox(pub).encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return {
        "v": RECORD_VERSION,
        "id": payload["id"],
        "sealed": base64.b64encode(blob).decode("ascii"),
    }


def cloud_can_open(envelope: dict, desk_public_b64: str) -> bool:
    """Always False. Kept as executable documentation of the core property.

    A SealedBox built from a PublicKey has no private half, so there is no code
    path — here or on Render — that turns an envelope back into a fact. The
    harness asserts this rather than trusting the sentence.
    """
    try:
        SealedBox(PublicKey(base64.b64decode(desk_public_b64))).decrypt(
            base64.b64decode(envelope["sealed"]))
        return True
    except Exception:
        return False


# ── opening (desk only; walks DPAPI -> DEK -> private half) ─────────────────

def desk_public_b64() -> str:
    """The public half to hand the cloud. Needs no key unwrap."""
    from modules import memory_crypto  # lazy: Render has no `cryptography`
    return memory_crypto.x25519_public_b64()


def desk_private_raw() -> bytes:
    """The raw private half, unwrapped DPAPI -> DEK -> private.

    Raises MemoryLockedError if the key store is unavailable. That is not a
    per-record failure and must never be quarantined — see drain().
    """
    from modules import memory_crypto
    return memory_crypto.x25519_private_raw()


def regenerate_desk_keypair() -> str:
    """Re-handshake: fresh keypair, same DEK. Returns the new public half.

    Undrained facts sealed to the old public half become unopenable and will
    quarantine on arrival — accepted, and the reason the handshake re-runs on
    every connect rather than once.
    """
    from modules import memory_crypto
    return memory_crypto.rotate_x25519_keypair()


def open_envelope(envelope: dict, private_raw: Optional[bytes] = None) -> dict:
    """Open one envelope. Raises FactSealError; never returns garbage."""
    if not isinstance(envelope, dict):
        raise MalformedRecordError(f"envelope is {type(envelope).__name__}, not a dict")
    missing = [f for f in _ENVELOPE_FIELDS if f not in envelope]
    if missing:
        raise MalformedRecordError(f"envelope missing {', '.join(missing)}")
    if envelope["v"] != RECORD_VERSION:
        raise MalformedRecordError(
            f"envelope version {envelope['v']!r}, this desk speaks v{RECORD_VERSION}")

    try:
        blob = base64.b64decode(envelope["sealed"], validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise MalformedRecordError(f"sealed blob is not base64: {exc}") from exc

    raw = private_raw if private_raw is not None else desk_private_raw()
    try:
        opened = SealedBox(PrivateKey(raw)).decrypt(blob)
    except (CryptoError, ValueError, TypeError) as exc:
        raise SealOpenError(
            f"seal rejected — wrong key, tampering, or a truncated record ({exc})") from exc

    try:
        payload = json.loads(opened.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedRecordError(f"sealed bytes are not a JSON record: {exc}") from exc
    if not isinstance(payload, dict):
        raise MalformedRecordError(
            f"sealed payload is {type(payload).__name__}, not a record")

    missing = [f for f in _PAYLOAD_REQUIRED if f not in payload]
    if missing:
        raise MalformedRecordError(f"payload missing {', '.join(missing)}")
    if payload["id"] != envelope["id"]:
        # The id is the only thing outside the seal that the drain trusts —
        # for idempotency and for naming the quarantine file. If the two
        # disagree, the envelope was rewritten in transit.
        raise MalformedRecordError(
            f"envelope id {envelope['id']!r} != sealed id {payload['id']!r}")
    return payload


def open_or_quarantine(envelope: dict, private_raw: Optional[bytes] = None) -> Optional[dict]:
    """Open one record, or dead-letter it and return None.

    The drain-facing call. A poison record costs one quarantine file and a log
    line; it never raises, so it can never stop the records behind it.
    """
    try:
        return open_envelope(envelope, private_raw)
    except FactSealError as exc:
        quarantine(envelope, str(exc))
        return None


def drain(envelopes: Iterable[dict],
          private_raw: Optional[bytes] = None) -> tuple[list[dict], int]:
    """Open a batch. Returns (payloads that opened, count quarantined).

    The key is unwrapped ONCE, up front and outside the loop, on purpose: if
    the key store is locked that is not any record's fault, so MemoryLockedError
    propagates and the batch is left untouched for the next attempt. Only
    per-record faults quarantine.
    """
    raw = private_raw if private_raw is not None else desk_private_raw()
    opened: list[dict] = []
    bad = 0
    for envelope in envelopes:
        payload = open_or_quarantine(envelope, raw)
        if payload is None:
            bad += 1
        else:
            opened.append(payload)
    if bad:
        print(f"[FACT_SEAL] drained {len(opened)}, quarantined {bad} "
              f"(inspect {QUARANTINE_DIR.name}/).", flush=True)
    return opened, bad


# ── dead-letter store (same discipline as task_queue's poison-task cap) ─────

def quarantine(envelope: object, reason: str) -> Optional[Path]:
    """Keep a poison record for inspection and say so out loud.

    task_queue.claim_next_pending() dead-letters rather than re-serving so one
    bad task cannot wedge the queue behind it; same rule here. The record is
    KEPT — a queue that quietly empties itself looks exactly like success.
    """
    ident = "unknown"
    if isinstance(envelope, dict) and isinstance(envelope.get("id"), str):
        if _UUID_RE.match(envelope["id"]):
            ident = envelope["id"]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    print(f"[FACT_SEAL] ☠ quarantined fact {ident}: {reason}", flush=True)
    try:
        QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
        path = QUARANTINE_DIR / f"{stamp}_{ident}.json"
        path.write_text(json.dumps({
            "quarantined_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "reason": reason,
            "envelope": envelope if isinstance(envelope, (dict, list, str, int, float, bool, type(None)))
                        else repr(envelope),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except Exception as exc:  # noqa: BLE001
        # Losing the FILE must not lose the SIGNAL, and must not kill the
        # drain either — the line above already went to the log.
        print(f"[FACT_SEAL] ⛔ could not write the quarantine file for {ident}: {exc}",
              flush=True)
        return None


def list_quarantined() -> list[Path]:
    if not QUARANTINE_DIR.exists():
        return []
    return sorted(QUARANTINE_DIR.glob("*.json"))


def quarantine_count() -> int:
    return len(list_quarantined())
