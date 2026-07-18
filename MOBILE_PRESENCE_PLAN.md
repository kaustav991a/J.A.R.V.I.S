# JARVIS — Away→Mobile Presence & Handoff — Plan

> Authored 2026-07-19 in response to Kaustav's question: *"how can it detect I'm
> away and switch to my mobile — does JARVIS need a dedicated mobile app?"*
> **Short answer: no app is required for the core handoff — Telegram already is
> the mobile surface and most of the away→phone routing shipped in Phase 4. A
> dedicated app is only worth it for automatic presence, live agent-cam, and a
> branded UI. This doc specs the near-term no-app path (Track B) and scopes the
> app (Track C).**

---

## 0. What already exists — do NOT rebuild

| Capability | Where |
|---|---|
| Owner-notify fan-out (desk HUD + desk TTS + **phone**) | `modules/owner_notify.py` (`configure()` + fan-out) |
| Phone leg = Telegram direct bot **or** cloud-bridge `alert` frame | `modules/telegram_bot.py::send_text_to_owner`, `modules/cloud_bridge.py::send_alert_to_owner` |
| Standby routes alerts to phone, skips desk TTS | `background_monitor.py::_check_cycle` (Phase 4) |
| Desk presence (owner at the desk) via YuNet face + motion | `modules/face_gate.py` (`FaceGate`, `AbsenceTracker`, `MotionDetector`), driven by `gesture_daemon.py` |
| Login/online state | `main.py::SYSTEM_ONLINE` (+ `is_system_online_fn` / `is_online_fn` passed to daemons) |
| Always-on brain when PC is OFF | `cloud_gateway.py` on Render (Telegram) |
| Two-way from phone: CONFIRM answer, `approve task <id>` | Phase 4, `main.py::run_remote_command` |

So: intruder snapshots, health/CPU alarms, daemon-down alerts, finished/failed
tasks, and calendar reminders **already reach Telegram when you're away from the
desk**, and you can already authorise risky actions and approve paused tasks from
the phone. Telegram covers text + photo + voice-note + commands, free.

## 1. The actual gap

Two things Telegram + the current stack do **not** do:

1. **Automatic presence beyond the desk.** `face_gate` knows you left the *desk*
   (camera). Nothing knows you left the *room* or the *house*. Today "away"
   effectively means `not SYSTEM_ONLINE` (login/standby) or desk-absence.
2. **Rich mobile UX** — live agent-cam, inline CONFIRM buttons, voice wake,
   geofence. Telegram is text-first pull.

Track B closes gap #1 cheaply. Track C closes both, at cost.

---

## Track B — Phone-on-WiFi presence probe (near-term, NO app)

**Idea:** the backend periodically checks whether your phone is reachable on the
home LAN. Phone on home WiFi = **HOME**; gone for a sustained window = **AWAY**.
This gives an automatic "left the house" signal with zero app to build — the same
trick already used for the phone IP-Webcam (`192.168.0.105:8080`).

### Design
- **NEW `modules/presence_probe.py`** — pure-ish poller, no heavy deps:
  - Detection methods, most→least reliable on Windows:
    1. **ARP-table hit for the phone's MAC** (`arp -a`) after a priming ping to
       its IP — survives phone WiFi power-save better than raw ICMP.
    2. **TCP connect** to a known phone port (e.g. IP-Webcam `:8080`) when that
       app runs — a positive is unambiguous.
    3. **ICMP ping** to the phone IP — simplest but phones often drop pings to
       save battery (false "away"), so it's the weakest signal.
  - **State machine HOME/AWAY with asymmetric debounce** (the important part —
    phones sleep their WiFi radio, so a few missed probes is normal):
    - any single positive → **HOME** immediately;
    - **AWAY** only after N consecutive misses over a grace window
      (`JARVIS_PRESENCE_AWAY_GRACE`, default ~180 s).
  - **Config (env):** `JARVIS_PHONE_IP`, `JARVIS_PHONE_MAC` (preferred — pin the
    phone to a **fixed MAC for the home SSID** to defeat per-network MAC
    randomization), `JARVIS_PRESENCE_POLL` (default 30 s), `JARVIS_PRESENCE_AWAY_GRACE`.
  - Public API: `PresenceProbe.start()/stop()`, `.is_home() -> bool`,
    `.state -> "HOME"|"AWAY"|"UNKNOWN"`, and an `on_change(cb)` hook.

### Integration
- Run it as one more lifespan daemon (Pattern B thread), like `background_monitor`.
- Feed its `is_home()` into a **fused presence** helper (see §2) that
  `owner_notify` and `background_monitor` consult to decide desk-vs-phone routing —
  **independent of `SYSTEM_ONLINE`** (which is login state, not physical presence).
