# JARVIS — Login / Wake Screen Revamp

> Kaustav wants the wake→login experience to feel cinematic and *real*, not sudden.
> Scope is **visual/UX only** — the auth LOGIC for Kinshuk, Mousumi, and Backdoor is
> already implemented and must NOT be reworked (see §5). The three weak spots to fix:
> (1) the sudden wake — add staged boot visuals; (2) the name prompt — give it a real
> on-screen identity step; (3) Kaustav face-auth — make it *look* like a scan is
> actually happening, with proper transitions and a success/fail moment.
>
> Part of UPGRADES_AND_FLUIDITY.md §3. Build after the current G5 desktop items,
> before Electron. Frontend-heavy + a small backend status contract.

---

## 0. Current state (from the code — what's weak)

- **Wake is abrupt.** No staged boot animation exists. On wake the backend just emits
  text statuses `booting → waking → online` (`main.py:2133/2136`, backdoor
  `main.py:1591/1600/1608`) and the frontend only cycles a log line + flips widgets
  `widget-sleep→widget-awake` via `hasWokenUp` (`App.jsx:415-421`). `Visualizer`
  reacts to status but there's no "system coming alive" sequence.
- **Name prompt is voice-only, no visual.** It appears only as the fallback when the
  face scan is inconclusive: `security_locked` "Please state your name"
  (`main.py:2220`) → `security_listening` (`main.py:2226`) → alias match
  (`main.py:2237-2239`). Nothing renders on screen but a log line.
- **Kaustav face-auth looks fake / janky.** `FaceScanOverlay.jsx` runs a **self-timed**
  3-phase animation (`setTimeout` 0 / 1500 / 4000 ms) that is **not synced** to the
  real scan: the backend does one `security_locked` + "OPTICAL SENSORS" message
  (`main.py:2144`), then a **blocking 10 s** `vision.scan_for_faces(10)`
  (`main.py:2150`), then the welcome. The overlay is triggered only by the
  "OPTICAL SENSORS" substring (`App.jsx:403-404`) and **just disappears** when the
  next status arrives — there is no explicit success or failure state, so the timing
  drifts and there's no satisfying "matched!" moment.

## 1. Revamp goals

1. **Staged wake/boot** — a short, good-looking power-on sequence after the wake word.
2. **Identity step** — an on-screen "state your name" moment showing the three
   identities, with live mic feedback.
3. **Believable face-auth** — overlay driven by REAL scan sub-states with a clear
   success (lock-on + welcome) and failure (retry → name fallback) transition.
4. Keep Mousumi's ceremony (already good), Kinshuk's flow, and Backdoor untouched.

---

## 2. Backend status contract (small, additive)

The revamp needs the backend to narrate the login as discrete frames instead of one
blocking call. Add these WS statuses (additive — keep the old ones working). All are
thin `safe_send`/`safe_send_all` emits around the existing logic in the STAGE 1B
biometric branch (`main.py:2142+`) and the name-fallback branch (`main.py:2219+`).

| New status | When | Payload | Replaces / wraps |
|---|---|---|---|
| `boot_sequence` | wake accepted, before briefing | `{step, total, label}` per step | the bare `booting` text |
| `identity_prompt` | asking for the name | `{users:["KAUSTAV","KINSHUK","MOUSUMI"]}` | `security_locked` "state your name" |
| `identity_listening` | mic open for the name | — | `security_listening` |
| `auth_face_start` | optical sensors on | — | `security_locked`+"OPTICAL SENSORS" |
| `auth_face_scanning` | during `scan_for_faces` | `{progress?}` | (new — currently a blocking gap) |
| `auth_face_matching` | comparing to DB | — | (new) |
| `auth_face_success` | match found | `{user}` | the welcome message |
| `auth_face_fail` | no/!match | `{reason}` | drops to `identity_prompt` |

Implementation note: `vision.scan_for_faces(10)` (`vision.py:28`) is one blocking
call. To feed `auth_face_scanning` progress, either (a) emit `scanning`/`matching`
around it (cheap, no real progress) — good enough for the look — or (b) refactor
`scan_for_faces` to accept a progress callback that pushes frames. Start with (a).

Backward-compat: keep emitting the legacy `security_locked`/`booting`/`waking` so
nothing else breaks; the new frontend keys on the new statuses first and falls back.

---

## 3. Frontend components

### 3.1 `BootSequence.jsx` (NEW) — staged power-on
- Full-screen takeover on wake, dismisses when `waking`/`online` arrives (gate the
  animation to the REAL boot, not a fixed timer — hold the last step until the
  backend says ready, so it never finishes before JARVIS does).
- Scripted steps (each a line that types/ticks in, with a progress rail):
  `POWER CORE ONLINE → NEURAL LINK ESTABLISHED → LOADING MEMORY BANKS →
  CALIBRATING SENSORS → SYSTEMS NOMINAL`.
