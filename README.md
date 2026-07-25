# J.A.R.V.I.S. — Locally-Hosted Autonomous AI Assistant

A fully autonomous, voice-activated, locally-hosted AI assistant inspired by Iron Man's J.A.R.V.I.S.

It pairs a cinematic React holographic HUD with a Python "nervous system" that does privacy-first local voice (wake word → streaming STT → reasoning → local TTS), continuous camera-based spatial awareness, hand-gesture mouse control, deep OS/app automation, a 4-tier memory system, and an always-on **cloud gateway** so you can reach JARVIS from Telegram even when this PC is off.

Cloud reasoning runs on **Groq** (with local Ollama/LLaVA fallback for vision), giving near-zero-latency conversation and multi-step ReAct planning.

---

## ⚙️ Architecture

Two processes, connected over WebSockets:

* **Backend (Python / FastAPI)** — the nervous system. Audio pipeline (wake word, STT, TTS, echo cancellation, full-duplex), LLM routing, computer vision, OS/app automation, the proactive daemon, and durable task queue. Entry point: `jarvis-backend/main.py` (ASGI app `main:app`).
* **Frontend (React / Vite, optionally Electron)** — the holographic HUD. Frameless kiosk UI with widgets, GSAP animation, and audio visualizers. Entry: `jarvis-frontend/`.
* **Cloud Gateway (separate, optional)** — `jarvis-backend/cloud_gateway.py`, a tiny self-contained FastAPI + Telegram bot deployed to a free always-on host (Render + UptimeRobot). Reachable when the desk PC is off. See [`jarvis-backend/CLOUD_GATEWAY.md`](jarvis-backend/CLOUD_GATEWAY.md).

---

## 🚀 Key Features

* **Privacy-first local voice pipeline**
  * *Wake word* — Picovoice Porcupine, ~0.1% CPU background listening + instant barge-in.
  * *Streaming STT* — **Vosk** for low-latency incremental transcription, with **faster-whisper** for high-accuracy batch transcription.
  * *Acoustic echo cancellation + full-duplex* — JARVIS hears you over its own voice and adapts mid-sentence.
  * *TTS* — local **Piper** streaming synthesis, Edge-TTS fallback.
* **Autonomy engine** — ReAct planner + self-healing worker loop, durable goal/task queue, overnight worker, and a guarded self-improvement loop (propose → branch → test → PR, never auto-merge).
* **Continuous spatial awareness** — YOLOv8 + DeepFace over IP cameras: who's present, emotional state, proactive UI lock when you leave frame.
* **Hand-gesture mouse control** — full in-air pointer via **MediaPipe Hands** (21 landmarks, CPU-only ~30 fps, 100% local). Open-palm engage/disengage gate (nothing hijacks the cursor by accident), index-finger cursor with One-Euro smoothing, pinch left/right/double-click, pinch-hold drag & drop, and scroll — injected through `ctypes SendInput` for 30 Hz latency. Pure-logic state machine (`gesture_engine.py`) is harness-tested; the loop runs as a supervised daemon (crash → auto-restart). Now with relative-trackpad + acceleration + clutch + dwell right-click and a calibration wizard (G5.1/G5.2). See [`JARVIS_MASTER_ROADMAP.md`](JARVIS_MASTER_ROADMAP.md).
* **Multi-window holographic HUD** — the full-screen kiosk HUD plus two ambient **Electron** modes: a top-center **Notch** (voice-visualizer orb + live status) and a right-edge **Sidecar** (clock, task queue, system vitals, calendar). All share one backend WebSocket.
* **4-tier memory** — RAM short-term → SQLite core facts → ChromaDB semantic vectors → episodic daily summaries. Plus a personal-document RAG cortex.
* **Deep OS / app control** — file, terminal, GUI, workspace, browser, macro, and OS agents; Android-TV control over ADB.
* **Digital life manager** — Google Calendar, Gmail, Google Fit via OAuth2.
* **Proactive daemon** — watches system load, fatigue, late hours, and calendar, and speaks up unprompted.
* **Remote access** — Telegram gateway on the desk (when PC on) + always-on cloud gateway (when PC off).

---

## 🛠️ Tech Stack

