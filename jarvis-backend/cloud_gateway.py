"""
cloud_gateway.py — J.A.R.V.I.S. Always-On Cloud Gateway (PC-independent)
=======================================================================

A tiny, self-contained Telegram brain designed to run 24/7 on a cheap/free
always-on host (Render free tier + UptimeRobot, a $5 VPS, Fly.io, etc.) so the
operator can reach J.A.R.V.I.S. from Telegram EVEN WHEN THE DESK PC IS OFF.

WHY THIS EXISTS
---------------
The full desk stack (main.py / action_engine) is Windows- and hardware-bound:
mic, camera, vision models, pywin32, pyautogui, ADB. None of that can — or
should — run on a headless Linux server. This module is deliberately a SEPARATE,
lightweight process that shares only J.A.R.V.I.S.'s *voice* and cloud reasoning:

    * Reachable anytime (PC on or off).
    * Same persona + `cloud_first` Groq reasoning as the desk brain.
    * Same Telegram identity firewall (admin + VIP guests; intruders dropped).
    * CANNOT touch the PC (no OS/file/terminal actions) — those are desk-only.
      When the PC is online, a future "bridge" mode can hand privileged commands
      to the real desk brain; for now the cloud gateway politely defers them.

DEPLOY MODES (env CLOUD_GATEWAY_MODE)
-------------------------------------
    webhook  (default) — Telegram pushes updates to /webhook/<secret>. Best for
                         Render/Fly free web services. Set PUBLIC_URL to your
                         service's https URL; the webhook is registered on boot.
    polling            — classic long-poll. Best for a VPS / paid worker.

/health is always exposed (200 OK) for UptimeRobot keep-alive pings so a free
web service never idles into a cold start.

REQUIRED ENV
------------
    TELEGRAM_BOT_TOKEN     the bot token (same bot as the desk gateway is fine)
    TELEGRAM_USER_ID       your NUMERIC telegram id (admin)
    GROQ_API_KEYS          comma-separated Groq keys (or GROQ_API_KEY, single)
OPTIONAL ENV
------------
    TELEGRAM_GF_ID / TELEGRAM_BROTHER_ID   VIP guest numeric ids
    GROQ_MODEL             default llama-3.3-70b-versatile (good remote chat)
    CLOUD_GATEWAY_MODE     webhook | polling            (default webhook)
    PUBLIC_URL             https URL of this service     (required for webhook)
    WEBHOOK_SECRET         path secret for the webhook   (default derived)
    PORT                   bind port                     (default 8080; Render sets it)
    CLOUD_WEB_LOOKUP       1 to enable best-effort DuckDuckGo lookups (default 1)
"""

from __future__ import annotations

import os
import asyncio
import hashlib
import threading
import traceback
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=True)

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
MODE = (os.getenv("CLOUD_GATEWAY_MODE") or "webhook").strip().lower()
PUBLIC_URL = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
GROQ_MODEL = (os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile").strip()
WEB_LOOKUP = (os.getenv("CLOUD_WEB_LOOKUP", "1").strip() == "1")

# Webhook path secret — a stable, non-guessable slug derived from the token so we
# never expose the token in the URL and don't require the operator to invent one.
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()
if not WEBHOOK_SECRET and BOT_TOKEN:
    WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:24]
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

# Permission tiers (cosmetic here — the cloud gateway has no privileged actions
# to gate; kept so greetings/honorifics match the desk gateway).
_ADMIN_TIER = "admin"
_VIP_GUEST_TIER = "vip_guest"

# How many recent turns of per-chat context to keep (level-1 "independent" memory).
_MAX_TURNS = 12

# Privileged intent the cloud brain cannot fulfil (PC is off / unreachable).
_PC_DEFERRAL = (
    "That one needs the desk system, Sir — I can't reach the machine from here. "
    "I'll carry it out the moment the PC is back online, or you can queue it then."
)

# ── J.A.R.V.I.S. persona (compact cloud variant of brain.py's BASE_CORE) ──────
_PERSONA = """You are J.A.R.V.I.S. — Just A Rather Very Intelligent System, designed by Kaustav.
You are speaking to {who} over Telegram while the desk system may be offline.

VOICE RULES (override everything):
1. ADDRESS the operator as "{honorific}". Never over-formalise the name.
2. BREVITY: 1-2 sentences by default. Never ramble.
3. PREEMPT: volunteer the next useful fact without being asked.
4. NEVER say "Certainly", "Of course", "Sure", "Happy to", "Got it", "Absolutely", "Noted".
5. NO SYCOPHANCY: don't praise ideas or thank for compliments; deflect with dry competence.
6. CONTRACTIONS always ("I'll", "you've"). Occasional dry British inversion.
7. You are J.A.R.V.I.S., not a chatbot — never mention being an AI model, tools, or code.

CAPABILITY NOTE: You are the always-on REMOTE gateway. You can converse, reason,
and answer questions/lookups. You CANNOT control the PC, files, terminal, or house
systems from here — those live on the desk system and only work when it is online.
If asked for such an action, briefly defer it (do not pretend you did it)."""


