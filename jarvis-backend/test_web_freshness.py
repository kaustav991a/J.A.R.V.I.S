"""Harness for web-lookup freshness — why J.A.R.V.I.S. quoted a 2025 article.

He asked about rain on 2026-08-17 and was warned using a 2025 monsoon piece. No
mechanism was broken: the phone's envelope already carried local wall time
(`src/lib/ask.ts` in jarvis-mobile), so the model knew what *today* was. What it
never knew was how old the *evidence* was, because every search snippet reached it
undated:

    bits.append(f"- {title}: {content}")

Tavily returns `published_date` and it was discarded. Worse, recency meant
appending the words "latest result today" to the query — a hint to the ranker, not
a filter, which a well-ranked article from last year satisfies completely.

So the properties below are about evidence carrying its own age:

  1. a dated result says when it was published, normalised to one shape;
  2. an undated one says so out loud rather than going bare next to dated ones —
     a bare snippet among stamped ones reads as "recent enough not to need one";
  3. a time-sensitive query asks Tavily for a dated window (`topic`/`days`), and
     an ordinary one does not: restricting "what is a pangolin" to the last
     fortnight of news is a different wrong answer;
  4. every block states today's date, so age needs no arithmetic to see;
  5. DuckDuckGo snippets are marked unknown, because DDG has no date to give and
     an unmarked DDG block would read as the dated kind.

No network: `urllib.request.urlopen` and `ddgs` are both stubbed.
"""

import json
import os
import sys
import types
import urllib.request

os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402

_real_urlopen = urllib.request.urlopen


class _FakeResp:
    """Just enough of an http response for `with urlopen(...) as r: r.read()`."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


# what the last request asked Tavily for, so the filters can be asserted on
_sent = {}


def _serve(payload):
    """Point urlopen at one canned Tavily body and record what was requested."""

    def _fake(req, timeout=None):  # noqa: ARG001
        _sent.clear()
        _sent.update(json.loads(req.data.decode()))
        return _FakeResp(payload)

    urllib.request.urlopen = _fake


def _setup():
    cg._TAVILY_KEY = "harness-tavily-key"
    cg.WEB_LOOKUP = True
    _sent.clear()
    urllib.request.urlopen = _real_urlopen


def test_a_dated_result_carries_its_date():
    _serve({"results": [{"title": "Monsoon batters the coast",
                         "content": "Heavy rain across the district.",
                         "published_date": "2025-06-14"}]})
    out = cg._web_lookup("will it rain")
    assert "[2025-06-14] Monsoon batters the coast" in out, out


def test_an_rfc_date_is_normalised_to_the_same_shape():
    """Two sources, two formats — comparable only if both come out ISO."""
    _serve({"results": [{"title": "A", "content": "x",
                         "published_date": "Mon, 14 Jun 2025 09:00:00 GMT"},
                        {"title": "B", "content": "y",
                         "published_date": "2026-08-18"}]})
    out = cg._web_lookup("latest news")
    assert "[2025-06-14] A: x" in out, out
    assert "[2026-08-18] B: y" in out, out


def test_an_undated_result_says_so_rather_than_going_bare():
    _serve({"results": [{"title": "Undated blog", "content": "It rains here."}]})
    out = cg._web_lookup("will it rain")
    assert "[date unknown] Undated blog" in out, out


def test_an_unparseable_date_is_shown_not_dropped():
    """A strange date still tells the model the claim has an age."""
    _serve({"results": [{"title": "C", "content": "z",
                         "published_date": "last Tuesday-ish"}]})
    out = cg._web_lookup("current price")
    assert "last Tuesday-ish" in out, out
    assert "C: z" in out, out


def test_a_time_sensitive_query_asks_for_a_dated_window():
    _serve({"results": [{"title": "A", "content": "x"}]})
    cg._web_lookup("what is the score")
    assert _sent.get("topic") == "news", _sent
    assert _sent.get("days") == cg._TAVILY_FRESH_DAYS, _sent


def test_an_ordinary_question_is_not_restricted_to_the_news():
    _serve({"results": [{"title": "A", "content": "x"}]})
    cg._web_lookup("what is a pangolin")
    assert "topic" not in _sent, _sent
    assert "days" not in _sent, _sent


def test_the_query_itself_is_no_longer_nudged_for_tavily():
    """The nudge was the old recency mechanism and it is what failed."""
    _serve({"results": [{"title": "A", "content": "x"}]})
    cg._web_lookup("weather today")
    assert _sent.get("query") == "weather today", _sent


def test_every_block_states_todays_date():
    import datetime as _dt

    today = _dt.datetime.now(cg._OPERATOR_TZ).date().isoformat()
    _serve({"results": [{"title": "A", "content": "x", "published_date": "2025-01-01"}]})
    out = cg._web_lookup("will it rain")
    assert f"Today is {today}" in out, out


def test_the_summary_line_survives():
    _serve({"answer": "It is raining.",
            "results": [{"title": "A", "content": "x", "published_date": "2026-08-18"}]})
    out = cg._web_lookup("will it rain")
    assert "- Summary: It is raining." in out, out


def test_ddg_snippets_are_marked_unknown():
    """DDG has no date to give, and an unmarked block reads as the dated kind."""
    _serve({"results": []})  # Tavily answers nothing, so DDG takes the turn

    class _FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def text(self, _query, max_results=6):  # noqa: ARG002
            return [{"title": "Some page", "body": "Rain expected."}]

    fake = types.ModuleType("ddgs")
    fake.DDGS = _FakeDDGS
    sys.modules["ddgs"] = fake
    try:
        out = cg._web_lookup("will it rain")
    finally:
        sys.modules.pop("ddgs", None)
    assert "[date unknown] Some page: Rain expected." in out, out
    assert "Today is" in out, out


def test_a_disabled_lookup_still_returns_nothing():
    cg.WEB_LOOKUP = False
    try:
        assert cg._web_lookup("will it rain") == ""
    finally:
        cg.WEB_LOOKUP = True


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        _setup()
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        finally:
            urllib.request.urlopen = _real_urlopen
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
