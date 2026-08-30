"""
face_gate.py — Phase G3 owner gate for gesture control (CPU-cheap, ONNX)
========================================================================

Answers three questions from the SAME frames the gesture loop already has
(no second camera, no TensorFlow):

    1. Is the OWNER at the desk?          -> gestures allowed, PC unlocked
    2. Is a STRANGER showing hands?       -> deny gestures + phone alert
    3. Is anyone there at all / moving?   -> absence -> soft lock

Stack: YuNet face detector + SFace face recogniser — both ship inside
opencv(-contrib), tiny ONNX models in models/, a few ms per check on CPU.
Deliberately NOT DeepFace/TF (ambient_vision's stack): that is ~seconds per
verify; the gate must run every second or two inside a 30 fps loop.

Enrolment: enroll_face.py writes models/owner_embeddings.npz (N SFace
embeddings of the owner). Cosine similarity >= 0.363 = same person (the
SFace-standard threshold).

Pure-logic pieces (AbsenceTracker, cosine_best) have no cv2/numpy state and
are unit-tested in test_face_gate.py with a fake clock. Heavy imports stay
inside FaceGate so importing this module costs nothing (ambient_vision
pattern).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DETECT_MODEL = os.path.join("models", "face_detection_yunet_2023mar.onnx")
RECOG_MODEL = os.path.join("models", "face_recognition_sface_2021dec.onnx")
OWNER_DB = os.path.join("models", "owner_embeddings.npz")
COSINE_THRESHOLD = 0.363  # SFace-standard same-person threshold
# A face that misses the threshold but still scores this high is far more likely
# the owner off-axis than a different person: live, an off-axis owner lands in
# the high 0.2s/low 0.3s while a genuine stranger sits near 0.0-0.1. Such a face
# is still NOT the owner (gestures stay denied) — it is only counted as weaker
# evidence by StrangerConfirmer, so a head turn can't fire an intruder alert.
UNCERTAIN_FLOOR = float(os.getenv("JARVIS_FACE_UNCERTAIN_FLOOR", "0.25"))


def cosine_best(feat, owner_feats) -> float:
    """Best cosine similarity between one embedding and the owner set.
    Pure math (lists or numpy arrays) so the harness can test it without cv2."""
    import math

    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1e-9
        nb = math.sqrt(sum(x * x for x in b)) or 1e-9
        return dot / (na * nb)

    return max((cos(feat, o) for o in owner_feats), default=-1.0)


@dataclass
class GateResult:
    any_face: bool = False
    owner_present: bool = False
    stranger_present: bool = False   # a face that is NOT the owner
    owner_score: float = -1.0
    faces: int = 0
    face_box: tuple | None = None    # (x, y, w, h) px of the largest face (G5.4 ROI seed)
    top_score: float = -1.0          # best cosine seen this pass, match or not
    uncertain: bool = False          # the non-owner face scored >= UNCERTAIN_FLOOR


class StrangerConfirmer:
    """Debounce for "a stranger is at the desk".

    SFace flips the owner to `stranger` for a check or two whenever he is
    slightly off-axis — harmless while unlocked, but once the desk locks a
    single such check used to fire a Telegram snapshot of the owner himself.
    So require EVIDENCE, not one frame: each consecutive stranger check adds
    1.0 (or 0.5 when `uncertain`, i.e. the face scored close to the owner) and
    the stranger is asserted at `needed`. Any check without a stranger clears
    the streak, and so does a gap longer than `window_s` — two sightings a
    minute apart are not one person walking up.

    Pure logic, fake-clock testable; the daemon feeds it once per face check
    (NOT once per camera frame), so `needed` counts checks at the face cadence.
    """

    def __init__(self, needed: float = 3.0, window_s: float = 3.0,
                 uncertain_weight: float = 0.5):
        self.needed = max(1.0, float(needed))
        self.window_s = window_s
        self.uncertain_weight = uncertain_weight
        self.evidence = 0.0
        self.confirmed = False
        self._last_t: float | None = None

    def update(self, stranger_present: bool, t: float,
               uncertain: bool = False) -> bool:
        """Feed one face check; returns True while a stranger is confirmed."""
        if not stranger_present:
            self.evidence = 0.0
            self.confirmed = False
            self._last_t = t
            return False
        if self._last_t is not None and (t - self._last_t) > self.window_s:
            self.evidence = 0.0          # stale streak — restart from this check
        self.evidence += self.uncertain_weight if uncertain else 1.0
        self._last_t = t
        self.confirmed = self.evidence >= self.needed
        return self.confirmed

    def reset(self) -> None:
        self.evidence = 0.0
        self.confirmed = False
        self._last_t = None


class AbsenceTracker:
    """Owner-away logic with a motion fallback: a turned head loses the face
    but typing/leaning is still motion — only "no face AND no motion for
    absent_after_s" counts as away. Pure logic, fake-clock testable."""

    def __init__(self, absent_after_s: float = 6.0):
        self.absent_after_s = absent_after_s
        self._last_presence_t: float | None = None
        # Has a face - anyone's - actually been seen? The auto-lock is only
        # armed once one has, because clearing the lock requires one.
        self._saw_a_face = False

    def update(self, owner_present: bool, any_face: bool, moving: bool,
               t: float) -> bool:
        """Feed one observation; returns True when the desk counts as absent."""
        if owner_present or any_face or moving:
            self._last_presence_t = t
            if owner_present or any_face:
                self._saw_a_face = True
            return False
        if self._last_presence_t is None:  # never saw anyone yet — not "left"
            return False
        # **Never enter a state whose exit has never been demonstrated.**
        #
        # 2026-08-30: he was away from home. The daemon's phone-camera URLs were
        # unreachable, so it fell back to the built-in webcam pointed at an empty
        # room. No face was ever seen - but `moving` was, because a frame-diff
        # over a real sensor picks up light changes and noise. That armed the
        # tracker, stillness ran out the timer, and the desk soft-locked.
        #
        # The lock's exits are: be RECOGNISED by the camera, or type the code at
        # the keyboard. Both need a person at the desk. So a lock armed by motion
        # alone, with no face ever seen, is a lock nobody present can open - and
        # he ended up powering the machine off at the case.
        #
        # Motion still keeps a session alive (a turned head loses the face while
        # typing continues, which is the whole reason motion is here). It just
        # cannot be the ONLY evidence that there was ever someone to leave.
        if not self._saw_a_face:
            return False
        return (t - self._last_presence_t) >= self.absent_after_s

    def reset(self, t: float | None = None) -> None:
        self._last_presence_t = t


