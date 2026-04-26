# J.A.R.V.I.S. (Locally Hosted AI Assistant)

A fully autonomous, voice-activated, and locally hosted AI assistant heavily inspired by Iron Man's J.A.R.V.I.S. from the MCU. 

Featuring a cinematic React-based holographic HUD, offline privacy-first voice pipelines, continuous spatial awareness, deep OS integration, and a dynamic 4-tier memory system. Powered by ultra-fast LLM inference via Groq, allowing for zero-latency conversational flows and multi-step reasoning.

---

## ⚙️ Core Architecture

The system is divided into two distinct environments connected via WebSockets:

* **The Backend (Python/FastAPI):** Acts as the Central Nervous System. Handles audio processing, LLM inference, computer vision, local STT/TTS, automated workflows, and the proactive intelligence daemon.
* **The Frontend (React/Vite):** Acts as the Holographic HUD. A frameless, full-screen kiosk interface featuring interactive widgets, GSAP animations, and dynamic visualizers.

---

## 🚀 Key Features

* **Privacy-First Local Voice Pipeline:** 
  * *Speech-to-Text:* `faster-whisper` for lightning-fast, offline transcription.
  * *Text-to-Speech:* Local `piper-tts` for high-quality, zero-latency streaming synthesis.
  * *Wake Word:* Picovoice Porcupine for ultra-lightweight (0.1% CPU) background listening and instantaneous barge-in interruption.
* **Continuous Spatial Awareness:** Uses `YOLOv8` and `DeepFace` via IP cameras to constantly monitor the room, detect who is present, analyze their emotional state, and trigger proactive UI locks when the user leaves the frame.
* **4-Tier Memory System:** 
  * *Tier 1 (RAM):* Short-term conversational context with auto-compression.
  * *Tier 2 (SQLite):* Permanent core facts and user preferences.
  * *Tier 3 (ChromaDB Vector):* Semantic search for vast knowledge retrieval.
  * *Tier 4 (Episodic):* Automatically logs and summarizes daily sessions into vector space for long-term recall.
* **Digital Life Manager:** Deep OAuth2 integrations with Google Calendar, Gmail, and Google Fit. JARVIS can read, summarize, and send emails, manage your schedule, and monitor your health metrics.
* **Proactive Intelligence Daemon:** JARVIS doesn't just wait for commands. A background monitor tracks system resources, user fatigue (2+ hour sessions), late-night schedules, and calendar events, speaking up proactively when necessary.
* **Smart Home & Environment Control:** Built-in ADB protocol for dynamically waking up and controlling Android TVs over the local network.
* **Visual Context (Screen OCR):** Uses `PyAutoGUI` and `Tesseract OCR` to let JARVIS "read" what is currently on your screen when asked.
* **Immersive Cinematic UI:** Features custom React widgets (Sticky Notes, Secure Browser, Health Vitals, Inbox) and an exclusive "Introduction Ceremony" lockdown protocol for VIP guests.

---

## 🛠️ Tech Stack

**AI & Processing**
* **LLM Engine:** Groq Cloud API (Llama 3.1 8B) for ultra-fast reasoning
* **Speech-to-Text:** `faster-whisper` (Local)
* **Text-to-Speech:** `piper-tts` (Local streaming) + Edge TTS (Fallback)
* **Wake Word:** Picovoice Porcupine (Local)
* **Computer Vision:** `ultralytics` (YOLOv8) + `deepface` + OpenCV
* **Vector Database:** ChromaDB

**Backend (Python)**
* **Framework:** FastAPI + Uvicorn + WebSockets
* **Automation:** `pyautogui`, `pytesseract`, `psutil`, ADB Shell
* **Authentication:** Google OAuth2 API Client

**Frontend (React)**
* **Framework:** React 18 + Vite
* **Styling:** SCSS + CSS Modules
* **Animation:** GSAP, HTML5 Canvas Visualizers

---

## 📂 Project Structure

```text
JARVIS-PROJECT/
├── jarvis-backend/               # Python Central Nervous System
│   ├── modules/                  # Specialized Agents
│   │   ├── local_stt.py          # Faster-Whisper integration
│   │   ├── local_tts.py          # Piper TTS integration
│   │   ├── gmail_agent.py        # Email processing
│   │   ├── calendar_agent.py     # Scheduling
│   │   ├── health_agent.py       # Google Fit integration
│   │   ├── episodic_memory.py    # ChromaDB Vector Logging
│   │   └── screen_reader.py      # Tesseract OCR
│   ├── brain.py                  # Core LLM prompt and parsing logic
│   ├── action_engine.py          # OS & System Execution router
│   ├── background_monitor.py     # Proactive daemon loop
│   ├── ambient_vision.py         # YOLO/DeepFace vision loop
│   └── main.py                   # FastAPI WebSocket Server
│
├── jarvis-frontend/              # React Holographic HUD
│   ├── src/
│   │   ├── components/           # React Widgets (Browser, Notes, Calculator)
│   │   ├── App.jsx               # WebSocket Client & Master State
│   │   └── index.css             # HUD Sci-Fi Styling
│   └── package.json
```

---

## 🔧 Getting Started

### Prerequisites
- Python 3.10+
- Node.js 18+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) installed on your system
- ffmpeg installed on your system

### 1. Backend Setup
```bash
cd jarvis-backend
python -m venv venv
# Windows: .\venv\Scripts\activate
# Mac/Linux: source venv/bin/activate

pip install -r requirements.txt

# Create your environment file
cp .env.example .env
```
*Configure your `.env` with your `GROQ_API_KEY` and `PICOVOICE_ACCESS_KEY`.*

### 2. Frontend Setup
```bash
cd jarvis-frontend
npm install
npm run dev
```

### 3. Launch JARVIS
Ensure the backend server is running:
```bash
cd jarvis-backend
uvicorn main:app --reload
```
Navigate to `http://localhost:5173` to initialize the Holographic HUD.

---

## 🔐 Privacy & Security
This system is designed with a **Privacy-First** methodology. 
* All microphone listening, wake word detection, audio transcription, voice synthesis, and facial recognition occur **100% locally** on your machine. 
* Only highly compressed text strings are sent to the Groq API for LLM inference.
* The system features an automatic camera-based UI lockdown protocol when the authenticated user steps away from the machine.

---

## 📜 Roadmap & Progression
JARVIS was built in 9 distinct architectural phases:
1. **Foundation:** Wake word, TTS, and core React HUD.
2. **The Brain:** LLM integration via Groq and basic action routing.
3. **Memory:** Short-term and long-term SQLite databases.
4. **Autonomy Engine:** Vector semantic memory (ChromaDB) and proactive background monitoring.
5. **Perception Layer:** Ambient spatial awareness (YOLO/DeepFace) and Screen OCR.
6. **Digital Life Manager:** Gmail and Google Calendar deep integration.
7. **The Fortress:** Google Fit integration and multi-user biometric UI states.
8. **Privacy-First Voice:** Complete transition to local STT (faster-whisper) and local streaming TTS (piper).
9. **Reasoning & Routines:** Task chaining, custom wake word (Porcupine), and automated daily briefings.