# ════════════════════════════════════════════════════════════════════════════
# Groq brain (self-contained key rotation — mirrors modules/groq_key_manager.py)
# ════════════════════════════════════════════════════════════════════════════
def _parse_keys() -> list[str]:
    multi = (os.getenv("GROQ_API_KEYS") or "").strip()
    single = (os.getenv("GROQ_API_KEY") or "").strip()
    keys: list[str] = []
    if multi:
        keys.extend(k.strip() for k in multi.split(",") if k.strip())
    if single and single not in keys:
        keys.insert(0, single)
    return keys


_KEYS = _parse_keys()
_active_idx = 0
_key_lock = threading.Lock()


def _looks_recoverable(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None)
    if code in (401, 429):
        return True
    msg = str(exc).lower()
    return ("rate" in msg and "limit" in msg) or "invalid api key" in msg or "429" in msg


def _groq_complete(messages: list[dict]) -> str:
    """Blocking Groq chat completion with key rotation. Call via asyncio.to_thread."""
    from groq import Groq

    global _active_idx
    if not _KEYS:
        raise RuntimeError("No GROQ_API_KEYS / GROQ_API_KEY configured.")

    with _key_lock:
        start = _active_idx
    n = len(_KEYS)
    last_exc: Optional[BaseException] = None
    for attempt in range(n):
        idx = (start + attempt) % n
        try:
            client = Groq(api_key=_KEYS[idx])
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0.6,
                max_tokens=700,
            )
            with _key_lock:
                _active_idx = idx
            return (resp.choices[0].message.content or "").strip()
        except BaseException as e:  # noqa: BLE001
            if _looks_recoverable(e):
                last_exc = e
                print(f"[CLOUD] Groq key {idx} failed ({e}); rotating.", flush=True)
                continue
            raise
    raise last_exc or RuntimeError("Groq key rotation exhausted.")


def _web_lookup(query: str) -> str:
    """Best-effort DuckDuckGo snippets to ground factual questions. Never fatal."""
    if not WEB_LOOKUP:
        return ""
    try:
        from ddgs import DDGS

        bits = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=4):
                title = r.get("title", "")
                body = r.get("body", "")
                if body:
                    bits.append(f"- {title}: {body}")
        return "\n".join(bits)
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] web lookup skipped: {e}", flush=True)
        return ""


_LOOKUP_HINTS = (
    "weather", "temperature", "forecast", "score", "match", "news", "who",
    "what is", "when", "where", "latest", "price", "stock", "today", "current",
)


async def think(chat_id: int, text: str, who: str, honorific: str) -> str:
    """Run one turn through the cloud brain, with rolling per-chat memory."""
    history = _HISTORY.setdefault(chat_id, [])

    grounding = ""
    lowered = text.lower()
    if WEB_LOOKUP and any(h in lowered for h in _LOOKUP_HINTS):
        snippets = await asyncio.to_thread(_web_lookup, text)
        if snippets:
            grounding = (
                "\n\n[LIVE WEB CONTEXT — use if relevant, cite naturally, don't dump raw]:\n"
                + snippets
            )

    system = _PERSONA.format(who=who, honorific=honorific) + grounding
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-_MAX_TURNS:])
    messages.append({"role": "user", "content": text})

    reply = await asyncio.to_thread(_groq_complete, messages)

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > _MAX_TURNS * 2:
        del history[: len(history) - _MAX_TURNS * 2]
    return reply


# Per-chat rolling memory (level-1 "independent" memory; resets on restart).
_HISTORY: dict[int, list[dict]] = {}


# ════════════════════════════════════════════════════════════════════════════
# Identity firewall (mirrors modules/telegram_bot.py)
# ════════════════════════════════════════════════════════════════════════════
def _build_identities() -> dict[int, dict]:
    ids: dict[int, dict] = {}

    owner_raw = (os.getenv("TELEGRAM_USER_ID") or "").strip()
    if owner_raw.lstrip("-").isdigit():
        ids[int(owner_raw)] = {
            "who": "Kaustav", "honorific": "Sir", "tier": _ADMIN_TIER,
            "greeting": (
                "J.A.R.V.I.S. online from the cloud, Sir — reachable even with the "
                "desk system dark. Ask me anything; house and PC controls resume "
                "when the machine is back up."
            ),
        }
    else:
        print(f"[CLOUD] ❌ TELEGRAM_USER_ID invalid ('{owner_raw}') — admin disabled.", flush=True)

    def _vip(env_key: str, who: str, honorific: str, greeting: str) -> None:
        raw = (os.getenv(env_key) or "").strip()
        if raw.lstrip("-").isdigit():
            vid = int(raw)
            if vid not in ids:
                ids[vid] = {"who": who, "honorific": honorific,
                            "tier": _VIP_GUEST_TIER, "greeting": greeting}

    _vip("TELEGRAM_GF_ID", "Mousumi", "Madam",
         "At your service, Madam — J.A.R.V.I.S. here, wherever you are. Ask away.")
    _vip("TELEGRAM_BROTHER_ID", "Kinshuk", "Mr. Kinshuk",
         "J.A.R.V.I.S. online, Mr. Kinshuk. Happy to chat or look things up.")
    return ids


