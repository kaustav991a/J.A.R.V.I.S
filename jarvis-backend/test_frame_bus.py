r"""
test_frame_bus.py — shared camera frame bus (no camera, no cv2, no threads needed)

Run: venv\Scripts\python.exe test_frame_bus.py

Three subsystems each opened their own capture on the same phone camera, and an
IP Webcam MJPEG endpoint does not survive that: starting a face scan while the
gesture daemon streamed killed the daemon with "camera stream died (30
consecutive read failures)" and a 30s blind retry. Since face-auth runs at every
wake, gesture control was guaranteed to drop when the owner walked up.

frame_bus makes one subsystem the owner and everyone else a reader. What has to
hold for that to be safe, and is pinned here:

  * a reader never sees a frame that isn't there, or one old enough to be a lie
    (an empty chair from a minute ago must not satisfy a face scan);
  * `after_seq` hands each frame out once, so a scan loop can't re-run
    recognition on one stale image and "match" from a single frame;
  * readers get COPIES — one consumer's in-place edit must not corrupt the
    owner's frame or another reader's;
  * `active()` is the fallback signal: when it is False the caller opens its own
    capture, so the daemon being off must never mean "no camera at all".

The clock is injected, so staleness is tested exactly rather than with sleeps.
Frames are stand-in objects with `.copy()`, which is all the bus requires — it
deliberately imports neither cv2 nor numpy.
"""

import threading

from modules import frame_bus

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


class Frame:
    """Stands in for a cv2 frame: identity we can assert on, plus .copy()."""

    def __init__(self, tag):
        self.tag = tag
        self.copies = 0

    def copy(self):
        c = Frame(self.tag)
        self.copies += 1
        return c

    def __repr__(self):
        return f"Frame({self.tag!r})"


def setup():
    frame_bus.clear()


def test_cold_bus_yields_nothing():
    setup()
    check(frame_bus.latest(now=100.0) is None, "no frame before anything is published")
    check(not frame_bus.active(now=100.0), "cold bus is not active")
    check(frame_bus.source() is None, "no source on a cold bus")
    check(frame_bus.age(now=100.0) is None, "no age on a cold bus")


def test_publish_then_read():
    setup()
    seq = frame_bus.publish(Frame("a"), source="http://cam/video", now=10.0)
    check(seq == 1, f"first publish is seq 1, got {seq}")
    got = frame_bus.latest(now=10.1)
    check(got is not None, "published frame is readable")
    frame, s = got
    check(frame.tag == "a" and s == 1, f"right frame and seq, got {frame!r} {s}")
    check(frame_bus.active(now=10.1), "bus is active right after a publish")
    check(frame_bus.source() == "http://cam/video", "source is reported for logging")


def test_stale_frame_is_not_offered():
    setup()
    frame_bus.publish(Frame("old"), now=10.0)
    check(frame_bus.latest(max_age_s=1.5, now=11.0) is not None,
          "1.0s old frame is still usable")
    check(frame_bus.latest(max_age_s=1.5, now=11.6) is None,
          "1.6s old frame is refused — a face scan must not see an empty chair")
    check(not frame_bus.active(max_age_s=1.5, now=11.6),
          "a stale bus reports inactive so the caller opens its own capture")
    check(abs(frame_bus.age(now=11.6) - 1.6) < 1e-9, "age reports the gap")


def test_stale_then_fresh_recovers():
    # the daemon dropping to ~2fps (locked tier) or stalling must not permanently
    # poison the bus
    setup()
    frame_bus.publish(Frame("old"), now=10.0)
    check(frame_bus.latest(now=20.0) is None, "gone stale")
    frame_bus.publish(Frame("new"), now=20.0)
    got = frame_bus.latest(now=20.1)
    check(got is not None and got[0].tag == "new", "a fresh publish revives the bus")


