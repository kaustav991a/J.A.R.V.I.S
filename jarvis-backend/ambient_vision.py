"""
Ambient vision daemon — shared optical cache has **no heavy imports** so brain /
background_monitor / pytest can load without TensorFlow, DeepFace, YOLO, or OpenCV.
Vision stacks load lazily on first daemon loop iteration.
"""
import threading
import time
import os

# Camera (IP Webcam) endpoints — env-overridable so the IP can change without code edits.
# CAMERA_URL is the MJPEG video stream; CAMERA_BASE is the lightweight reachability ping.


def _default_camera_url() -> str:
    """First stream URL of the SHARED camera priority list.

    ambient_vision, gesture_daemon and vision.scan_for_faces must follow the same
    phone address; each used to carry its own hardcoded IP and they drifted apart
    (this one and vision.py both pointed at a 192.168.0.106 that no longer
    existed). Parsed by hand rather than via gesture_camera.parse_sources because
    this module deliberately has NO heavy imports (that one pulls in cv2).
    Device indices are skipped — an int index is meaningless as a URL.
    """
    raw = os.getenv("JARVIS_CAM_SOURCES") or os.getenv("JARVIS_CAM") or ""
    for part in raw.split(","):
        s = part.strip()
        if s and not s.isdigit():
            return s
    return "http://192.168.0.106:8080/video"


CAMERA_URL  = os.getenv("JARVIS_CAMERA_URL") or _default_camera_url()
CAMERA_BASE = os.getenv("JARVIS_CAMERA_BASE", CAMERA_URL.rsplit("/", 1)[0])

# --- SHARED CACHE ---
shared_optical_cache = {
    "objects_in_view": set(),
    "people_in_view": set(),
    "dominant_emotion": "neutral",
    "last_updated": 0,
    "camera_active": False,
    "last_person_seen_time": 0,
    "user_absent": False,
    "intruder_detected": False,
    "last_known_user": None,
    # --- HUD optical-feed overlay (the browser pulls the raw MJPEG itself; these
    #     let it draw JARVIS's detection boxes on top, scaled to the displayed video).
    "detections": [],   # list of {label, box:[x1,y1,x2,y2], conf, identity?, emotion?}
    "frame_w": 0,       # native width of the analysed frame (box coords are in this space)
    "frame_h": 0,
    "camera_url": CAMERA_URL,  # where the HUD should pull the live stream from
}