| Layer | Tech |
|---|---|
| Reasoning LLM | Groq Cloud (Llama 3.x) with key rotation; Ollama/LLaVA local fallback for vision |
| Streaming STT | Vosk (`vosk-model-small-en-us-0.15`) |
| Batch STT | faster-whisper (auto-downloaded) |
| TTS | Piper (`en-gb-alan-low`) + Edge-TTS fallback |
| Wake word | Picovoice Porcupine |
| Vision | Ultralytics YOLOv8 + DeepFace + OpenCV |
| Hand tracking | MediaPipe Hands (21 landmarks, CPU) + One-Euro filter → `ctypes SendInput` |
| Vector DB | ChromaDB |
| Backend | FastAPI + Uvicorn + WebSockets |
| Automation | pyautogui, pywinauto (UIA), pytesseract, psutil, Playwright, ADB |
| Frontend | React 18 + Vite + SCSS + GSAP (+ Electron shell) |

---

## 📂 Project Structure

```text
JARVIS-Project/
├── jarvis-backend/                 # Python nervous system
│   ├── main.py                     # FastAPI WebSocket server (ASGI app: main:app)
│   ├── watchdog.py                 # Supervisor: launches & auto-restarts main
│   ├── brain.py                    # LLM prompts, routing, parsing
│   ├── action_engine.py            # OS / system execution router
│   ├── ambient_vision.py           # YOLO / DeepFace vision loop
│   ├── background_monitor.py       # Proactive daemon
│   ├── cloud_gateway.py            # Always-on Telegram cloud brain (deploy separately)
│   ├── modules/                    # Specialized agents (stt, tts, planner, os/file/terminal/tv/github… )
│   │   ├── gesture_engine.py       # Pure hand-gesture state machine (landmarks → intents; harness-tested)
│   │   ├── gesture_pointer.py      # ctypes SendInput cursor backend (move/click/drag/scroll)
│   │   └── gesture_camera.py       # Shared frame source for hand tracking
│   ├── gesture_daemon.py           # Supervised hand-control loop (auto-restart)
│   ├── models/vosk/                # ⬇️ Vosk STT model  (NOT in git — see Fresh Install)
│   ├── en-gb-alan-low.onnx(.json)  # ⬇️ Piper voice      (NOT in git — see Fresh Install)
│   ├── yolov8n.pt                  # ⬇️ YOLO weights     (auto-downloads on first use)
│   ├── credentials/                # 🔒 Google OAuth client secret (NOT in git)
│   ├── *.db / *_chroma_db/         # 🔄 Memory stores (NOT in git — auto-created empty)
│   └── requirements.txt / requirements-cloud.txt
└── jarvis-frontend/                # React holographic HUD
    ├── src/                        # Components, App.jsx (WS client), HUD styling
    │   ├── NotchView.jsx           # Top-center notch window (voice orb + status)
    │   └── SidecarView.jsx         # Right-edge sidecar (clock, tasks, vitals, calendar)
    ├── electron/                   # Multi-window Electron shell (main.js, preload.js)
    └── package.json
```

---

## 🔧 Getting Started

### Prerequisites
- **Python 3.10+** (developed on 3.13)
- **Node.js 18+** (developed on 24)
- **Tesseract OCR** on PATH — <https://github.com/tesseract-ocr/tesseract>
- **ffmpeg** on PATH
- (Optional) **ADB** for Android-TV control, **Ollama** for local vision fallback

### 1. Backend
```bash
cd jarvis-backend
python -m venv venv
# Windows:      .\venv\Scripts\activate
# macOS/Linux:  source venv/bin/activate
pip install -r requirements.txt
```
Create `jarvis-backend/.env` (see **Configuration** below) with at least `GROQ_API_KEYS`.

Download the model assets that are **not** shipped in git (see **Fresh Install** — this is required for voice to work).

### 2. Frontend
```bash
cd jarvis-frontend
npm install
```

### 3. Run
Backend (choose one):
```bash
cd jarvis-backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000   # direct
# or, supervised with auto-restart:
python watchdog.py
```
Frontend:
```bash
cd jarvis-frontend
npm run dev            # browser HUD at http://localhost:5173
# or the desktop app:
npm run electron:dev
```
Open the HUD; it connects to the backend WebSocket on port 8000.

---

## 💾 Fresh Install / Disaster Recovery

