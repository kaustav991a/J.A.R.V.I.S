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
