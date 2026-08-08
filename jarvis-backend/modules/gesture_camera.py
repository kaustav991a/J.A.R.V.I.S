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
   to open), "device busy/failing" (opens but never delivers a frame),
   "stream unreachable" and "stream connected but stalled" (URL), including the
   mobile-data-IP trap hit during G1 setup: IP Webcam must show its Wi-Fi IP
   (same subnet as this PC), not the 192.0.0.x mobile-data address.

   Every source — index OR URL — must hand over a real frame before it is
   accepted, so a stream that connects and then stalls (app backgrounded,
   camera held by another app on the phone) is rejected instead of being
   auto-selected over a later working source.

Used by gesture_spike.py now; gesture_daemon.py adopts it in G3.
"""

from __future__ import annotations

import socket
import threading
import time
from urllib.parse import urlparse

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


def parse_sources(sources_env: str | None, legacy_cam: str | None = None) -> list:
    """Priority-ordered candidate list for camera auto-select (G6.3).

    `sources_env` (JARVIS_CAM_SOURCES) is a comma list of device indices and/or
    stream URLs, tried in order. Falls back to the single legacy `legacy_cam`
    (JARVIS_CAM) when the list is empty/unset. Blanks and duplicates are dropped,
    original order preserved. Digit entries become ints (device indices), the
    rest stay URL strings.
    """
    raw = sources_env if sources_env and sources_env.strip() else legacy_cam
    if not raw or not raw.strip():
        return [0]
    out: list = []
    seen: set[str] = set()
    for part in raw.split(","):
        s = part.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(int(s) if s.isdigit() else s)
    return out or [0]


def url_reachable(url: str, timeout: float = 1.5, connect=None) -> bool:
    """Fast TCP reachability probe for a stream URL.

    cv2.VideoCapture blocks for a long time on an unreachable host, which would
    stall camera auto-select on every dead source. A cheap TCP connect to the
    URL's host:port fails in ~`timeout` instead, so a dead source is skipped
    quickly. `connect` is injectable for tests (defaults to socket).
    """
    parsed = urlparse(url if "://" in url else "http://" + url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connect = connect or socket.create_connection
    try:
        conn = connect((host, port), timeout)
    except OSError:
        return False
    try:
        conn.close()
    except Exception:  # noqa: BLE001 — best-effort close of a throwaway probe
        pass
    return True


INDEX_FRAME_ATTEMPTS = 10
STREAM_FRAME_ATTEMPTS = 20  # a WiFi MJPEG stream needs longer to hand over frame 1
FRAME_WAIT_S = 0.1


def _default_capture(spec):
    if isinstance(spec, int):
        return cv2.VideoCapture(spec, cv2.CAP_DSHOW)
    return cv2.VideoCapture(spec)


def _first_frame(cap, attempts: int, sleep) -> bool:
    """True once the capture actually hands over a frame.

    "isOpened()" only means the device/socket was acquired — a busy webcam or a
    stalled MJPEG stream opens and then delivers nothing. Auto-select would
    happily pick such a source over a later working one, so every source is
    frame-validated here before it is accepted.
    """
    for i in range(attempts):
        ok, _frame = cap.read()
        if ok:
            return True
        if i + 1 < attempts:
            sleep(FRAME_WAIT_S)
    return False


def _open_source(source, width: int, height: int, url_res: str | None,
                 *, capture=None, sleep=None):
    capture = capture or _default_capture
    sleep = sleep or time.sleep
    if isinstance(source, int):
        cap = capture(source)
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
        if _first_frame(cap, INDEX_FRAME_ATTEMPTS, sleep):  # held by another app?
            return cap
        cap.release()
        raise CameraError(
            "busy",
            f"camera index {source} exists but delivers no frames — "
            "device busy.\n"
            "  - another app (ambient_vision? Zoom? browser tab) is likely "
            "holding it — close it and retry",
        )
    url = decorate_url(str(source), url_res)
    cap = capture(url)
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
    if _first_frame(cap, STREAM_FRAME_ATTEMPTS, sleep):
        return cap
    cap.release()
    raise CameraError(
        "stream",
        f"stream connected but delivered no frames: {url}\n"
        "  - the app is running but not streaming — is it backgrounded, or is "
        "the phone camera held by another app (Camera/WhatsApp/Zoom)?\n"
        "  - re-open the app and press Start server, then retry\n"
        "  - if two camera apps are installed, only ONE can hold the camera",
    )


def open_first_available(sources, width: int = 640, height: int = 480,
                         url_res: str | None = "640x480",
                         probe_timeout: float = 1.5,
                         reachable=None, opener=None):
    """Camera auto-select (G6.3): open the FIRST source that works.

    Tries each entry of `sources` in order. URL sources get a fast TCP
    reachability probe first (`url_reachable`) so a dead host is skipped in
    ~`probe_timeout` instead of blocking cv2; device indices go straight to the
    opener. Returns ``(cap, chosen_source)`` for the first source that both
    opens and delivers a frame. Raises ``CameraError("absent", ...)`` with a
    per-source summary when none work. `reachable`/`opener` are injectable for
    tests.
    """
    reachable = reachable or (lambda u: url_reachable(u, probe_timeout))
    opener = opener or _open_source
    errors: list[str] = []
    for src in sources:
        if isinstance(src, str) and not reachable(src):
            errors.append(f"{src}: unreachable (no TCP connect in {probe_timeout}s)")
            continue
        try:
            cap = opener(src, width, height, url_res)
        except CameraError as e:
            first_line = str(e).splitlines()[0] if str(e) else e.kind
            errors.append(f"{src}: [{e.kind}] {first_line}")
            continue
        return cap, src
    detail = "\n  - ".join(errors) if errors else "(no sources configured)"
    raise CameraError(
        "absent",
        "no working camera among configured sources:\n  - " + detail,
    )


# How long the reader tolerates getting no frame before it treats the capture
# as stalled and tries to reopen it.
#
# This used to be a COUNT — 30 consecutive failed reads with a 50ms sleep, so
# 1.55 seconds — and hitting it killed the reader thread permanently. That is
# far too tight and far too terminal for an MJPEG stream. One corrupt JPEG
# segment (ffmpeg logs "overread N") makes cv2 return ok=False while its decoder
# resyncs to the next SOI marker, and on a 27fps phone stream that resync
# routinely takes longer than 1.55s. The result, measured on 2026-08-08: the
# gesture daemon tore down its whole session — capture released, auto-select
# re-run, MediaPipe re-initialised — five times in twenty-five minutes, on a
# stream that was independently verified healthy (548 frames in a 20s pull, no
# disconnect). A recoverable decoder desync was being escalated to a dead camera.
STALL_SECONDS = 5.0
# Consecutive REOPEN attempts that must fail before the camera is really dead.
# Reopening is the recovery a desync actually needs; dying is not.
MAX_REOPEN_ATTEMPTS = 3
# Pause between reopen attempts, so a genuinely absent camera does not spin.
REOPEN_BACKOFF_S = 0.5


class FrameSource:
    """Threaded latest-frame camera reader — consumers never see backlog.

    A stalled capture is REOPENED, not fatal. Only when reopening fails
    ``MAX_REOPEN_ATTEMPTS`` times in a row does the source report itself dead.
    """

    def __init__(self, source, width: int = 640, height: int = 480,
                 url_res: str | None = "640x480", *, cap=None, reopen=None,
                 stall_seconds: float = STALL_SECONDS,
                 max_reopen: int = MAX_REOPEN_ATTEMPTS):
        self.source = source
        self._cap = cap if cap is not None else _open_source(
            source, width, height, url_res)
        # How to get a fresh capture when this one stalls. Injectable so a
        # harness can drive the recovery path without a real camera; by default
        # it re-opens exactly the source this instance was built from.
        self._reopen = reopen or (
            lambda: _open_source(source, width, height, url_res))
        self._stall_seconds = stall_seconds
        self._max_reopen = max_reopen
        self.reopens = 0          # observable: how many times recovery ran
        self._lock = threading.Condition()
        self._frame = None
        self._seq = 0
        self._dead: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="gesture-frame-source")
        self._thread.start()

    def _reopen_once(self) -> bool:
        """Swap in a fresh capture. True if we got one."""
        try:
            fresh = self._reopen()
        except Exception:
            return False
        if fresh is None:
            return False
        old, self._cap = self._cap, fresh
        try:
            old.release()
        except Exception:
            pass          # the old handle is being discarded either way
        self.reopens += 1
        return True

    def _loop(self) -> None:
        last_ok = time.monotonic()
        reopen_failures = 0
        while not self._stop.is_set():
            try:
                ok, frame = self._cap.read()
            except Exception:
                ok, frame = False, None
            if ok:
                last_ok = time.monotonic()
                reopen_failures = 0
                with self._lock:
                    self._frame = frame
                    self._seq += 1
                    self._lock.notify_all()
                continue

            # No frame this tick. A short gap is a decoder hiccup, not a death.
            if time.monotonic() - last_ok < self._stall_seconds:
                time.sleep(0.05)
                continue

            # Stalled long enough to act on. Try recovery before giving up.
            if self._reopen_once():
                last_ok = time.monotonic()
                reopen_failures = 0
                continue

            reopen_failures += 1
            if reopen_failures >= self._max_reopen:
                with self._lock:
                    self._dead = (
                        f"camera stream died (no frame for "
                        f"{self._stall_seconds:.0f}s and {reopen_failures} "
                        f"reopen attempts failed)")
                    self._lock.notify_all()
                return
            time.sleep(REOPEN_BACKOFF_S)

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


def make_frame_source(sources, width: int = 640, height: int = 480,
                      url_res: str | None = "640x480",
                      probe_timeout: float = 1.5, reachable=None):
    """Auto-select a camera from `sources` and wrap it in a FrameSource (G6.3).

    Opens the first working source via ``open_first_available`` (so the reader
    thread never re-opens it) and records which one was chosen on
    ``FrameSource.source``. Raises ``CameraError`` if none work.
    """
    cap, chosen = open_first_available(
        sources, width, height, url_res, probe_timeout, reachable)
    return FrameSource(chosen, width, height, url_res, cap=cap)
