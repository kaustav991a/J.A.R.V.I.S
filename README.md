# J.A.R.V.I.S
# J.A.R.V.I.S. (Locally Hosted AI Assistant)

A voice-activated, locally hosted AI development co-pilot featuring a React-based cinematic HUD, offline wake-word detection, and native OS window manipulation. Powered entirely by local LLMs via Ollama to ensure absolute privacy and zero-latency context processing.

---

## ⚙️ Core Architecture

The system is divided into two distinct environments connected via WebSockets:

* **The Backend (Python/FastAPI):** Acts as the Central Nervous System. Handles audio processing, LLM inference, native OS manipulation, and automated workflows.
* **The Frontend (React/Vite):** Acts as the Holographic HUD. A frameless, full-screen kiosk interface featuring a Web Audio API-driven reactive plasma core.

---

## 🚀 Key Features

* **Offline Wake Word:** Lightweight, CPU-friendly standby mode that listens for specific acoustic phrases ("Wake up", "Daddy's home") without streaming audio to the cloud.
* **Two-Tier Memory System:** * *Short-Term RAM:* Sliding-window context for active conversations.
    * *Long-Term Core:* SQLite database for autonomous retention of developer preferences, tech stacks, and workflows.
* **Cascading Window Manager:** Uses native Win32 APIs (`pygetwindow`) to physically control, resize, and cascade 3rd-party OS applications (VS Code, Photoshop) directly from voice commands.
* **Design-to-Code Pipeline:** Direct integration with the Figma REST API to autonomously slice, download, and route named `.png` assets into local project directories.
* **Silent Override ("Stealth Mode"):** An absolute-priority "Mute" command that instantly hides the React UI, pauses web media, and suppresses audio feedback while allowing heavy background tasks (compiling, downloading) to finish silently.

---

## 🛠️ Tech Stack

**AI & Processing**
* **LLM:** Llama 3 (8B) via [Ollama](https://ollama.com/)
* **Wake Word:** Picovoice Porcupine (Offline)
* **Speech-to-Text / TTS:** SpeechRecognition / pyttsx3

**Backend (jarvis-backend)**
* **Framework:** FastAPI + Uvicorn
* **Communication:** WebSockets
* **OS Integration:** `psutil` (Hardware Telemetry), `pygetwindow` (Spatial Window Management)

**Frontend (jarvis-frontend)**
* **Framework:** React 18 + Vite
* **Styling:** SCSS + CSS Modules
* **Animation:** GSAP & Three.js (Reactive Visualizer)

---

## 📂 Project Structure

```text
JARVIS-PROJECT/
├── jarvis-backend/               # Python Central Nervous System
│   ├── venv/                     # Virtual Environment
│   ├── main.py                   # FastAPI WebSocket Server
│   ├── brain.py                  # Ollama / Llama 3 Integration
│   ├── action_engine.py          # OS & System Execution
│   └── recorder.py               # Audio Capture & Wake Word
│
├── jarvis-frontend/              # React Holographic HUD
│   ├── src/
│   │   ├── components/           # React Widgets (Visualizer, Status)
│   │   ├── App.jsx               # WebSocket Client & State
│   │   └── index.css             # HUD Sci-Fi Styling
│   ├── package.json
│   └── vite.config.js
│
├── .gitignore                    # Global ignore (node_modules, venv)
└── README.md                     # Project documentation
