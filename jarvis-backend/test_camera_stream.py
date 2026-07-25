"""Harness for modules/camera_stream.py — the HUD's read-only view of the shared
camera. Fake frames, fake clock, fake encoder: no cv2, no camera, no HTTP."""

from modules import camera_stream as cs


# ---- access control ---------------------------------------------------- #
# A live webcam feed of the owner's desk is a different class of payload from
# the rest of the (unauthenticated, local) API, so loopback-only is a hard gate.

def test_only_loopback_clients_allowed():
    for host in ("127.0.0.1", "127.1.2.3", "localhost", "::1", "[::1]",
                 "::ffff:127.0.0.1", "LOCALHOST"):
        assert cs.is_local_client(host) is True, host
    for host in ("192.168.0.42", "10.0.0.5", "example.com", "", None,
                 "127.evil.com", "1270.0.0.1"):
        assert cs.is_local_client(host) is False, host


def test_stream_kill_switch():
    assert cs.stream_enabled({}) is True                            # default on
    assert cs.stream_enabled({"JARVIS_CAMERA_STREAM": "1"}) is True
    assert cs.stream_enabled({"JARVIS_CAMERA_STREAM": "0"}) is False


def test_stream_info_advertises_the_local_path_not_the_camera():
    """What /api/vision/state may tell the browser about the feed.

    The old contract handed over the phone's raw MJPEG URL, so the HUD panel
    became a second consumer on a stream that serves one. The replacement must
    never contain a camera address — only a flag and a local path.
    """
    info = cs.stream_info(True, {})
    assert info == {"stream_available": True, "stream_path": "/api/camera/stream"}
    assert info["stream_path"].startswith("/"), "must be a local path, not a URL"


def test_stream_info_unavailable_when_nobody_publishes():
    assert cs.stream_info(False, {})["stream_available"] is False


def test_stream_info_respects_the_kill_switch():
    # Endpoint off => don't advertise it; the panel would only get a 404.
    off = {"JARVIS_CAMERA_STREAM": "0"}
    assert cs.stream_info(True, off)["stream_available"] is False
    assert cs.stream_info(False, off)["stream_available"] is False


def test_clamp_fps_never_spins_the_loop():
    assert cs.clamp_fps(None) == cs.DEFAULT_FPS
    assert cs.clamp_fps("abc") == cs.DEFAULT_FPS
    assert cs.clamp_fps(0) == cs.DEFAULT_FPS
    assert cs.clamp_fps(-5) == cs.DEFAULT_FPS
    assert cs.clamp_fps(float("nan")) == cs.DEFAULT_FPS
    assert cs.clamp_fps(999) == cs.MAX_FPS
    assert cs.clamp_fps(8) == 8.0


def test_part_frames_a_jpeg():
    chunk = cs.part(b"\xff\xd8body")
    assert chunk.startswith(f"--{cs.BOUNDARY}\r\n".encode())
    assert b"Content-Type: image/jpeg" in chunk
    assert b"Content-Length: 6" in chunk
    assert chunk.endswith(b"\xff\xd8body\r\n")


# ---- the generator ------------------------------------------------------ #

class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def _reader(frames):
    """frame_bus-shaped read(after_seq): hands out each frame once, then None."""
    state = {"i": 0}

    def read(after_seq):
        if state["i"] >= len(frames):
            return None
        f = frames[state["i"]]
        state["i"] += 1
        return (f, state["i"])          # seq is monotonic, like the bus

    return read


def test_stream_yields_one_part_per_frame():
    clk = Clock()
    out = list(cs.mjpeg_stream(_reader(["a", "b", "c"]),
                              lambda f: f.encode(), fps=10,
                              sleep=clk.sleep, now=clk.now))
    assert len(out) == 3
    assert out[0].endswith(b"a\r\n") and out[2].endswith(b"c\r\n")


def test_stream_ends_when_publisher_goes_quiet():
    """A dead camera must collapse the stream so the overlay can fall back —
    not hang forever on a frozen last frame."""
    clk = Clock()
    gen = cs.mjpeg_stream(_reader(["a"]), lambda f: f.encode(), fps=10,
                          sleep=clk.sleep, now=clk.now)
    assert len(list(gen)) == 1
    assert clk.t >= 2.0          # it waited out the idle grace before giving up


def test_stream_respects_max_seconds():
    """A forgotten <img> tag must not hold a reader open indefinitely."""
    clk = Clock()
    endless = lambda after_seq: ("f", after_seq + 1)      # noqa: E731
    # fps=4 so the fake clock steps in exact binary fractions (0.25) — a 10fps
    # 0.1 step accumulates float drift and yields an off-by-one 11th frame.
    out = list(cs.mjpeg_stream(endless, lambda f: b"j", fps=4,
                               max_seconds=1.0, sleep=clk.sleep, now=clk.now))
    assert len(out) == 4         # 4fps for 1s
    assert clk.t >= 1.0


def test_stream_stops_when_client_disconnects():
    clk = Clock()
    calls = {"n": 0}

    def connected():
        calls["n"] += 1
        return calls["n"] <= 3

    endless = lambda after_seq: ("f", after_seq + 1)      # noqa: E731
    out = list(cs.mjpeg_stream(endless, lambda f: b"j", fps=10,
                               sleep=clk.sleep, now=clk.now,
                               is_connected=connected))
    assert len(out) == 3         # 4th check is the one that ends it


def test_stream_encodes_each_frame_once():
    """`read(after_seq)` is the bus contract: a slow publisher must not make us
    re-JPEG the same image."""
    clk = Clock()
    seen = []

    def read(after_seq):
        assert after_seq in (0, 1), after_seq   # we always pass the last seq back
        if after_seq >= 1:
            return None
        return ("only", 1)

    list(cs.mjpeg_stream(read, lambda f: seen.append(f) or b"j", fps=10,
                         sleep=clk.sleep, now=clk.now))
    assert seen == ["only"]


def test_unencodable_frame_is_skipped_not_fatal():
    clk = Clock()
    out = list(cs.mjpeg_stream(_reader(["a", "b"]),
                               lambda f: None if f == "a" else b"jpg", fps=10,
                               sleep=clk.sleep, now=clk.now))
    assert len(out) == 1


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
