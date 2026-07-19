r"""
gesture_roi.py — G5.4 distance mitigation (crop-around-hand ROI)
================================================================

Problem: MediaPipe HandLandmarker resizes whatever image it's given down to
its ~192 px model input. A hand across the room occupies a handful of pixels in
a full 640×480 frame, so after that internal downscale it's gone — landmarks
fail or jitter. Feeding a *cropped* region instead makes the distant hand fill
a much larger share of the model input, and capturing from a higher-res source
gives that crop real pixels to work with.

This module is the pure geometry: track the hand's last position, decide the
crop rect for the next frame, and — critically — remap the crop-relative
landmarks MediaPipe returns back into full-frame-normalized coordinates. That
remap is what keeps the cursor stable: the engine always sees full-frame space,
so a changing crop rect never makes the pointer jump.

Self-adaptive by design: a near hand produces a large box → expand+clamp gives
a crop ≈ the whole frame (no zoom, no harm); a far hand produces a tiny box →
crop tightens to `min_frac`, zooming in. So the ROI path is safe to leave on.

No cv2/numpy — all math on plain tuples, so test_gesture_roi.py exercises it
with a fake clock and synthetic landmarks (project convention: pure logic +
self-running harness).
"""

from __future__ import annotations

import os


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def hand_box(pts) -> tuple[float, float, float, float]:
    """Bounding box (x, y, w, h) in normalized coords of the given landmarks.
    `pts` are (x, y[, z]) already in full-frame-normalized space."""
    xs = [clamp01(p[0]) for p in pts]
    ys = [clamp01(p[1]) for p in pts]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return (x0, y0, x1 - x0, y1 - y0)


def expand_box(box, scale: float, min_frac: float) -> tuple[float, float, float, float]:
    """Grow a normalized box about its centre by `scale`, floor each side at
    `min_frac` of the frame, clamp to 1.0, then shift fully inside [0, 1]."""
    x, y, w, h = box
    cx, cy = x + w / 2.0, y + h / 2.0
    w = min(max(w * scale, min_frac), 1.0)
    h = min(max(h * scale, min_frac), 1.0)
    x = min(max(cx - w / 2.0, 0.0), 1.0 - w)
    y = min(max(cy - h / 2.0, 0.0), 1.0 - h)
    return (x, y, w, h)


def to_px(box, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Normalized box -> integer pixel rect (rx, ry, rw, rh), clamped to frame,
    every side at least 1 px, and never running past the frame edge."""
    x, y, w, h = box
    rw = max(1, min(frame_w, int(round(w * frame_w))))
    rh = max(1, min(frame_h, int(round(h * frame_h))))
    rx = min(max(0, int(round(x * frame_w))), frame_w - rw)
    ry = min(max(0, int(round(y * frame_h))), frame_h - rh)
    return (rx, ry, rw, rh)


def remap_landmarks(raw, crop_px, frame_w: int, frame_h: int):
    """Landmarks normalized to the crop -> normalized to the full frame.

    MediaPipe returns coords in [0, 1] of whatever image it saw (the crop);
    this puts them back in full-frame space so the engine's mapping is
    continuous regardless of how the crop rect moves. `z` (if present) is a
    relative depth, not a spatial coord — passed through untouched."""
    rx, ry, rw, rh = crop_px
    out = []
    for p in raw:
        fx = (rx + p[0] * rw) / frame_w
        fy = (ry + p[1] * rh) / frame_h
        out.append((fx, fy) + tuple(p[2:]))
    return out


def face_anchored_box(face_box_norm, down: float = 0.35,
                      scale: float = 3.0) -> tuple[float, float, float, float]:
    """A search box around and below the face — where a gesturing hand most
    likely is when no hand is being tracked yet. `face_box_norm` is the face's
    (x, y, w, h) in normalized full-frame coords."""
    fx, fy, fw, fh = face_box_norm
    cx = fx + fw / 2.0
    cy = fy + fh / 2.0 + down
    w = min(fw * scale, 1.0)
    h = min(fh * scale, 1.0)
    x = min(max(cx - w / 2.0, 0.0), 1.0 - w)
    y = min(max(cy - h / 2.0, 0.0), 1.0 - h)
    return (x, y, w, h)


class RoiTracker:
    """Stateful hand-follow crop planner (pure geometry; no cv2/numpy).

    update(hand_box) each time a hand is found, miss() when none is, then ask
    next_crop() for the pixel rect to feed MediaPipe next frame (None = use the
    full frame). Grows the crop after `widen_after` misses (hand moved out of
    the box) and drops back to a full-frame rescan after `reset_after`.
    """

    def __init__(self, expand: float = 1.9, min_frac: float = 0.30,
                 follow: float = 0.6, widen_after: int = 3, reset_after: int = 8):
        self.expand = expand
        self.min_frac = min_frac
        self.follow = follow          # 0..1, higher = snappier box tracking
        self.widen_after = widen_after
        self.reset_after = reset_after
        self.box: tuple[float, float, float, float] | None = None
        self.misses = 0

    @classmethod
    def from_env(cls) -> "RoiTracker":
        def f(name, default):
            try:
                return float(os.getenv(name, ""))
            except (TypeError, ValueError):
                return default

        def i(name, default):
            try:
                return int(os.getenv(name, ""))
            except (TypeError, ValueError):
                return default

        return cls(
            expand=f("JARVIS_ROI_EXPAND", 1.9),
            min_frac=f("JARVIS_ROI_MIN_FRAC", 0.30),
            follow=f("JARVIS_ROI_FOLLOW", 0.6),
            widen_after=i("JARVIS_ROI_WIDEN_AFTER", 3),
            reset_after=i("JARVIS_ROI_RESET_AFTER", 8),
        )

    def update(self, hand_box_norm) -> None:
        """A hand was detected (box in full-frame-normalized coords)."""
        if self.box is None:
            self.box = tuple(hand_box_norm)
        else:
            a = self.follow
            self.box = tuple(o + a * (n - o) for o, n in zip(self.box, hand_box_norm))
        self.misses = 0

    def miss(self) -> None:
        """No hand this frame; drop the anchor after enough consecutive misses."""
        self.misses += 1
        if self.misses >= self.reset_after:
            self.box = None

    def next_crop(self, frame_w: int, frame_h: int, face_box_norm=None):
        """Pixel rect to feed the detector next frame, or None for full frame."""
        if self.box is not None and self.misses < self.reset_after:
            # after widen_after misses the hand likely left the box — grow it
            grow = 1.0
            if self.misses >= self.widen_after:
                grow = 1.0 + 0.5 * (self.misses - self.widen_after + 1)
            b = expand_box(self.box, self.expand * grow, self.min_frac)
            return to_px(b, frame_w, frame_h)
        if face_box_norm is not None:
            return to_px(face_anchored_box(face_box_norm), frame_w, frame_h)
        return None
