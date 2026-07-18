r"""
test_gesture_arbiter.py — G4 cursor-arbiter harness (no hardware, no pytest)

Run: venv\Scripts\python.exe test_gesture_arbiter.py

Verifies the referee that keeps hand gestures and JARVIS's own GUI automation
off the single OS cursor at the same time:
  * hold() hard-suspends and is reference-counted (nests)
  * mark() opens a self-extending activity window that expires (self-healing)
  * is_suspended() = hold active OR inside the window
  * the suspends() decorator wraps a call, releasing even on exception
  * release() underflow is a no-op
  * concurrent acquire/release from many threads leave the count balanced
"""
import threading

from modules import gesture_arbiter as ga


# --- controllable clock ---------------------------------------------------- #
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def setup():
    ga._reset()
    clk = Clock()
    ga.set_clock(clk)
    return clk


# --- cases ----------------------------------------------------------------- #
def test_default_not_suspended():
    setup()
    check(not ga.is_suspended(), "fresh arbiter is not suspended")
    check(ga.active_reason() is None, "fresh reason is None")


def test_hold_suspends_and_releases():
    setup()
    check(not ga.is_suspended(), "not suspended before hold")
    with ga.hold("gui:test"):
        check(ga.is_suspended(), "suspended inside hold")
        check(ga.active_reason() == "gui:test", "reason set inside hold")
    check(not ga.is_suspended(), "resumed after hold exits")
    check(ga.active_reason() is None, "reason None after resume")


def test_nested_hold_refcount():
    setup()
    ga.acquire("outer")
    ga.acquire("inner")
    check(ga.is_suspended(), "suspended with 2 holds")
    ga.release()
    check(ga.is_suspended(), "still suspended after 1 of 2 releases")
    ga.release()
    check(not ga.is_suspended(), "resumed after both releases")


def test_mark_window_expires():
    clk = setup()
    ga.mark("gui:atom")
    check(ga.is_suspended(), "suspended right after mark")
    clk.advance(ga.WINDOW_S - 0.01)
    check(ga.is_suspended(), "still suspended just before window end")
    clk.advance(0.02)
    check(not ga.is_suspended(), "resumed after window elapses")


def test_mark_self_extends():
    clk = setup()
    ga.mark("gui:atom")
    clk.advance(ga.WINDOW_S - 0.1)
    ga.mark("gui:atom")            # re-pulse before expiry
    clk.advance(ga.WINDOW_S - 0.1)  # past the FIRST window, inside the second
    check(ga.is_suspended(), "re-marking extends the window")
    clk.advance(0.2)
    check(not ga.is_suspended(), "expires after the extended window")


def test_hold_outlasts_expired_window():
    clk = setup()
    with ga.hold("gui:long"):
        ga.mark("gui:atom")
        clk.advance(ga.WINDOW_S + 5.0)   # window long gone...
        check(ga.is_suspended(), "hold keeps it suspended past window expiry")
    check(not ga.is_suspended(), "resumes once the hold exits")


def test_window_outlasts_released_hold():
    clk = setup()
    ga.acquire("hold")
    ga.mark("gui:atom")
    ga.release()                          # hold gone, but a fresh mark stands
    check(ga.is_suspended(), "window keeps it suspended after hold released")
    clk.advance(ga.WINDOW_S + 0.01)
    check(not ga.is_suspended(), "resumes after the trailing window expires")


def test_suspends_decorator():
    setup()
    seen = {}

    @ga.suspends("gui:decorated")
    def do_work():
        seen["suspended"] = ga.is_suspended()
        seen["reason"] = ga.active_reason()
        return 42

    result = do_work()
    check(result == 42, "decorated fn returns its value")
    check(seen["suspended"] is True, "suspended inside decorated call")
    check(seen["reason"] == "gui:decorated", "reason set inside decorated call")
    check(not ga.is_suspended(), "resumed after decorated call")


def test_suspends_decorator_releases_on_exception():
    setup()

    @ga.suspends("gui:boom")
    def blow_up():
        raise RuntimeError("boom")

    try:
        blow_up()
    except RuntimeError:
        pass
    check(not ga.is_suspended(), "hold released even when the call raised")


def test_release_underflow_is_noop():
    setup()
    ga.release()                          # no matching acquire
    ga.release()
    check(not ga.is_suspended(), "underflow release does not go negative")
    ga.acquire("x")
    check(ga.is_suspended(), "acquire after underflow still suspends")
    ga.release()
    check(not ga.is_suspended(), "single release resumes (count clamped at 0)")


def test_concurrent_acquire_release_balanced():
    setup()
    ga.set_clock(__import__("time").monotonic)  # real clock; test the refcount only

    def worker():
        for _ in range(500):
            ga.acquire("t")
            ga.release()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(not ga.is_suspended(), "count balanced back to 0 under concurrency")


TESTS = [
    test_default_not_suspended,
    test_hold_suspends_and_releases,
    test_nested_hold_refcount,
    test_mark_window_expires,
    test_mark_self_extends,
    test_hold_outlasts_expired_window,
    test_window_outlasts_released_hold,
    test_suspends_decorator,
    test_suspends_decorator_releases_on_exception,
    test_release_underflow_is_noop,
    test_concurrent_acquire_release_balanced,
]


def main():
    print("=" * 60)
    print("gesture_arbiter harness")
    print("=" * 60)
    for t in TESTS:
        t()
    ga._reset()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
