r"""
enroll_face.py — enrol the owner's face for the gesture/lock gate (G3/G4)
=========================================================================

Captures N *quality-gated, pose-diverse* face samples from the camera, computes
SFace embeddings and writes models/owner_embeddings.npz — the identity database
modules/face_gate.py matches against. A spread of angles/lighting beats the
single known_faces/kaustav.jpg that ambient_vision uses, and beats 12 near-
identical frames of you holding still.

G4 capture UX:
  * per-frame quality gate — rejects low-confidence, too-far, or clipped faces
    and blurry crops, with the reason shown on-screen ("MOVE CLOSER", etc.)
  * pose guidance — cycles prompts (look left / right / up / closer …) so the
    12 samples actually span angles
  * diversity gate — skips a frame that's near-identical to one already kept, so
    you can't bank 12 copies of the same pose (best-effort: after a few stuck
    seconds it accepts anyway, so enrolment always finishes)
  * post-capture report — pairwise-cosine stats + warnings (outlier frame? no
    diversity?) so a bad enrolment is caught before you rely on it

Run:   venv\Scripts\python.exe enroll_face.py            (camera, 12 samples)
       venv\Scripts\python.exe enroll_face.py --from-image known_faces\kaustav.jpg
Keys:  ESC abort · S save early (once >= 3 samples)
Env:   JARVIS_CAM / JARVIS_CAM_RES / JARVIS_CAM_MIRROR — same as gesture_spike.py
       JARVIS_OWNER            owner name stamped into the db (default KAUSTAV)
       JARVIS_ENROLL_MIN_FRAC  min face long-edge as frac of frame (default 0.11)
       JARVIS_ENROLL_MIN_SCORE min YuNet detector confidence (default 0.85)
       JARVIS_ENROLL_BLUR      min Laplacian variance, lower = blurrier (default 45)
"""

import math
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:  # standalone script — pull JARVIS_CAM etc. from .env like main.py does
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from modules.face_gate import DETECT_MODEL, OWNER_DB, RECOG_MODEL, cosine_best

SAMPLES = 12
GAP_S = 0.35        # min spacing so we don't grab 12 copies of one frame
STUCK_S = 5.0       # if diversity keeps rejecting this long, accept anyway
DUP_SIM = 0.955     # candidate this close to a kept sample = "no new angle"

# Rotated through as samples land, so the kept set spans real angles.
POSE_PROMPTS = [
    "look straight at the camera",
    "turn your head slightly LEFT",
    "turn slightly RIGHT",
    "tilt your chin UP a little",
    "tilt DOWN a little",
    "lean a bit CLOSER",
    "lean a bit BACK",
    "look straight again",
]


# --- pure helpers (no cv2 — unit-tested in test_enroll_face.py) ------------ #

def face_box_ok(face, frame_w: int, frame_h: int,
                min_frac: float = 0.11, min_score: float = 0.85):
    """Gate one YuNet detection row [x, y, w, h, ...landmarks..., score].

    Returns (ok: bool, reason: str). reason is a short on-screen hint when not ok.
    """
    x, y, w, h = float(face[0]), float(face[1]), float(face[2]), float(face[3])
    score = float(face[-1])
    if score < min_score:
        return False, "HOLD STILL"          # detector unsure — usually motion blur
    long_frac = max(w / frame_w, h / frame_h)
    if long_frac < min_frac:
        return False, "MOVE CLOSER"
    if x < 0 or y < 0 or x + w > frame_w or y + h > frame_h:
        return False, "CENTER YOUR FACE"    # box clipped by the frame edge
    return True, "ok"


def too_similar(feat, accepted, max_sim: float = DUP_SIM) -> bool:
    """True if this embedding is near-identical to one already kept (no new angle)."""
    if not accepted:
        return False
    return cosine_best(feat, accepted) > max_sim


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


def enroll_report(feats, outlier_floor: float = 0.30, dup_ceiling: float = 0.985) -> dict:
    """Pairwise-cosine health check over the captured set.

    A same-person set should sit comfortably above SFace's 0.363 same-person
    line; a pair far below that hints a stray face crept in, while an all-pairs-
    identical set means no angle diversity. Pure math — harness-testable.
    """
    n = len(feats)
    pairs = [_cos(feats[i], feats[j]) for i in range(n) for j in range(i + 1, n)]
    mn = min(pairs) if pairs else 1.0
    mx = max(pairs) if pairs else 1.0
    avg = sum(pairs) / len(pairs) if pairs else 1.0
    warnings = []
    if pairs and mn < outlier_floor:
        warnings.append(
            f"possible outlier frame (min pairwise {mn:.2f} < {outlier_floor}) — "
            "a non-owner face may have been captured; re-enroll if the gate misfires")
    if pairs and mn > dup_ceiling:
        warnings.append(
            f"samples nearly identical (min pairwise {mn:.2f}) — move your head "
            "more next time so the gate generalises across angles")
    return {"n": n, "min": mn, "max": mx, "avg": avg, "warnings": warnings}


# --- cv2-backed pieces (imported lazily so the pure helpers stay cv2-free) - #

