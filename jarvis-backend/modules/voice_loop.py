"""
voice_loop.py — who owns the one microphone (review finding R5)
===============================================================

There is exactly one microphone on this desk, so there is exactly one
wake-word loop. F-11 (2026-08-08) established that: the loop lives inside the
`/ws` handler, so reloading the HUD used to start a SECOND one — two threads in
`wait_for_wake_word`, every `[VAD]`/`[STT]` line twice, one spoken "wake up"
booting the system twice.

The fix for F-11 was a single-owner token: first connection wins, later ones
stay connected but view-only, ownership released in the handler's `finally`.
**That release could not run.** Review finding R5, 2026-08-16:

  * The owner spends nearly all its idle life blocked inside
    `asyncio.to_thread(wait_for_wake_word)`.
  * Starlette only moves `client_state` to DISCONNECTED inside `receive()`
    (see `starlette/websockets.py`) — and that handler calls `receive()`
    nowhere while it is listening. So the socket dying is *unobservable*: the
    handler stays parked in the mic thread, the `finally` never runs, and the
    token is held by a connection that no longer exists.
  * The new connection made exactly ONE claim attempt, lost, and parked in a
    view-only loop with no re-claim path — after telling the owner
    `SYSTEM OFFLINE // STANDBY FOR VOICE INPUT`.

**So: reload the HUD while JARVIS is idle and the microphone is dead, while the
HUD says it is listening.** Every wake word after that is heard by nobody.

Three things close it, and all three are needed:

1. **The disconnect has to be observable.** `main.websocket_endpoint` now runs
   one `receive()` watcher task per connection for the whole of its life. That
   is what flips `client_state`, and the watcher releases ownership the moment
   its client goes — so a reload frees the token in milliseconds rather than
   never.
2. **A claim may EVICT a dead owner** (below). Belt and braces for the case the
   watcher itself dies, and the literal fix R5 asks for.
3. **The losing connection re-attempts**, and the mic thread stands down.
   `wait_for_wake_word` takes a `should_abort` predicate and checks it once per
   listen window, so the outgoing owner lets go of the microphone device within
   ~5s; the incoming one waits on `mic_session` for exactly that before opening
   its own. Handing over the TOKEN without handing over the DEVICE would
   recreate F-11 with extra steps.

A live owner is never evicted. Two HUDs open at once still means one mic and one
listener, which is the F-11 contract unchanged.

Nothing here imports Starlette: liveness is read off whatever `client_state` /
`application_state` the object happens to carry, so the harness can drive the
real code with a stub socket.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

#: `starlette.websockets.WebSocketState.CONNECTED`. Compared by value rather
#: than imported so this module stays dependency-free and testable.
_CONNECTED = 1

#: How long the incoming owner waits for the outgoing one to let go of the
#: microphone device. `wait_for_wake_word`'s listen window is 5s, so a healthy
#: stand-down lands well inside this.
MIC_HANDOVER_TIMEOUT_S = 8.0

#: How often a view-only connection re-attempts the claim. Short enough that a
#: reload feels instant, long enough that a second idle HUD costs nothing.
CLAIM_RETRY_S = 2.0


def socket_is_live(sock: Any) -> bool:
    """False once either half of the socket has been observed as gone.

    Both states matter and for different reasons: `client_state` flips when a
    `receive()` sees `websocket.disconnect` (this is what the watcher task is
    for), and `application_state` flips when a `send()` hits an `OSError` —
    starlette re-raises that as `WebSocketDisconnect(1006)`. An object carrying
    neither attribute is treated as live, which is what a test stub wants.
    """
    for attr in ("client_state", "application_state"):
        state = getattr(sock, attr, None)
        if state is None:
            continue
        if getattr(state, "value", state) != _CONNECTED:
            return False
    return True


class VoiceLoopOwnership:
    """The single-owner token, plus the microphone hand-over interlock."""

    def __init__(self, is_live: Optional[Callable[[Any], bool]] = None) -> None:
        self._lock = threading.RLock()
        self._owner: Any = None
        self._is_live = is_live or socket_is_live
        # Set == no thread is inside the blocking mic call.
        self._mic_free = threading.Event()
        self._mic_free.set()
        self.evictions = 0

    # ── the token ────────────────────────────────────────────────────────────

    def claim(self, sock: Any) -> bool:
        """True if `sock` now owns the wake-word loop.

        Idempotent for the current owner. A LIVE owner is never displaced — a
        second HUD is a viewer, exactly as F-11 requires. A DEAD one is evicted,
        because its handler is parked in the mic thread and its `finally` is
        never going to run.
        """
        with self._lock:
            owner = self._owner
            if owner is None or owner is sock:
                self._owner = sock
                return True
            if not self._is_live(owner):
                self.evictions += 1
                self._owner = sock
                return True
            return False

    def release(self, sock: Any) -> None:
        """Give the token up, but only if this connection is what holds it.

        Called from two places now — the handler's `finally` and the watcher
        task — so it must stay safe to call twice, and safe to call from a
        connection that never owned anything.
        """
        with self._lock:
            if self._owner is sock:
                self._owner = None

    def owns(self, sock: Any) -> bool:
        """Read by the mic thread once per listen window. Cheap on purpose."""
        with self._lock:
            return self._owner is sock

    @property
    def owner(self) -> Any:
        with self._lock:
            return self._owner

    # ── the device ───────────────────────────────────────────────────────────

    def mic_session(self) -> "_MicSession":
        """Context manager held for the duration of a blocking mic call."""
        return _MicSession(self)

    def wait_for_mic_release(self, timeout: float = MIC_HANDOVER_TIMEOUT_S) -> bool:
        """Block until no thread is inside the mic call. False on timeout.

        A timeout is not fatal — a HUD that never listens is worse than a rare
        overlap — but the caller is expected to say so out loud.
        """
        return self._mic_free.wait(timeout)

    def mic_is_free(self) -> bool:
        return self._mic_free.is_set()

    # ── tests ────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        with self._lock:
            self._owner = None
            self.evictions = 0
        self._mic_free.set()


class _MicSession:
    def __init__(self, owner: VoiceLoopOwnership) -> None:
        self._o = owner

    def __enter__(self) -> "_MicSession":
        self._o._mic_free.clear()
        return self

    def __exit__(self, *exc) -> bool:
        self._o._mic_free.set()
        return False


#: The process-wide token. One microphone, one of these.
ownership = VoiceLoopOwnership()
