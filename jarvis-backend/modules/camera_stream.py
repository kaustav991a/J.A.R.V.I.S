"""
camera_stream.py — G6.1 follow-up: serve the shared camera to the HUD
====================================================================

The FaceAuthOverlay was deliberately shipped without a live feed: the camera URL
was hardcoded deep in `vision.py` and nothing exposed frames to the frontend, so
the overlay could only draw an abstract scan animation while the real 10s scan
ran blind behind it. `modules/frame_bus.py` fixed the plumbing — whoever owns the
camera publishes — so the HUD can now watch the SAME frames the recogniser sees
without opening a second capture (which is exactly what used to kill the gesture
daemon's stream).

Two rules this module exists to enforce:

1. **It never opens a camera.** It only re-serves what an owner already
   published. No owner publishing ⇒ no stream (the overlay keeps its abstract
   animation). This is what keeps the "one owner, many readers" invariant from
   being quietly broken by a browser tab.
2. **Loopback only, and killable.** This endpoint hands out a live view of a
   webcam pointed at the owner's desk. The backend's other endpoints are
   unauthenticated because they carry no such payload; a camera feed is a
   different class of data, so it is refused for any non-loopback client and can
   be turned off outright with `JARVIS_CAMERA_STREAM=0`.

Everything here is pure (injectable read/encode/sleep/clock) so the whole
generator is exercised in test_camera_stream.py with fake frames — no cv2, no
camera, no HTTP server.
"""

from __future__ import annotations

import os

BOUNDARY = "jarvisframe"
CONTENT_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"

DEFAULT_FPS = 10.0
DEFAULT_MAX_S = 120.0      # a forgotten <img> must not hold a reader forever
MAX_FPS = 30.0

# Loopback hosts. The frontend is served from the same machine (Vite dev server
# or the packaged Electron shell), so this covers every legitimate viewer.
_LOCAL_HOSTS = {"localhost", "::1", "::ffff:127.0.0.1"}


def stream_enabled(env=None) -> bool:
    """`JARVIS_CAMERA_STREAM=0` disables the endpoint entirely."""
    env = os.environ if env is None else env
    return env.get("JARVIS_CAMERA_STREAM", "1") == "1"


def is_local_client(host: str | None) -> bool:
    """True only for loopback clients — see rule 2 in the module docstring."""
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):   # bracketed IPv6
        h = h[1:-1]
    if h in _LOCAL_HOSTS:
        return True
    # the whole 127/8 block, but as a literal IPv4 ONLY: a prefix test would let
    # the hostname "127.evil.com" through, and a Host header is attacker-supplied.
    parts = h.split(".")
    if len(parts) != 4 or parts[0] != "127":
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def clamp_fps(fps: float | None) -> float:
    """Keep a caller-supplied fps sane: a bogus value must not spin the loop."""
    try:
        v = float(DEFAULT_FPS if fps is None else fps)
    except (TypeError, ValueError):
        return DEFAULT_FPS
    if v != v or v <= 0:                         # NaN or non-positive
        return DEFAULT_FPS
    return min(v, MAX_FPS)


def part(jpeg: bytes) -> bytes:
    """One multipart chunk for a single JPEG."""
    return (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
            f"Content-Length: {len(jpeg)}\r\n\r\n").encode("ascii") + jpeg + b"\r\n"


def mjpeg_stream(read, encode, *, fps: float = DEFAULT_FPS,
                 max_seconds: float = DEFAULT_MAX_S,
                 sleep=None, now=None, is_connected=None):
    """Yield multipart JPEG chunks from an already-published frame source.

    `read(after_seq)` returns `(frame, seq)` or None (the frame_bus contract, so
    a frame is encoded at most ONCE — a slow publisher must not make us re-JPEG
    the same image). `encode(frame)` returns JPEG bytes or None. The generator
    ends on `max_seconds`, on `is_connected()` going False, or when the publisher
    goes away for longer than the idle grace — a dead camera should collapse the
    stream so the overlay can fall back, not hang on a frozen last frame.
    """
    import time as _time
    sleep = _time.sleep if sleep is None else sleep
    now = _time.monotonic if now is None else now

    interval = 1.0 / clamp_fps(fps)
    idle_grace = max(2.0, interval * 20)   # ~20 missed frames = publisher gone
    started = now()
    last_frame_t = started
    seq = 0

    while True:
        if is_connected is not None and not is_connected():
            return
        t = now()
        if (t - started) >= max_seconds:
            return
        got = read(seq)
        if got is None:
            if (t - last_frame_t) >= idle_grace:
                return                      # nobody is publishing any more
            sleep(interval)
            continue
        frame, seq = got
        last_frame_t = t
        jpeg = encode(frame)
        if jpeg:
            yield part(jpeg)
        sleep(interval)
