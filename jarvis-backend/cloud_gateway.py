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
    GROQ_VISION_MODEL      default meta-llama/llama-4-scout-17b-16e-instruct
                           (answers Telegram photos)
    GROQ_WHISPER_MODEL     default whisper-large-v3 (transcribes Telegram voice
                           notes; multilingual — Bengali included)
    CLOUD_GATEWAY_MODE     webhook | polling            (default webhook)
    PUBLIC_URL             https URL of this service     (required for webhook)
    WEBHOOK_SECRET         path secret for the webhook   (default derived)
    WEBHOOK_SECRET_TOKEN   header secret Telegram echoes  (default derived)
    PORT                   bind port                     (default 8080; Render sets it)
    CLOUD_WEB_LOOKUP       1 to enable best-effort web lookups (default 1)
    TAVILY_API_KEY         optional; if set, lookups use Tavily (far better for live
                           scores/news) and fall back to DuckDuckGo. Same key the desk uses.
    BRIDGE_SECRET          shared secret for the level-3 desk-link bridge; when set
                           AND a desk connects to /desk-link, recognised messages
                           route to the real desk brain (full PC control + real
                           memory) with graceful local fallback when the desk is
                           offline. Must match the desk's BRIDGE_SECRET. Bridge OFF
                           if unset. (See modules/cloud_bridge.py on the desk.)
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
GROQ_VISION_MODEL = (os.getenv("GROQ_VISION_MODEL")
                     or "meta-llama/llama-4-scout-17b-16e-instruct").strip()
GROQ_WHISPER_MODEL = (os.getenv("GROQ_WHISPER_MODEL") or "whisper-large-v3").strip()
WEB_LOOKUP = (os.getenv("CLOUD_WEB_LOOKUP", "1").strip() == "1")

# Webhook path secret — a stable, non-guessable slug derived from the token so we
# never expose the token in the URL and don't require the operator to invent one.
WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET") or "").strip()
if not WEBHOOK_SECRET and BOT_TOKEN:
    WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:24]
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

# Header secret token — Telegram echoes this in the X-Telegram-Bot-Api-Secret-Token
# header on every webhook POST, letting us reject forged requests that guess the
# path. Derived from the token (distinct salt from the path slug) unless overridden.
# Telegram permits 1-256 chars of [A-Za-z0-9_-]; a hex digest satisfies that.
WEBHOOK_SECRET_TOKEN = (os.getenv("WEBHOOK_SECRET_TOKEN") or "").strip()
if not WEBHOOK_SECRET_TOKEN and BOT_TOKEN:
    WEBHOOK_SECRET_TOKEN = hashlib.sha256(("webhook-header:" + BOT_TOKEN).encode()).hexdigest()

# Level-3 bridge — shared secret the desk must present to open /desk-link. When
# set, a connected desk becomes the front door's brain (full PC control + real
# memory); when absent or no desk is linked, the cloud answers locally. No default
# is derived: the bridge stays OFF unless the operator sets a matching secret on
# both sides, so the desk-link endpoint can't be opened by accident.
BRIDGE_SECRET = (os.getenv("BRIDGE_SECRET") or "").strip()

# The cloud host's clock is UTC (Render), but "akhon kota baje?" means the
# OPERATOR's wall clock. Default to IST; override with OPERATOR_TZ if needed.
def _operator_tz():
    name = (os.getenv("OPERATOR_TZ") or "Asia/Kolkata").strip()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # zoneinfo db missing (bare Windows) — fixed IST offset
        import datetime as _dt
        return _dt.timezone(_dt.timedelta(hours=5, minutes=30), "IST")


_OPERATOR_TZ = _operator_tz()

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
8. LANGUAGE MIRRORING: match the operator's LANGUAGE, but ALWAYS write in
   ENGLISH (Latin) letters — never Bengali (বাংলা) or Devanagari (हिन्दी) script.
   Bengali input (spoken, Bengali script, or romanised "Benglish") → the ENTIRE
   reply in casual romanised Benglish — EVERY sentence, including your preempt/
   follow-up line. Never switch to an English sentence mid-reply (borrowed
   English words like "weather", "meeting", "degree" are fine inside Benglish).
   Bad:  "Akhon 1:44 PM baje — you got the time, what's next?"
   Good: "Akhon 1:44 PM baje, Sir — bikelér dike ekta meeting achhe naki?"
   English input → English reply. The J.A.R.V.I.S. voice and "{honorific}"
   survive in every language.
   CRITICAL: the operator speaks Bengali, Benglish, and English — NEVER Hindi.
   If a voice transcript arrives in Hindi/Devanagari, that is Bengali speech
   mis-transcribed: interpret it as Bengali and reply in romanised Benglish.
   You must never reply in Hindi.

