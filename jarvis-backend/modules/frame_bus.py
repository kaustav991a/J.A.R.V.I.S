"""
frame_bus.py — one camera owner, many readers.

Three subsystems wanted the same phone camera and each opened its OWN capture:
gesture_daemon (continuous ~30fps), vision.scan_for_faces (10s face-auth scan)
and ambient_vision (a fresh VideoCapture every 6s). An IP Webcam MJPEG endpoint
does not survive that. Measured live on 2026-07-25: starting a face scan while
the daemon was streaming killed the daemon outright —

    [GESTURE] session fault: camera stream died (30 consecutive read failures)

followed by a 30s blind retry. Since face-auth runs at every wake, gesture
control was guaranteed to drop exactly when the user arrived.

So: whoever owns the camera PUBLISHES each frame here, and everyone else READS.
No second capture is opened, no contention exists. Readers get `None` when the
bus is cold or stale, which is the signal to fall back to opening their own
capture (daemon off, JARVIS_GESTURE=0, camera still starting).

Deliberately dependency-free — threading and time only. ambient_vision imports
this at module scope and must stay loadable without cv2/TensorFlow/YOLO so
brain and background_monitor can import it, and the test harness needs it
without a camera.

Frames are stored by REFERENCE (cv2's read() allocates a fresh array per call,
so there is no buffer to tear) and copied on READ, so the cost lands only when
somebody actually consumes a frame rather than 30 times a second.
"""

import threading
import time

# A frame older than this is treated as no frame at all. The daemon publishes at
# 2–30fps depending on its state tier, so 1.5s tolerates the slowest tier (~2fps
# when locked) plus a stall, while still refusing to hand a face scan a picture
# of an empty chair from a minute ago.
DEFAULT_MAX_AGE_S = 1.5

_lock = threading.Lock()
_frame = None
_seq = 0                 # monotonic; lets a reader wait for a genuinely NEW frame
_stamp = 0.0
_source: str | None = None


def publish(frame, source: str | None = None, now: float | None = None) -> int:
    """Called by the camera owner for every frame it reads. Returns the new seq."""
    global _frame, _seq, _stamp, _source
    if frame is None:
        return _seq
    with _lock:
        _frame = frame
        _seq += 1
        _stamp = time.monotonic() if now is None else now
        if source is not None:
            _source = source
        return _seq


def latest(max_age_s: float = DEFAULT_MAX_AGE_S, after_seq: int | None = None,
           now: float | None = None):
    """Newest frame as ``(frame_copy, seq)``, or None.

    None means "nothing usable": never published, older than `max_age_s`, or —
    when `after_seq` is given — nothing newer than the caller already saw. That
    last case is what lets a scan loop consume each frame once instead of
    re-running recognition on one stale image.
    """
    t = time.monotonic() if now is None else now
    with _lock:
        if _frame is None or _seq == 0:
            return None
        if max_age_s is not None and (t - _stamp) > max_age_s:
            return None
        if after_seq is not None and _seq <= after_seq:
            return None
        frame, seq = _frame, _seq
    # copy OUTSIDE the lock: consumers must not be able to mutate the published
    # frame (or each other's), but the owner must never block on a memcpy.
    return (frame.copy() if hasattr(frame, "copy") else frame), seq


def active(max_age_s: float = DEFAULT_MAX_AGE_S, now: float | None = None) -> bool:
    """True when a camera owner is currently feeding the bus."""
    t = time.monotonic() if now is None else now
    with _lock:
        return _frame is not None and _seq > 0 and (t - _stamp) <= max_age_s


def source() -> str | None:
    """Which camera the owner selected, for logging."""
    with _lock:
        return _source


def age(now: float | None = None) -> float | None:
    """Seconds since the last publish, or None if nothing was ever published."""
    t = time.monotonic() if now is None else now
    with _lock:
        return None if _seq == 0 else t - _stamp


def clear() -> None:
    """Owner released the camera — drop the frame so no reader mistakes it for live.

    Staleness alone would cover this, but a session that ends cleanly should not
    leave a 1.5s window where readers still believe a camera is attached.
    """
    global _frame, _seq, _stamp, _source
    with _lock:
        _frame = None
        _seq = 0
        _stamp = 0.0
        _source = None
