# JARVIS — Pre-Electron Test Plan

> **Goal:** prove JARVIS is bug-free on the desktop **before** we package the
> Electron .exe (Electron comes after ALL desktop work is complete; the mobile app
> comes after Electron). Split into **Automatic** (Claude runs — no human, no
> hardware) and **Manual** (Kaustav runs — needs camera / mic / phone / GUI).
> Run the automatic suite after every change; run the full manual pass once, right
> before the Electron build. Last baseline: **2026-07-19 — 205/205 automatic green.**

Legend: ✅ passing · ⬜ not yet run · ⚠️ needs setup

---

## A. AUTOMATIC — Claude runs (pure logic, no hardware)

One command runs them all (from `jarvis-backend`, with the venv):

```
for t in action_parser failure_detection owner_notify gesture_arbiter \
         enroll_face gesture_calibration gesture_engine face_gate \
         calibrate_gesture ; do .\venv\Scripts\python.exe test_$t.py ; done
```

| Harness | Tests | Covers | Status |
|---|---:|---|---|
| `test_action_parser.py` | 24 | LLM-reply → action parse spine (fences, prose, arrays, truncation, alias remap) | ✅ |
| `test_failure_detection.py` | 17 | honest failure vs false "Done, Sir" (`_is_failure` context-aware) | ✅ |
| `test_owner_notify.py` | 20 | Phase-4 owner fan-out (desk/TTS/phone legs, fallback order, isolation) | ✅ |
| `test_gesture_arbiter.py` | 28 | cursor ownership referee (hold/mark/suspend) — gestures vs JARVIS GUI | ✅ |
| `test_enroll_face.py` | 17 | enrollment quality gate (blur/size/edge, diversity, report) | ✅ |
| `test_gesture_calibration.py` | 31 | calibration JSON persistence + defaults<JSON<env resolution | ✅ |
| `test_gesture_engine.py` | 49 | gesture state machine incl. G5.1 relative/accel/clutch/dwell | ✅ |
| `test_face_gate.py` | 5 | owner/stranger/absence presence logic | ✅ |
| `test_calibrate_gesture.py` | 14 | G5.2 wizard derivation (palm_sign, thresholds, reach) | ✅ |
| **Total** | **205** | | **✅** |

**As new pure logic lands (G5.3/G5.4/G5.5, presence probe), add a harness here and
keep this count green.**

## A2. SEMI-AUTOMATIC — need the backend running (Claude can drive)

Start the backend, then run. These exercise the real HTTP/WS surface.

| Harness | Covers | Prereq | Status |
|---|---|---|---|
| `test_ping.py` | backend reachable on `127.0.0.1:8000` | `uvicorn main:app` up | ⚠️ needs server |
| `test_ui_bridge_e2e.py` | WS UI-bridge frames (macros/daemons → HUD) | server up | ⚠️ needs server |

Run: `.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000`
in one shell, then the harness in another.

## A3. BLOCKED-AUTOMATIC — need `pytest` (not in the venv)

