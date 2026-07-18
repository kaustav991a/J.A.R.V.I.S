r"""
gesture_arbiter.py — G4 mutual exclusion: hand gestures vs JARVIS GUI automation
================================================================================

Hand-gesture cursor control (gesture_daemon -> SendInput) and JARVIS's own GUI
automation (human_gui_agent -> pyautogui / pywinauto) both drive the SINGLE OS
cursor. Run them at once and they fight: the hand jerks the pointer while JARVIS
is mid-click, or a gesture click lands in the window JARVIS just re-focused.

Before G4 the workaround was manual ("say 'hand control off' first"). This module
is the shared referee that makes the hand-off automatic AND reversible, without
touching the user's own on/off switch (gestures_enabled). Two mechanisms:

    hold(reason)   Hard suspend for the duration of a with-block / decorated call.
                   Reference-counted, so nested GUI ops compose (an autonomous
                   task that calls ghost_type both hold; gestures resume only
                   when the OUTERMOST exits). Use around a whole GUI operation
                   INCLUDING its internal waits (vision-LLM calls, 2 s render
                   sleeps) so a long pause can't let the hand leak back in.

    mark(reason)   Activity pulse: suspend for the next WINDOW_S seconds,
                   self-extending on each call. The cursor atoms (move/click/
                   type/press/scroll) call this, so ANY stray cursor motion
                   outside a hold() still suspends gestures, and control
                   auto-resumes a beat after automation stops. Self-healing:
                   a GUI op that crashes mid-way cannot wedge gestures off
                   forever — the window simply expires.

    is_suspended() The gate the gesture daemon polls every loop.
                   True while (hold count > 0) OR (now < activity window).

No dependency on the daemon or the GUI agent — both sides import only this, so
there is no import cycle and the referee is unit-testable with no hardware
(the clock is injectable via set_clock()).
"""
from __future__ import annotations

import functools
import threading
import time

# Activity-pulse tail. Also bridges sub-WINDOW_S gaps between consecutive atoms
# (e.g. move -> 0.3 s human pause -> click) so the suspend doesn't flicker off
# between two steps of the same operation.
WINDOW_S = 1.5

_clock = time.monotonic          # overridable for tests via set_clock()
_lock = threading.RLock()
_hold_count = 0
_window_until = 0.0
_reason: str | None = None


def set_clock(fn) -> None:
    """Test hook: replace the monotonic clock (e.g. a controllable fake)."""
    global _clock
    _clock = fn


def _reset() -> None:
    """Test hook: clear all state so cases don't bleed into each other."""
    global _hold_count, _window_until, _reason, _clock
    with _lock:
        _hold_count = 0
        _window_until = 0.0
        _reason = None
        _clock = time.monotonic


def _suspended_locked() -> bool:
    return _hold_count > 0 or _clock() < _window_until


def is_suspended() -> bool:
    """True while JARVIS holds the cursor (hard hold) or acted recently (window)."""
    with _lock:
        return _suspended_locked()


def active_reason() -> str | None:
    """The most recent suspend reason while suspended, else None (for the HUD)."""
    with _lock:
        return _reason if _suspended_locked() else None


def mark(reason: str = "gui") -> None:
    """Pulse the activity window WINDOW_S seconds into the future (self-extends)."""
    global _window_until, _reason
    with _lock:
        _window_until = _clock() + WINDOW_S
        _reason = reason


def acquire(reason: str = "gui") -> None:
    """Increment the hard-suspend refcount. Pair with exactly one release()."""
    global _hold_count, _reason
    with _lock:
        _hold_count += 1
        _reason = reason


def release() -> None:
    """Decrement the hard-suspend refcount (clamped at 0 — underflow is a no-op)."""
    global _hold_count
    with _lock:
        if _hold_count > 0:
            _hold_count -= 1


class hold:
    """Context manager: hard-suspend gestures for the duration of the block.

    Reentrant/nestable via the refcount. Releases on the way out even if the
    block raised, so a GUI op that errors mid-way still hands control back.
    """

    def __init__(self, reason: str = "gui"):
        self.reason = reason

    def __enter__(self) -> "hold":
        acquire(self.reason)
        return self

    def __exit__(self, *exc) -> bool:
        release()
        return False  # never swallow the exception


def suspends(reason: str = "gui"):
    """Decorator form of hold() — wrap a coarse GUI entry point in a hard suspend.

    Use on operations whose internal waits (vision-LLM calls, render sleeps,
    focus-recovery pauses) can exceed the mark() window, so gestures stay
    suspended for the whole call rather than flickering back mid-op.
    """

    def deco(fn):
        @functools.wraps(fn)
        def wrap(*args, **kwargs):
            with hold(reason):
                return fn(*args, **kwargs)
        return wrap

    return deco
