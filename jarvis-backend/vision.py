import cv2
from deepface import DeepFace
import os
import time

# =========================
# LOAD KNOWN IDENTITIES
# =========================
def get_known_identities(known_faces_dir="known_faces"):
    identities = {}

    if not os.path.exists(known_faces_dir):
        os.makedirs(known_faces_dir)
        print(f"[VISION] Created {known_faces_dir}/ directory. Add images.")
        return identities

    for filename in os.listdir(known_faces_dir):
        if filename.lower().endswith((".jpg", ".jpeg", ".png")):
            name = os.path.splitext(filename)[0].upper()
            identities[name] = os.path.join(known_faces_dir, filename)

    return identities


# =========================
# FRAME SOURCES
# =========================
# Two ways to get frames, one `.read()` contract, so the scan loop below doesn't
# branch: either the shared bus (the gesture daemon owns the camera) or our own
# capture (nobody does). See modules/frame_bus.py for why sharing is mandatory.

class _BusFrames:
    """Frames off the shared bus — never opens a camera."""

    def __init__(self, poll_s: float = 0.02):
        from modules import frame_bus
        self._bus = frame_bus
        self._seq = 0          # only ever consume frames we haven't seen
        self._poll_s = poll_s

    def read(self):
        got = self._bus.latest(after_seq=self._seq)
        if got is None:
            # no NEW frame yet (or the owner went away). Sleep briefly so the
            # 10s scan window isn't burned spinning on a slow publisher — the
            # daemon drops to ~2fps when the desk is locked.
            time.sleep(self._poll_s)
            return False, None
        frame, self._seq = got
        return True, frame

    def release(self):
        pass               # we never owned the camera, nothing to hand back


class _CapFrames:
    """Frames from a capture this scan opened itself.

    It PUBLISHES what it reads: owning the camera makes this scan the bus owner
    for its duration, so anything else that wants frames (the HUD's live
    face-auth feed, ambient vision) reads them here instead of opening a second
    capture the phone stream can't serve. Symmetric with gesture_daemon.
    """

    def __init__(self, cap, source: str | None = None):
        self._cap = cap
        self._source = source

    def read(self):
        ret, frame = self._cap.read()
        if ret and frame is not None:
            from modules import frame_bus
            frame_bus.publish(frame, source=self._source)
        return ret, frame

    def release(self):
        try:
            self._cap.release()
        except Exception:
            pass
        # we were the owner — don't leave readers thinking a camera is live.
        # (If the gesture daemon started mid-scan it re-publishes within a frame,
        # so clearing here can only ever cost one frame of staleness.)
        from modules import frame_bus
        frame_bus.clear()


