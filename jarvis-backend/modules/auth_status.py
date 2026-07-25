r"""
auth_status.py — G6.1 login/wake face-auth status contract
==========================================================

The old FaceScanOverlay was self-timed (setTimeout 1500/4000ms) and never synced
to the real 10s vision.scan_for_faces — so the animation "finished" while the
scan was still running and just vanished with no success/fail. This is the small
ADDITIVE contract that drives a real, gated overlay (FaceAuthOverlay): the
backend emits a frame at each real transition and the overlay HOLDS the scan
animation until success/fail actually arrives — it never outruns reality.

Frames are plain status dicts (same channel as every other UI frame):

    {"status": "auth_face_start"}                    camera activating
    {"status": "auth_face_scanning"}                 scan running — overlay holds here
    {"status": "auth_face_success", "user": "KAUSTAV"}   matched -> green lock-on
    {"status": "auth_face_fail", "reason": "no_match"}    no match -> red reject

    {"status": "auth_face_matching", "box": [x, y, w, h]}  a face was found,
                                                           recognition running

The legacy `security_locked` + "OPTICAL SENSORS" frame is kept alongside these
(it still drives the security barrier and is the fallback for an un-updated
frontend).

`matching` is emitted from INSIDE `vision.scan_for_faces` via its `on_phase`
callback the moment the Haar pass finds a face and DeepFace verification starts,
and it reverts to `scanning` if that face fails to match and the loop keeps
looking. `box` is NORMALISED (0..1 fractions of frame width/height) so the
overlay can draw it over any feed size without knowing the capture resolution.

Pure builder (no I/O) so test_auth_status.py can assert the frame shapes.
"""

from __future__ import annotations

# Every stage the overlay understands.
VALID_STAGES = ("start", "scanning", "matching", "success", "fail")


def normalise_box(box, frame_w: int, frame_h: int) -> list | None:
    """Pixel `(x, y, w, h)` -> `[x, y, w, h]` as 0..1 fractions, clamped.

    The overlay renders the feed at whatever size the layout gives it, so the
    box has to travel resolution-independent. Returns None for a degenerate box
    (zero-size frame, empty rect) rather than emitting a divide-by-zero or a
    rectangle the overlay would draw at the origin.
    """
    if not box or len(box) != 4 or not frame_w or not frame_h:
        return None
    x, y, w, h = (float(v) for v in box)
    if w <= 0 or h <= 0:
        return None

    def frac(v, span):
        return max(0.0, min(1.0, v / float(span)))

    nx, ny = frac(x, frame_w), frac(y, frame_h)
    # clamp the extent to what is left of the frame, so a box the detector ran
    # off the edge stays inside 0..1 instead of overflowing the overlay.
    return [round(nx, 4), round(ny, 4),
            round(min(frac(w, frame_w), 1.0 - nx), 4),
            round(min(frac(h, frame_h), 1.0 - ny), 4)]


def face_frame(stage: str, user: str | None = None, reason: str | None = None,
               box=None) -> dict:
    """Build an additive face-auth status frame. `user` rides on success,
    `reason` on fail, `box` (normalised) on matching; all omitted when None so
    the frame stays minimal."""
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown auth-face stage: {stage!r} (valid: {VALID_STAGES})")
    frame: dict = {"status": f"auth_face_{stage}"}
    if user is not None:
        frame["user"] = user
    if reason is not None:
        frame["reason"] = reason
    if box is not None:
        frame["box"] = list(box)
    return frame