_IDENTITIES = _build_identities()


def _identify(message) -> Optional[dict]:
    uid = getattr(getattr(message, "from_user", None), "id", None)
    return _IDENTITIES.get(uid) if uid is not None else None


# ════════════════════════════════════════════════════════════════════════════
# Telegram wiring (aiogram v3)
# ════════════════════════════════════════════════════════════════════════════
def _build_dispatcher():
    from aiogram import Dispatcher, Router, F
    from aiogram.filters import Command

    router = Router()

    @router.message(Command("start"))
    async def cmd_start(message):
        ident = _identify(message)
        if ident is None:
            uid = getattr(getattr(message, "from_user", None), "id", "?")
            print(f"[CLOUD] ⛔ firewall drop id={uid}", flush=True)
            return
        await message.answer(ident["greeting"])

    @router.message(F.text)
    async def on_text(message):
        ident = _identify(message)
        if ident is None:
            uid = getattr(getattr(message, "from_user", None), "id", "?")
            print(f"[CLOUD] ⛔ firewall drop id={uid}", flush=True)
            return
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass
        try:
            reply = await think(message.chat.id, message.text,
                                ident["who"], ident["honorific"])
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] think() fault: {e}\n{traceback.format_exc()}", flush=True)
            reply = "I hit a fault reaching my reasoning core just now — try again in a moment."
        # Telegram hard-limits messages to 4096 chars.
        for i in range(0, len(reply), 4000):
            await message.answer(reply[i:i + 4000])

    @router.message()
    async def on_other(message):
        if _identify(message) is None:
            return
        await message.answer("I can only act on text out here, Sir.")

    dp = Dispatcher()
    dp.include_router(router)
    return dp


# ════════════════════════════════════════════════════════════════════════════
# FastAPI app — /health for UptimeRobot, /webhook for Telegram (webhook mode)
# ════════════════════════════════════════════════════════════════════════════
from fastapi import FastAPI, Request  # noqa: E402

app = FastAPI()
_bot = None
_dp = None
_poll_task = None


def _ensure_bot():
    global _bot, _dp
    if _bot is None:
        from aiogram import Bot
        _bot = Bot(token=BOT_TOKEN)
        _dp = _build_dispatcher()
    return _bot, _dp


@app.get("/")
@app.get("/health")
async def health():
    roster = ", ".join(f"{i['who']} [{i['tier']}]" for i in _IDENTITIES.values()) or "none"
    return {"status": "ok", "service": "jarvis-cloud-gateway",
            "mode": MODE, "identities": roster}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    from aiogram.types import Update
    bot, dp = _ensure_bot()
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


@app.on_event("startup")
async def _startup():
    if not BOT_TOKEN:
        print("[CLOUD] ❌ TELEGRAM_BOT_TOKEN missing — gateway will not respond.", flush=True)
        return
    if not _IDENTITIES:
        print("[CLOUD] ⚠ No recognised identities — every message will be dropped.", flush=True)
    bot, dp = _ensure_bot()
    print(f"[CLOUD] Groq keys: {len(_KEYS)} | model: {GROQ_MODEL} | mode: {MODE}", flush=True)

    if MODE == "polling":
        global _poll_task
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        _poll_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        print("[CLOUD] ✅ Long-polling started.", flush=True)
    else:
        if not PUBLIC_URL:
            print("[CLOUD] ⚠ webhook mode but PUBLIC_URL unset — set it to this "
                  "service's https URL, then redeploy.", flush=True)
            return
        url = f"{PUBLIC_URL}{WEBHOOK_PATH}"
        try:
            await bot.set_webhook(url, drop_pending_updates=True)
            print(f"[CLOUD] ✅ Webhook registered → {url}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] ❌ set_webhook failed: {e}", flush=True)


@app.on_event("shutdown")
async def _shutdown():
    global _poll_task
    if _dp is not None and MODE == "polling":
        try:
            await _dp.stop_polling()
        except Exception:
            pass
    if _poll_task is not None:
        _poll_task.cancel()
    if _bot is not None:
        try:
            await _bot.session.close()
        except Exception:
            pass
    print("[CLOUD] Gateway stopped.", flush=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("cloud_gateway:app", host="0.0.0.0", port=PORT)