# =========================
# MAIN SCAN FUNCTION
# =========================
def scan_for_faces(timeout=10, on_phase=None):
    """Scan the shared camera for a known face.

    `on_phase(stage, box=None, frame_size=None)` is an OPTIONAL progress callback
    (G6.1): it fires with "matching" + the pixel face box the moment the Haar
    pass finds a face and DeepFace verification starts, and with "scanning" again
    if that face fails to match and the loop keeps looking. It exists so the
    FaceAuthOverlay can show the real phases instead of a self-timed animation.
    Called from THIS thread (the scan runs in to_thread), so the callback must be
    thread-safe — main.py routes it through socket_manager.schedule_ui_update.
    Any exception it raises is swallowed: progress reporting must never be able
    to fail an authentication.
    """

    def phase(stage, box=None, frame_size=None):
        if on_phase is None:
            return
        try:
            on_phase(stage, box=box, frame_size=frame_size)
        except Exception as e:      # noqa: BLE001 — never fail auth over a UI frame
            print(f"[VISION] on_phase({stage}) failed: {e}")

    identities = get_known_identities()
    if not identities:
        print("[VISION] No known faces found.")
        return None

    print("[VISION] Connecting to Camera...")

    # Camera source resolves EXACTLY like the gesture stack (JARVIS_CAM_SOURCES
    # priority list, legacy JARVIS_CAM fallback). This was a hardcoded
    # 192.168.0.106:8080 — a third phone IP that drifted stale, so every face
    # scan bailed with "Camera unreachable" even while the gesture daemon was
    # streaming fine from a different address. open_first_available keeps the
    # fast-fail property the old urllib ping was there for (TCP probe per URL,
    # ~1.5 s, never lets cv2 hang) and additionally frame-validates, so a
    # connected-but-stalled stream is skipped instead of scanned blindly.
    from modules.gesture_camera import (CameraError, open_first_available,
                                        parse_sources)

    # PREFER THE SHARED BUS. If the gesture daemon already owns the camera, read
    # its frames instead of opening a second capture: doing that killed the
    # daemon's stream outright ("camera stream died (30 consecutive read
    # failures)", measured 2026-07-25), and since face-auth runs at every wake it
    # dropped gesture control precisely when the owner walked up. Only when no
    # owner is publishing (daemon off, JARVIS_GESTURE=0, camera still coming up)
    # do we open our own.
    from modules import frame_bus

    if frame_bus.active():
        provider = _BusFrames()
        print(f"[VISION] camera source: {frame_bus.source()} (shared with gesture daemon)")
    else:
        sources = parse_sources(os.getenv("JARVIS_CAM_SOURCES"), os.getenv("JARVIS_CAM"))
        try:
            cap, chosen = open_first_available(sources, probe_timeout=1.5)
        except CameraError as e:
            print(f"[VISION] Camera unreachable, skipping facial scan. [{e.kind}] {e}")
            return None
        provider = _CapFrames(cap, source=str(chosen))
        print(f"[VISION] camera source: {chosen}")

    # Haar Cascade (fast)
    cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    face_cascade = cv2.CascadeClassifier(cascade_path)

    start_time = time.time()
    detected_name = None

    frame_count = 0
    matching_reported = False

    # 🔥 cache system
    last_detected = None
    last_time = 0
    cooldown = 3  # seconds

    print("[VISION] Scanning...")

    while time.time() - start_time < timeout:
        ret, frame = provider.read()
        if not ret:
            continue

        frame_count += 1

        # 🔥 skip frames (reduce CPU)
        if frame_count % 8 != 0:
            continue

        # 🔥 cooldown (skip heavy processing)
        if time.time() - last_time < cooldown:
            continue

        # 🔥 downscale frame (BIG speed boost)
        small_frame = cv2.resize(frame, (320, 240))
        gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)

        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=6,
            minSize=(80, 80)
        )

        if len(faces) > 0:
            print("[VISION] Face detected")
            matching_reported = True

            # scale back to original frame
            (x, y, w, h) = faces[0]
            x, y, w, h = int(x * (frame.shape[1] / 320)), int(y * (frame.shape[0] / 240)), int(w * (frame.shape[1] / 320)), int(h * (frame.shape[0] / 240))

            face_crop = frame[y:y+h, x:x+w]

            if face_crop.size == 0:
                continue

            # real "matching" phase: a face IS on camera and recognition is
            # running. The overlay lifts off its idle scan loop here.
            phase("matching", box=(x, y, w, h),
                  frame_size=(frame.shape[1], frame.shape[0]))

            temp_path = "temp_face.jpg"
            cv2.imwrite(temp_path, face_crop)

            print("[VISION] Verifying...")

            best_match = None
            best_score = 1.0

            for name, img_path in identities.items():
                try:
                    result = DeepFace.verify(
                        img1_path=temp_path,
                        img2_path=img_path,
                        model_name="OpenFace",          # 🔥 lightweight model
                        detector_backend="opencv",      # 🔥 fastest
                        enforce_detection=False
                    )

                    dist = result['distance']

                    if dist < best_score:
                        best_score = dist
                        best_match = name

                    # 🔥 early exit
                    if dist < 0.4:
                        detected_name = name
                        break

                except Exception:
                    continue

            # fallback best match
            if not detected_name and best_score < 0.5:
                detected_name = best_match

            if os.path.exists(temp_path):
                os.remove(temp_path)

            if detected_name:
                print(f"[VISION] ✅ MATCH: {detected_name}")

                last_detected = detected_name
                last_time = time.time()

                break

            # a face was there but nobody we know — back to searching, so the
            # overlay doesn't sit on "matching" for the rest of the window.
            if matching_reported:
                matching_reported = False
                phase("scanning")

    provider.release()
    cv2.destroyAllWindows()

    if detected_name:
        return detected_name
    else:
        print("[VISION] ❌ No match")
        return None


# =========================
# TEST
# =========================
if __name__ == "__main__":
    scan_for_faces(timeout=15)