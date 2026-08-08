r"""
test_gesture_camera.py — G6.3 camera source auto-select (pure logic)

Run: venv\Scripts\python.exe test_gesture_camera.py

Exercises parse_sources (ordering/dedup/fallback), url_reachable (via an
injected connect), open_first_available (first-working-wins /
skip-unreachable / skip-erroring / all-fail) via injected reachable + opener,
and _open_source's frame validation (index AND url must deliver a real frame)
via an injected capture factory. No real camera or network is touched.
"""

import time

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


# ------------------------------------------------------- _open_source frame gate

class _FakeCap:
    """Capture stub: opens or not, delivers a frame after `fail_frames` reads."""

    def __init__(self, opened=True, fail_frames=0, total_frames=99):
        self._opened = opened
        self._fail_frames = fail_frames
        self._total = total_frames
        self.reads = 0
        self.released = False
        self.props = {}

    def isOpened(self):  # noqa: N802 — cv2 API name
        return self._opened

    def read(self):
        self.reads += 1
        if self.reads <= self._fail_frames or self.reads > self._fail_frames + self._total:
            return False, None
        return True, f"FRAME{self.reads}"

    def set(self, prop, value):
        self.props[prop] = value

    def release(self):
        self.released = True


def _capture_factory(caps):
    """capture(spec) -> caps[spec]; records the exact specs requested."""
    seen = []

    def capture(spec):
        seen.append(spec)
        return caps[spec]

    return capture, seen


def _noop_sleep(_s):
    pass


def test_url_accepted_only_after_a_real_frame():
    cap = _FakeCap(opened=True, fail_frames=0)
    capture, seen = _capture_factory({"http://a/video?640x480": cap})
    got = gc._open_source("http://a/video", 640, 480, "640x480",
                          capture=capture, sleep=_noop_sleep)
    check(got is cap, "working stream returned")
    check(cap.reads == 1, f"frame was actually read, got {cap.reads} reads")
    check(seen == ["http://a/video?640x480"], f"decorate_url applied, got {seen}")
    check(not cap.released, "working stream not released")


def test_url_opens_but_stalls_raises_stream():
    cap = _FakeCap(opened=True, fail_frames=999)  # connects, never delivers
    capture, _ = _capture_factory({"http://dead/video?640x480": cap})
    try:
        gc._open_source("http://dead/video", 640, 480, "640x480",
                        capture=capture, sleep=_noop_sleep)
    except CameraError as e:
        check(e.kind == "stream", f"stalled stream -> stream, got {e.kind}")
        check("no frames" in str(e), f"message names the stall, got {str(e)[:60]}")
        check(cap.reads == gc.STREAM_FRAME_ATTEMPTS,
              f"retried {gc.STREAM_FRAME_ATTEMPTS}x, got {cap.reads}")
        check(cap.released, "stalled stream released")
    else:
        check(False, "expected CameraError for a stream that delivers no frames")


def test_url_slow_first_frame_is_tolerated():
    cap = _FakeCap(opened=True, fail_frames=3)  # MJPEG warm-up
    capture, _ = _capture_factory({"http://slow/video?640x480": cap})
    slept = []
    got = gc._open_source("http://slow/video", 640, 480, "640x480",
                          capture=capture, sleep=slept.append)
    check(got is cap, "slow-but-alive stream accepted")
    check(len(slept) == 3, f"waited between retries, got {len(slept)} sleeps")


def test_url_not_opened_still_unreachable():
    cap = _FakeCap(opened=False)
    capture, _ = _capture_factory({"http://x/video?640x480": cap})
    try:
        gc._open_source("http://x/video", 640, 480, "640x480",
                        capture=capture, sleep=_noop_sleep)
    except CameraError as e:
        check(e.kind == "stream", "unopened url -> stream")
        check("unreachable" in str(e), "unreachable message kept")
        check(cap.reads == 0, "no read attempted on an unopened stream")
    else:
        check(False, "expected CameraError for an unopened stream")


def test_index_frame_gate_unchanged():
    cap = _FakeCap(opened=True, fail_frames=999)
    capture, _ = _capture_factory({0: cap})
    try:
        gc._open_source(0, 640, 480, "640x480", capture=capture, sleep=_noop_sleep)
    except CameraError as e:
        check(e.kind == "busy", f"index with no frames -> busy, got {e.kind}")
        check(cap.reads == gc.INDEX_FRAME_ATTEMPTS,
              f"index retried {gc.INDEX_FRAME_ATTEMPTS}x, got {cap.reads}")
        check(len(cap.props) == 2, f"width/height still set, got {cap.props}")
    else:
        check(False, "expected CameraError('busy') for an index with no frames")


def test_stalled_stream_loses_to_next_working_source():
    """The bug this gate fixes: a connect-but-no-frames URL must not win."""
    stalled = _FakeCap(opened=True, fail_frames=999)
    working = _FakeCap(opened=True, fail_frames=0)
    capture, _ = _capture_factory({
        "http://stalled/video?640x480": stalled,
        "http://good/video?640x480": working,
    })

    def opener(src, w, h, url_res):
        return gc._open_source(src, w, h, url_res, capture=capture, sleep=_noop_sleep)

    cap, chosen = gc.open_first_available(
        ["http://stalled/video", "http://good/video"],
        reachable=lambda u: True, opener=opener)  # both hosts answer TCP
    check(chosen == "http://good/video", f"stalled source skipped, got {chosen}")
    check(cap is working, "returned cap is the frame-delivering one")


