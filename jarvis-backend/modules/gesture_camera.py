"""
gesture_camera.py — Phase G2 frame source (G1 carry-forwards, plan §4)
=======================================================================

Fixes the two G1 live-run findings:

1. **Stream lag** — a WiFi MJPEG stream (phone IP Webcam) buffers frames; a
   plain cap.read() loop falls behind and the cursor lags the hand. A reader
   thread keeps ONLY the newest frame; consumers always process live video.
   Lower-res request: for IP Webcam "/video" URLs a resolution query is
   appended (e.g. "?640x480") unless the URL already has one.

2. **Camera error messages** — distinguishes "no device present" (index fails
   to open), "device busy/failing" (opens but never delivers a frame) and
   "stream unreachable" (URL), including the mobile-data-IP trap hit during
   G1 setup: IP Webcam must show its Wi-Fi IP (same subnet as this PC), not
   the 192.0.0.x mobile-data address.

Used by gesture_spike.py now; gesture_daemon.py adopts it in G3.
"""

from __future__ import annotations

import threading
import time

import cv2


class CameraError(RuntimeError):
    """kind: "absent" | "busy" | "stream" | "dead"."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def decorate_url(url: str, res: str | None = "640x480") -> str:
    """Append a resolution query to an IP Webcam /video URL (lag mitigation)."""
    if res and "/video" in url and "?" not in url:
        return f"{url}?{res}"
    return url


def _open_source(source, width: int, height: int, url_res: str | None):
    if isinstance(source, int):
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise CameraError(
                "absent",
                f"no camera at index {source} — no device present.\n"
                "  - is a webcam plugged in? try another index (0, 1, …)\n"
                "  - or use a phone: IP Webcam app -> "
                "http://<phone-wifi-ip>:8080/video",
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        for _ in range(10):  # device present but held by another app?
            ok, _frame = cap.read()
            if ok:
                return cap
            time.sleep(0.1)
        cap.release()
        raise CameraError(
            "busy",
            f"camera index {source} exists but delivers no frames — "
            "device busy.\n"
            "  - another app (ambient_vision? Zoom? browser tab) is likely "
            "holding it — close it and retry",
        )
    url = decorate_url(str(source), url_res)
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        cap.release()
        raise CameraError(
            "stream",
            f"stream unreachable: {url}\n"
            "  - is the IP Webcam app running (Start server)?\n"
            "  - phone and PC must be on the SAME Wi-Fi: use the phone's "
            "Wi-Fi IP (192.168.x.x here), NOT the mobile-data 192.0.0.x one\n"
            "  - test the URL in a browser on this PC first",
        )
    return cap


class FrameSource:
    """Threaded latest-frame camera reader — consumers never see backlog."""

    def __init__(self, source, width: int = 640, height: int = 480,
                 url_res: str | None = "640x480"):
        self.source = source
        self._cap = _open_source(source, width, height, url_res)
        self._lock = threading.Condition()
        self._frame = None
        self._seq = 0
        self._dead: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="gesture-frame-source")
        self._thread.start()

    def _loop(self) -> None:
        fails = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok:
                fails += 1
                if fails > 30:
                    with self._lock:
                        self._dead = "camera stream died (30 consecutive read failures)"
                        self._lock.notify_all()
                    return
                time.sleep(0.05)
                continue
            fails = 0
            with self._lock:
                self._frame = frame
                self._seq += 1
                self._lock.notify_all()

    def read_new(self, last_seq: int = 0, timeout: float = 1.0):
        """Block until a frame newer than last_seq arrives; returns (seq, frame).

        Raises CameraError("dead") if the reader thread gave up. Only the
        newest frame is kept, so a slow consumer skips stale frames instead
        of drifting behind the stream (the G1 lag fix).
        """
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._seq <= last_seq and self._dead is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._lock.wait(remaining):
                    break
            if self._dead is not None:
                raise CameraError("dead", self._dead)
            if self._seq <= last_seq:
                return last_seq, None  # timeout — caller decides
            return self._seq, self._frame

    def release(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self._cap.release()
