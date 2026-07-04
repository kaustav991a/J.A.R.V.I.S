# J.A.R.V.I.S. Cloud Gateway — Always-On Telegram Brain

Reach J.A.R.V.I.S. from Telegram **even when your desk PC is off**, for **$0** on
Render's free tier kept warm by UptimeRobot.

## What this is (and isn't)

| | Cloud Gateway (this) | Desk J.A.R.V.I.S. (main.py) |
|---|---|---|
| Runs on | Render/VPS, 24/7 | your Windows PC |
| Needs the PC | ❌ no | ✅ yes |
| Chat, Q&A, weather/scores/news lookups | ✅ | ✅ |
| Telegram voice notes (Groq Whisper, multilingual incl. Bengali) | ✅ | ✅ |
| Telegram photos (Groq Llama-4 vision) | ✅ | ✅ (described, then answered by the desk brain) |
| Bengali / Benglish / English — mirrors your language | ✅ | ✅ |
| PC control, files, terminal, mic/HUD/camera vision | ❌ (politely defers) | ✅ |
| Memory | own rolling chat memory (resets on restart) | full ChromaDB/SQLite |

Same bot, same persona, same identity firewall. The two run **independently** —
they do **not** share memory yet (see "Later" below).

## Files
- `cloud_gateway.py` — the whole gateway (self-contained; imports none of the desk stack).
- `requirements-cloud.txt` — tiny dep set (no torch/tensorflow/pyaudio/pywin32).
- `render.yaml` — Render Blueprint (lives at the **repo root**, not in this folder).

## Deploy to Render (free)

1. **Push to GitHub.** `cloud_gateway.py`, `requirements-cloud.txt`, `render.yaml`
   are safe to commit. Your `.env` is gitignored — secrets are set in the dashboard, not committed.

2. **Create the service.** Render → **New** → **Blueprint** → pick this repo.
   It reads `render.yaml` and creates `jarvis-cloud-gateway` (free plan).

3. **Set the secret env vars** (Render → service → Environment):
   - `TELEGRAM_BOT_TOKEN` — your bot token
   - `TELEGRAM_USER_ID` — your numeric id
   - `TELEGRAM_GF_ID`, `TELEGRAM_BROTHER_ID` — optional VIP ids
   - `GROQ_API_KEYS` — your comma-separated Groq keys
   - `PUBLIC_URL` — leave blank for the first deploy.
   - `WEBHOOK_SECRET_TOKEN` — *optional.* A header secret Telegram echoes on every
     webhook POST so forged requests to the path are rejected. Leave unset and a
     stable one is derived from the bot token automatically; set it only if you
     want to pin your own value.

4. **First deploy** → Render assigns a URL like
   `https://jarvis-cloud-gateway.onrender.com`. Copy it, set **`PUBLIC_URL`** to
   exactly that (no trailing slash), and **Save** (Render redeploys). On boot the
   log prints `✅ Webhook registered → …/webhook/…`.

5. **Test:** open Telegram, send `/start`, then chat. J.A.R.V.I.S. replies.

## Keep it awake with UptimeRobot (free)

Render free services sleep after ~15 min idle. Prevent it:

1. [uptimerobot.com](https://uptimerobot.com) → **Add New Monitor**.
2. Type **HTTP(s)**, URL = `https://<your-service>.onrender.com/health`, interval **5 min**.
3. Save. The pings keep the service warm → replies stay instant.

`/health` returns `{"status":"ok", ...}` — no secrets exposed.

## Local test (optional)

```bash
cd jarvis-backend
pip install -r requirements-cloud.txt
CLOUD_GATEWAY_MODE=polling python cloud_gateway.py
```
Polling mode needs no PUBLIC_URL — good for testing on your own machine. It reads
the same `.env` as the desk stack.

> ⚠️ Don't run **polling** on the cloud while the **desk** bot is also polling the
> **same bot token** — two pollers fight over updates. For a shared bot, keep the
> cloud on **webhook** (default) and the desk on polling; they coexist fine.

## Level-3 desk↔cloud bridge (DONE — 2026-07-04)

The cloud gateway can now be the **single front door**: when your desk PC is online,
it forwards each recognized message to the **real desk brain** (full PC control + real
memory) over an authenticated WebSocket, and falls back to its own local brain when the
PC is off. There is only one memory, and no bot-token contention.

**Enable it (both sides share one secret):**

1. **Cloud** — set `BRIDGE_SECRET` in the Render dashboard (any long random string) and
   redeploy. Keep the cloud on **webhook** mode.
2. **Desk** — in `jarvis-backend/.env`:
   ```dotenv
   JARVIS_CLOUD_BRIDGE=1
   JARVIS_BRIDGE_URL=wss://jarvis-cloud-gateway.onrender.com/desk-link
   BRIDGE_SECRET=<same secret as the cloud>
   ```
   On boot the desk logs `[BRIDGE] ✅ Linked to cloud front door → …`; the cloud logs
   `[CLOUD] ✅ Desk linked — remote commands now route to the desk brain.`

When the bridge is enabled, the desk starts the bridge **instead of** its own Telegram
poller (`main.py`), so nothing competes for the token. Turn it off (`JARVIS_CLOUD_BRIDGE=0`)
to revert to the desk polling directly.

- **Level 2 — shared memory** (not needed once the bridge is on): point both at one hosted
  DB. The bridge supersedes this by keeping memory on the desk.

> ⚠️ When running `cloud_gateway.py` **locally** for a test, NEVER start it in **polling**
> mode against your real bot token — polling first calls `delete_webhook`, which knocks the
> live production gateway off its webhook. Use webhook mode without `PUBLIC_URL` (no network),
> or a throwaway test bot token.
