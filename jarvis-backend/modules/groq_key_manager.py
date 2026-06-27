"""
Groq API key pool + automatic rotation on HTTP 429 (rate limits) and HTTP 401
(invalid/expired keys), so one bad entry in GROQ_API_KEYS does not brick the stack.

Environment:
  GROQ_API_KEYS — comma-separated keys (optional)
  GROQ_API_KEY  — single key; merged into the pool if not already listed (first slot)

If neither is set, callers fall back to empty key (legacy Groq client behaviour).
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable

from dotenv import load_dotenv
from groq import Groq

load_dotenv(override=True)


def parse_groq_api_keys() -> list[str]:
    """Parse GROQ_API_KEYS plus GROQ_API_KEY into a deduped ordered list."""
    multi = (os.getenv("GROQ_API_KEYS") or "").strip()
    single = (os.getenv("GROQ_API_KEY") or "").strip()
    keys: list[str] = []
    if multi:
        keys.extend(k.strip() for k in multi.split(",") if k.strip())
    if single:
        # Accept accidental "key1,key2" in GROQ_API_KEY (must not be sent as one secret).
        if "," in single:
            parts = [k.strip() for k in single.split(",") if k.strip()]
            for p in reversed(parts):
                if p not in keys:
                    keys.insert(0, p)
        elif single not in keys:
            keys.insert(0, single)
    return keys


_KEYS: list[str] = parse_groq_api_keys()
GROQ_API_KEYS_LIST: list[str] = list(_KEYS)

_lock = threading.Lock()
_active_idx: int = 0


def groq_key_count() -> int:
    return len(_KEYS)


def has_groq_keys() -> bool:
    return bool(_KEYS)


def _is_rate_limit(exc: BaseException) -> bool:
    if type(exc).__name__ == "RateLimitError":
        return True
    code = getattr(exc, "status_code", None)
    if code == 429:
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    if "429" in msg and ("rate" in msg or "too many requests" in msg):
        return True
    return False


def _is_invalid_api_key(exc: BaseException) -> bool:
    """Groq rejected the key (wrong/revoked/expired) — try next key in pool."""
    if type(exc).__name__ == "AuthenticationError":
        return True
    code = getattr(exc, "status_code", None)
    if code == 401:
        return True
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None) == 401:
        return True
    msg = str(exc).lower()
    if "invalid api key" in msg or "invalid_api_key" in msg:
        return True
    return False


def _sync_brain_module_client(key_client: Groq) -> None:
    """Keep brain.client pointing at the Groq instance used for the latest attempt."""
    import sys

    m = sys.modules.get("brain")
    if m is not None:
        setattr(m, "client", key_client)


def get_initial_client() -> Groq:
    """First Groq client for module-level export (brain.client)."""
    if not _KEYS:
        return Groq(api_key=os.getenv("GROQ_API_KEY") or "")
    return Groq(api_key=_KEYS[0])


def run_with_key_rotation(call_fn: Callable[[Groq], Any]) -> Any:
    """
    Execute call_fn(groq_client). On rate limit (429) or invalid key (401),
    retry with each remaining key in the pool.

    Updates brain.client to the client used for each attempt (successful or not).
    On success, records the working key index as the active key for the next call.
    """
    global _active_idx

    if not _KEYS:
        c = Groq(api_key=os.getenv("GROQ_API_KEY") or "")
        _sync_brain_module_client(c)
        return call_fn(c)

    last_exc: BaseException | None = None
    with _lock:
        start_idx = _active_idx
    n = len(_KEYS)

    for attempt in range(n):
        idx = (start_idx + attempt) % n
        key_client = Groq(api_key=_KEYS[idx])
        _sync_brain_module_client(key_client)
        try:
            out = call_fn(key_client)
            with _lock:
                _active_idx = idx
            return out
        except BaseException as e:
            if _is_rate_limit(e):
                last_exc = e
                print(
                    f"[BRAIN] Rate limit hit on Key {idx}. Rotating to next key...",
                    flush=True,
                )
                continue
            if _is_invalid_api_key(e):
                last_exc = e
                print(
                    f"[BRAIN] Invalid API key rejected on Key {idx}. Rotating to next key...",
                    flush=True,
                )
                continue
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Groq API key rotation exhausted without a recoverable error")
