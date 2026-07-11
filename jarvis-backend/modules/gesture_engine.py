"""
gesture_engine.py — Phase G2 gesture state machine (HAND_GESTURE_CONTROL_PLAN.md §2, §3)
=========================================================================================

Pure logic: 21 hand landmarks per frame -> stream of intent tuples. NO I/O, no
mediapipe import, no ctypes — fully unit-testable with synthetic landmark
sequences (test_gesture_engine.py), same no-hardware harness discipline as
Phases 1–4.

Input : landmarks = sequence of 21 (x, y, z) in normalised image coords
        (mediapipe order: 0 wrist, 4 thumb tip, 8 index tip, 12 middle tip, …),
        t = monotonic seconds, handedness = "Right" | "Left".
Output: list of intent tuples per frame:

    ("engaged",)            palm-gate toggled control ON
    ("disengaged",)         palm-gate toggled control OFF (or tracking lost)
    ("move", nx, ny)        cursor target, normalised 0..1 (margin-mapped,
                            One-Euro-filtered, deadzoned)
    ("click",)              left tap
    ("double_click",)       two left taps inside the double window
    ("right_click",)        thumb–middle pinch tap
    ("drag_start",)         left pinch held past tap window -> mouse down
    ("drag_end",)           pinch released while dragging   -> mouse up
    ("scroll", ticks)       signed wheel ticks (positive = scroll up)

The pointer backend (modules/gesture_pointer.py) is a dumb executor of these.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# MediaPipe hand landmark indices used by the engine.
WRIST = 0
THUMB_TIP = 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_PIP, RING_TIP = 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class OneEuroFilter:
    """One-Euro filter (Casiez et al.) — industry standard for cursor jitter.

    Low speed -> heavy smoothing (steady hand = steady cursor); high speed ->
    light smoothing (no perceptible lag on fast moves).
    """

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.015,
                 d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self) -> None:
        self._t = None
        self._x = None
        self._dx = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._t is None:
            self._t, self._x = t, x
            return x
        dt = max(t - self._t, 1e-6)
        self._t = t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = self._alpha(cutoff, dt)
        self._x = a * x + (1 - a) * self._x
        return self._x


@dataclass
class GestureConfig:
    # camera->screen mapping: only the middle of the frame maps to the full
    # screen so fingertips never have to reach the frame edge (G1-proven).
    margin: float = 0.15
    # One-Euro cursor smoothing (normalised units).
    min_cutoff: float = 1.2
    beta: float = 0.015
    d_cutoff: float = 1.0
    # no move emitted below this normalised displacement (steady-hand hold).
    deadzone: float = 0.004
    # pinch hysteresis, distances normalised by hand size (wrist->middle MCP):
    # DOWN below pinch_down, UP above pinch_up — the gap kills chatter.
    pinch_down: float = 0.40
    pinch_up: float = 0.60
    # a pinch state flip must persist this many consecutive frames.
    debounce_frames: int = 2
    tap_max_s: float = 0.25        # pinch shorter than this = tap (click)
    double_window_s: float = 0.40  # second tap inside this = double-click
    # engage gate: open palm facing camera, held this long, toggles control.
    engage_hold_s: float = 1.0
    require_palm_facing: bool = True
    # sign of the wrist->indexMCP x wrist->pinkyMCP cross product that means
    # "palm faces camera" for a RIGHT hand in the (possibly mirrored) frame.
    # Calibratable because frame mirroring flips it.
    palm_sign: int = 1
    # scroll: vertical index-tip velocity -> wheel ticks (hand up = scroll up).
    scroll_gain: float = 40.0
    # tracking loss: release a drag quickly (dropped frames must not leave the
    # mouse button stuck down), disengage after a longer gap.
    lost_drag_grace_s: float = 0.2
    lost_disengage_s: float = 2.0


class _PinchTracker:
    """Debounced hysteresis for one thumb–fingertip pair."""

    def __init__(self, cfg: GestureConfig):
        self._cfg = cfg
        self.down = False
        self.down_t = 0.0
        self._flip_count = 0

    def reset(self) -> None:
        self.down = False
        self._flip_count = 0

    def update(self, d: float, t: float) -> str | None:
        """Feed one normalised distance; returns "down"/"up" on transition."""
        if self.down:
            want = d <= self._cfg.pinch_up  # stay down until clearly open
        else:
            want = d < self._cfg.pinch_down
        if want == self.down:
            self._flip_count = 0
            return None
        self._flip_count += 1
        if self._flip_count < self._cfg.debounce_frames:
            return None
        self._flip_count = 0
        self.down = want
        if want:
            self.down_t = t
            return "down"
        return "up"


class GestureEngine:
    """Landmarks in, intents out. Owns no camera, no cursor, no threads."""

    def __init__(self, config: GestureConfig | None = None):
        self.cfg = config or GestureConfig()
        self.engaged = False
        self._fx = OneEuroFilter(self.cfg.min_cutoff, self.cfg.beta, self.cfg.d_cutoff)
        self._fy = OneEuroFilter(self.cfg.min_cutoff, self.cfg.beta, self.cfg.d_cutoff)
        self._left = _PinchTracker(self.cfg)
        self._right = _PinchTracker(self.cfg)
        self._palm_start: float | None = None
        self._palm_armed = True     # must leave the open-palm pose to re-arm
        self._dragging = False
        self._last_click_t = -1e9
        self._last_emit: tuple[float, float] | None = None
        self._scroll_y: float | None = None
        self._scroll_acc = 0.0
        self._last_seen_t: float | None = None

    # ------------------------------------------------------------------ #

    def process(self, landmarks, t: float, handedness: str = "Right") -> list[tuple]:
        """One frame. landmarks may be None (no hand detected)."""
        if landmarks is None:
            return self._on_lost(t)
        self._last_seen_t = t

        lm = [(p[0], p[1]) for p in landmarks]
        hand_size = _dist(lm[WRIST], lm[MIDDLE_MCP])
        if hand_size < 1e-4:
            return self._on_lost(t)

        wrist = lm[WRIST]
        ext = {
            "index": _dist(lm[INDEX_TIP], wrist) > _dist(lm[INDEX_PIP], wrist),
            "middle": _dist(lm[MIDDLE_TIP], wrist) > _dist(lm[MIDDLE_PIP], wrist),
            "ring": _dist(lm[RING_TIP], wrist) > _dist(lm[RING_PIP], wrist),
            "pinky": _dist(lm[PINKY_TIP], wrist) > _dist(lm[PINKY_PIP], wrist),
        }
        d_left = _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / hand_size
        d_right = _dist(lm[THUMB_TIP], lm[MIDDLE_TIP]) / hand_size

        intents: list[tuple] = []
        open_pose = (all(ext.values())
                     and d_left > self.cfg.pinch_up
                     and d_right > self.cfg.pinch_up)
        self._update_engage_gate(open_pose, lm, t, handedness, intents)
        if not self.engaged:
            return intents

        self._update_pinches(d_left, d_right, t, intents)
        scrolling = self._update_scroll(ext, lm, intents)
        # Cursor freezes while a pinch is held but not yet a drag: the finger
        # dip of a tap must not drag the cursor off its target mid-click.
        # The open palm is the gate pose — it never drives the cursor either
        # (pointing does), so holding it to disengage can't drag the pointer.
        pending_tap = self._left.down and not self._dragging
        if not scrolling and not pending_tap and not open_pose:
            self._update_move(lm[INDEX_TIP], t, intents)
        return intents

    # ------------------------------------------------------------------ #

    def _on_lost(self, t: float) -> list[tuple]:
        intents: list[tuple] = []
        self._palm_start = None
        if not self.engaged or self._last_seen_t is None:
            return intents
        gap = t - self._last_seen_t
        if self._dragging and gap > self.cfg.lost_drag_grace_s:
            self._dragging = False
            intents.append(("drag_end",))
        if gap > self.cfg.lost_disengage_s:
            self._disengage(intents)
        return intents

    def _palm_facing(self, lm, handedness: str) -> bool:
        v1 = (lm[INDEX_MCP][0] - lm[WRIST][0], lm[INDEX_MCP][1] - lm[WRIST][1])
        v2 = (lm[PINKY_MCP][0] - lm[WRIST][0], lm[PINKY_MCP][1] - lm[WRIST][1])
        cross_z = v1[0] * v2[1] - v1[1] * v2[0]
        sign = self.cfg.palm_sign if handedness == "Right" else -self.cfg.palm_sign
        return cross_z * sign > 0

    def _update_engage_gate(self, open_pose, lm, t, handedness,
                            intents) -> None:
        open_palm = open_pose
        if open_palm and self.cfg.require_palm_facing:
            open_palm = self._palm_facing(lm, handedness)

        if not open_palm:
            self._palm_start = None
            self._palm_armed = True
            return
        if not self._palm_armed:
            return
        if self._palm_start is None:
            self._palm_start = t
            return
        if t - self._palm_start < self.cfg.engage_hold_s:
            return
        # held long enough — toggle
        self._palm_start = None
        self._palm_armed = False
        if self.engaged:
            self._disengage(intents)
        else:
            self.engaged = True
            self._reset_motion_state()
            intents.append(("engaged",))

    def _disengage(self, intents: list) -> None:
        if self._dragging:
            self._dragging = False
            intents.append(("drag_end",))
        self.engaged = False
        self._reset_motion_state()
        intents.append(("disengaged",))

    def _reset_motion_state(self) -> None:
        self._fx.reset()
        self._fy.reset()
        self._left.reset()
        self._right.reset()
        self._dragging = False
        self._last_emit = None
        self._scroll_y = None
        self._scroll_acc = 0.0

    def _update_pinches(self, d_left, d_right, t, intents) -> None:
        # Mutual exclusion: a thumb–index pinch also shortens thumb–middle.
        # Left has priority while down; right only tracks when clearly alone.
        left_ev = self._left.update(d_left, t)
        if self._left.down or left_ev == "up":
            self._right.reset()
            right_ev = None
        else:
            right_ev = self._right.update(d_right, t)

        if left_ev == "up":
            if self._dragging:
                self._dragging = False
                intents.append(("drag_end",))
            elif t - self._left.down_t <= self.cfg.tap_max_s:
                if t - self._last_click_t <= self.cfg.double_window_s:
                    self._last_click_t = -1e9
                    intents.append(("double_click",))
                else:
                    self._last_click_t = t
                    intents.append(("click",))
        elif self._left.down and not self._dragging \
                and t - self._left.down_t > self.cfg.tap_max_s:
            self._dragging = True
            intents.append(("drag_start",))

        if right_ev == "up" and t - self._right.down_t <= self.cfg.tap_max_s:
            intents.append(("right_click",))

    def _update_scroll(self, ext, lm, intents) -> bool:
        pose = (ext["index"] and ext["middle"]
                and not ext["ring"] and not ext["pinky"]
                and not self._left.down and not self._right.down)
        if not pose:
            self._scroll_y = None
            self._scroll_acc = 0.0
            return False
        y = lm[INDEX_TIP][1]
        if self._scroll_y is not None:
            # hand up (y shrinks) -> positive ticks -> scroll up
            self._scroll_acc += (self._scroll_y - y) * self.cfg.scroll_gain
            ticks = int(self._scroll_acc)
            if ticks:
                self._scroll_acc -= ticks
                intents.append(("scroll", ticks))
        self._scroll_y = y
        return True

    def _update_move(self, index_tip, t, intents) -> None:
        m = self.cfg.margin
        nx = (index_tip[0] - m) / (1 - 2 * m)
        ny = (index_tip[1] - m) / (1 - 2 * m)
        nx = min(max(nx, 0.0), 1.0)
        ny = min(max(ny, 0.0), 1.0)
        nx = self._fx(nx, t)
        ny = self._fy(ny, t)
        if self._last_emit is not None \
                and _dist((nx, ny), self._last_emit) < self.cfg.deadzone:
            return
        self._last_emit = (nx, ny)
        intents.append(("move", nx, ny))
