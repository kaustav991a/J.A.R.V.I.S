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

# ── Which Groq model, in ONE place ────────────────────────────────────────────
# Live-gate session 4. `llama-3.1-8b-instant` was hardcoded in five files and was
# the default in two more, and Groq had decommissioned it: every call answered
#
#   404 — "The model `llama-3.1-8b-instant` does not exist or you do not have
#          access to it."
#
# so memory extraction, episodic summaries and the GUI agent's parser failed on
# EVERY turn. All three swallow their errors by design, which is why a dead model
# id looked like nothing at all.
#
# This is the SAME decommissioning that took `llama-3.3-70b-versatile` on
# 2026-08-16. That one was fixed where it was noticed — `GROQ_TOOL_MODEL` in
# llm_router, and the cloud gateway, which already runs gpt-oss-120b — and the
# cheap leg was left behind. Root cause #4: a class fixed one site at a time
# stays open. So the id lives here, next to the keys, where the four callers that
# hardcoded it already import from.
#
# Measured against the live catalogue this session, one real desk-shaped payload
# (17,755 chars, JSON reply carrying a whole file) per model:
#
#   openai/gpt-oss-120b    1.5s  finish=stop  288 completion tokens  valid JSON
#   openai/gpt-oss-20b     1.9s  finish=stop  815 completion tokens  valid JSON
#   qwen/qwen3.6-27b       400 — its template needs a `user` message, not `system`
#   groq/compound-mini     400 — "last message role must be 'user'"
#
# 20b was tried first, to keep the chat leg on a different id from the tool leg
# (`test_tool_call.py` asserted they differ, so that a rate limit on one would not
# close the other). It does not survive the desk's REAL payload. Measured on the
# failing shape — 9-14 messages, ~16-19k chars, action catalogue, conversation
# history, streamed:
#
#   openai/gpt-oss-120b   0.6s / 0.6s / 5.0s   clean JSON on all three turns
#   openai/gpt-oss-20b    0.5s / 3.6s / 30.0s  one turn returned ZERO characters,
#                         and on the live desk it answered
#                           400 tool_use_failed — "Tool choice is none, but model
#                           called a tool"
#                         with failed_generation holding the action it wanted:
#                           {"name":"assistant","arguments":{"actions":[...]}}
#                         so the whole Groq leg failed and every turn escalated
#   groq/compound-mini    routes internally to other models and hit THEIR rate
#                         limits (gpt-oss-120b, llama-3.3-70b-versatile)
#   groq/compound         "Request Entity Too Large" on a 19k-char payload
#   qwen/qwen3.6-27b      streams a <think> block INSIDE content — 3,271 chars of
#                         monologue for "what is the capital of Iceland"
#
# So there is no second viable id on this account, and the separation the harness
# asked for cannot be bought. Both legs run 120b, which means they now share one
# daily bucket: a chat-side rate limit takes the tool loop with it. That is a real
# cost, accepted knowingly, because the alternative measured worse — an empty
# answer and a dead leg are worse than a shared quota. If a small plain instruct
# model returns to this account, split them again.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def groq_model() -> str:
    """The Groq model id every desk-side caller should use.

    Read through a function rather than bound at import time so a corrected
    `.env` takes effect on the next call instead of on the next reboot.
    """
    return (os.getenv("GROQ_MODEL") or "").strip() or DEFAULT_GROQ_MODEL


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
