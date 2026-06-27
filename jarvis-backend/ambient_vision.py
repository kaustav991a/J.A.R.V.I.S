"""
Ambient vision daemon — shared optical cache has **no heavy imports** so brain /
background_monitor / pytest can load without TensorFlow, DeepFace, YOLO, or OpenCV.
Vision stacks load lazily on first daemon loop iteration.
"""
import threading
import time
import os

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
}


class AmbientVisionDaemon:
    def __init__(self, camera_url="http://192.168.0.105:8080/video", interval=6.0):
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
        try:
            import urllib.request

            urllib.request.urlopen("http://192.168.0.105:8080", timeout=1.0)
            return True
        except Exception:
            return False

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
                continue

            shared_optical_cache["camera_active"] = True

            cap = cv2.VideoCapture(self.camera_url)
            if not cap.isOpened():
                continue

            ret, frame = cap.read()
            cap.release()

            if not ret or frame is None:
                continue

            detected_objects = set()
            person_boxes = []

            model = self._ensure_yolo()
            if model:
                results = model.predict(source=frame, imgsz=320, conf=0.4, verbose=False)
                if len(results) > 0:
                    boxes = results[0].boxes
                    for box in boxes:
                        cls_id = int(box.cls[0])
                        label = model.names[cls_id]
                        detected_objects.add(label)

                        if label == "person":
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            person_boxes.append((x1, y1, x2, y2))

            shared_optical_cache["objects_in_view"] = detected_objects

            detected_people = set()
            if person_boxes and self.identities:
                for (x1, y1, x2, y2) in person_boxes:
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
                    except Exception:
                        pass

                    if best_match:
                        detected_people.add(best_match)
                    else:
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
            shared_optical_cache["last_updated"] = time.time()


ambient_vision_daemon = AmbientVisionDaemon(interval=6.0)
