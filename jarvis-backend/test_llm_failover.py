r"""
test_llm_failover.py — G5.7 LLM-router robustness (no network)

Run: venv\Scripts\python.exe test_llm_failover.py

Covers the ollama empty-200 fix: an HTTP 200 with an empty message must RAISE
(so universal_llm_call trips the breaker and escalates to cloud) instead of
silently returning "" — which downstream narrates as a false success. Also
checks the non-empty paths still return, and that the cascade escalates past a
failing ollama to the next provider. `requests` is faked; no real HTTP.
"""

import json as _json
import types

from modules import llm_router

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


class FakeResp:
    def __init__(self, content=None, lines=None):
        self._content = content
        self._lines = lines or []

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._content}}

    # streaming context-manager protocol (`with requests.post(..., stream=True) as r`)
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self):
        for ln in self._lines:
            yield ln


def _fake_requests(resp):
    m = types.SimpleNamespace()
    m.post = lambda *a, **k: resp
    return m


def _call(stream, resp):
    old = llm_router.requests
    llm_router.requests = _fake_requests(resp)
    try:
        return llm_router._call_ollama(
            [{"role": "user", "content": "hi"}], 0.5, 50, stream, False, None, 5.0)
    finally:
        llm_router.requests = old


def _raises(fn):
    try:
        r = fn()
        # streaming: the raise happens eagerly inside _call_ollama (next(g)), so a
        # return value means it did NOT raise
        return False, r
    except Exception:
        return True, None


def test_empty_200_nonstream_raises():
    raised, _ = _raises(lambda: _call(False, FakeResp(content="")))
    check(raised, "empty 200 (non-stream) must raise, not return ''")


def test_whitespace_200_nonstream_raises():
    raised, _ = _raises(lambda: _call(False, FakeResp(content="   \n ")))
    check(raised, "whitespace-only 200 must raise")


def test_nonempty_nonstream_returns():
    out = _call(False, FakeResp(content="  hello, Sir  "))
    check(out == "hello, Sir", "non-empty 200 returns stripped content")


def test_empty_200_stream_raises():
    raised, _ = _raises(lambda: _call(True, FakeResp(lines=[])))
    check(raised, "empty 200 stream must raise")


def test_nonempty_stream_returns_generator():
    lines = [_json.dumps({"message": {"content": c}}).encode() for c in ("foo", "bar")]
    gen = _call(True, FakeResp(lines=lines))
    check("".join(gen) == "foobar", "non-empty stream yields concatenated content")


def test_cascade_escalates_past_failing_ollama():
    saved = (llm_router._route_order, llm_router._call_ollama,
             llm_router._call_groq, llm_router._trip_ollama_breaker)
    tripped = {"v": False}
    llm_router._route_order = lambda *a, **k: ["ollama", "groq"]

    def _boom(*a, **k):
        raise RuntimeError("ollama returned an empty 200 response")

    llm_router._call_ollama = _boom
    llm_router._call_groq = lambda *a, **k: "CLOUD_OK"
    llm_router._trip_ollama_breaker = lambda e: tripped.__setitem__("v", True)
    try:
        out = llm_router.universal_llm_call(
            [{"role": "user", "content": "hi"}], stream=False)
        check(out == "CLOUD_OK", "cascade escalates to groq when ollama raises")
        check(tripped["v"], "ollama breaker is tripped on the empty-200 failure")
    finally:
        (llm_router._route_order, llm_router._call_ollama,
         llm_router._call_groq, llm_router._trip_ollama_breaker) = saved


# ── The cloud provider order (2026-08-15: Gemini became primary) ─────────────
# The order decides which model answers every ordinary turn, so it is worth
# pinning: a silent revert to Groq-first would show up only as replies drifting
# back into worse Benglish, which nobody would read as a config regression.

import os as _os  # noqa: E402


def _with_env(**kv):
    """Set env for one call and restore it — including keys that were absent."""
    saved = {k: _os.environ.get(k) for k in kv}

    def restore():
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
    for k, v in kv.items():
        if v is None:
            _os.environ.pop(k, None)
        else:
            _os.environ[k] = v
    return restore


def test_gemini_is_the_default_primary():
    restore = _with_env(JARVIS_CLOUD_ORDER=None)
    try:
        check(llm_router._cloud_chain()[0] == "gemini",
              "Gemini must be the default primary cloud provider")
        check(llm_router._cloud_chain() == ["gemini", "groq", "openrouter"],
              "the default chain is gemini -> groq -> openrouter")
    finally:
        restore()


def test_the_order_is_overridable_without_a_code_change():
    restore = _with_env(JARVIS_CLOUD_ORDER="groq, gemini")
    try:
        check(llm_router._cloud_chain() == ["groq", "gemini"],
              "JARVIS_CLOUD_ORDER must be honoured, whitespace and all")
    finally:
        restore()


def test_a_typo_cannot_silence_the_cloud():
    """An unrecognised order must fall back to every provider, not to none —
    an empty chain would look exactly like every provider being down."""
    restore = _with_env(JARVIS_CLOUD_ORDER="grok,gemeni,nonsense")
    try:
        chain = llm_router._cloud_chain()
        check(chain == list(llm_router._KNOWN_CLOUD),
              f"a fully-invalid order must fall back to all providers, got {chain}")
    finally:
        restore()


def test_duplicates_are_collapsed_order_preserved():
    restore = _with_env(JARVIS_CLOUD_ORDER="gemini,groq,gemini,groq")
    try:
        check(llm_router._cloud_chain() == ["gemini", "groq"],
              "a repeated provider is not attempted twice")
    finally:
        restore()


def test_cloud_first_puts_the_chain_ahead_of_local():
    """cloud_first is this box's configured mode (17GB, CPU-only), so the chain
    must lead and ollama must be the tail, not the head."""
    saved_mode = llm_router.LLM_MODE
    restore = _with_env(JARVIS_CLOUD_ORDER="gemini,groq")
    llm_router.LLM_MODE = "cloud_first"
    try:
        order = llm_router._route_order("standard", None)
        check(order[0] == "gemini", f"cloud_first must lead with gemini, got {order}")
        check(order[-1] == "ollama" or "ollama" not in order,
              f"ollama must be the last resort, got {order}")
    finally:
        llm_router.LLM_MODE = saved_mode
        restore()


def test_a_llava_model_is_still_local_only():
    """Regression guard: the vision pin must survive the reorder — there is no
    cloud llava, so a cloud-first chain must not capture it."""
    check(llm_router._route_order("heavy", "llava:latest") == ["ollama"],
          "a llava model stays local-only regardless of the cloud order")


TESTS = [test_empty_200_nonstream_raises, test_whitespace_200_nonstream_raises,
         test_nonempty_nonstream_returns, test_empty_200_stream_raises,
         test_nonempty_stream_returns_generator,
         test_cascade_escalates_past_failing_ollama,
         test_gemini_is_the_default_primary,
         test_the_order_is_overridable_without_a_code_change,
         test_a_typo_cannot_silence_the_cloud,
         test_duplicates_are_collapsed_order_preserved,
         test_cloud_first_puts_the_chain_ahead_of_local,
         test_a_llava_model_is_still_local_only]


def main():
    print("=" * 60)
    print("llm_router failover harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