- On `HOME→AWAY` transition: optionally have JARVIS proactively push "You've left
  — I'll route everything to your phone" and arm the desk soft-lock early.
- On `AWAY→HOME`: relax back to desk delivery.

### Caveats (write them in the code, don't let them surprise later)
- Phone WiFi power-save drops ARP entries → **needs the long AWAY grace**, never a
  single-miss flip.
- **MAC randomization**: modern phones use a random MAC per SSID; pin a fixed MAC
  for the home network in phone settings, or match by IP (DHCP-reserved).
- Granularity is **home-network only** — not room-level, not GPS. That's Track C.
- Runs only while the PC (backend) is on; when the PC is OFF the cloud gateway is
  already the away brain, so the probe isn't needed then.

### Verification
- **Harness:** the HOME/AWAY debounce state machine is pure — unit-test with a
  fake probe function (single hit → HOME, N misses → AWAY, flapping stays HOME).
- **Live:** walk out with the phone → after the grace window, an alert should
  arrive on Telegram and the desk should stop being the delivery target.

**Effort:** small — one module + lifespan wiring + a fusion helper + harness. Days.

---

## Track C — Dedicated JARVIS mobile app (scope)

Worth it **only** for what Telegram + Track B can't do: automatic **geofence /
GPS / BT presence**, **live agent-cam**, richer push, voice wake, and a branded
JARVIS UI (notch/takeover parity with the desk HUD vision).

### Stack
- **Flutter** (recommended) — one codebase iOS+Android, good background/geofence
  and FCM support.
- React Native — alternative; reuses JS skills from the web HUD.
- **PWA — not recommended for this**: iOS restricts background presence and push,
  which are the whole point.

### Feature tiers
- **MVP**
  - **Push** via FCM — `owner_notify` gains an FCM leg next to the Telegram leg.
  - **Command send** — reuse `/api/backdoor` (or a dedicated `/api/mobile/command`)
    over the cloud relay so it works with the PC off.
  - **Presence report** — app posts WiFi SSID + (opt-in) coarse geofence to a NEW
    `POST /api/presence`; this **supersedes Track B** when the app is installed
    (B stays as the fallback when the app is absent/offline).
- **v2**
  - **Live agent-cam** — the "takeover" view: stream what JARVIS is doing
    (WebRTC, or MJPEG over the cloud bridge).
  - **Inline CONFIRM / approve-task buttons** (map to the Phase-4 grammar).
  - **Voice note → STT** command path.
- **v3**
  - Notch/takeover UI parity with the planned Electron shell.

### Backend work C implies
- **NEW `POST /api/presence`** ingest → same fused-presence source as Track B
  (app wins when reporting; probe is the fallback).
- **NEW FCM sender module** (device-token registry, owner-only).
- **Auth**: per-device token + shared secret, same discipline as the cloud bridge;
  never trust an unauthenticated presence/command post.
- Reuse `cloud_gateway.py` as the always-on relay so the app works with the PC off.

**Effort:** large (weeks), plus app-store signing/maintenance. Recommend deferring
until after Electron packaging, and only if live-cam/geofence are actually wanted.

---

## 2. Presence fusion (how the signals combine)

One helper (e.g. `modules/presence.py` or fold into `owner_notify`) answers
*"where is Kaustav?"* from three tiers, most-specific first:

| Tier | Signal | Source |
|---|---|---|
| **AT_DESK** | owner face present at the desk | `face_gate` owner_present |
| **HOME** (not at desk) | phone on home WiFi, or app SSID report | Track B probe / Track C `/api/presence` |
| **AWAY** | phone off home WiFi, or geofence exited | Track B (grace) / Track C |

**Routing policy:**
- AT_DESK → desk HUD + desk TTS (phone silent).
- HOME-not-desk → desk HUD + **phone** (you're around but not looking).
- AWAY → **phone only** (+ cloud gateway relay if the PC is off).

This is a thin layer over the existing `owner_notify` fan-out — it just chooses
which legs fire.

---

## 3. Recommended sequencing

1. **Track B now** — small, immediate "left home → everything goes to phone" win,
   no app. Harness the debounce, live-gate the walk-out.
2. **Track C later** — after Electron packaging; if it ships, `/api/presence`
   supersedes B (B remains the fallback), and the FCM leg joins `owner_notify`.

Neither is required for the *basic* away→phone handoff that already works via
Telegram today; both are upgrades to how *automatically* and *richly* it happens.
