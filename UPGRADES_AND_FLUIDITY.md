# JARVIS — Upgrades & Fluidity Guide

> Answer to *"what upgrades can we do, and how do we make JARVIS fluid to use?"*
> Two lenses: **Fluidity** (how it *feels* — instant, smooth, natural) and
> **Upgrades** (new power). Cross-references the G5 plan (RELIABILITY_HARDENING.md
> §10) and MOBILE_PRESENCE_PLAN.md. Order of work stays: **finish desktop → Electron
> → mobile app.**

---

## 1. FLUIDITY — make it feel instant, smooth, natural

### 1.1 Already shipped (the base is smooth)
- Voice stays real-time: Groq is the PRIMARY LLM leg for latency; TTS streams
  sentence-by-sentence; blocking handlers run off the event loop (G5.0 #2) so
  audio/UI never freeze mid-action.
- HUD survives drops: auto-reconnect with backoff (G5.0 #5), guarded frames
  (G5.0 #6), off-screen clamp (G5.0 #9).
- Gestures feel natural: One-Euro smoothing, dynamic deadzone, pinch hysteresis,
  cursor arbiter (G4), and now **relative trackpad + acceleration + clutch**
  (G5.1) and a **calibration wizard** (G5.2).

### 1.2 High-impact fluidity upgrades still to do
| Upgrade | Why it feels better | Where |
|---|---|---|
| **Visible mic / voice affordance** | Today there is NO mic button — `startVoiceCommand` is dead code. A voice-first product needs an obvious "talk to me" control + listening animation. | G5.7 frontend |
| **Cursor halo + edge toasts** | Mid-air gesture control has no tactile feedback; a ring that tracks state (grab/click/clutch) + corner toasts ("hand ready", "JARVIS driving") make it feel alive and predictable. | G5.3 |
| **Precision mode** | When the hand is slow, clamp harder so tiny targets (×, text lines) are selectable — removes the "almost got it" frustration. | G5.5 |
| **Control from across the room** | Crop-around-face ROI + 720p + confidence knobs so gestures work at a distance, not just at the desk. | G5.4 |
| **Connection-based boot log** | The HUD currently claims "systems warm" on a timer even when the backend is down — misleading. Tie readiness to the actual WS connect. | G5.7 frontend |
| **Command-terminal feedback** | Typed-command failures are console-only and the input is unlabeled; surface errors in the HUD. | G5.7 frontend |
| **Lower first-response latency** | Optional: warm the primary model / prefetch, trim prompt further (history cap already done), speak an instant acknowledgement before the full answer streams. | brain/router |

### 1.3 Gesture "feel" polish (after the live gate)
- Flip the relative-trackpad default on once G5.1 gates well.
- Tune accel curve + base_gain per your calibration (`[`/`]` in the spike, `w` to save).
- Decide clutch ergonomics live (palm-tilt threshold) — pairs with G5.1.

---

## 2. ROBUSTNESS UPGRADES — fewer surprises (G5.7 backlog)

**Backend**
- Barge-in leaks the synthesis producer thread + upstream LLM stream on interrupt — clean up.
- No boot config preflight — a half-configured `.env` degrades silently; add a "what's missing" report on startup.
- `llm_router._call_ollama` returns `""` on an empty 200 instead of failing over — make it honest.
- `working_memory` is mutated from worker threads without a lock — add one.
- ~40 fire-and-forget `create_task(speak_text(...))` swallow TTS errors — log them.
- `watchdog.py` respawns a permanently-broken process forever — add give-up + owner alert.

**Frontend**
- `BrowserWidget` iframe has no fallback for framing-blocked sites (Google etc.).
- `DataOverlay` modal has no Escape / focus-trap.
- `CalculatorWidget` uses `eval()` on live input — replace with a safe expression parser.
- Widget drag is right-click-only with no discoverable hint.

---

## 3. CAPABILITY UPGRADES — new power (bigger rocks)

| Upgrade | What it adds | Plan | When |
|---|---|---|---|
| **Away → mobile presence** | Auto "left the desk / left the house" → route to phone. Track B (phone-WiFi probe, no app) then Track C (app). | MOBILE_PRESENCE_PLAN.md | B: soon (desktop-side); C: after Electron |
| **Electron single-exe** | One .exe boots FE+BE; notch (idle chat) → fullscreen takeover overlay (live agent-cam). | RELIABILITY §0, §9.6 | after ALL desktop work + tests |
| **Dedicated mobile app** | FCM push, geofence presence, live agent-cam, voice, branded UI. | MOBILE_PRESENCE_PLAN.md Track C | after Electron |
| **Deeper autonomy** | More self-healing, richer overnight worker plans, more agents. | Phase 4 base | incremental |
| **Vision/quality** | Gemini vision default (done) — could add better OCR/screen understanding. | Phase 5 | incremental |

---

## 4. Recommended near-term order (desktop, no hardware blocking)
1. **G5.3 overlays** (cursor halo + toasts) — biggest *felt* fluidity jump for gestures.
2. **G5.4 distance** + **G5.5 precision** — make gesture control usable everywhere.
3. **G5.7 backlog** — the mic button + robustness fixes (these help EVERY interaction, not just gestures).
4. **Track B presence probe** — cheap away-detection win.
5. Full **TEST_PLAN.md** pass (automatic + manual).
6. **Electron** packaging.
7. **Mobile app** (Track C).

> The single highest fluidity-per-effort item is the **visible mic/voice affordance
> (G5.7)** — a voice-first assistant with no visible way to start talking is the
> biggest "doesn't feel finished" gap. Worth pulling forward.