class AmbientVisionDaemon:
    def __init__(self, camera_url=CAMERA_URL, interval=6.0):
        self.camera_url = camera_url
        self.interval = interval
        self.base_interval = interval
        self.idle_interval = 10.0
        self.no_person_streak = 0
        self.intruder_streak = 0
        self.running = False
        self.thread = None
        self.model = None  # lazy YOLO
        self._yolo_failed = False
        self.identities = self._get_known_identities()

    def _ensure_yolo(self):
        if self._yolo_failed:
            return None
        if self.model is None:
            try:
                from ultralytics import YOLO

                print("[AMBIENT VISION] Loading YOLOv8n...", flush=True)
                self.model = YOLO("yolov8n.pt")
            except Exception as e:
                print(f"[AMBIENT VISION] Error loading YOLO: {e}")
                self._yolo_failed = True
                self.model = False
                return None
        return self.model if self.model is not False else None

    def _get_known_identities(self, known_faces_dir="known_faces"):
        identities = {}
        if not os.path.exists(known_faces_dir):
            return identities
        for filename in os.listdir(known_faces_dir):
            if filename.lower().endswith((".jpg", ".jpeg", ".png")):
                name = os.path.splitext(filename)[0].upper()
                identities[name] = os.path.join(known_faces_dir, filename)
        return identities

    def _check_camera(self):
        # If the gesture daemon is publishing, the camera is provably alive and
        # this HTTP ping is both redundant and a second connection to the phone.
        from modules import frame_bus

        if frame_bus.active():
            return True
        try:
            import urllib.request

            urllib.request.urlopen(CAMERA_BASE, timeout=1.0)
            return True
        except Exception:
            return False

    def _grab_frame(self, cv2):
        """One frame, preferring the shared bus over opening our own capture.

        This used to build a fresh VideoCapture every interval — a third reader
        on a phone MJPEG stream that only reliably serves one, which is what
        killed the gesture daemon mid-session. Falls back to its own capture when
        no owner is publishing (gesture daemon off / JARVIS_GESTURE=0).
        """
        from modules import frame_bus

        got = frame_bus.latest()
        if got is not None:
            return got[0]
        cap = cv2.VideoCapture(self.camera_url)
        if not cap.isOpened():
            return None
        ret, frame = cap.read()
        cap.release()
        return frame if ret else None

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._daemon_loop, daemon=True)
            self.thread.start()
            print("[AMBIENT VISION] Daemon started in background.", flush=True)

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

    def _daemon_loop(self):
        import cv2

        while self.running:
            time.sleep(self.interval)

            if not self._check_camera():
                shared_optical_cache["camera_active"] = False
                shared_optical_cache["objects_in_view"] = set()
                shared_optical_cache["people_in_view"] = set()
                shared_optical_cache["detections"] = []
                continue

            shared_optical_cache["camera_active"] = True

            frame = self._grab_frame(cv2)
            if frame is None:
                continue

            detected_objects = set()
            person_boxes = []
            detections = []
            fh, fw = frame.shape[:2]

            model = self._ensure_yolo()
            if model:
                results = model.predict(source=frame, imgsz=320, conf=0.4, verbose=False)
                if len(results) > 0:
                    boxes = results[0].boxes
                    for box in boxes:
                        cls_id = int(box.cls[0])
                        label = model.names[cls_id]
                        conf = float(box.conf[0])
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        detected_objects.add(label)

                        det = {"label": label, "box": [x1, y1, x2, y2], "conf": round(conf, 2)}
                        detections.append(det)

                        if label == "person":
                            # carry the det dict so identity/emotion can be attached below
                            person_boxes.append((x1, y1, x2, y2, det))

            shared_optical_cache["objects_in_view"] = detected_objects
            shared_optical_cache["frame_w"] = fw
            shared_optical_cache["frame_h"] = fh

            detected_people = set()
            if person_boxes and self.identities:
                for (x1, y1, x2, y2, det) in person_boxes:
                    h, w, _ = frame.shape
                    x1 = max(0, x1 - 20)
                    y1 = max(0, y1 - 20)
                    x2 = min(w, x2 + 20)
                    y2 = min(h, y2 + 20)

                    face_crop = frame[y1:y2, x1:x2]
                    if face_crop.size == 0:
                        continue

                    temp_path = "temp_ambient_face.jpg"
                    cv2.imwrite(temp_path, face_crop)

                    best_match = None
                    best_score = 0.5

                    for name, img_path in self.identities.items():
                        try:
                            from deepface import DeepFace

                            res = DeepFace.verify(
                                img1_path=temp_path,
                                img2_path=img_path,
                                model_name="OpenFace",
                                detector_backend="opencv",
                                enforce_detection=False,
                            )
                            dist = res["distance"]
                            if dist < best_score:
                                best_score = dist
                                best_match = name
                            if dist < 0.4:
                                break
                        except Exception:
                            continue

                    if os.path.exists(temp_path):
                        os.remove(temp_path)

                    try:
                        from modules.emotion_detector import analyze_facial_emotion

                        emotion = analyze_facial_emotion(face_crop)
                        if emotion:
                            shared_optical_cache["dominant_emotion"] = emotion
                            det["emotion"] = emotion
                    except Exception:
                        pass

                    if best_match:
                        det["identity"] = best_match
                        detected_people.add(best_match)
                    else:
                        det["identity"] = "Unknown Person"
                        detected_people.add("Unknown Person")

            if detected_people:
                shared_optical_cache["last_person_seen_time"] = time.time()
                self.no_person_streak = 0
                self.interval = self.base_interval

                known_people = [p for p in detected_people if p != "Unknown Person"]
                unknown_people = [p for p in detected_people if p == "Unknown Person"]

                if known_people:
                    shared_optical_cache["last_known_user"] = known_people[0]
                    shared_optical_cache["user_absent"] = False
                    shared_optical_cache["intruder_detected"] = False
                    self.intruder_streak = 0

                if unknown_people and not known_people:
                    self.intruder_streak += 1
                    if self.intruder_streak >= 2:
                        shared_optical_cache["intruder_detected"] = True
                else:
                    self.intruder_streak = 0
                    shared_optical_cache["intruder_detected"] = False
            else:
                shared_optical_cache["dominant_emotion"] = "neutral"
                self.no_person_streak += 1

                if self.no_person_streak >= 3:
                    self.interval = self.idle_interval

                last_seen = shared_optical_cache.get("last_person_seen_time", 0)
                if last_seen > 0 and (time.time() - last_seen) > 30:
                    if shared_optical_cache.get("last_known_user"):
                        shared_optical_cache["user_absent"] = True

            shared_optical_cache["people_in_view"] = detected_people
            shared_optical_cache["detections"] = detections
            shared_optical_cache["last_updated"] = time.time()


ambient_vision_daemon = AmbientVisionDaemon(interval=6.0)
