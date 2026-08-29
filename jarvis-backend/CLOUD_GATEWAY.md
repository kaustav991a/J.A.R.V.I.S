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

## The phone: `WS /app-link` (DONE — 2026-08-12)

The mobile app ([J.A.R.V.I.S-Mobile](https://github.com/kaustav991a/J.A.R.V.I.S-Mobile),
branch `feat/mobile-hud`) dials the gateway here. Same brain, same routing as
Telegram: **desk linked → the real desk answers; desk off → the cloud brain
answers**, and the phone never has to know which.

**Turn it on:** set **`APP_TOKEN`** in the Render dashboard (any long random
string) and redeploy. Leave it unset and it falls back to `BRIDGE_SECRET`; with
both unset the route refuses every connection and `/health` reports
`"app_link": false`, which is the flag the app reads before it will fall back to
the cloud at all. Put the same value into the app's Connection screen.

| | |
|---|---|
| URL | `wss://<service>.onrender.com/app-link?token=<APP_TOKEN>` |
| Auth | query parameter — React Native's `WebSocket` cannot set handshake headers |
| Phone sends | a bare command string, raw audio **bytes** for a voice note, or `{"type":"voice","format":"m4a","audio":"<base64>"}` |
| Phone reads | the desk's own frame shapes: `{"status":…,"message":…,"user":…}`, `{"status":"sync","type":"telemetry","data":{…}}`, and `{"type":"transcript","text":…}` |

Notes worth knowing:

- **Voice works from day one.** A clip is transcribed by the same Groq Whisper
  path Telegram voice notes use (multilingual — Bengali and Benglish included),
  and the transcript comes back as its own frame so the phone logs it as *him*
  speaking rather than as J.A.R.V.I.S.
- **Telemetry is real or absent.** While a desk is linked the gateway asks it for
  a vitals snapshot every `APP_TELEMETRY_SECS` (default 15) and forwards it. With
  no desk there are no numbers — the cloud never invents them.
- **A keepalive every `APP_KEEPALIVE_SECS`** (default 20) stops the phone's 30s
  frame watchdog from tearing down an idle socket. It carries no message, so it
  cannot write a line into the chat log.
- **Telegram is untouched.** The one shared code path is the `/desk-link` reader,
  where a frame is handed to a phone only if its `req_id` was registered by a
  phone request. `APP_CHAT_ID` (default `-90001`) is never a real chat, and the
  relay refuses it outright as a second line of defence.

Harness: `test_app_link.py` (29 checks) — auth refusals, the health flag, desk
routing, silent-desk fallback, voice in both encodings, and the parser.

> ⚠️ When running `cloud_gateway.py` **locally** for a test, NEVER start it in **polling**
> mode against your real bot token — polling first calls `delete_webhook`, which knocks the
> live production gateway off its webhook. Use webhook mode without `PUBLIC_URL` (no network),
> or a throwaway test bot token.

---

## Capability tokens, and rotating a secret without locking yourself out (2026-08-29)

Two rows of the security goal, and one change. Before this, **one string opened
five doors and none of them ever closed**: the socket (a brain that answers as
him), the push address, the commute schedule, the fact store that feeds every
system prompt, and `/app-say`, which puts words in the assistant's mouth. Any
leak of that string was a leak of all five, permanently.

### What the phone does now

```
POST /app-tokens          Authorization: Bearer <APP_TOKEN>      # master only
  -> {"tokens": {"link": "j1.link.<exp>.<mac>", "push": …, "state": …,
                 "memory": …, "say": …},
      "expires_at": 1767000000, "ttl_days": 30}
```

A capability token is `j1.<cap>.<exp>.<mac>`, where the mac is HMAC-SHA256 over
the first three fields **keyed by `APP_TOKEN` itself**. Nothing is stored, and
three properties fall out of that:

- **verification is stateless** — any instance verifies any token, which matters
  on a platform that replaces the container without warning;
- **rotation is revocation** — change `APP_TOKEN` and every derived token stops
  verifying in the same instant, with no table to clean up;
- **a capability token cannot mint another.** `/app-tokens` takes the master and
  nothing else, so a leaked `push` token buys the push route until it expires and
  nothing more.

The master still opens every route, deliberately: the installed app presents it,
and an auth change that locks him out of his own assistant would be worse than
the leak it prevents. `/health` counts every master use per route, so finishing
the migration is a number rather than a hope:

```json
"app_auth": {"capabilities": ["link","push","state","memory","say"],
             "ttl_days": 30, "master_calls": {"app-fact": 2}}
```

`APP_TOKEN_TTL_DAYS` (default 30) is declared in `render.yaml`. A caller may ask
for a shorter TTL and never a longer one.

Refusals say which kind they are, because the phone's move differs: `401
{"error":"token_expired"}` means mint again (the app does, once, then retries),
and `403 {"error":"wrong_capability"}` means the client used the wrong one of its
own tokens — a bug, not an intruder. The socket has only close code 1008, so
there the distinction lives in the log.

### Rotating `BRIDGE_SECRET`

The live value went through Render's access log before redaction landed, and it
still opens `/desk-link`. What blocked the rotation was ordering rather than
work: the gateway and the desk read the same secret from two different places, so
whichever moved first locked the other out — and the desk may be **off** when the
change is made.

Both are accepted for one window now:

1. set `BRIDGE_SECRET` to the new value and **`BRIDGE_SECRET_OLD` to the outgoing
   one**, and deploy. Nothing breaks: the desk is still connecting on the old one;
2. move the desk's `.env` to the new value whenever that machine is next on, and
   restart the bridge;
3. watch `/health`:

   ```json
   "bridge_rotation": {"old_accepted": true, "connects_on_old": 0}
   ```

   Every connect still arriving on the old secret is counted and logged loudly.
   Zero after the desk has reconnected means step 4 is safe;
4. delete `BRIDGE_SECRET_OLD` and deploy. `bridge_rotation` goes `null`, and the
   leaked value opens nothing.

Harness: `test_app_tokens.py` (68 checks) — one token per door and refused at the
other four, expiry and its boundary, master-only minting, rotation-as-revocation,
the mac recomputed from the primitives, and a source pin that no route compares
against the master by hand again.
