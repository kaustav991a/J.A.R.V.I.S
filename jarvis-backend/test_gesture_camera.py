r"""
test_gesture_camera.py — G6.3 camera source auto-select (pure logic)

Run: venv\Scripts\python.exe test_gesture_camera.py

Exercises parse_sources (ordering/dedup/fallback), url_reachable (via an
injected connect), and open_first_available (first-working-wins /
skip-unreachable / skip-erroring / all-fail) via injected reachable + opener.
No real camera or network is touched.
"""

from modules import gesture_camera as gc
from modules.gesture_camera import CameraError

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


# ---------------------------------------------------------------- parse_sources

def test_parse_comma_list_order_and_types():
    out = gc.parse_sources("0, http://a/video ,1", None)
    check(out == [0, "http://a/video", 1], f"ordered mixed list, got {out}")


def test_parse_dedup_preserves_first():
    out = gc.parse_sources("1,1,http://a,http://a,2", None)
    check(out == [1, "http://a", 2], f"dedup keeps first order, got {out}")


def test_parse_blank_entries_dropped():
    out = gc.parse_sources("0,, ,http://a", None)
    check(out == [0, "http://a"], f"blanks dropped, got {out}")


def test_parse_env_overrides_legacy():
    out = gc.parse_sources("http://a", "9")
    check(out == ["http://a"], f"env wins over legacy, got {out}")


def test_parse_falls_back_to_legacy():
    check(gc.parse_sources(None, "http://legacy/video") == ["http://legacy/video"],
          "legacy used when env unset")
    check(gc.parse_sources("   ", "2") == [2], "whitespace env -> legacy")


def test_parse_all_empty_defaults_index0():
    check(gc.parse_sources(None, None) == [0], "no config -> [0]")
    check(gc.parse_sources("", "") == [0], "empty strings -> [0]")


# ----------------------------------------------------------------- url_reachable

class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_reachable_true_on_connect():
    seen = {}

    def connect(addr, timeout):
        seen["addr"] = addr
        seen["timeout"] = timeout
        return _FakeConn()

    ok = gc.url_reachable("http://192.168.0.103:4747/video", timeout=2.0, connect=connect)
    check(ok, "reachable when connect succeeds")
    check(seen["addr"] == ("192.168.0.103", 4747), f"host/port parsed, got {seen.get('addr')}")
    check(seen["timeout"] == 2.0, "timeout forwarded to connect")


def test_reachable_false_on_oserror():
    def connect(addr, timeout):
        raise OSError("connection refused")

    check(not gc.url_reachable("http://192.168.0.103:8080/video", connect=connect),
          "unreachable when connect raises OSError")


def test_reachable_default_ports():
    got = {}

    def connect(addr, timeout):
        got["addr"] = addr
        return _FakeConn()

    gc.url_reachable("http://host/video", connect=connect)
    check(got["addr"] == ("host", 80), f"http default port 80, got {got.get('addr')}")
    gc.url_reachable("https://host/video", connect=connect)
    check(got["addr"] == ("host", 443), f"https default port 443, got {got.get('addr')}")


def test_reachable_schemeless():
    got = {}

    def connect(addr, timeout):
        got["addr"] = addr
        return _FakeConn()

    gc.url_reachable("192.168.0.103:4747", connect=connect)
    check(got["addr"] == ("192.168.0.103", 4747), f"schemeless parsed, got {got.get('addr')}")


def test_reachable_no_host():
    def connect(addr, timeout):
        raise AssertionError("connect must not run when there is no host")

    check(not gc.url_reachable("", connect=connect), "empty url -> False")


# ------------------------------------------------------------ open_first_available

def _opener_factory(open_ok):
    """opener returning a sentinel cap for sources in `open_ok`, else raising."""
    calls = []

    def opener(src, w, h, url_res):
        calls.append(src)
        if src in open_ok:
            return f"CAP<{src}>"
        raise CameraError("stream", f"stream unreachable: {src}")

    return opener, calls


def test_open_first_wins():
    opener, calls = _opener_factory(open_ok={"http://a", "http://b"})
    cap, chosen = gc.open_first_available(
        ["http://a", "http://b"], reachable=lambda u: True, opener=opener)
    check(chosen == "http://a", f"first working source wins, got {chosen}")
    check(cap == "CAP<http://a>", "returns the chosen source's cap")
    check(calls == ["http://a"], f"stops at first success, got {calls}")


def test_open_skips_unreachable():
    opener, calls = _opener_factory(open_ok={"http://b"})
    reach = lambda u: u != "http://a"  # a is unreachable
    cap, chosen = gc.open_first_available(
        ["http://a", "http://b"], reachable=reach, opener=opener)
    check(chosen == "http://b", f"skips unreachable to next, got {chosen}")
    check(calls == ["http://b"], f"opener never runs for unreachable, got {calls}")


def test_open_skips_erroring_source():
    opener, calls = _opener_factory(open_ok={1})
    cap, chosen = gc.open_first_available(
        ["http://a", 1], reachable=lambda u: True, opener=opener)
    check(chosen == 1, f"falls through open error to next, got {chosen}")
    check(calls == ["http://a", 1], f"tried both in order, got {calls}")


def test_open_index_skips_reachability():
    def reach(u):
        raise AssertionError("reachability must not run for an int index")

    opener, calls = _opener_factory(open_ok={0})
    cap, chosen = gc.open_first_available([0], reachable=reach, opener=opener)
    check(chosen == 0, "int index chosen without a TCP probe")


def test_open_all_fail_raises_with_summary():
    opener, calls = _opener_factory(open_ok=set())
    try:
        gc.open_first_available(["http://a", 1], reachable=lambda u: True, opener=opener)
    except CameraError as e:
        check(e.kind == "absent", "all sources failing -> absent")
        msg = str(e)
        check("http://a" in msg and "1" in msg, f"summary lists each source, got {msg}")
    else:
        check(False, "expected CameraError when all sources fail")


def test_open_only_source_unreachable_raises():
    opener, calls = _opener_factory(open_ok={"http://a"})
    try:
        gc.open_first_available(["http://a"], reachable=lambda u: False, opener=opener)
    except CameraError as e:
        check("unreachable" in str(e), "unreachable noted in the summary")
        check(calls == [], "opener never called when the only source is unreachable")
    else:
        check(False, "expected CameraError when the only source is unreachable")


TESTS = [
    test_parse_comma_list_order_and_types, test_parse_dedup_preserves_first,
    test_parse_blank_entries_dropped, test_parse_env_overrides_legacy,
    test_parse_falls_back_to_legacy, test_parse_all_empty_defaults_index0,
    test_reachable_true_on_connect, test_reachable_false_on_oserror,
    test_reachable_default_ports, test_reachable_schemeless, test_reachable_no_host,
    test_open_first_wins, test_open_skips_unreachable, test_open_skips_erroring_source,
    test_open_index_skips_reachability, test_open_all_fail_raises_with_summary,
    test_open_only_source_unreachable_raises,
]


def main():
    print("=" * 60)
    print("gesture_camera auto-select harness (G6.3)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
