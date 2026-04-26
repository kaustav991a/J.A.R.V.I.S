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
# MAIN SCAN FUNCTION
# =========================
def scan_for_faces(timeout=10):

    identities = get_known_identities()
    if not identities:
        print("[VISION] No known faces found.")
        return None

    print("[VISION] Connecting to Camera...")

    camera_url = "http://10.14.124.8:8080/video"
    
    # --- NEW: Fast Camera Ping ---
    # Quickly check if the camera is reachable to avoid OpenCV's long timeout freeze
    try:
        import urllib.request
        # Check the base URL instead of the video stream so it doesn't download the stream
        urllib.request.urlopen("http://10.14.124.8:8080", timeout=1.0)
    except Exception as e:
        print(f"[VISION] Camera unreachable, skipping facial scan. ({e})")
        return None

    cap = cv2.VideoCapture(camera_url)

    if not cap.isOpened():
        print("[VISION] ERROR: Cannot access camera.")
        return None

    # Haar Cascade (fast)
    cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    face_cascade = cv2.CascadeClassifier(cascade_path)

    start_time = time.time()
    detected_name = None

    frame_count = 0

    # 🔥 cache system
    last_detected = None
    last_time = 0
    cooldown = 3  # seconds

    print("[VISION] Scanning...")

    while time.time() - start_time < timeout:
        ret, frame = cap.read()
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

            # scale back to original frame
            (x, y, w, h) = faces[0]
            x, y, w, h = int(x * (frame.shape[1] / 320)), int(y * (frame.shape[0] / 240)), int(w * (frame.shape[1] / 320)), int(h * (frame.shape[0] / 240))

            face_crop = frame[y:y+h, x:x+w]

            if face_crop.size == 0:
                continue

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

    cap.release()
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