"""
context_state.py — Presence & Context-State Machine (Roadmap §2.3)
==================================================================

Gives J.A.R.V.I.S. a lightweight sense of *what state you're in over time* —
not just *who* you are (biometrics) — so he can act APPROPRIATELY: quiet when
you're focused, silent when you're away or asleep, briefing-ready when you return.

States:
    WORKING   — focused/engaged (focus mode, or recent code/terminal activity)
    RELAXING  — present, casual, evening, media-ish
    AWAY      — not detected in frame for a while
    ASLEEP    — night hours + away/idle
    IDLE      — present but quiet (the neutral default)

It derives state from signals it can already see — ambient-vision presence, the
clock, focus mode, and recency of the last command — with NO new hardware. The
classifier is a pure function (`classify`) so it's fully unit-testable; `current()`
gathers the live signals and classifies them.

Proactivity consults `should_offer_proactive(priority)` so non-critical chatter is
suppressed when you're WORKING/AWAY/ASLEEP, while high-priority alerts (security,
critical health) always get through.
"""

from __future__ import annotations

import time

WORKING = "WORKING"
RELAXING = "RELAXING"
AWAY = "AWAY"
ASLEEP = "ASLEEP"
IDLE = "IDLE"

# Priority levels a proactive event can declare.
_PRIORITY = {"low": 0, "normal": 1, "high": 2}

# Per-state minimum priority allowed to interrupt.
#   low(0)  = ambient chatter, "you've been at it 2h"
#   normal(1) = calendar reminders, weather deltas, health nudges
#   high(2) = intruder/security, critical battery/thermal
_STATE_MIN_PRIORITY = {
    WORKING:  _PRIORITY["normal"],   # don't break focus with idle chatter
    RELAXING: _PRIORITY["low"],      # anything goes
    IDLE:     _PRIORITY["low"],
    AWAY:     _PRIORITY["high"],     # only urgent things while you're gone
    ASLEEP:   _PRIORITY["high"],     # let the man sleep
}

# Tunables.
AWAY_AFTER_S = 90        # absent this long (vision) → AWAY
ASLEEP_IDLE_S = 1800     # 30 min idle during night hours contributes to ASLEEP
WORKING_RECENT_CMD_S = 180  # a command in the last 3 min → still engaged


def classify(
    *,
    hour: int,
    user_absent: bool,
    seconds_since_command: float | None,
    focus_mode: bool = False,
    recent_intent: str | None = None,
    camera_active: bool = True,
) -> str:
    """Pure classifier — given signals, return the context state.

    `recent_intent` is the last command's module/intent (e.g. "CODER") when known.
    """
    night = (hour >= 23 or hour < 6)
    idle = seconds_since_command is None or seconds_since_command > ASLEEP_IDLE_S

    # AWAY/ASLEEP only trust vision when the camera is actually active.
    if camera_active and user_absent:
        if night and idle:
            return ASLEEP
        return AWAY
    # No camera but deep night + long idle → assume asleep.
    if night and idle and not camera_active:
        return ASLEEP

    # Present & engaged?
    engaged_cmd = seconds_since_command is not None and seconds_since_command <= WORKING_RECENT_CMD_S
    if focus_mode or (engaged_cmd and (recent_intent in ("CODER", "PC_OP", "GENERAL") or recent_intent is None)):
        # Treat sustained recent interaction as working unless it's clearly leisure.
        if recent_intent == "RELAX":
            return RELAXING
        return WORKING

    # Present but quiet.
    if 18 <= hour <= 23 and not engaged_cmd:
        return RELAXING
    return IDLE


class ContextStateMachine:
    def __init__(self) -> None:
        self.state = IDLE
        self.changed_at = time.time()
        self._last_intent: str | None = None

    def note_intent(self, intent: str | None) -> None:
        if intent:
            self._last_intent = intent

    def current(self, *, focus_mode: bool = False,
                seconds_since_command: float | None = None) -> str:
        """Gather live signals and (re)classify. Returns the current state."""
        hour = time.localtime().tm_hour
        absent, cam = False, True
        try:
            from ambient_vision import shared_optical_cache as cache
            absent = bool(cache.get("user_absent", False))
            cam = bool(cache.get("camera_active", True))
        except Exception:
            pass

        new_state = classify(
            hour=hour,
            user_absent=absent,
            seconds_since_command=seconds_since_command,
            focus_mode=focus_mode,
            recent_intent=self._last_intent,
            camera_active=cam,
        )
        if new_state != self.state:
            print(f"[CONTEXT] State: {self.state} → {new_state}", flush=True)
            self.state = new_state
            self.changed_at = time.time()
        return self.state

    def should_offer_proactive(self, priority: str = "low", *,
                               focus_mode: bool = False,
                               seconds_since_command: float | None = None) -> bool:
        """Should a proactive event of this priority be allowed to speak right now?"""
        state = self.current(focus_mode=focus_mode, seconds_since_command=seconds_since_command)
        return _PRIORITY.get(priority, 0) >= _STATE_MIN_PRIORITY.get(state, 0)


# Process-wide singleton.
context_state = ContextStateMachine()