- Visuals: arc-reactor spin-up (reuse the `hud-core-pulse` motif), sweeping scanline,
  subtle glitch/CRT power-on, audio-reactive ring off `Visualizer`.
- Trigger: `status === "boot_sequence"` (or first `booting`) → show; `waking`/`online`
  → play the final "SYSTEMS NOMINAL" beat then fade to the HUD.
- Reuse: `ScanlineTransition.jsx` for the reveal into the HUD.

### 3.2 `IdentityPrompt.jsx` (NEW) — the name step
- Shows three identity cards — **KAUSTAV** (admin), **KINSHUK** (Level 2), **MOUSUMI**
  (V.I.P.) — with a "STATE YOUR NAME" headline and a live mic-listening pulse.
- On `identity_listening`, animate a waveform/pulse; when the backend resolves the
  name, highlight that card and hand off to the right auth path:
  - KAUSTAV → `FaceAuthOverlay` (§3.3),
  - KINSHUK → its existing passkey flow (unchanged) — optionally a small "AWAITING
    RELATION / PASSKEY" visual, but logic stays,
  - MOUSUMI → existing `IntroductionCeremony` (unchanged).
- This also becomes the **visible affordance** the product lacks — pairs with the
  G5.7 mic-button fluidity item.

### 3.3 `FaceAuthOverlay.jsx` (REWORK of `FaceScanOverlay.jsx`) — believable scan
- **Drive it by the backend `auth_face_*` statuses, not `setTimeout`.** Phases:
  `auth_face_start` → reticle acquires; `auth_face_scanning` → laser sweep + mesh
  points lock onto a face box (optionally overlay the live camera frame or a stylized
  wireframe); `auth_face_matching` → "MATCHING BIOMETRIC SIGNATURE" with a scanning
  bar; `auth_face_success` → **green lock-on**, brackets snap in, "IDENTITY CONFIRMED
  — WELCOME BACK, SIR", then transition to the HUD; `auth_face_fail` → **red reject**,
  "NO MATCH", shake, then fall back to `IdentityPrompt`.
- Make it feel real: show the actual camera feed (JARVIS already has the IP-cam stream
  used by gestures/`CameraFeedWidget`) behind the reticle, draw the detected face box,
  and only fire success when the backend says matched. Optional face-mesh dots.
- Keep the current corner-bracket / scan-grid / scan-point aesthetic from
  `FaceScanOverlay.jsx` — just re-time it to the real states and add success/fail.

### 3.4 App.jsx wiring
- Add state: `bootStep`, `identityState`, `faceAuthState`; handle the new statuses in
  the `onmessage` dispatcher (next to the existing `security_locked` handling at
  `App.jsx:403-421`), keeping the old branches as fallback.
- Mount `BootSequence`, `IdentityPrompt`, `FaceAuthOverlay` in
  `dashboard-container__main` (`App.jsx:660-675`) alongside the current overlays.

---

## 4. Transitions (the "smooth" part)
- One shared easing/token set for all login overlays (reuse `HUD_EASE`).
- Each stage **cross-fades** into the next (no hard cuts); the face success →
  HUD reveal uses `ScanlineTransition`.
- Never let an animation outrun reality: the boot sequence's final step and the face
  "matching" bar both HOLD until the backend confirms, so the visuals always end on
  the real event, not a guessed timer (the core bug today).
- Respect `prefers-reduced-motion` (accessibility + low-CPU box) with a shorter path.

---

## 5. Already implemented — DO NOT rework the logic (visual-only touch-ups OK)
- **Kinshuk** — voice passkey flow (relation "brother" → passkey "brotherhood",
  `main.py:2254-2293`; face path `main.py:2165-2175`) + "brother" persona
  (`brain.py:1101-1106`). Keep. Optional: a small "AWAITING RELATION/PASSKEY" visual.
- **Mousumi** — V.I.P. `IntroductionCeremony.jsx` (`main.py:2177-2216`) + Madam persona
  (`brain.py:1093-1100`). Already cinematic — leave it; just share the easing tokens.
- **Backdoor** — `/api/backdoor` (`main.py:1456`, `App.jsx:587`) bypasses auth by
  design for testing (`main.py:1590-1609`). Unchanged; the boot sequence may still
  play on a backdoor wake, but no identity/face step.

---

## 6. Phasing
1. **Backend status contract** (§2) — additive emits + `scan_for_faces` scanning/
   matching wrap. Small, harnessable (assert the frames emit in order on a mocked scan).
2. **`FaceAuthOverlay` rework** (§3.3) — the highest-impact fix (the janky one).
3. **`BootSequence`** (§3.1) — the "not sudden" win.
4. **`IdentityPrompt`** (§3.2) — the name step + doubles as the mic affordance.
5. Polish transitions (§4), reduced-motion, live-gate with Kaustav on the real camera.

Verify: `npm run build`; a small backend harness for the status-frame ordering; then
Kaustav live-gates each path (Kaustav face success + fail→fallback, Kinshuk, Mousumi,
Backdoor) on the real rig before this counts as done.