> **Read this if you're cloning onto a new machine (e.g. after a disk failure).**
> The git repo intentionally does **not** contain large model binaries or your
> personal memory databases. Everything below is either downloadable or
> regenerated automatically — **none of it lives only in git.**

After `git clone` + the two dependency installs above, restore these:

| # | What | Where it goes | How to get it |
|---|------|---------------|---------------|
| 1 | **Vosk streaming-STT model** (~40 MB) | `jarvis-backend/models/vosk/` (extract so `models/vosk/am/`, `graph/`, `ivector/` sit directly inside), **or** point `JARVIS_VOSK_MODEL` at any extracted model dir | Download `vosk-model-small-en-us-0.15` from <https://alphacephei.com/vosk/models> and unzip. |
| 2 | **Piper voice** (~61 MB) | `jarvis-backend/en-gb-alan-low.onnx` **and** `en-gb-alan-low.onnx.json` (both files, same folder) | From <https://huggingface.co/rhasspy/piper-voices> → `en/en_GB/alan/low/`. |
| 3 | **faster-whisper model** | auto | Downloads itself from Hugging Face on first transcription and caches under `~/.cache`. No manual step. |
| 4 | **YOLOv8n weights** (`yolov8n.pt`) | `jarvis-backend/` | Auto-downloads via `ultralytics` on first vision use, or grab it from the Ultralytics releases. |
| 5 | **Google OAuth client secret** | `jarvis-backend/credentials/` (path via `JARVIS_GOOGLE_CREDENTIALS`) | From Google Cloud Console (Gmail/Calendar/Fit scopes). Only needed for those integrations. |
| 6 | **`.env`** | `jarvis-backend/.env` | Recreate from **Configuration** below — it is gitignored and never committed. |
| 7 | **Memory databases** (`jarvis_memory.db`, `jarvis_longterm.db`, `jarvis_tasks.db`, `memory/vector_db/`, `personal_chroma_db/`) | auto | **Auto-created empty on first run.** A fresh clone starts with a blank memory — this is expected. To carry memory across machines, copy these files from a backup manually. |

Then run as in **Run** above. Voice needs #1 and #2; the rest are optional or automatic.

---

## 🔑 Configuration (`.env`)

Only `GROQ_API_KEYS` (or `GROQ_API_KEY`) is required; everything else is optional.

