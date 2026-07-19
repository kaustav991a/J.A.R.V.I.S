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


TESTS = [test_empty_200_nonstream_raises, test_whitespace_200_nonstream_raises,
         test_nonempty_nonstream_returns, test_empty_200_stream_raises,
         test_nonempty_stream_returns_generator,
         test_cascade_escalates_past_failing_ollama]


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
