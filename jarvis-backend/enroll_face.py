r"""
enroll_face.py — enrol the owner's face for the gesture/lock gate (G3)
======================================================================

Captures N face samples from the camera, computes SFace embeddings and writes
models/owner_embeddings.npz — the identity database modules/face_gate.py
matches against. Multiple samples (angles, lighting) beat the single
known_faces/kaustav.jpg that ambient_vision uses.

Run:   venv\Scripts\python.exe enroll_face.py            (camera, 12 samples)
       venv\Scripts\python.exe enroll_face.py --from-image known_faces\kaustav.jpg
Env:   JARVIS_CAM / JARVIS_CAM_RES — same camera source as gesture_spike.py
       JARVIS_OWNER — owner name stamped into the db (default KAUSTAV)

Move your head slightly (left/right/near/far) while it captures. ESC aborts.
"""

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import cv2
import numpy as np

from modules.face_gate import DETECT_MODEL, OWNER_DB, RECOG_MODEL

SAMPLES = 12
GAP_S = 0.35  # min spacing between samples so they aren't 12 identical frames


def build_models():
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


def save(feats, owner):
    os.makedirs(os.path.dirname(OWNER_DB), exist_ok=True)
    np.savez(OWNER_DB, embeddings=np.array(feats), name=owner)
    print(f"saved {len(feats)} embeddings for {owner} -> {OWNER_DB}")


def main() -> int:
    owner = os.getenv("JARVIS_OWNER", "KAUSTAV").upper()
    det, rec = build_models()

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
    print(f"enrolling {owner}: look at the camera, move your head slightly…")
    try:
        while len(feats) < SAMPLES:
            seq, frame = fs.read_new(seq, timeout=2.0)
            if frame is None:
                continue
            if mirror:
                frame = cv2.flip(frame, 1)
            face = largest_face(det, frame)
            now = time.monotonic()
            if face is not None and now - last_t >= GAP_S:
                feats.append(rec.feature(rec.alignCrop(frame, face)).flatten())
                last_t = now
                print(f"  sample {len(feats)}/{SAMPLES}")
            if face is not None:
                x, y, w, h = (int(v) for v in face[:4])
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 204), 2)
            cv2.putText(frame, f"ENROLL {owner}  {len(feats)}/{SAMPLES}", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 204), 2)
            cv2.imshow("JARVIS enroll", frame)
            if (cv2.waitKey(1) & 0xFF) == 27:
                print("aborted")
                return 1
    finally:
        fs.release()
        cv2.destroyAllWindows()

    save(feats, owner)
    print("verify: hold your face to the camera and run the gesture daemon — "
          "the HUD chip should show OWNER.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