class MotionDetector:
    """Frame-difference motion score on a tiny grayscale image — ~0.1 ms."""

    def __init__(self, threshold: float = 6.0):
        self.threshold = threshold
        self._prev = None
        self.score = 0.0

    def update(self, frame_bgr) -> bool:
        import cv2

        small = cv2.cvtColor(cv2.resize(frame_bgr, (80, 60)), cv2.COLOR_BGR2GRAY)
        if self._prev is None:
            self._prev = small
            self.score = 0.0
            return False
        diff = cv2.absdiff(small, self._prev)
        self._prev = small
        self.score = float(diff.mean())
        return self.score > self.threshold


class FaceGate:
    """YuNet + SFace owner check. Call check(frame) at YOUR cadence (the
    gesture daemon decides per state — this class does one full pass per call)."""

    def __init__(self, detect_model: str = DETECT_MODEL,
                 recog_model: str = RECOG_MODEL,
                 owner_db: str = OWNER_DB,
                 threshold: float = COSINE_THRESHOLD,
                 max_faces: int = 3,
                 uncertain_floor: float = UNCERTAIN_FLOOR):
        self.threshold = threshold
        self.max_faces = max_faces
        self.uncertain_floor = uncertain_floor
        self._detect_model = detect_model
        self._recog_model = recog_model
        self._owner_db = owner_db
        self._detector = None
        self._recognizer = None
        self._owner_feats = None
        self._size = None
        self.available = None   # None = not initialised yet, then bool
        self.last = GateResult()
        # F-25. `check()` returns the PREVIOUS result on any fault, which means
        # `.last` cannot tell "a fresh pass saw no face" from "the camera is
        # blind and you are reading a verdict from a minute ago". A caller that
        # cannot tell those apart can arm a barrier on a stale reading and then
        # never satisfy the fresh reading needed to lift it — which is exactly
        # how the desk soft-lock trapped the owner at his own desk.
        #
        # This counter advances ONLY on a completed pass. A fault leaves it
        # alone, so a caller can watch it instead of trusting `.last`. A counter
        # rather than a timestamp on purpose: the daemon runs on
        # `time.perf_counter()` and mixing clocks across a module boundary is its
        # own bug.
        self.checks_ok = 0

    # ------------------------------------------------------------------ #

    def _ensure(self) -> bool:
        if self.available is not None:
            return self.available
        try:
            import cv2
            import numpy as np

            if not (os.path.exists(self._detect_model)
                    and os.path.exists(self._recog_model)):
                raise FileNotFoundError(
                    f"face models missing ({self._detect_model} / "
                    f"{self._recog_model}) — run tools to download opencv_zoo "
                    "YuNet + SFace models")
            db = np.load(self._owner_db)
            self._owner_feats = [f for f in db["embeddings"]]
            if not self._owner_feats:
                raise ValueError("owner_embeddings.npz is empty — run enroll_face.py")
            self._detector = cv2.FaceDetectorYN_create(
                self._detect_model, "", (320, 240), 0.7, 0.3, 5000)
            self._recognizer = cv2.FaceRecognizerSF_create(self._recog_model, "")
            self.available = True
        except Exception as e:  # noqa: BLE001 — gate degrades, never crashes the loop
            print(f"[FACE-GATE] unavailable: {e}", flush=True)
            self.available = False
        return self.available

    def check(self, frame_bgr) -> GateResult:
        """One detect+recognise pass. Returns (and caches on .last) the result.
        On any fault returns the previous result — the daemon's absence timer
        handles prolonged blindness."""
        if not self._ensure():
            return self.last
        try:
            import cv2
            import numpy as np

            h, w = frame_bgr.shape[:2]
            if self._size != (w, h):
                self._detector.setInputSize((w, h))
                self._size = (w, h)
            _, faces = self._detector.detect(frame_bgr)
            res = GateResult()
            if faces is not None and len(faces):
                # largest faces first; cap the per-check work
                faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
                res.faces = len(faces)
                res.any_face = True
                res.face_box = tuple(float(v) for v in faces[0][:4])  # largest, (x,y,w,h) px
                best_other = -1.0
                for f in faces[: self.max_faces]:
                    aligned = self._recognizer.alignCrop(frame_bgr, f)
                    feat = self._recognizer.feature(aligned).flatten()
                    score = float(max(
                        np.dot(feat, o) / ((np.linalg.norm(feat) or 1e-9)
                                           * (np.linalg.norm(o) or 1e-9))
                        for o in self._owner_feats))
                    res.top_score = max(res.top_score, score)
                    if score >= self.threshold:
                        res.owner_present = True
                        res.owner_score = max(res.owner_score, score)
                    else:
                        res.stranger_present = True
                        best_other = max(best_other, score)
                # a near-miss face is probably the owner off-axis, not an
                # intruder — still not the owner, but weaker alert evidence.
                res.uncertain = res.stranger_present and best_other >= self.uncertain_floor
            self.last = res
            self.checks_ok += 1     # a real verdict — see the note in __init__
            return res
        except Exception as e:  # noqa: BLE001
            print(f"[FACE-GATE] check fault: {e}", flush=True)
            # Deliberately does NOT advance `checks_ok`: the caller is about to
            # be handed a verdict that is not about the present moment, and it
            # must be able to know that.
            return self.last
