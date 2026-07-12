"""
fast_path.py — Deterministic Low-Latency Lane (Roadmap §3.4)
============================================================

A tiny, LLM-free fast-lane for the most common trivial commands. These need no
reasoning — matching them with regex and answering/acting directly removes the
cloud round-trip entirely, so "mute", "lock the screen", or "what time is it"
respond essentially instantly.

`match(user_text)` returns:
    None                                  → not a fast-path command (use the brain)
    {"say": "<text>"}                     → answer directly, NO action engine call
    {"action": {...}, "say": "<text>"}    → run this one action, then say the line

Deliberately conservative: it only fires on tightly-anchored phrases so it can
never hijack a real, nuanced command. Anything it isn't sure about → None → brain.
"""

from __future__ import annotations

import re
import datetime

# Strip polite/wake prefixes so "jarvis, mute" matches "mute".
_PREFIX_RE = re.compile(
    r"^\s*(hey\s+|ok\s+|okay\s+)?(jarvis|jervis)?[,\s]*\s*(please\s+|could you\s+|can you\s+)?",
    re.IGNORECASE,
)
_TRAILING_RE = re.compile(r"[\s.!,?]+$")


def _normalise(text: str) -> str:
    t = _PREFIX_RE.sub("", text or "", count=1)
    t = _TRAILING_RE.sub("", t)
    return t.strip().lower()


# (compiled pattern, builder) — first full-match wins. Patterns are anchored so
# only a command that IS essentially the trivial phrase matches.
def _time_answer() -> dict:
    now = datetime.datetime.now()
    # Windows strftime lacks %-I, so format 12-hour and strip a leading zero.
    return {"say": f"It's {now.strftime('%I:%M %p').lstrip('0')}, Sir."}


def _date_answer() -> dict:
    now = datetime.datetime.now()
    return {"say": f"It's {now.strftime('%A, %B %d, %Y')}, Sir."}


def _media(cmd: str, say: str) -> dict:
    return {"action": {"action_type": "os_control", "target": cmd}, "say": say}


_RULES: list[tuple[re.Pattern, object]] = [
    # ── Time / date — answered directly, no engine ──────────────────────────
    (re.compile(r"^(what('?s| is) the time|what time is it|tell me the time|the time)$"), _time_answer),
    (re.compile(r"^(what('?s| is) (the |today'?s )?date|what day is it|today'?s date|the date)$"), _date_answer),
    # ── Media / volume / lock — single deterministic os_control action ──────
    (re.compile(r"^(mute|mute (the )?(audio|sound|volume))$"), lambda: _media("mute", "Muted, Sir.")),
    (re.compile(r"^(unmute|unmute (the )?(audio|sound|volume))$"), lambda: _media("unmute", "Unmuted, Sir.")),
    (re.compile(r"^(pause|pause (the )?(music|track|song|media|playback)|pause it)$"),
     lambda: _media("play_pause", "Paused, Sir.")),
    (re.compile(r"^(play|resume|resume (the )?(music|track|song|media|playback)|play it)$"),
     lambda: _media("play_pause", "Resumed, Sir.")),
    (re.compile(r"^(next|next (track|song)|skip|skip (this|the) (track|song))$"),
     lambda: _media("next_track", "Skipped, Sir.")),
    (re.compile(r"^(previous|previous (track|song)|go back a (track|song)|last (track|song))$"),
     lambda: _media("prev_track", "Going back, Sir.")),
    (re.compile(r"^(lock( the| my)? (screen|pc|computer|workstation|system)|lock up|lock it)$"),
     lambda: {"action": {"action_type": "os_control", "target": "lock_screen"}, "say": "Locking down, Sir."}),
    # ── G3 gesture control + presence lock — toggled directly, no engine hop ─
    (re.compile(r"^(hand control|gesture control|gestures|hand gestures) on$"),
     lambda: _gesture_toggle(True)),
    (re.compile(r"^(hand control|gesture control|gestures|hand gestures) off$"),
     lambda: _gesture_toggle(False)),
    (re.compile(r"^(auto ?lock on|enable auto ?lock|presence lock on)$"),
     lambda: _auto_lock_toggle(True)),
    (re.compile(r"^(auto ?lock off|disable auto ?lock|presence lock off)$"),
     lambda: _auto_lock_toggle(False)),
]


def _gesture_toggle(on: bool) -> dict:
    try:
        from gesture_daemon import gesture_daemon
        gesture_daemon.set_gestures_enabled(on)
        return {"say": f"Hand control {'engaged — show me your index finger to start' if on else 'off'}, Sir."}
    except Exception:
        return {"say": "Gesture system is not running, Sir."}


def _auto_lock_toggle(on: bool) -> dict:
    try:
        from gesture_daemon import gesture_daemon
        gesture_daemon.set_auto_lock(on)
        return {"say": f"Presence lock {'armed' if on else 'disarmed'}, Sir."}
    except Exception:
        return {"say": "Gesture system is not running, Sir."}


def match(user_text: str):
    """Return a fast-path directive dict, or None to defer to the brain."""
    if not user_text:
        return None
    norm = _normalise(user_text)
    if not norm or len(norm) > 40:   # trivial commands are short
        return None
    for pattern, builder in _RULES:
        if pattern.fullmatch(norm):
            try:
                return builder()
            except Exception:
                return None
    return None
