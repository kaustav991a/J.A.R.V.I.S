"""
gesture_engine.py — Phase G3 gesture state machine (HAND_GESTURE_CONTROL_PLAN.md §2, §3)
=========================================================================================

Pure logic: 21 hand landmarks per frame -> stream of intent tuples. NO I/O, no
mediapipe import, no ctypes — fully unit-testable with synthetic landmark
sequences (test_gesture_engine.py), same no-hardware harness discipline as
Phases 1–4.

G3 vocabulary (natural-grab rework — G2's pinch-does-everything scheme caused
laggy clicks and drag-selected text, live-gate finding):

    index finger up, 1 s      START control (from idle; other poses ignored)
    open palm (facing camera) MOVE cursor — palm-knuckle centroid, not the
                              fingertip, so a pinch or grab doesn't jerk the
                              cursor off target while the fingers close
    thumb+index tap           LEFT CLICK — fires the moment the pinch lands
    second tap inside 1 s     DOUBLE CLICK (only if the cursor stayed put)
    thumb+middle tap          RIGHT CLICK
    closed fist               GRAB: mouse down while closed, move to drag,
                              open the hand to drop (click and grab are
                              separate gestures on purpose)
    index+middle vertical     SCROLL (hand up = scroll up)
    back of open hand, 1.5 s  STOP control

Input : landmarks = sequence of 21 (x, y, z) in normalised image coords
        (mediapipe order: 0 wrist, 4 thumb tip, 8 index tip, 12 middle tip, …),
        t = monotonic seconds, handedness = "Right" | "Left".
Output: list of intent tuples per frame:

    ("engaged",)            index-up hold turned control ON
    ("disengaged",)         back-of-hand hold turned control OFF (or tracking lost)
    ("move", nx, ny)        cursor target, normalised 0..1 (margin-mapped,
                            One-Euro-filtered, deadzoned)
    ("click",)              left pinch tap
    ("double_click",)       two left taps inside the double window, same spot
    ("right_click",)        thumb–middle pinch tap
    ("drag_start",)         fist closed -> mouse down
    ("drag_end",)           fist opened -> mouse up
    ("scroll", ticks)       signed wheel ticks (positive = scroll up)

The pointer backend (modules/gesture_pointer.py) is a dumb executor of these.

Live-readable state for the daemon/HUD (updated every frame, read-only):
    .engaged         bool
    .pose            committed pose: "palm" | "back_palm" | "fist" |
                     "index_only" | "two_finger" | "other" | "none"
    .start_progress  0..1 while the index-up start hold is arming
    .stop_progress   0..1 while the back-of-hand stop hold is arming
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# MediaPipe hand landmark indices used by the engine.
WRIST = 0
THUMB_TIP = 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
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
    # screen so the hand never has to reach the frame edge (G1-proven).
    margin: float = 0.15
    # cursor sensitivity: how much of the frame maps to the full screen.
    # 1.0 = original (middle 70% of the frame). Higher = a smaller central
    # band spans the screen = LESS hand travel for the same cursor distance.
    # Env JARVIS_GESTURE_SENSITIVITY + live +/- keys in gesture_spike.py.
    # Supersedes `margin`.
    sensitivity: float = 1.5
    # One-Euro cursor smoothing (normalised units). Lower min_cutoff = smoother
    # when the hand is still; beta keeps fast moves lag-free.
    min_cutoff: float = 1.0
    beta: float = 0.015
    d_cutoff: float = 1.0
    # no move emitted below this normalised displacement (steady-hand hold).
    deadzone: float = 0.004
    # pinch hysteresis, distances normalised by hand size (wrist->middle MCP):
    # DOWN below pinch_down, UP above pinch_up — the gap kills chatter.
    pinch_down: float = 0.40
    pinch_up: float = 0.60
    # a pinch or pose flip must persist this many consecutive frames.
    debounce_frames: int = 2
    # second pinch tap inside this window = double-click…
    double_window_s: float = 1.0
    # …but only if the cursor moved less than this since the first tap
    # (clicking two different buttons quickly must stay two single clicks).
    double_max_move: float = 0.05
    # index finger up, held this long, turns control ON (from idle).
    start_hold_s: float = 1.0
    # back of the open hand shown this long turns control OFF.
    stop_hold_s: float = 1.5
    # palm must face the camera to drive the cursor; the flipped hand is the
    # stop sign. When False (facing detection broken on this camera), moving
    # works in both orientations and index-up 1 s TOGGLES control instead.
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

    @staticmethod
    def from_env() -> "GestureConfig":
        """Build from JARVIS_* env vars — standalone scripts + daemon share this."""
        import os

        def _flt(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, ""))
            except (TypeError, ValueError):
                return default

        return GestureConfig(
            require_palm_facing=os.getenv("JARVIS_PALM_FACING", "1") == "1",
            palm_sign=int(os.getenv("JARVIS_PALM_SIGN", "1")),
            sensitivity=_flt("JARVIS_GESTURE_SENSITIVITY", 1.5),
            min_cutoff=_flt("JARVIS_GESTURE_SMOOTH", 1.0),
        )


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


class _PoseTracker:
    """Debounced pose classifier commit — a pose flip must persist N frames."""

    def __init__(self, cfg: GestureConfig):
        self._cfg = cfg
        self.pose = "none"
        self._candidate = "none"
        self._count = 0

    def reset(self) -> None:
        self.pose = "none"
        self._candidate = "none"
        self._count = 0

    def update(self, raw: str) -> str:
        if raw == self.pose:
            self._candidate, self._count = raw, 0
            return self.pose
        if raw != self._candidate:
            self._candidate, self._count = raw, 1
        else:
            self._count += 1
        if self._count >= self._cfg.debounce_frames:
            self.pose = raw
            self._count = 0
        return self.pose


class GestureEngine:
    """Landmarks in, intents out. Owns no camera, no cursor, no threads."""

    def __init__(self, config: GestureConfig | None = None):
        self.cfg = config or GestureConfig()
        self.engaged = False
        self.pose = "none"
        self.start_progress = 0.0
        self.stop_progress = 0.0
        self._fx = OneEuroFilter(self.cfg.min_cutoff, self.cfg.beta, self.cfg.d_cutoff)
        self._fy = OneEuroFilter(self.cfg.min_cutoff, self.cfg.beta, self.cfg.d_cutoff)
        self._left = _PinchTracker(self.cfg)
        self._right = _PinchTracker(self.cfg)
        self._pose_tracker = _PoseTracker(self.cfg)
        self._start_t: float | None = None   # index-up hold start
        self._stop_t: float | None = None    # back-of-hand hold start
        self._dragging = False
        self._last_click_t = -1e9
        self._last_click_pos: tuple[float, float] | None = None
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

        raw = self._classify(ext, lm, handedness, d_left)
        pose = self._pose_tracker.update(raw)
        self.pose = pose

        intents: list[tuple] = []
        if not self.engaged:
            self._update_start_gate(pose, t, intents)
            return intents
        self.start_progress = 0.0

        # A grab is STICKY: once the fist closes, only a clearly opened hand
        # (palm or back-of-hand) drops it — transitional "other" frames while
        # the fist moves must not fumble the drag (live finding).
        if self._dragging and pose in ("palm", "back_palm"):
            self._dragging = False
            intents.append(("drag_end",))

        if self._update_stop_gate(pose, t, intents):
            return intents

        self._update_pinches(pose, d_left, d_right, t, intents)
        if pose == "fist" and not self._dragging \
                and not self._left.down and not self._right.down:
            self._dragging = True
            intents.append(("drag_start",))
        scrolling = self._update_scroll(pose, lm, intents)

        # The cursor is driven by the palm-knuckle centroid, and ONLY while
        # the open palm shows (raw classify must agree — the instant the
        # fingers start closing the cursor freezes, so a click/grab can't
        # drag it off target: the G2 drag-select bug) or while a grab is
        # held (then it always follows — that IS the drag).
        if not scrolling and (
                (pose == "palm" and raw == "palm") or self._dragging):
            self._update_move(self._palm_centroid(lm), t, intents)
        return intents

    # ------------------------------------------------------------------ #

    @staticmethod
    def _palm_centroid(lm) -> tuple[float, float]:
        """Mean of the four finger knuckles — stable through pinch AND fist."""
        xs = (lm[INDEX_MCP][0] + lm[MIDDLE_MCP][0] + lm[RING_MCP][0] + lm[PINKY_MCP][0]) / 4.0
        ys = (lm[INDEX_MCP][1] + lm[MIDDLE_MCP][1] + lm[RING_MCP][1] + lm[PINKY_MCP][1]) / 4.0
        return (xs, ys)

    def _classify(self, ext, lm, handedness: str, d_left: float) -> str:
        n = ext
        if n["index"] and n["middle"] and n["ring"] and n["pinky"]:
            if not self.cfg.require_palm_facing:
                return "palm"
            return "palm" if self._palm_facing(lm, handedness) else "back_palm"
        if n["index"] and n["middle"] and not n["ring"] and not n["pinky"]:
            return "two_finger"
        if n["index"] and not n["middle"] and not n["ring"] and not n["pinky"]:
            return "index_only"
        # A closed hand is a GRAB only when the thumb is NOT pinching the index:
        # thumb+index touching is always a click intent (user spec), even with
        # the rest of the hand curled. Tolerant of one misread finger (the
        # tip-PIP extension test is noisy on tilted fists — live finding).
        if d_left >= self.cfg.pinch_down \
                and (int(n["index"]) + int(n["middle"]) + int(n["ring"])) <= 1:
            return "fist"
        return "other"

    def _on_lost(self, t: float) -> list[tuple]:
        intents: list[tuple] = []
        self._start_t = None
        self._stop_t = None
        self.start_progress = 0.0
        self.stop_progress = 0.0
        self._pose_tracker.reset()
        self.pose = "none"
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

    # ---- start / stop gates ------------------------------------------ #

    def _update_start_gate(self, pose: str, t: float, intents: list) -> None:
        """Idle: index finger up, held start_hold_s, turns control ON."""
        if pose != "index_only":
            self._start_t = None
            self.start_progress = 0.0
            return
        if self._start_t is None:
            self._start_t = t
        held = t - self._start_t
        self.start_progress = min(held / self.cfg.start_hold_s, 1.0)
        if held < self.cfg.start_hold_s:
            return
        self._start_t = None
        self.start_progress = 0.0
        self.engaged = True
        self._reset_motion_state()
        intents.append(("engaged",))

    def _update_stop_gate(self, pose: str, t: float, intents: list) -> bool:
        """Active: back of the open hand (or index-up when facing detection is
        off) held stop_hold_s turns control OFF. Returns True while arming so
        the frame drives nothing else."""
        stop_pose = "back_palm" if self.cfg.require_palm_facing else "index_only"
        if pose != stop_pose:
            self._stop_t = None
            self.stop_progress = 0.0
            return False
        if self._stop_t is None:
            self._stop_t = t
        held = t - self._stop_t
        self.stop_progress = min(held / self.cfg.stop_hold_s, 1.0)
        if held >= self.cfg.stop_hold_s:
            self._stop_t = None
            self.stop_progress = 0.0
            self._disengage(intents)
        return True

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
        self._start_t = None
        self._stop_t = None
        self.start_progress = 0.0
        self.stop_progress = 0.0

    # ---- clicks / grab / scroll / move ------------------------------- #

    def _update_pinches(self, pose, d_left, d_right, t, intents) -> None:
        """Click fires the moment a pinch lands (down-confirm) — snappy, and a
        pinch can never turn into a drag (grab is the fist).

        Left (thumb+index) vs right (thumb+middle) are told apart by which
        touch is CLOSER — not by absolute finger-extension, which was too
        fragile and silently dropped real taps (live finding). Both are
        suppressed inside a committed fist so a grab can't fire a click.
        """
        in_fist = pose == "fist"
        left_ok = not in_fist and d_left <= d_right
        right_ok = not in_fist and d_right < d_left
        left_ev = self._left.update(d_left if left_ok else 2.0, t)
        if self._left.down or left_ev == "up":
            self._right.reset()   # left has priority while down
            right_ev = None
        else:
            right_ev = self._right.update(d_right if right_ok else 2.0, t)

        if left_ev == "down":
            pos = self._last_emit
            same_spot = (pos is None or self._last_click_pos is None
                         or _dist(pos, self._last_click_pos) <= self.cfg.double_max_move)
            if t - self._last_click_t <= self.cfg.double_window_s and same_spot:
                self._last_click_t = -1e9
                intents.append(("double_click",))
            else:
                self._last_click_t = t
                self._last_click_pos = pos
                intents.append(("click",))

        if right_ev == "down":
            intents.append(("right_click",))

    def _update_scroll(self, pose: str, lm, intents) -> bool:
        if pose != "two_finger" or self._dragging \
                or self._left.down or self._right.down:
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

    def _update_move(self, driver, t, intents) -> None:
        # sensitivity-scaled active region centred on the frame: only a band of
        # half-width `half` maps to the full screen, so higher sensitivity =
        # a narrower band = less hand travel. Clamped so it can't collapse.
        half = max(0.35 / max(self.cfg.sensitivity, 0.1), 0.05)
        lo = 0.5 - half
        span = 2.0 * half
        nx = min(max((driver[0] - lo) / span, 0.0), 1.0)
        ny = min(max((driver[1] - lo) / span, 0.0), 1.0)
        nx = self._fx(nx, t)
        ny = self._fy(ny, t)
        if self._last_emit is not None \
                and _dist((nx, ny), self._last_emit) < self.cfg.deadzone:
            return
        self._last_emit = (nx, ny)
        intents.append(("move", nx, ny))