# ── FrameSource recovery (F-08, live gate 2026-08-08) ────────────────────────
# The reader used to allow 30 failed reads at 50ms — 1.55s — and then kill its
# own thread permanently. One corrupt JPEG desyncs cv2's decoder for longer than
# that, so the gesture daemon tore down its whole session five times in twenty
# five minutes against a stream measured healthy (548 frames / 20s). These pin
# the two properties that were missing: a short gap is survivable, and a long
# one is recovered by REOPENING rather than by dying.

def _wait_until(predicate, timeout=3.0):
    """True as soon as predicate() holds; False if it never does in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_brief_desync_does_not_kill_the_reader():
    # Fails a few reads, then delivers — the ordinary decoder hiccup.
    cap = _FakeCap(opened=True, fail_frames=4)
    fs = gc.FrameSource("x", cap=cap, reopen=lambda: None,
                        stall_seconds=2.0, max_reopen=2)
    try:
        got = _wait_until(lambda: fs._seq > 0)
        check(got, "a frame arrived after a brief run of failed reads")
        check(fs._dead is None, f"reader still alive, got dead={fs._dead}")
        check(fs.reopens == 0, f"no reopen needed for a brief gap, got {fs.reopens}")
    finally:
        fs.release()


def test_stalled_capture_is_reopened_not_declared_dead():
    dead_cap = _FakeCap(opened=True, fail_frames=999)   # connects, never delivers
    fresh_cap = _FakeCap(opened=True, fail_frames=0)
    fs = gc.FrameSource("x", cap=dead_cap, reopen=lambda: fresh_cap,
                        stall_seconds=0.05, max_reopen=3)
    try:
        got = _wait_until(lambda: fs._seq > 0)
        check(got, "frames flow again after the stalled capture was reopened")
        check(fs.reopens >= 1, f"recovery actually ran, got reopens={fs.reopens}")
        check(fs._dead is None,
              f"a stall that reopens is NOT death, got dead={fs._dead}")
    finally:
        fs.release()


def test_reopen_releases_the_stale_capture():
    dead_cap = _FakeCap(opened=True, fail_frames=999)
    fresh_cap = _FakeCap(opened=True, fail_frames=0)
    fs = gc.FrameSource("x", cap=dead_cap, reopen=lambda: fresh_cap,
                        stall_seconds=0.05, max_reopen=3)
    try:
        _wait_until(lambda: fs.reopens >= 1)
        check(dead_cap.released, "the stale capture was released, not leaked")
    finally:
        fs.release()


def test_dead_only_after_reopen_attempts_are_exhausted():
    dead_cap = _FakeCap(opened=True, fail_frames=999)
    attempts = []

    def _failing_reopen():
        attempts.append(1)
        raise OSError("camera really is gone")

    backoff = gc.REOPEN_BACKOFF_S
    gc.REOPEN_BACKOFF_S = 0.0          # keep the harness fast
    fs = gc.FrameSource("x", cap=dead_cap, reopen=_failing_reopen,
                        stall_seconds=0.05, max_reopen=3)
    try:
        got = _wait_until(lambda: fs._dead is not None)
        check(got, "a camera that cannot be reopened does eventually die")
        check(len(attempts) >= 3,
              f"tried to reopen before dying, got {len(attempts)} attempts")
        check("reopen attempts failed" in (fs._dead or ""),
              f"message says recovery was tried, got {fs._dead!r}")
        try:
            fs.read_new(0, timeout=0.1)
        except CameraError as e:
            check(e.kind == "dead", f"read_new raises dead, got {e.kind}")
        else:
            check(False, "read_new should raise once the source is dead")
    finally:
        gc.REOPEN_BACKOFF_S = backoff
        fs.release()


TESTS = [
    test_parse_comma_list_order_and_types, test_parse_dedup_preserves_first,
    test_parse_blank_entries_dropped, test_parse_env_overrides_legacy,
    test_parse_falls_back_to_legacy, test_parse_all_empty_defaults_index0,
    test_reachable_true_on_connect, test_reachable_false_on_oserror,
    test_reachable_default_ports, test_reachable_schemeless, test_reachable_no_host,
    test_open_first_wins, test_open_skips_unreachable, test_open_skips_erroring_source,
    test_open_index_skips_reachability, test_open_all_fail_raises_with_summary,
    test_open_only_source_unreachable_raises,
    test_url_accepted_only_after_a_real_frame, test_url_opens_but_stalls_raises_stream,
    test_url_slow_first_frame_is_tolerated, test_url_not_opened_still_unreachable,
    test_index_frame_gate_unchanged, test_stalled_stream_loses_to_next_working_source,
    test_brief_desync_does_not_kill_the_reader,
    test_stalled_capture_is_reopened_not_declared_dead,
    test_reopen_releases_the_stale_capture,
    test_dead_only_after_reopen_attempts_are_exhausted,
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