Dependency-averse project → `pytest` was never installed, so these don't run today.
Two options: `pip install pytest` (then they're automatic), or convert them to the
self-running `if __name__ == "__main__"` pattern like the others.

| Harness | Covers |
|---|---|
| `test_governance.py` | governance tiers (AUTO/CONFIRM/BLOCK) + cid-scoping |
| `test_android_tv_agent.py` | ADB/TV control |
| `test_github_agent.py` | GitHub agent |
| `test_gmail_agent.py` | Gmail agent |
| `tests/test_briefing.py`, `tests/test_scheduler.py`, `tests/test_hardware.py` | briefing / scheduler / hardware probes |

**Decision for Kaustav:** OK to `pip install pytest` (dev-only, not shipped) so
Claude can run these too? It closes a real coverage gap (governance especially).

---

## B. MANUAL — Kaustav runs (hardware / phone / camera / GUI)

Do the whole list once before the Electron build. Tick each; note anything odd.

### B1. Boot & reliability
- ⬜ Cold start backend (`watchdog.py`) → boots, no crash, no traceback.
- ⬜ Wake / morning briefing fires even if Gmail/Calendar/Health is offline
  (G5.0 #1 — must NOT NameError-crash the wake).
- ⬜ Kill the backend while the HUD is open → HUD auto-reconnects (backoff), no
  manual reload (G5.0 #5). Bring it back → HUD recovers.
- ⬜ During a long action (read_screen / terminal / email) TTS + UI stay responsive
  (G5.0 #2 — no event-loop freeze).

### B2. Voice pipeline
- ⬜ Wake word → listens → command → correct action, spoken reply.
- ⬜ Barge-in: talk over JARVIS mid-sentence → it stops and listens.
- ⬜ A command that returns data (email/RAG/DOM) is SPOKEN as a summary, not raw
  JSON/text (G5.0 #4).
- ⬜ Benglish reply stays Latin-script, never বাংলা/Devanagari.

### B3. GUI automation (the "sometimes works" area)
- ⬜ Cold start: "open Notepad and write a poem" → Notepad opens, text typed
  verbatim (UIA path), saved to the right file.
- ⬜ "open google chrome" → Chrome launches (NOT google.com).
- ⬜ "open google" → google.com; "open youtube" → youtube.com.
- ⬜ Save flow writes the exact file with exact content (clipboard-first path).
- ⬜ Multi-monitor / high-DPI: clicks land where the vision model sees (if you have
  that setup).

### B4. Gesture control (G3/G4/G5.1/G5.2)
- ⬜ **Enroll**: `python enroll_face.py` → 12-sample guided capture → re-seeds
  `owner_embeddings.npz` (replaces the 1-sample seed). Report shows diversity OK.
- ⬜ **G4 arbiter**: engage the hand, then trigger a real ghost_type/autopilot →
  cursor must NOT fight, HUD chip shows "JARVIS DRIVING".
- ⬜ **G3 vocab**: index-up start, click a taskbar icon (no text-select), grab-drag
  a file, scroll a page, back-of-hand stop. Waving must never engage.
- ⬜ **Away soft-lock**: walk away 6s → lock overlay + screen off; return → auto
  unlock. Someone else → deny + Telegram snapshot.
- ⬜ **G5.1 relative mode**: `gesture_spike.py <url>`, press `r` → REL. Feel accel
  (`[`/`]` gain). Small hand move = precise; fast = flick. No gorilla-arm.
- ⬜ **G5.1 clutch**: engaged in REL, brief back-of-hand → move hand → re-face palm.
  Cursor must NOT jump (HUD shows CLUTCH).
- ⬜ **G5.1 dwell right-click**: quick pinch = left click; hold pinch ≥0.5s = right
  click. thumb+middle no longer right-clicks.
- ⬜ **G5.2 wizard**: `python calibrate_gesture.py [--relative] <url>` → palm/pinch/
  reach stages → `w` saves. Restart spike → calibration persisted (palm_sign auto,
  no JARVIS_PALM_* fiddling).
- ⬜ **HUD chip staleness**: kill the gesture daemon (or camera) → chip disappears
  within ~6s (no latched "HAND ACTIVE") (G5.0 #7).

### B5. Autonomy → phone (Phase 4) — needs the phone
- ⬜ PC in standby, stress CPU or trigger the camera intruder path → alert lands on
  Telegram (not just the desk).
- ⬜ Over Telegram ask for a CONFIRM action (e.g. send an email) → "confirm/cancel?"
  → reply "confirm" → it runs.
- ⬜ `/task` a goal with a CONFIRM step → pause report → "approve task <id>" →
  finishes where it stopped. "deny task <id>" drops it.
- ⬜ Calendar reminder while in standby → arrives on the phone.
- ⬜ (Optional) desk linked but frozen → cloud answers itself after ~45s.

### B6. LLM cascade (Phase 5)
- ⬜ Normal voice command with Groq keys pulled from `.env` (or forced Groq fail) →
  reply still arrives via Gemini; `[ROUTER]` log shows the escalation.
- ⬜ A vision command → Gemini vision first, llava offline fallback.

### B7. Cloud gateway (PC OFF) — needs the phone
- ⬜ PC off, Telegram a live-info question → cloud answers via Tavily (not "go find
  out"); honest failure if the lookup truly returns nothing.

### B8. Frontend HUD
- ⬜ Drag widgets in move-mode; positions persist; shrink the window → nothing
  strands off-screen (G5.0 #9).
- ⬜ Backend down → "API unreachable" banner shows the host; comes back → clears.
- ⬜ All widgets (Health/Email/Calendar/Camera/Task) load from the backend host
  (VITE_API_BASE, G5.0 #8).

---

## C. Exit criteria for the Electron build
1. Section A + A2 fully green (and A3 if pytest is enabled).
2. Every B checkbox ticked, no open ⚠️.
3. No uncommitted work; branch pushed + merged.
4. Then, and only then, start Electron packaging.
