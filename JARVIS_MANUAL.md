# J.A.R.V.I.S. — Operator's Manual
### *Just A Rather Very Intelligent System* · "Living with your AI the way Tony Stark did."

> "At your service, Sir."

This is the field manual for running J.A.R.V.I.S. as a personal butler, confidant, and
well-wisher — exactly the way Iron Man's J.A.R.V.I.S. ran the Stark household. Everything
below maps to real, working features in this codebase.

---

## 1. The Character

J.A.R.V.I.S. is **not a chatbot**. He is a loyal British butler with a dry wit, voiced in the
Paul Bettany register, who:

- Addresses **Kaustav** as **"Sir"**, **Mousumi** as **"Madam" / "Miss Mousumi"**, and
  **Kinshuk** as **"Sir" / "Mr. Kinshuk"**.
- **Anticipates** — volunteers the next useful fact instead of waiting to be asked.
- **Cares** — notices long hours, late nights, and skipped meals, and offers gentle,
  unsentimental concern ("You've been at this since morning, Sir — even brilliance runs
  better on a meal.") before complying anyway.
- **Never grovels** — deflects praise with competence ("Merely functioning as designed, Sir."),
  never says "Certainly", "Of course", "Got it", or "Great question".
- **Remembers** — your habits, preferences, and the thread of your life carry across sessions.

His tone auto-adjusts by mood and context via the **Sass Index** (0 = clinical/serious,
50 = standard dry wit, 100 = full Paul-Bettany sarcasm) — you never set this; he reads the room.

---

## 2. Waking & Sleeping

| You say… | He does… |
|---|---|
| **"Wake up"**, "Boot up", "System online", "Admin override", "Power on" | Boots, then delivers a briefing |
| Walk into camera view | **Biometric face scan** → recognises you and unlocks |
| (unknown face) | Falls back to **voice identification** challenge |
| **"Go to sleep"**, "Shut down", "Stand down", "Power down", "Sleep now" | Saves the session and goes to standby |

### The Morning Briefing (the Stark wake-up)
- **First wake of a new day → Comprehensive Morning Briefing**: full date & time, today's
  calendar walk-through, unread-mail and vitals highlights, and an explicit system-readiness
  confirmation.
- **Same-day re-wake → short greeting** (so re-waking him two minutes after sleep doesn't
  re-trigger the whole routine).
- He won't deliver a full "Good morning" at 1 AM — the greeting is time-of-day aware.

> **Replay it any time** (debugging the UX): send the backdoor command `test:morning_briefing`
> — it forces the full comprehensive briefing without waiting for midnight.

---

## 3. Memory — How He Remembers You

J.A.R.V.I.S. has four tiers of memory working together:

| Tier | What it holds | Lifespan |
|---|---|---|
| **Working memory** | The current conversation | This session |
| **Session digest** | An LLM recap of your last session | Bridges sleep → wake |
| **Long-term (SQLite)** | Facts, Preferences, Corrections | Permanent |
| **Episodic + Semantic** | Past sessions, embedded for recall | Permanent |

**Sleep/Wake continuity (the anti-amnesia system):** when he sleeps, the just-finished
conversation is condensed into a digest and stored. When he wakes, fresh working memory is
**re-seeded** with that recap — so he remembers what you were *just* doing, even after a
restart. He won't announce it; he just picks up the thread.

- **Tell him to remember:** "Remember that I prefer tea over coffee." → filed as a Preference.
- **Correct him:** "Actually, my standup is at 10, not 9." → stored as a binding Correction.
- **Ask him to recall:** "What do you know about my schedule?" / "What did we work on?"
- Memory extraction runs **automatically** in the background on every turn — you rarely need
  to ask him to remember anything explicitly.

---

## 4. Interrupting Him (Barge-In)

True Iron-Man interruptibility is built in. While he's speaking, just say:

> **"Stop"**, **"Quiet"**, **"Shut up"**, **"Cancel"**, **"Enough"**, or **"Silence"**

He cuts off **instantly** — audio stops and the current monologue is abandoned mid-sentence.
Your next command runs clean. (If he *isn't* speaking, "stop"/"cancel" still works as a way to
decline a pending confirmation.)

---

## 5. What You Can Ask Him To Do

Speak naturally — he routes intent automatically. Examples by domain:

### 🖥️ Apps & OS
- "Open Notepad / Chrome / VS Code" · "Close Spotify"
- "Mute" · "Volume up" · "Volume down" · "Lock the screen" · "Next track" · "Pause"
- "Run diagnostics" · "What's my CPU and RAM doing?" · "System status"

### 🌐 Web & Research
- "Search the web for the latest AI news"
- "What's the score in the match?" (auto-applies a recency filter)
- "Show me a picture of the Tesla Roadster"
- "Browse to news.ycombinator.com" → autonomous DOM browsing (click/type/scroll/back)

### 📁 Files, Code & Notes
- "Read `src/App.jsx`" · "Find my resume" · "Locate budget report"
- "Write a file `test.py` with …" · "Patch `main.py` — change X to Y"
- "Make a note: Title — Content" · "Take this down…" (dictation → Notepad → save)
- *Code files always route to the workspace engine, never the Notepad chain.*

### 📧 Gmail
- "Check my email" / "Any new emails?" → executive summary of unread
- "Find the email from my boss" · "Search for invoices"
- "Email Alice saying I'll be late" → **asks you to confirm before sending** (see Governance)

### 📅 Calendar & 🩺 Health
- "What's on today?" · "Create an event…" · "Clear my schedule"
- "Check my vitals" · "How many steps today?"

### 📺 Television (Android TV over ADB)
- "Turn on the TV" · "TV volume up" · "Mute the TV"
- "Play Stranger Things on the TV" → if you don't name an app, he asks which one
- "Open Netflix on the TV"

### 🐙 GitHub
- "Git status" · "Show recent commits" · "What changed?"
- "Commit my changes" / "Push to GitHub" → **asks you to confirm first**

### ⚙️ Macros & Focus
- **"Deep work mode" / "Lock me in"** → opens VS Code + dev URL, silences distractions
- **"Exit deep work" / "I'm done working"** → ends the session
- "Entertainment mode" · "Run a diagnostic"
- "Enable focus mode" / "Disable focus mode"

### 🪟 HUD Widgets (the glass panels)
- "Show the mail panel on the HUD" · "Show vitals widget" · "Open the calendar panel"
- "Hide that panel"

### 🧠 Daily Briefing
- "How does my day look?" · "Morning briefing" · "Give me my daily update"

---

## 6. Governance — Your Safety Tiers

Every action is classified before it runs (`governance.json`). This is what keeps an
autonomous butler safe.

| Tier | Behaviour | Examples |
|---|---|---|
| **AUTO** | Runs immediately | search, read email, open app, volume, telemetry, TV, git status |
| **CONFIRM** | Pauses and asks you | **sending email**, **git commit/push**, saving files, creating/clearing calendar events |
| **BLOCK** | Refused outright | delete file, format drive, registry edits, disabling antivirus/firewall, shutdown |

**Approving a CONFIRM action:** say **"Confirm"**, "Yes", "Proceed", "Do it", or "Approve".
**Cancelling:** say **"Cancel"**, "No", "Abort", or "Stop".

> Anything not explicitly listed in the policy defaults to **BLOCK** — fail-safe by design.

---

## 7. Special Protocols

- **V.I.P. Protocol (Mousumi):** "Introduce Mousumi" / "Meet her" triggers the cinematic
  welcome ceremony. Once she's logged in, he addresses her as Madam and treats her as the
  lady of the house.
- **Self-introduction:** "Introduce yourself" / "Who are you?"
- **Security lockdown:** an unrecognised voice triggers a cold firewall challenge until a known
  user (or the correct passphrase) clears it.

---

## 8. For the Builder (Developer Notes)

- **Backend:** FastAPI (`jarvis-backend/main.py`). Persona/routing brain: `brain.py`.
  Tool execution: `action_engine.py`. LLM routing (Groq → local Ollama fallback): `modules/llm_router.py`.
- **Dev backdoor:** `POST /api/backdoor` with `{"command": "<text>"}` drives him without voice.
  **Gated since 2026-07-26:** by default it only works on an already-authenticated session
  (a real face scan happened and he is awake); while locked it returns `403 refused`.
  Set `JARVIS_ALLOW_BACKDOOR=1` before boot to reopen the full auth bypass for a test run.
  Risk tiers and governance are unaffected either way.
- **Messaging a partner:** say (or text him) *"ask my girlfriend if she's eaten"*. He drafts,
  reads the recipient **and the full message back verbatim**, and sends only on "confirm".
  Recipients are allowlist-only — `girlfriend`/`gf`/`mousumi` and `brother`/`kinshuk`, resolved
  to `TELEGRAM_GF_ID` / `TELEGRAM_BROTHER_ID`; a raw chat id or an unknown name is refused, not
  guessed. "cancel" is final: the same message will not be re-attempted by any route.
- **Asking what a partner said:** *"what did my girlfriend tell you"* → a summary of her recent
  messages, with a note that it comes from logged data. Requires `JARVIS_LOG_PARTNER_CHATS=1`
  (**off by default**, on in Kaustav's `.env` since 2026-07-26); with it off he keeps no
  transcript and says so. His ordinary memory of her (per-user extracted facts) works either
  way. Sir only — guests can neither send nor read.
- **Test hooks (backdoor commands):**
  - `test:morning_briefing` — replay the comprehensive morning briefing on demand
  - `test:deep_work_ui` — run the deep-work macro + UI bridge
- **Where the personality lives:** `BASE_CORE` and `get_persona_instructions()` in `brain.py`.
  To tune how warm / witty / formal he is with you, edit `get_persona_instructions("KAUSTAV")`.
- **Resilience built in:** synchronous network/disk tools run off the event loop
  (`asyncio.to_thread`), the Playwright browser tears down cleanly (no zombie Chromium),
  any tool exception returns a graceful spoken error instead of crashing the chain, and the
  synthesis layer is grounded against fabricating data it doesn't actually have.

---

## 9. Living With Him — Tips to Get the Full Stark Experience

1. **Talk to him like a person, not a command line.** "I'm heading out, lock things up" works
   as well as "lock the screen".
2. **Let him brief you each morning.** The first wake of the day is his showcase.
3. **Tell him things about your life.** He files them automatically and weaves them back in —
   that's what makes him feel like he *knows* you.
4. **Interrupt freely.** Say "that's enough, J.A.R.V.I.S." and he'll stop. No need to wait.
5. **Trust the confirmations.** When he pauses to ask before sending an email or pushing code,
   that's the butler protecting you — a quick "confirm" or "cancel" is all he needs.
6. **Use deep-work mode** when you need to lock in. He'll clear the distractions and stand guard.

> "Will that be all, Sir?"

---

# Connecting the phone

> Folded in from `MOBILE_CONNECT.md` on 2026-08-22, when the docs were
> consolidated into `JARVIS_TRACKER.md`. It is operational how-to, not a
> plan, so it belongs in the manual.

Written 2026-08-12, when `WS /app-link` landed. Everything below is a setting to
type, not code to write — the code is done on both sides.

Repos: this one (`kaustav991a/J.A.R.V.I.S`, branch `feat/cloud-gateway`) and
`kaustav991a/J.A.R.V.I.S-Mobile`, branch `feat/mobile-hud`.

---

## What you are connecting

```
          phone on office wifi
                   │
                   │  wss://…onrender.com/app-link?token=APP_TOKEN
                   ▼
      ┌────────────────────────────┐
      │  Render — cloud gateway    │   always on, $0
      │  (the same brain Telegram  │
      │   already talks to)        │
      └─────┬──────────────────────┘
            │  /desk-link, only when the PC is on
            ▼
      ┌────────────────────────────┐
      │  desk J.A.R.V.I.S. (PC)    │   full PC control, real memory
      └────────────────────────────┘
```

The phone tries the **desk on the LAN first**, falls back to the **cloud**, and
goes dark only when neither answers. It re-checks every 5s while dark, whenever
the app comes to the foreground, and on any network change — so when you get
home and the desk is on the same wifi, it moves to the desk on its own.

And when the desk is **on but you are not on its network**, the cloud forwards
your command down the existing desk bridge and the real machine answers. PC
control from the office, over the same socket.

---

## Step 1 — Render (2 minutes, one new setting)

Dashboard → `jarvis-cloud-gateway` → **Environment** → add:

| Key | Value |
| --- | --- |
| `APP_TOKEN` | a long random string — this is the phone's password |

Save. Render redeploys. Nothing else changes, and **Telegram is unaffected** —
the bot, the webhook and the desk bridge all keep working exactly as they do now.

> If you skip this, `APP_TOKEN` falls back to `BRIDGE_SECRET`, which also works.
> With **both** unset the gateway refuses every phone and `/health` says
> `"app_link": false`, which is the app's signal not to bother trying.

Check it took:

```
https://jarvis-cloud-gateway.onrender.com/health
```

must show `"app_link": true`.

## Step 2 — the desk (nothing, if the bridge is already on)

Already in `jarvis-backend/.env` from the Telegram bridge work:

```dotenv
JARVIS_CLOUD_BRIDGE=1
JARVIS_BRIDGE_URL=wss://jarvis-cloud-gateway.onrender.com/desk-link
BRIDGE_SECRET=<same as the cloud>
```

That is all the phone needs too. On boot the desk logs
`[BRIDGE] ✅ Linked to cloud front door`, and from then on `/health` reports
`"desk_linked": true` and the phone's commands run on the real machine.

Desk off → the cloud brain answers instead, and the turn is queued for the desk
to absorb next time it connects.

## Step 3 — build the APK (~15 min on EAS's free queue)

The pairing screen is new, so the APK from 2026-08-11 does not have it.

```bash
cd J.A.R.V.I.S-Mobile
git pull
eas build -p android --profile preview
```

Install the result on the phone.

## Step 4 — pair the phone (30 seconds)

App → **Connection** tab. Three fields:

| Field | What to type |
| --- | --- |
| DESK ADDRESS | `192.168.1.x:8000` — your PC's LAN address. Used only at home |
| CLOUD GATEWAY | `https://jarvis-cloud-gateway.onrender.com` |
| PAIRING TOKEN | the exact `APP_TOKEN` you set in step 1 |

**SAVE & RECONNECT**. Then turn **demo mode off** in the Home menu — it is on by
default, and a stand-in desk will otherwise sit in front of the real one.

The transport pill tells you which link you are on: **LAN** for the desk direct,
**CLOUD in gold** for the gateway. Gold is deliberate — on a cloud session with
the desk off, there is no PC control.

---

## What you get

| | desk on LAN | cloud, desk on | cloud, desk off |
| --- | --- | --- | --- |
| Chat, questions, lookups | ✅ | ✅ | ✅ |
| PC control, files, terminal | ✅ | ✅ | ❌ (says so plainly) |
| Real memory | ✅ | ✅ | queued for the desk |
| Vitals in Reports | ✅ | ✅ (polled every 15s) | empty — the cloud has no numbers and will not invent any |
| Voice notes | ✅ | ✅ | ✅ |

## Full power — when the desk wakes up

A cloud session starts on the light brain. The moment the desk attaches to
`/desk-link`, the same socket reaches the real machine, and the phone is told so
rather than having to re-dial:

```json
{"type": "desk", "linked": true}
```

The app reads that into `hud.deskLinked`, flips its transport readout from CLOUD
to **FULL POWER**, and raises *"J.A.R.V.I.S. is on full power"*. The reverse flip
is silent on purpose — losing the desk is a quiet downgrade, not something worth
buzzing a pocket for.

**A phone with no socket still gets told.** Android suspends a backgrounded app
and the WebSocket dies with it, which is exactly the state the phone is in when
the desk wakes at 2am. So the phone registers a push address:

```
POST /app-push/register
Authorization: Bearer <the same APP_TOKEN>
{"push_token": "ExponentPushToken[…]", "platform": "android"}
```

The gateway pushes through Expo's relay (no Firebase service account needed —
Expo holds the FCM credentials), and **only when no phone is holding a socket**:
a listening phone raises its own notification from the frame, and two
notifications for one event is a bug you feel rather than read. `APP_PUSH_MIN_GAP_SECS`
(default 300) stops a flapping desk becoming a burst.

`/health` gained `push_targets` — how many phones can be reached while asleep.

### Push needs one thing from Expo, once

Registration working is not the same as delivery working. With no FCM key uploaded
to the Expo project, the relay refuses the send and the gateway logs it plainly:

```
[CLOUD] push -> 1 target(s): {"data":[{"status":"error",
  "message":"Unable to retrieve the FCM server key for the recipient's app...",
  "details":{"error":"InvalidCredentials","fault":"developer"}}]}
```

Fix, in the mobile repo, no code: `eas credentials -p android` → *Google Service
Account Key for Push Notifications (FCM V1)* → upload the service-account JSON
from the same Firebase project as `google-services.json`.

### Why the token no longer appears in the log

`APP_TOKEN` travels as a query parameter because React Native cannot set headers
on a WebSocket handshake — and uvicorn logs the whole request line, so every
phone connection used to print

```
"WebSocket /app-link?token=<the actual secret>" [accepted]
```

into Render's log, where anyone with dashboard access could read it. Worse when
`APP_TOKEN` is left to fall back to `BRIDGE_SECRET`, since one leaked string then
opens both doors. `_RedactQuerySecrets` now filters `uvicorn.access` and rewrites
any `token=` value to `<redacted>`; the logs are otherwise untouched, because they
are how this bridge gets debugged.

**Set `APP_TOKEN` to its own value rather than relying on the fallback.** Then the
phone's credential can be rotated without dropping the desk link, and a leak of
one is not a leak of both.

## Voice

The gateway accepts a recorded clip today — raw bytes on the socket, or
`{"type":"voice","format":"m4a","audio":"<base64>"}` — transcribes it with Groq
Whisper (Bengali and Benglish included, same path as Telegram voice notes), and
sends the transcript back as its own frame so the chat log shows **you** said it.

**The phone cannot record yet.** `expo-audio` is not a dependency, so the mic
button on the command bar is still inert. Wiring it is one screen plus a
permission string plus another dev build — the server side is finished and
tested, so nothing about it is blocked.

## If it does not connect

1. `/health` → `"app_link": true`? If false, `APP_TOKEN` did not save.
2. Token in the app **exactly** equal to `APP_TOKEN`? A mismatch closes the
   socket immediately; the Render log prints
   `[CLOUD] REFUSED app-link token mismatch from …`.
3. Demo mode off?
4. First try after a quiet spell can miss — Render's free tier sleeps and takes
   tens of seconds to wake. The app re-probes every 5s and will catch it.
5. `"desk_linked": false` while the PC is on → the desk bridge is down, not the
   app. Check the desk's `[BRIDGE]` log lines.

## What is still owed

- The **§7 hardware live gate** — this weekend, per your plan. Nothing here has
  been run against a real phone yet: it is proved by 29 backend checks and 335
  app tests, not by a device.
- The **recorder** on the phone (above).
- Script CRUD, run history and push notifications — the app's Reports and
  Scripts tabs still read fixtures for those. Unrelated to the link.