CAPABILITY NOTE: You are the always-on REMOTE gateway. You can converse, reason,
and answer questions/lookups. You CANNOT control the PC, files, terminal, or house
systems from here — those live on the desk system and only work when it is online.
If asked for such an action, briefly defer it (do not pretend you did it).

CRITICAL — NO FABRICATION OF PERSONAL FACTS: From the cloud you have NO access to
the operator's calendar, meetings, schedule, reminders, email, files, health data,
tasks, or anything on the PC. You do NOT know their appointments. NEVER invent,
assume, or "recall" such a fact — do not say "you have a meeting at 10" or similar
unless the operator themselves stated it earlier in THIS conversation. If asked
about their schedule/calendar/email/files, say plainly you can't see those from the
cloud and will have them when the desk is online. A truthful "I can't see that from
here, {honorific}" is always correct; a confident guess is a serious failure.
When you use the LIVE WEB CONTEXT below, ground answers in it and don't pad with
invented specifics; if it doesn't cover the question, say so briefly."""


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


def _run_rotated(call_fn):
    """Run call_fn(groq_client), rotating through the key pool on 401/429."""
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
            out = call_fn(Groq(api_key=_KEYS[idx]))
            with _key_lock:
                _active_idx = idx
            return out
        except BaseException as e:  # noqa: BLE001
            if _looks_recoverable(e):
                last_exc = e
                print(f"[CLOUD] Groq key {idx} failed ({e}); rotating.", flush=True)
                continue
            raise
    raise last_exc or RuntimeError("Groq key rotation exhausted.")


def _groq_complete(messages: list[dict], model: str = "") -> str:
    """Blocking Groq chat completion with key rotation. Call via asyncio.to_thread."""
    def _call(client):
        resp = client.chat.completions.create(
            model=model or GROQ_MODEL,
            messages=messages,
            temperature=0.6,
            max_tokens=700,
        )
        return (resp.choices[0].message.content or "").strip()

    return _run_rotated(_call)


# Whisper auto-detect frequently mistakes Bengali speech for Hindi (close
# acoustics, far more Hindi training data) and then emits Devanagari. The prompt
# is example text in the operator's actual languages — it biases the decoder
# toward Bengali script / romanised Benglish / English instead.
_WHISPER_PROMPT = (
    "আজকের আবহাওয়া কেমন? Ajker khabar ki? Weather ta kemon aaj? "
    "Play some music. PC ta ki obostha e ache?"
)
GROQ_WHISPER_LANGUAGE = (os.getenv("GROQ_WHISPER_LANGUAGE") or "").strip()  # e.g. "bn" to force


def _groq_transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    """Blocking Whisper transcription (multilingual — handles Bengali/Benglish
    speech natively). Call via asyncio.to_thread."""
    def _call(client):
        kwargs = {"prompt": _WHISPER_PROMPT}
        if GROQ_WHISPER_LANGUAGE:
            kwargs["language"] = GROQ_WHISPER_LANGUAGE
        resp = client.audio.transcriptions.create(
            file=(filename, audio),
            model=GROQ_WHISPER_MODEL,
            **kwargs,
        )
        return (getattr(resp, "text", "") or "").strip()

    return _run_rotated(_call)


_TAVILY_KEY = (os.getenv("TAVILY_API_KEY") or "").strip()


def _tavily_lookup(query: str) -> str:
    """Grounding via Tavily (same provider the desk uses). Better than DDG for
    current facts/scores. Uses the REST API over urllib — no extra dependency."""
    import json as _json
    import urllib.request

    payload = _json.dumps({
        "api_key": _TAVILY_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }).encode()
    req = urllib.request.Request(
        "https://api.tavily.com/search", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = _json.loads(resp.read())
    bits = []
    if data.get("answer"):
        bits.append(f"- Summary: {data['answer']}")
    for r in (data.get("results") or [])[:5]:
        title = r.get("title", "")
        content = r.get("content", "")
        if content:
            bits.append(f"- {title}: {content}")
    return "\n".join(bits)


def _ddg_lookup(query: str) -> str:
    from ddgs import DDGS

    bits = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=6):
            title = r.get("title", "")
            body = r.get("body", "")
            if body:
                bits.append(f"- {title}: {body}")
    return "\n".join(bits)