def test_after_seq_hands_each_frame_out_once():
    setup()
    frame_bus.publish(Frame("f1"), now=10.0)
    got = frame_bus.latest(after_seq=0, now=10.0)
    check(got is not None and got[1] == 1, "first frame is new to a seq-0 reader")
    check(frame_bus.latest(after_seq=1, now=10.0) is None,
          "same frame is NOT served twice — no re-running recognition on one image")
    frame_bus.publish(Frame("f2"), now=10.05)
    got2 = frame_bus.latest(after_seq=1, now=10.05)
    check(got2 is not None and got2[0].tag == "f2" and got2[1] == 2,
          f"the next frame is served, got {got2}")


def test_seq_is_monotonic_across_publishes():
    setup()
    seqs = [frame_bus.publish(Frame(f"f{i}"), now=10.0 + i * 0.03) for i in range(5)]
    check(seqs == [1, 2, 3, 4, 5], f"seq increments per publish, got {seqs}")


def test_readers_get_copies():
    setup()
    original = Frame("shared")
    frame_bus.publish(original, now=10.0)
    a, _ = frame_bus.latest(now=10.0)
    b, _ = frame_bus.latest(now=10.0)
    check(a is not original, "reader does not get the owner's frame object")
    check(a is not b, "two readers do not share one object")
    a.tag = "mutated by reader"
    check(original.tag == "shared", "a reader mutating its copy can't corrupt the owner")
    check(frame_bus.latest(now=10.0)[0].tag == "shared",
          "and can't corrupt what the next reader sees")


def test_publish_ignores_none():
    setup()
    frame_bus.publish(Frame("real"), now=10.0)
    seq = frame_bus.publish(None, now=10.1)
    check(seq == 1, f"publishing None doesn't bump seq, got {seq}")
    check(frame_bus.latest(now=10.1)[0].tag == "real", "and doesn't clobber the frame")


def test_clear_drops_the_camera():
    setup()
    frame_bus.publish(Frame("a"), source="cam", now=10.0)
    frame_bus.clear()
    check(frame_bus.latest(now=10.0) is None, "cleared bus offers nothing")
    check(not frame_bus.active(now=10.0),
          "cleared bus is inactive immediately — no 1.5s window where readers "
          "still think a camera is attached")
    check(frame_bus.source() is None, "source cleared too")
    # a reader that had consumed up to seq 5 must not be starved after a restart
    frame_bus.publish(Frame("after restart"), now=10.1)
    got = frame_bus.latest(after_seq=0, now=10.1)
    check(got is not None and got[1] == 1, "seq restarts at 1 after clear")


def test_concurrent_publish_and_read_is_race_free():
    setup()
    errors = []
    stop = threading.Event()

    def owner():
        try:
            i = 0
            while not stop.is_set():
                i += 1
                frame_bus.publish(Frame(f"f{i}"))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            seen = 0
            for _ in range(3000):
                got = frame_bus.latest(after_seq=seen)
                if got is not None:
                    frame, seen = got
                    _ = frame.tag          # touch it, as a real consumer would
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=owner) for _ in range(2)] + \
              [threading.Thread(target=reader) for _ in range(3)]
    for t in threads[2:]:
        t.start()
    for t in threads[:2]:
        t.start()
    for t in threads[2:]:
        t.join(timeout=30)
    stop.set()
    for t in threads[:2]:
        t.join(timeout=30)
    check(not errors, f"no thread raised under concurrent publish/read (got {errors[:2]})")
    setup()


TESTS = [
    test_cold_bus_yields_nothing,
    test_publish_then_read,
    test_stale_frame_is_not_offered,
    test_stale_then_fresh_recovers,
    test_after_seq_hands_each_frame_out_once,
    test_seq_is_monotonic_across_publishes,
    test_readers_get_copies,
    test_publish_ignores_none,
    test_clear_drops_the_camera,
    test_concurrent_publish_and_read_is_race_free,
]


def main():
    print("=" * 60)
    print("frame_bus shared-camera harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