```dotenv
# --- Required ---
GROQ_API_KEYS=key1,key2,key3        # comma-separated for rotation (or GROQ_API_KEY=single)

# --- LLM routing (optional) ---
GROQ_MODEL=llama-3.3-70b-versatile
JARVIS_LLM_MODE=cloud_first         # cloud_first | local_first
OLLAMA_URL=http://localhost:11434
OLLAMA_VISION_MODEL=llava
GEMINI_API_KEY=                     # optional extra fallback
ANTHROPIC_API_KEY=                  # optional (agent worker)

# --- Voice / vision (optional) ---
JARVIS_VOSK_MODEL=                  # override Vosk model dir (default: models/vosk/)
JARVIS_CAM_SOURCES=                 # camera priority list (comma: device indices and/or stream URLs).
                                    # ONE list for the whole stack — gestures, face scan and ambient
                                    # vision all follow it; first source that opens AND delivers a
                                    # frame wins, dead URLs skipped by a ~1.5s TCP probe.
JARVIS_CAMERA_URL=                  # pin ambient vision to one stream, ignoring the list above
JARVIS_CAM=                         # legacy single camera (device index or URL) — fallback when
                                    # JARVIS_CAM_SOURCES is unset
JARVIS_CAM_MIRROR=                  # 1 to flip horizontally if the gesture cursor moves inverted
JARVIS_GESTURE_OVERLAY=1            # cursor-halo/toast overlay (0 = off; the kill switch if
                                    # anything ever paints over the desktop)
JARVIS_OVERLAY_DEADMAN_S=20         # overlay self-exits after this long with no state frame
JARVIS_CAMERA_STREAM=1              # GET /api/camera/stream — MJPEG re-broadcast of the SHARED
                                    # camera for the HUD's live face-auth feed. Never opens a
                                    # camera itself (503 when nothing is publishing) and is
                                    # LOOPBACK-ONLY; 0 disables it outright. The HUD camera
                                    # panel reads this too — it never connects to the phone.
# Click-to-talk needs no env: POST /api/listen (the HUD mic button) asks the SERVER
# microphone to listen. The voice loops block inside recognizer.listen(), so the request
# is a one-shot flag consumed BETWEEN listen windows — expect ~3s awake / ~5s offline, and
# it expires after 15s. Offline it boots him exactly as saying "wake up" does (biometric
# path, never the admin bypass).
JARVIS_FACE_UNCERTAIN_FLOOR=0.25    # a non-owner face scoring above this is treated as "probably
                                    # the owner off-axis" and counts half towards a stranger alert
JARVIS_STRANGER_CONFIRM=3           # evidence needed before asserting a stranger (per face check,
                                    # ~0.5-1.5s apart) — stops one off-axis glance alerting on you
JARVIS_STRANGER_WINDOW_S=3          # gap that breaks the stranger streak
JARVIS_FULL_DUPLEX=1
JARVIS_AEC=1

# --- Presence (Track B: is he HOME even when not at the desk?) ---
# With neither of the first two set the probe never runs. Pin a fixed (non-random)
# MAC for the home SSID in Android WiFi settings — a MAC match survives DHCP moves.
JARVIS_PHONE_IP=                    # phone's LAN IP
JARVIS_PHONE_MAC=                   # phone's WiFi MAC (preferred signal)
JARVIS_PRESENCE_PORTS=8080,4747     # phone ports to try (IP Webcam, DroidCam)
JARVIS_PRESENCE_INTERVAL=60         # seconds between probes
JARVIS_PRESENCE_AWAY_GRACE=180      # unbroken miss streak before declaring AWAY (phones sleep
                                    # their WiFi radio — any hit means HOME immediately)

# --- Integrations (optional) ---
JARVIS_GOOGLE_CREDENTIALS=credentials/client_secret.json
OPENWEATHER_API_KEY=
TAVILY_API_KEY=                     # web search
JARVIS_TV_IP= / JARVIS_TV_NAME= / JARVIS_ADB_PATH=

# --- Remote gateways (optional) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_USER_ID=                   # your numeric Telegram id (admin)
TELEGRAM_GF_ID= / TELEGRAM_BROTHER_ID=   # VIP guest ids

# --- Level-3 desk↔cloud bridge (optional) ---
# When enabled, the always-on cloud gateway becomes the single Telegram front
# door and forwards messages to THIS desk (full PC control + real memory) over an
# authenticated socket; the desk stops polling Telegram directly. See below.
JARVIS_CLOUD_BRIDGE=0               # 1 to enable the bridge on this desk
JARVIS_BRIDGE_URL=                  # wss://<your-cloud>.onrender.com/desk-link
BRIDGE_SECRET=                      # shared secret; MUST match the cloud's BRIDGE_SECRET
```

The **cloud gateway** has its own env + deploy guide in [`jarvis-backend/CLOUD_GATEWAY.md`](jarvis-backend/CLOUD_GATEWAY.md).

---

## 🔐 Privacy & Security

* Wake word, transcription, synthesis, and facial recognition run **100% locally**. Only compressed text goes to the LLM API.
* Camera-based UI lockdown when the authenticated user leaves the frame.
* Secrets live only in the gitignored `.env` / `credentials/` — never committed. Personal memory databases are excluded from git (see Fresh Install).
* The cloud gateway verifies Telegram's `X-Telegram-Bot-Api-Secret-Token` on every webhook and holds **no PC-control powers** — chat/lookups only.

---

## 📜 Documentation

* [`CHANGELOG.md`](CHANGELOG.md) — release history
* [`JARVIS_MASTER_ROADMAP.md`](JARVIS_MASTER_ROADMAP.md) — **single source of truth**: what's done, what's left, decisions & constraints (merges the old roadmap / reliability / gesture / upgrades / mobile / login plans)
* [`TEST_PLAN.md`](TEST_PLAN.md) — automatic + manual test plan (pre-Electron)
* [`JARVIS_MANUAL.md`](JARVIS_MANUAL.md) — operation manual
* [`jarvis-backend/CLOUD_GATEWAY.md`](jarvis-backend/CLOUD_GATEWAY.md) — always-on Telegram deploy