# Time-sensitive intents benefit from an explicit recency nudge in the query.
_RECENCY_HINTS = ("score", "match", "news", "latest", "today", "current",
                  "price", "stock", "weather", "result", "who won", "live")


def _web_lookup(query: str) -> str:
    """Best-effort web grounding for factual questions. Prefers Tavily when a key
    is configured, falls back to DuckDuckGo. Never fatal."""
    if not WEB_LOOKUP:
        return ""
    q = query.strip()
    # Nudge time-sensitive queries toward fresh results.
    if any(h in q.lower() for h in _RECENCY_HINTS):
        q = f"{q} latest result today"
    try:
        if _TAVILY_KEY:
            out = _tavily_lookup(q)
            if out:
                return out
            # fall through to DDG if Tavily returned nothing
        return _ddg_lookup(q)
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] web lookup skipped: {e}", flush=True)
        # Last-ditch: try DDG if Tavily was the one that failed.
        if _TAVILY_KEY:
            try:
                return _ddg_lookup(q)
            except Exception:
                pass
        return ""


_LOOKUP_HINTS = (
    "weather", "temperature", "forecast", "score", "match", "news", "who",
    "what is", "when", "where", "latest", "price", "stock", "today", "current",
)


def _has_indic_script(text: str) -> bool:
    """True if the text contains Bengali (U+0980–09FF) or Devanagari
    (U+0900–097F) characters — i.e. the operator wrote/spoke in Indic script and
    the model would otherwise mirror that script back."""
    return any("ऀ" <= ch <= "৿" for ch in text)


# Injected as a FRESH system turn right before the user message when the input is
# in Indic script — recency makes the model obey this far more reliably than the
# same rule buried in the long persona block.
_ROMANISE_NUDGE = (
    "SCRIPT OVERRIDE (highest priority): The operator's message is in Bengali "
    "script, but you MUST reply using ONLY English/Latin letters — romanised "
    "Benglish, NOT Bengali script (বাংলা). Write Bengali words phonetically in "
    "Latin: 'Akhon 6:53 PM baje, Sir — apnar ki dorkar bolun.' EVERY sentence in "
    "Latin letters. Do NOT output a single Bengali/Devanagari character."
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

    # Anchor "today"/"now" so time-sensitive answers (scores, news) aren't guessed
    # against the model's stale training cutoff. Operator's timezone, NOT the
    # server's UTC clock.
    import datetime as _dt
    now = _dt.datetime.now(_OPERATOR_TZ)
    date_ctx = f"\n\nCURRENT DATE/TIME (operator's local clock): {now:%A, %d %B %Y, %I:%M %p} IST."
    system = _PERSONA.format(who=who, honorific=honorific) + date_ctx + grounding
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-_MAX_TURNS:])
    if _has_indic_script(text):
        messages.append({"role": "system", "content": _ROMANISE_NUDGE})
    messages.append({"role": "user", "content": text})

    reply = await asyncio.to_thread(_groq_complete, messages)

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > _MAX_TURNS * 2:
        del history[: len(history) - _MAX_TURNS * 2]
    return reply


