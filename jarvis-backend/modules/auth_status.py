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

The legacy `security_locked` + "OPTICAL SENSORS" frame is kept alongside these
(it still drives the security barrier and is the fallback for an un-updated
frontend). A real "matching" phase (fired when a face box is first found, with
the box coords + live feed) is a follow-up — it needs an on_phase callback inside
scan_for_faces, which this contract leaves room for but does not yet require.

Pure builder (no I/O) so test_auth_status.py can assert the frame shapes.
"""

from __future__ import annotations

# Every stage the overlay understands. "matching" is reserved for the follow-up
# that surfaces the detected face box mid-scan.
VALID_STAGES = ("start", "scanning", "matching", "success", "fail")


def face_frame(stage: str, user: str | None = None, reason: str | None = None) -> dict:
    """Build an additive face-auth status frame. `user` rides on success,
    `reason` on fail; both are omitted when None so the frame stays minimal."""
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown auth-face stage: {stage!r} (valid: {VALID_STAGES})")
    frame: dict = {"status": f"auth_face_{stage}"}
    if user is not None:
        frame["user"] = user
    if reason is not None:
        frame["reason"] = reason
    return frame
