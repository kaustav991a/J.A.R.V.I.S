"""
gesture_engine.py — Phase G3 gesture state machine (HAND_GESTURE_CONTROL_PLAN.md §2, §3)
=========================================================================================

Pure logic: 21 hand landmarks per frame -> stream of intent tuples. NO I/O, no
mediapipe import, no ctypes — fully unit-testable with synthetic landmark
sequences (test_gesture_engine.py), same no-hardware harness discipline as
Phases 1–4.

Vocabulary (G3 natural-grab rework + G5.1 ergonomics — vocab DECIDED with Kaustav
2026-07-19, RELIABILITY_HARDENING.md §10.7):

    index finger up, 1 s      START control (from idle; other poses ignored)
    open palm (facing camera) MOVE cursor — palm-knuckle centroid, not the
                              fingertip, so a pinch or grab doesn't jerk the
                              cursor off target while the fingers close.
                              G5.1: two mapping modes (GestureConfig.mapping_mode):
                              "absolute" (position->screen band, the G3 default)
                              or "relative" (trackpad: palm DELTA drives the cursor
                              with an acceleration curve — precise when slow, flicks
                              when fast; kills "gorilla arm"). Gate the flip with
                              env JARVIS_GESTURE_RELATIVE=1 until the live gate.
    thumb+index QUICK tap     LEFT CLICK (fires on release; a quick pinch)
    second tap inside 1 s     DOUBLE CLICK (only if the hand stayed put)
    thumb+index HOLD >0.5 s   RIGHT CLICK — pinch-and-hold "dwell", fires on
                              release. Replaces the finicky thumb+middle right-
                              click (RETIRED in G5.1).
    closed fist               GRAB: mouse down while closed, move to drag,
                              open the hand to drop (click and grab are
                              separate gestures on purpose)
    index+middle vertical     SCROLL (hand up = scroll up)
    back of open hand         CLUTCH (brief) — freeze the cursor and reposition
                              the hand with no jump on re-engage ("lift the mouse").
                              Held 1.5 s it becomes STOP control (disengage).

Input : landmarks = sequence of 21 (x, y, z) in normalised image coords
        (mediapipe order: 0 wrist, 4 thumb tip, 8 index tip, 12 middle tip, …),
        t = monotonic seconds, handedness = "Right" | "Left".
Output: list of intent tuples per frame:

    ("engaged",)            index-up hold turned control ON
    ("disengaged",)         back-of-hand hold turned control OFF (or tracking lost)
    ("move", nx, ny)        ABSOLUTE cursor target, normalised 0..1 (margin-mapped,
                            One-Euro-filtered, deadzoned) — absolute mode
    ("move_delta", dx, dy)  RELATIVE cursor move, signed screen-fraction deltas
                            (accel-scaled, One-Euro-smoothed, deadzoned) — relative
                            mode. The pointer adds these to the live cursor position.
    ("click",)              quick left pinch tap (on release)
    ("double_click",)       two quick left taps inside the double window, same spot
    ("right_click",)        left pinch held past the dwell threshold (on release)
    ("drag_start",)         fist closed -> mouse down
    ("drag_end",)           fist opened -> mouse up
    ("scroll", ticks)       signed wheel ticks (positive = scroll up)

The pointer backend (modules/gesture_pointer.py) is a dumb executor of these.

Live-readable state for the daemon/HUD (updated every frame, read-only):
    .engaged         bool
    .pose            committed pose: "palm" | "back_palm" | "fist" |
                     "index_only" | "two_finger" | "other" | "none"
    .clutch          bool — back-of-hand freeze active (reposition without moving)
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
    # G6.2: pinch_down is ALSO the pinch/fist boundary (see _classify). It was
    # 0.40, which overlapped a fist whose thumb rests NEAR (not on) the index:
    # such grabs registered as a long pinch -> spurious RIGHT CLICK, and slow
    # taps crossed the dwell -> right click instead of left. Lowered to 0.30 so a
    # click needs a genuine thumb-index TOUCH and a closed hand (thumb merely
    # near the index) reads as a grab. Live-tunable (env/calibration).
    pinch_down: float = 0.30
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
    # ---- G5.1 relative-trackpad mapping + acceleration + dwell -------------- #
    # "absolute" = G3 position->screen band (default until the live gate);
    # "relative" = trackpad: palm DELTA drives the cursor with an accel curve.
    mapping_mode: str = "absolute"
    # relative: screen-fraction moved per unit of (One-Euro-smoothed) palm-delta,
    # before the acceleration multiplier. Higher = faster cursor.
    base_gain: float = 1.4
    # acceleration curve A(V) over palm speed V (normalised frame-units / second):
    # below accel_v_lo -> accel_slow (dampen: precision for tiny targets);
    # above accel_v_hi -> accel_fast (amplify: flick across the screen);
    # linear in between.
    accel_slow: float = 0.6
    accel_fast: float = 2.2
    accel_v_lo: float = 0.15
    accel_v_hi: float = 1.5
    # left pinch held at least this long -> RIGHT CLICK on release ("dwell").
    # A shorter pinch is a normal left click. Replaces thumb+middle right-click.
    # G6.2: was 0.5 s — too short, so a deliberate tap (slowed further by the
    # pinch hysteresis + 2-frame debounce on both ends) routinely crossed it and
    # every click became a right click. Live data (Kaustav, 2026-07-19): real taps
    # held 0.2-1.4 s, intentional holds 2.9-4.5 s — a clean gap. 1.5 s makes any
    # tap a left click and only a purposeful hold a right click.
    # LIVE-GATED 2026-07-25 (48 events, phone cam @ sensitivity 3.0): left clicks
    # measured 0.23-1.27 s, right clicks 1.54-11.14 s — 1.5 s lands inside the
    # 1.27/1.54 gap with zero misclassifications. DO NOT LOWER: an intermediate
    # 0.75 s (a stale calibration JSON was shadowing this default with it) turned
    # 16 of those 33 left clicks into right clicks, reproducing the G6.2 bug.
    dwell_right_click_s: float = 1.5
    # G6.2: after a pinch release, suppress GRAB (fist) for this long so a
    # curled-hand click doesn't bleed straight into a drag as the hand reopens
    # through the fist-shaped zone. A real grab a beat later still engages.
    grab_after_pinch_s: float = 0.25
    # ---- G5.5 precision: fine-target damping when the hand is nearly still --- #
    # A SECOND-STAGE gain applied to BOTH mapping modes (absolute has no accel
    # curve, so this is its only precision lever). Below precision_v_lo palm
    # speed the cursor is clamped to precision_gain — steady enough to hit a ×
    # button or set a text caret; above precision_v_hi no damping (normal
    # targeting); linear between. Speed is the palm-centroid velocity in
    # normalised frame-units/second, the same units the accel curve uses.
    precision: bool = True
    precision_gain: float = 0.35
    precision_v_lo: float = 0.08
    precision_v_hi: float = 0.6

    @staticmethod
    def from_env() -> "GestureConfig":
        """Resolve config: dataclass defaults < calibration JSON < JARVIS_* env.

        The calibration JSON (modules.gesture_calibration) persists live-tuned
        knobs across restarts; a JARVIS_* env var is a hard per-session override
        applied ONLY when set, so an unset env never clobbers a persisted value.
        Standalone scripts (gesture_spike.py, enroll) + the daemon share this.
        """
        import os

        from modules import gesture_calibration

        cfg = GestureConfig()
        gesture_calibration.apply_to(cfg, gesture_calibration.load())

        pf = os.getenv("JARVIS_PALM_FACING")
        if pf is not None:
            cfg.require_palm_facing = pf == "1"
        ps = os.getenv("JARVIS_PALM_SIGN")
        if ps is not None:
            try:
                cfg.palm_sign = int(ps)
            except (TypeError, ValueError):
                pass
        for env_name, attr in (("JARVIS_GESTURE_SENSITIVITY", "sensitivity"),
                               ("JARVIS_GESTURE_SMOOTH", "min_cutoff"),
                               ("JARVIS_GESTURE_GAIN", "base_gain"),
                               # G6.2 click/grab live-tuning
                               ("JARVIS_PINCH_DOWN", "pinch_down"),
                               ("JARVIS_PINCH_UP", "pinch_up"),
                               ("JARVIS_DWELL_RIGHT_CLICK_S", "dwell_right_click_s"),
                               ("JARVIS_GRAB_AFTER_PINCH_S", "grab_after_pinch_s")):
            v = os.getenv(env_name)
            if v is not None:
                try:
                    setattr(cfg, attr, float(v))
                except (TypeError, ValueError):
                    pass
        rel = os.getenv("JARVIS_GESTURE_RELATIVE")
        if rel is not None:
            cfg.mapping_mode = "relative" if rel == "1" else "absolute"
        pr = os.getenv("JARVIS_GESTURE_PRECISION")
        if pr is not None:
            cfg.precision = pr == "1"
        pg = os.getenv("JARVIS_PRECISION_GAIN")
        if pg is not None:
            try:
                cfg.precision_gain = float(pg)
            except (TypeError, ValueError):
                pass
        return cfg


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
        self.clutch = False                   # back-of-hand freeze (G5.1)
        self.start_progress = 0.0
        self.stop_progress = 0.0
        self._fx = OneEuroFilter(self.cfg.min_cutoff, self.cfg.beta, self.cfg.d_cutoff)
        self._fy = OneEuroFilter(self.cfg.min_cutoff, self.cfg.beta, self.cfg.d_cutoff)
        self._left = _PinchTracker(self.cfg)   # thumb+index (click + dwell right-click)
        self._pose_tracker = _PoseTracker(self.cfg)
        self._start_t: float | None = None   # index-up hold start
        self._stop_t: float | None = None    # back-of-hand hold start
        self._dragging = False
        self._pinch_down_t: float | None = None      # left-pinch land time (dwell timer)
        self._pinch_up_t: float | None = None        # G6.2: last pinch release (grab cooldown)
        self._last_click_t = -1e9
        self.last_pinch_held_s = 0.0    # G6.2 diagnostic: hold length of the last pinch
        self._last_click_pos: tuple[float, float] | None = None
        self._last_emit: tuple[float, float] | None = None   # absolute mode last target
        self._rel_prev: tuple[float, float] | None = None    # relative: prev smoothed centroid
        self._rel_prev_t: float | None = None
        self._move_prev_c: tuple[float, float] | None = None  # G5.5: prev centroid (speed for precision)
        self._move_prev_t: float | None = None
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

        raw = self._classify(ext, lm, handedness, d_left)
        pose = self._pose_tracker.update(raw)
        self.pose = pose
        centroid = self._palm_centroid(lm)

        intents: list[tuple] = []
        if not self.engaged:
            self.clutch = False
            self._reset_rel()
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
            # Back-of-hand: CLUTCH (freeze) while it arms, STOP once held long
            # enough. Either way nothing else moves this frame; drop the relative
            # anchor so re-facing the palm resumes with no jump ("set the mouse
            # back down" after repositioning the hand).
            self._reset_rel()
            return intents
        self.clutch = False

        self._update_pinches(pose, d_left, centroid, t, intents)
        # GRAB starts on a committed fist — but never while a pinch is active, and
        # not in the brief cooldown right after a pinch release (G6.2: a curled
        # click reopens through the fist-shaped zone; that must not become a drag).
        grab_cool = (self._pinch_up_t is not None
                     and t - self._pinch_up_t <= self.cfg.grab_after_pinch_s)
        if pose == "fist" and not self._dragging and not self._left.down \
                and not grab_cool:
            self._dragging = True
            intents.append(("drag_start",))
        scrolling = self._update_scroll(pose, lm, intents)

        # The cursor is driven by the palm-knuckle centroid, and ONLY while
        # the open palm shows (raw classify must agree — the instant the
        # fingers start closing the cursor freezes, so a click/grab can't
        # drag it off target: the G2 drag-select bug) or while a grab is
        # held (then it always follows — that IS the drag). Any other frame
        # drops the relative anchor so the next move can't jump.
        if not scrolling and (
                (pose == "palm" and raw == "palm") or self._dragging):
            if self.cfg.mapping_mode == "relative":
                self._update_move_relative(centroid, t, intents)
            else:
                self._update_move(centroid, t, intents)
        else:
            self._reset_rel()
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
        self.clutch = False
        self._reset_rel()
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
            self.clutch = False
            return False
        if self._stop_t is None:
            self._stop_t = t
        held = t - self._stop_t
        self.stop_progress = min(held / self.cfg.stop_hold_s, 1.0)
        # back-of-hand while arming = CLUTCH (freeze + reposition); index-up-as-
        # stop (facing detection off) is not a clutch pose.
        self.clutch = stop_pose == "back_palm"
        if held >= self.cfg.stop_hold_s:
            self._stop_t = None
            self.stop_progress = 0.0
            self.clutch = False
            self._disengage(intents)
        return True

    def _disengage(self, intents: list) -> None:
        if self._dragging:
            self._dragging = False
            intents.append(("drag_end",))
        self.engaged = False
        self.clutch = False
        self._reset_motion_state()
        intents.append(("disengaged",))

    def _reset_rel(self) -> None:
        """Drop the mapping anchors so the next move re-syncs (no jump / no stale
        velocity). Called on every non-move frame (clutch, pinch, scroll, loss)."""
        self._rel_prev = None
        self._rel_prev_t = None
        self._move_prev_c = None
        self._move_prev_t = None

    def _reset_motion_state(self) -> None:
        self._fx.reset()
        self._fy.reset()
        self._left.reset()
        self._dragging = False
        self._pinch_down_t = None
        self._pinch_up_t = None
        self._last_emit = None
        self._reset_rel()
        self._scroll_y = None
        self._scroll_acc = 0.0
        self._start_t = None
        self._stop_t = None
        self.start_progress = 0.0
        self.stop_progress = 0.0

    # ---- clicks / grab / scroll / move ------------------------------- #

    def _update_pinches(self, pose, d_left, centroid, t, intents) -> None:
        """The left pinch (thumb+index) does BOTH clicks, split by DWELL (G5.1):

        - a QUICK pinch (down then up under dwell_right_click_s) = LEFT CLICK,
          with the double-click window (a 2nd quick tap, same spot, inside
          double_window_s -> DOUBLE CLICK);
        - a HELD pinch (>= dwell_right_click_s) = RIGHT CLICK.

        Both fire on the UP transition, once the hold length is known — so the
        cursor (frozen during the pinch) has settled on the target. The finicky
        thumb+middle right-click was RETIRED in G5.1. Pinches are suppressed
        inside a committed fist so a grab can't fire a click. `centroid` (palm
        knuckles) is the same-spot reference — stable and mapping-mode-agnostic.
        """
        # Suppress the pinch inside a committed fist so a grab can't fire a click —
        # BUT never abort a pinch that is ALREADY down: as a curled-hand pinch
        # RELEASES, d_left rises back through the fist-shaped zone and the pose can
        # momentarily read "fist"; forcing the pinch open there would misclassify
        # the hold length (G6.2). Once down, let real d_left decide the release.
        in_fist = pose == "fist" and not self._left.down
        ev = self._left.update(d_left if not in_fist else 2.0, t)
        if ev == "down":
            self._pinch_down_t = t
            return
        if ev != "up":
            return
        self._pinch_up_t = t   # G6.2: arm the grab cooldown

        down_t = self._pinch_down_t
        self._pinch_down_t = None
        held = (t - down_t) if down_t is not None else 0.0
        self.last_pinch_held_s = held   # diagnostic readout (spike prints it)

        if held >= self.cfg.dwell_right_click_s:
            intents.append(("right_click",))
            self._last_click_t = -1e9   # a dwell is not a left tap
            return

        same_spot = (self._last_click_pos is None
                     or _dist(centroid, self._last_click_pos) <= self.cfg.double_max_move)
        if t - self._last_click_t <= self.cfg.double_window_s and same_spot:
            self._last_click_t = -1e9
            intents.append(("double_click",))
        else:
            self._last_click_t = t
            self._last_click_pos = centroid
            intents.append(("click",))

    def _update_scroll(self, pose: str, lm, intents) -> bool:
        if pose != "two_finger" or self._dragging or self._left.down:
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
        tx = self._fx(nx, t)   # filtered target
        ty = self._fy(ny, t)
        # G5.5 precision: when the palm is moving slowly (centroid speed, the same
        # units the accel curve uses), EASE toward the target instead of snapping,
        # so a tremor can't knock the cursor off a tiny target. The deadzone is
        # tested against the RAW target (not the eased step), so the cursor keeps
        # inching until it is within deadzone of the target — same landing point
        # as with precision off, just a gentler approach (no settling bias). The
        # first frame after a (re)sync has no prior centroid, so no easing.
        nx, ny = tx, ty
        if self._move_prev_c is not None and self._move_prev_t is not None \
                and self._last_emit is not None:
            v = _dist(driver, self._move_prev_c) / max(t - self._move_prev_t, 1e-6)
            p = self._precision_gain(v)
            if p < 1.0:
                nx = self._last_emit[0] + p * (tx - self._last_emit[0])
                ny = self._last_emit[1] + p * (ty - self._last_emit[1])
        self._move_prev_c = driver
        self._move_prev_t = t
        if self._last_emit is not None \
                and _dist((tx, ty), self._last_emit) < self.cfg.deadzone:
            return
        self._last_emit = (nx, ny)
        intents.append(("move", nx, ny))

    # ---- G5.1 relative-trackpad move --------------------------------- #

    def _accel(self, v: float) -> float:
        """Acceleration multiplier for palm speed v (frame-units / second).

        Slow  -> accel_slow (dampen: precision on tiny targets).
        Fast  -> accel_fast (amplify: flick across the screen).
        Linear in between. Monotonic, so a steady drag scales smoothly.
        """
        lo, hi = self.cfg.accel_v_lo, self.cfg.accel_v_hi
        if v <= lo:
            return self.cfg.accel_slow
        if v >= hi:
            return self.cfg.accel_fast
        f = (v - lo) / (hi - lo)          # 0..1
        return self.cfg.accel_slow + f * (self.cfg.accel_fast - self.cfg.accel_slow)

    def _precision_gain(self, v: float) -> float:
        """G5.5 fine-target damping (both mapping modes). Extra gain reduction at
        low palm speed v (frame-units/s) so a tremor can't knock the cursor off a
        tiny target: below precision_v_lo -> precision_gain, above precision_v_hi
        -> 1.0 (no damping), linear between. Transient only — it eases the
        approach, it does not shift where the cursor lands (see _update_move)."""
        if not self.cfg.precision:
            return 1.0
        lo, hi = self.cfg.precision_v_lo, self.cfg.precision_v_hi
        if v <= lo:
            return self.cfg.precision_gain
        if v >= hi or hi <= lo:
            return 1.0
        f = (v - lo) / (hi - lo)
        return self.cfg.precision_gain + f * (1.0 - self.cfg.precision_gain)

    def _update_move_relative(self, centroid, t, intents) -> None:
        """Trackpad mapping: emit a signed screen-fraction DELTA from the change
        in the (One-Euro-smoothed) palm centroid, scaled by base_gain * accel.

        The One-Euro filters smooth the centroid position; the delta is taken
        between consecutive smoothed positions. `_rel_prev` is dropped (set None)
        on any non-move frame (clutch, pinch, scroll, loss) — so the frame after a
        freeze just re-syncs the anchor and emits nothing, guaranteeing NO cursor
        jump on re-engage ("lift the mouse, set it back down"). x is already
        mirror-correct upstream (the camera loop flips the frame)."""
        sx = self._fx(centroid[0], t)
        sy = self._fy(centroid[1], t)
        if self._rel_prev is None or self._rel_prev_t is None:
            self._rel_prev = (sx, sy)
            self._rel_prev_t = t
            return  # anchor sync frame — no move (prevents the re-engage jump)
        dx_n = sx - self._rel_prev[0]
        dy_n = sy - self._rel_prev[1]
        dt = max(t - self._rel_prev_t, 1e-6)
        self._rel_prev = (sx, sy)
        self._rel_prev_t = t
        dist = math.hypot(dx_n, dy_n)
        if dist < self.cfg.deadzone:
            return
        v = dist / dt
        # accel handles the mid/high range (flick vs precision); the G5.5
        # precision gain adds a harder clamp in the ultra-slow fine-targeting
        # regime so tiny targets are selectable. The two multiply.
        gain = self.cfg.base_gain * self._accel(v) * self._precision_gain(v)
        intents.append(("move_delta", dx_n * gain, dy_n * gain))