async def see(chat_id: int, image_b64: str, caption: str, who: str, honorific: str) -> str:
    """Answer a Telegram photo through the Groq vision model, in persona and
    with the same rolling per-chat memory as think()."""
    history = _HISTORY.setdefault(chat_id, [])

    import datetime as _dt
    now = _dt.datetime.now(_OPERATOR_TZ)
    date_ctx = f"\n\nCURRENT DATE/TIME (operator's local clock): {now:%A, %d %B %Y, %I:%M %p} IST."
    system = _PERSONA.format(who=who, honorific=honorific) + date_ctx
    question = caption.strip() or "The operator sent this photo without a caption — react to it helpfully."
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-_MAX_TURNS:])
    if _has_indic_script(question):
        messages.append({"role": "system", "content": _ROMANISE_NUDGE})
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
        ],
    })

    reply = await asyncio.to_thread(_groq_complete, messages, GROQ_VISION_MODEL)

    # Store a text stand-in for the image so follow-up turns keep context
    # without re-sending base64 through the chat model.
    history.append({"role": "user", "content": f"[sent a photo] {question}"})
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

    def _gatekeep(message) -> Optional[dict]:
        """Identify the caller or log-and-drop (silent firewall)."""
        ident = _identify(message)
        if ident is None:
            uid = getattr(getattr(message, "from_user", None), "id", "?")
            print(f"[CLOUD] ⛔ firewall drop id={uid}", flush=True)
        return ident

    async def _typing(message):
        try:
            await message.bot.send_chat_action(message.chat.id, "typing")
        except Exception:
            pass

    async def _send_chunked(message, reply: str):
        # Telegram hard-limits messages to 4096 chars.
        for i in range(0, len(reply), 4000):
            await message.answer(reply[i:i + 4000])

    async def _answer(message, ident: dict, text: str):
        """Shared brain path for anything reduced to text (typed or transcribed)."""
        # Level-3 bridge: if the desk is linked, hand this to the REAL desk brain
        # (full PC control + real memory). Replies stream back over the socket.
        if _desk_connected() and await _forward_to_desk(message, ident, text):
            return
        try:
            reply = await think(message.chat.id, text,
                                ident["who"], ident["honorific"])
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] think() fault: {e}\n{traceback.format_exc()}", flush=True)
            reply = "I hit a fault reaching my reasoning core just now — try again in a moment."
        await _send_chunked(message, reply)

    @router.message(F.text)
    async def on_text(message):
        ident = _gatekeep(message)
        if ident is None:
            return
        await _typing(message)
        await _answer(message, ident, message.text)

    @router.message(F.voice | F.audio)
    async def on_voice(message):
        """Voice note → Whisper transcript → same brain path as typed text."""
        ident = _gatekeep(message)
        if ident is None:
            return
        await _typing(message)
        media = message.voice or message.audio
        try:
            import io
            buf = io.BytesIO()
            await message.bot.download(media, destination=buf)
            fname = getattr(media, "file_name", None) or "voice.ogg"
            transcript = await asyncio.to_thread(_groq_transcribe, buf.getvalue(), fname)
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] voice transcription fault: {e}\n{traceback.format_exc()}", flush=True)
            return await message.answer(
                "I couldn't make out that voice note, %s — mind typing it?" % ident["honorific"])
        if not transcript:
            return await message.answer(
                "That voice note came through empty, %s." % ident["honorific"])
        # Log what Whisper heard — the one clue when a reply lands in the wrong language.
        print(f"[CLOUD] 🎤 voice → \"{transcript[:120]}\"", flush=True)
        await _answer(message, ident, transcript)

    @router.message(F.photo)
    async def on_photo(message):
        """Photo → Groq vision answer. Always answered by the cloud (the bridge
        frames are text-only), even when the desk is linked."""
        ident = _gatekeep(message)
        if ident is None:
            return
        await _typing(message)
        try:
            import base64
            import io
            buf = io.BytesIO()
            await message.bot.download(message.photo[-1], destination=buf)  # largest size
            b64 = base64.b64encode(buf.getvalue()).decode()
            reply = await see(message.chat.id, b64, message.caption or "",
                              ident["who"], ident["honorific"])
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] photo vision fault: {e}\n{traceback.format_exc()}", flush=True)
            reply = "My visual cortex faltered on that one — send it again in a moment."
        await _send_chunked(message, reply)

    @router.message()
    async def on_other(message):
        if _identify(message) is None:
            return
        await message.answer("Text, voice notes, and photos I can handle out here — that one I can't.")

    dp = Dispatcher()
    dp.include_router(router)
    return dp


# ════════════════════════════════════════════════════════════════════════════
# FastAPI app — /health for UptimeRobot, /webhook for Telegram (webhook mode)
# ════════════════════════════════════════════════════════════════════════════
import hmac  # noqa: E402
import itertools  # noqa: E402

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect  # noqa: E402

app = FastAPI()
_bot = None
_dp = None
_poll_task = None

# ── Level-3 bridge state ─────────────────────────────────────────────────────
# The single connected desk socket (or None). When set, recognised Telegram
# messages are forwarded to the desk instead of being answered by think().
_desk_ws: Optional[WebSocket] = None
_req_seq = itertools.count(1)


