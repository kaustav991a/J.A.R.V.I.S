"""listen_request.py — "the user pressed the mic button", crossing threads.

JARVIS is voice-first with a SERVER-side microphone: the HUD never captures
audio, it only watches state. So the mic button could not work the obvious way.
The two listening loops in `wakeword.py` sit inside blocking
`recognizer.listen(...)` calls on a worker thread, and the FastAPI event loop
cannot reach into them — there is nothing to await, and no client WebSocket
message is read while a loop is blocked.

The one seam that exists is BETWEEN listen windows: each loop iterates every few
seconds. So a click sets a flag here, and the loops consume it on their next
pass. Latency is therefore up to one listen window (~3s passive, ~5s offline),
which is the honest cost of not restructuring the audio stack.

Two rules this holds:

1. **One-shot.** `consume()` clears, so one click = one turn. A click that
   arrives twice does not queue two listens.
2. **It expires.** A click during a 40s LLM turn (or while the mic thread is
   dead) must not pop the microphone open minutes later, long after the user
   gave up and typed instead. Past `ttl_s` the request is simply gone.

Dependency-free (threading + time) and clock-injectable, so the whole thing is
exercised in test_listen_request.py with no audio device.
"""

from __future__ import annotations

import threading
import time

DEFAULT_TTL_S = 15.0


class ListenRequest:
    """A single pending 'start listening' request, safe across threads.

    Set from the API thread, consumed from the microphone thread.
    """

    def __init__(self, ttl_s: float = DEFAULT_TTL_S, clock=None):
        self.ttl_s = float(ttl_s)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._source: str | None = None
        self._stamp: float = 0.0

    # -- producer side (API) ------------------------------------------- #

    def request(self, source: str = "hud") -> None:
        """Record a click. A second click just refreshes the timestamp."""
        with self._lock:
            self._source = source or "hud"
            self._stamp = self._clock()

    # -- consumer side (microphone loops) ------------------------------ #

    def consume(self) -> str | None:
        """Take the pending request, or None. Clears it either way.

        Returns the source string so the loop can log *why* it woke — a button
        press and a spoken wake word should not look identical in the log.
        """
        with self._lock:
            src = self._source
            if src is None:
                return None
            expired = (self._clock() - self._stamp) > self.ttl_s
            self._source = None
            self._stamp = 0.0
            return None if expired else src

    def pending(self) -> bool:
        """True if a click is waiting and still fresh (does NOT clear it)."""
        with self._lock:
            if self._source is None:
                return False
            return (self._clock() - self._stamp) <= self.ttl_s

    def age(self) -> float | None:
        """Seconds since the click, or None if nothing is recorded."""
        with self._lock:
            return None if self._source is None else self._clock() - self._stamp

    def clear(self) -> None:
        """Drop any pending request — e.g. the session is shutting down."""
        with self._lock:
            self._source = None
            self._stamp = 0.0
