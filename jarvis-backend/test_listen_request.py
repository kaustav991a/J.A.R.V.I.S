"""Harness for modules/listen_request.py — the click-to-talk flag.

Injected clock, no audio device, no HTTP. What matters here is that a button
press reaches a BLOCKED microphone thread exactly once, and that a stale press
never pops the mic open long after the user gave up.
"""

import threading

from modules.listen_request import ListenRequest


class Clock:
    """Manual monotonic clock."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def test_nothing_pending_by_default():
    lr = ListenRequest(clock=Clock())
    assert lr.consume() is None
    assert lr.pending() is False
    assert lr.age() is None


def test_request_is_consumed_once():
    """One click = one turn. The second consume must come back empty, or a
    single press would make him listen again on the next loop pass."""
    lr = ListenRequest(clock=Clock())
    lr.request("hud")
    assert lr.pending() is True
    assert lr.consume() == "hud"
    assert lr.consume() is None
    assert lr.pending() is False


def test_source_is_reported_back():
    lr = ListenRequest(clock=Clock())
    lr.request("telegram")
    assert lr.consume() == "telegram"


def test_blank_source_falls_back_to_hud():
    lr = ListenRequest(clock=Clock())
    lr.request("")
    assert lr.consume() == "hud"


def test_expired_request_is_dropped():
    """A click during a 40s LLM turn must not open the mic afterwards."""
    c = Clock()
    lr = ListenRequest(ttl_s=15.0, clock=c)
    lr.request()
    c.advance(15.5)
    assert lr.pending() is False
    assert lr.consume() is None
    # ...and it stays gone: an expired consume clears the slot too.
    assert lr.pending() is False


def test_request_just_inside_the_window_still_fires():
    c = Clock()
    lr = ListenRequest(ttl_s=15.0, clock=c)
    lr.request()
    c.advance(14.9)
    assert lr.consume() == "hud"


def test_second_click_refreshes_the_deadline():
    """He was speaking, the user clicked again — the newer press is the live one."""
    c = Clock()
    lr = ListenRequest(ttl_s=10.0, clock=c)
    lr.request()
    c.advance(9.0)
    lr.request()          # refresh
    c.advance(5.0)        # 14s after the FIRST click, 5s after the second
    assert lr.consume() == "hud"


def test_age_tracks_the_click():
    c = Clock()
    lr = ListenRequest(clock=c)
    lr.request()
    c.advance(3.0)
    assert lr.age() == 3.0


def test_clear_drops_a_pending_request():
    lr = ListenRequest(clock=Clock())
    lr.request()
    lr.clear()
    assert lr.consume() is None


def test_only_one_thread_wins_the_request():
    """The real topology: the API thread sets, a mic thread consumes. Whatever
    the interleaving, a single press must be delivered exactly once."""
    lr = ListenRequest()
    lr.request()
    wins = []
    barrier = threading.Barrier(8)

    def consumer():
        barrier.wait()
        if lr.consume() is not None:
            wins.append(1)

    threads = [threading.Thread(target=consumer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1, f"delivered {len(wins)} times, expected exactly 1"


def test_stress_set_and_consume_never_duplicates():
    """100 presses, consumers racing: never more consumes than presses."""
    lr = ListenRequest()
    consumed = []
    lock = threading.Lock()
    stop = threading.Event()

    def producer():
        for _ in range(100):
            lr.request()

    def consumer():
        while not stop.is_set():
            got = lr.consume()
            if got is not None:
                with lock:
                    consumed.append(got)

    cs = [threading.Thread(target=consumer) for _ in range(3)]
    for t in cs:
        t.start()
    p = threading.Thread(target=producer)
    p.start()
    p.join()
    stop.set()
    for t in cs:
        t.join()
    assert len(consumed) <= 100
    assert all(c == "hud" for c in consumed)


def test_wakeword_module_exposes_a_shared_request():
    """The API and the mic loops must touch the SAME object, and a click must
    never be able to hand out admin — the offline phrase is the guest one."""
    import importlib.util
    import pathlib
    import re

    src = pathlib.Path(__file__).with_name("wakeword.py").read_text(encoding="utf-8")
    assert "listen_request = ListenRequest()" in src, "no module-level flag"
    assert re.search(r'CLICK_WAKE_PHRASE\s*=\s*"wake up"', src), "click phrase changed"
    assert "admin override" not in src.split("CLICK_WAKE_PHRASE")[1][:200], \
        "a button press must never take the admin-override path"
    # Both loops must consume it, not just the passive one.
    assert src.count("listen_request.consume()") >= 3, \
        "expected the flag to be checked in both loops and the no-mic fallback"
    assert importlib.util.find_spec("modules.listen_request") is not None


if __name__ == "__main__":
    import sys
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