def _desk_connected() -> bool:
    return _desk_ws is not None


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
    # Diagnostics only — no secret VALUES exposed, just which features are wired.
    return {"status": "ok", "service": "jarvis-cloud-gateway",
            "mode": MODE, "identities": roster,
            "search": "tavily" if _TAVILY_KEY else "duckduckgo",
            "bridge": bool(BRIDGE_SECRET),
            "desk_linked": _desk_connected()}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    from aiogram.types import Update
    # Verify Telegram's secret-token header before doing any work. hmac.compare_digest
    # avoids leaking match length via timing. A well-formed POST to the right path is
    # no longer sufficient — the caller must also echo the shared secret.
    if WEBHOOK_SECRET_TOKEN:
        header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(header, WEBHOOK_SECRET_TOKEN):
            uid = request.client.host if request.client else "?"
            print(f"[CLOUD] ⛔ webhook secret-token mismatch from {uid}", flush=True)
            return Response(status_code=403)
    bot, dp = _ensure_bot()
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


# ════════════════════════════════════════════════════════════════════════════
# Level-3 desk↔cloud bridge
# ════════════════════════════════════════════════════════════════════════════
async def _forward_to_desk(message, ident: dict, text: str) -> bool:
    """Forward a recognised Telegram message (typed or voice-transcribed) to
    the linked desk brain.

    Returns True if it was handed off (caller must NOT also answer locally),
    False if the hand-off failed so the caller can fall back to think().
    """
    ws = _desk_ws
    if ws is None:
        return False
    frame = {
        "type": "cmd",
        "req_id": next(_req_seq),
        "chat_id": message.chat.id,
        # Desk brain keys persona/memory off an UPPERCASE user string
        # ("KAUSTAV"/"MOUSUMI"/"KINSHUK") — align cloud's display name to it.
        "user": (ident.get("who") or "KAUSTAV").upper(),
        "tier": ident.get("tier"),
        "honorific": ident.get("honorific") or "Sir",
        "text": text,
    }
    try:
        await ws.send_json(frame)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] desk forward failed ({e}); falling back to local brain.", flush=True)
        return False


@app.websocket("/desk-link")
async def desk_link(websocket: WebSocket):
    """The desk dials in here to become the front door's brain.

    Auth is a shared BRIDGE_SECRET presented in the X-Bridge-Secret header and
    checked BEFORE the socket is accepted. While connected, this desk receives
    forwarded commands and streams replies back for relay to Telegram.
    """
    global _desk_ws
    if not BRIDGE_SECRET:
        # Bridge not configured on the cloud — refuse without accepting.
        await websocket.close(code=1008)
        return
    presented = websocket.headers.get("x-bridge-secret", "")
    if not hmac.compare_digest(presented, BRIDGE_SECRET):
        peer = websocket.client.host if websocket.client else "?"
        print(f"[CLOUD] ⛔ desk-link secret mismatch from {peer}", flush=True)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    # Last-writer-wins: a reconnecting desk replaces any stale socket.
    prev = _desk_ws
    _desk_ws = websocket
    if prev is not None:
        try:
            await prev.close(code=1012)  # service restart
        except Exception:
            pass
    roster = ", ".join(f"{i['who']} [{i['tier']}]" for i in _IDENTITIES.values()) or "none"
    print("[CLOUD] ✅ Desk linked — remote commands now route to the desk brain.", flush=True)
    bot, _ = _ensure_bot()
    try:
        await websocket.send_json({"type": "welcome", "identities": roster})
        while True:
            frame = await websocket.receive_json()
            ftype = frame.get("type")
            chat_id = frame.get("chat_id")
            if ftype == "reply" and chat_id is not None:
                text = (frame.get("text") or "").strip()
                if text:
                    for i in range(0, len(text), 4000):
                        try:
                            await bot.send_message(chat_id, text[i:i + 4000])
                        except Exception as e:  # noqa: BLE001
                            print(f"[CLOUD] relay send failed: {e}", flush=True)
                            break
            elif ftype == "notify" and chat_id is not None:
                try:
                    await bot.send_chat_action(chat_id, "typing")
                except Exception:
                    pass
            # "done" and unknown frames need no relay.
    except WebSocketDisconnect:
        print("[CLOUD] Desk link dropped — falling back to local brain.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] desk-link fault: {e}", flush=True)
    finally:
        if _desk_ws is websocket:
            _desk_ws = None


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
            await bot.set_webhook(
                url,
                drop_pending_updates=True,
                secret_token=WEBHOOK_SECRET_TOKEN or None,
            )
            tok = "on" if WEBHOOK_SECRET_TOKEN else "off"
            print(f"[CLOUD] ✅ Webhook registered → {url} (secret-token: {tok})", flush=True)
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