def build_models():
    import cv2
    det = cv2.FaceDetectorYN_create(DETECT_MODEL, "", (320, 240), 0.7, 0.3, 5000)
    rec = cv2.FaceRecognizerSF_create(RECOG_MODEL, "")
    return det, rec


def largest_face(det, frame):
    h, w = frame.shape[:2]
    det.setInputSize((w, h))
    _, faces = det.detect(frame)
    if faces is None or not len(faces):
        return None
    return max(faces, key=lambda f: f[2] * f[3])


def blur_var(rec, frame, face) -> float:
    """Laplacian variance of the aligned crop — low = blurry."""
    import cv2
    aligned = rec.alignCrop(frame, face)
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def save(feats, owner):
    import numpy as np
    os.makedirs(os.path.dirname(OWNER_DB), exist_ok=True)
    np.savez(OWNER_DB, embeddings=np.array(feats), name=owner)
    print(f"saved {len(feats)} embeddings for {owner} -> {OWNER_DB}")


def main() -> int:
    import cv2

    owner = os.getenv("JARVIS_OWNER", "KAUSTAV").upper()
    min_frac = float(os.getenv("JARVIS_ENROLL_MIN_FRAC", "0.11"))
    min_score = float(os.getenv("JARVIS_ENROLL_MIN_SCORE", "0.85"))
    min_blur = float(os.getenv("JARVIS_ENROLL_BLUR", "45"))
    det, rec = build_models()

    # --- single-image seed path (unchanged) ---
    if len(sys.argv) > 2 and sys.argv[1] == "--from-image":
        img = cv2.imread(sys.argv[2])
        if img is None:
            print(f"FAIL: cannot read {sys.argv[2]}")
            return 1
        face = largest_face(det, img)
        if face is None:
            print("FAIL: no face found in the image")
            return 1
        feat = rec.feature(rec.alignCrop(img, face)).flatten()
        save([feat], owner)
        return 0

    from modules.gesture_camera import CameraError, FrameSource

    _src = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("JARVIS_CAM", "0")).strip()
    source = int(_src) if _src.isdigit() else _src
    try:
        fs = FrameSource(source, 640, 480,
                         url_res=os.getenv("JARVIS_CAM_RES", "640x480") or None)
    except CameraError as e:
        print(f"FAIL[{e.kind}]: {e}")
        return 1

    mirror = os.getenv("JARVIS_CAM_MIRROR", "1") == "1"
    feats, seq, last_t = [], 0, 0.0
    print(f"enrolling {owner}: follow the on-screen prompt; move your head between "
          f"samples. ESC aborts, S saves early (>= 3).")
    try:
        while len(feats) < SAMPLES:
            seq, frame = fs.read_new(seq, timeout=2.0)
            if frame is None:
                continue
            if mirror:
                frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]
            face = largest_face(det, frame)
            now = time.monotonic()
            prompt = POSE_PROMPTS[len(feats) % len(POSE_PROMPTS)]
            hint = "SHOW YOUR FACE"
            box_color = (0, 165, 255)  # amber = not yet captured this frame

            if face is not None:
                ok, reason = face_box_ok(face, w, h, min_frac, min_score)
                if not ok:
                    hint = reason
                elif blur_var(rec, frame, face) < min_blur:
                    hint = "HOLD STILL"       # sharp-enough crop required
                elif now - last_t >= GAP_S:
                    feat = rec.feature(rec.alignCrop(frame, face)).flatten()
                    stuck = (now - last_t) >= STUCK_S
                    if too_similar(feat, feats) and not stuck:
                        hint = "MOVE YOUR HEAD"
                    else:
                        feats.append(feat)
                        last_t = now
                        hint = "CAPTURED"
                        print(f"  sample {len(feats)}/{SAMPLES}  ({prompt})")
                else:
                    hint = "good — hold"
                x, y, bw, bh = (int(v) for v in face[:4])
                box_color = (0, 255, 204) if hint in ("CAPTURED", "good — hold") else (0, 165, 255)
                cv2.rectangle(frame, (x, y), (x + bw, y + bh), box_color, 2)

            cv2.putText(frame, f"ENROLL {owner}   {len(feats)}/{SAMPLES}", (10, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 204), 2)
            cv2.putText(frame, f"POSE: {prompt}", (10, 54),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(frame, hint, (10, h - 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, box_color, 2)
            cv2.imshow("JARVIS enroll", frame)
            k = cv2.waitKey(1) & 0xFF
            if k == 27:                       # ESC
                print("aborted")
                return 1
            if k in (ord("s"), ord("S")) and len(feats) >= 3:
                print(f"saving early with {len(feats)} samples")
                break
    finally:
        fs.release()
        cv2.destroyAllWindows()

    if len(feats) < 3:
        print(f"FAIL: only {len(feats)} samples — need at least 3. Nothing saved.")
        return 1

    report = enroll_report(feats)
    print(f"quality: {report['n']} samples, pairwise cosine "
          f"min={report['min']:.2f} avg={report['avg']:.2f} max={report['max']:.2f}")
    for w in report["warnings"]:
        print(f"  WARNING: {w}")

    save(feats, owner)
    print("verify: hold your face to the camera and run the gesture daemon — "
          "the HUD chip should show OWNER.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
