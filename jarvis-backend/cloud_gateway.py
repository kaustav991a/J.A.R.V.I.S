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
    GROQ_MODEL             default openai/gpt-oss-120b (llama-3.3-70b-versatile
                           was decommissioned by Groq 2026-08-16)
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

# A log character must not be able to abort an operation. This file prints 19
# non-ASCII lines and is its own process, so it cannot inherit main.py's
# hardening. See modules/utf8_stdout.py.
#
# Guarded, following this file's own rule for reaching into modules/ (see the
# fact_outbox import below): a gateway that will not BOOT is a far worse failure
# than a log line that will not encode. On Render the streams are UTF-8 already,
# so this is for a local Windows run — which is exactly where it matters.
try:
    import modules.utf8_stdout  # noqa: F401
except Exception:  # noqa: BLE001
    pass

import os
import asyncio
import hashlib
import re
import threading
import time
import traceback
from typing import NamedTuple, Optional

from dotenv import load_dotenv

# C#11a Step 4 — the sealed fact outbox. The FIRST thing this gateway imports out
# of modules/, and safe to: modules/__init__.py is a comment, and fact_outbox +
# fact_seal reach for nothing but stdlib and pynacl. Guarded anyway — a deploy
# whose requirements-cloud.txt predates pynacl must still answer Telegram, just
# without queueing.
try:
    from modules import fact_outbox
except Exception as _outbox_exc:  # noqa: BLE001
    fact_outbox = None
    print(f"[CLOUD] ⚠ sealed-fact outbox unavailable ({_outbox_exc}) — PC-off turns "
          f"will NOT be queued for the desk.", flush=True)

load_dotenv(override=True)

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
MODE = (os.getenv("CLOUD_GATEWAY_MODE") or "webhook").strip().lower()
PUBLIC_URL = (os.getenv("PUBLIC_URL") or "").strip().rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
# `llama-3.3-70b-versatile` was decommissioned by Groq on 2026-08-16. Gemini is
# the text brain now, so this is the FALLBACK leg — which is exactly the leg you
# do not find out is broken until the primary is already failing.
GROQ_MODEL = (os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b").strip()
# This default is DEAD. On 2026-08-14 a photo came back
# `404 model_not_found: meta-llama/llama-4-scout-17b-16e-instruct does not exist or
# you do not have access to it` — Groq retires model ids, and a hardcoded one ages
# into a 404 with no warning. Left in place rather than replaced with a guess that
# would age the same way: set GROQ_VISION_MODEL to something current, or point
# LLM_PROVIDER_VISION at gemini, which is what was actually done.
#
# 2026-08-19: `render.yaml` now DECLARES `GROQ_VISION_MODEL=qwen/qwen3.6-27b`, so
# a Blueprint deploy always carries a live id and this default is never reached
# there. It stays dead on purpose — as the record of how a hardcoded model id
# fails, and so that a deploy which somehow arrives without the variable fails
# loudly at the call rather than quietly answering with something unintended.
GROQ_VISION_MODEL = (os.getenv("GROQ_VISION_MODEL")
                     or "meta-llama/llama-4-scout-17b-16e-instruct").strip()
GROQ_WHISPER_MODEL = (os.getenv("GROQ_WHISPER_MODEL") or "whisper-large-v3").strip()
WEB_LOOKUP = (os.getenv("CLOUD_WEB_LOOKUP", "1").strip() == "1")

# ── Which brain answers what ─────────────────────────────────────────────────
#
# Chosen per capability, not once for everything, because the two providers are
# not better at the same things. Groq is markedly faster; Gemini follows a long
# system prompt more closely and is natively multimodal — and it can be *told*
# that a clip is code-switched Bengali/English, which a fixed ASR model like
# Whisper can only guess at. Guessing wrong is the reported transcription bug.
#
#   LLM_PROVIDER        default for all three   (groq | gemini)
#   LLM_PROVIDER_TEXT   chat and reasoning
#   LLM_PROVIDER_VISION photos
#   LLM_PROVIDER_AUDIO  voice transcription
#
# Groq stays the default everywhere: it is the configuration that has been in
# production, and a provider switch should be a decision rather than a surprise
# arriving with a deploy.
_PROVIDER_DEFAULT = (os.getenv("LLM_PROVIDER") or "groq").strip().lower()


def _provider_for(capability: str) -> str:
    chosen = (os.getenv(f"LLM_PROVIDER_{capability.upper()}") or _PROVIDER_DEFAULT).strip().lower()
    return chosen if chosen in ("groq", "gemini") else "groq"


# Model ids are read from the environment so a switch does not need a deploy.
# Defaults are the ids Google documents for these roles as of 2026-08-14.
GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-3.5-flash").strip()
GEMINI_VISION_MODEL = (os.getenv("GEMINI_VISION_MODEL") or GEMINI_MODEL).strip()
GEMINI_AUDIO_MODEL = (os.getenv("GEMINI_AUDIO_MODEL") or GEMINI_MODEL).strip()

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

# Mobile app front door — the pairing token the phone presents on /app-link.
# React Native's WebSocket cannot set handshake headers, so this one travels as
# a query parameter rather than a header like the bridge secret does. Defaults to
# BRIDGE_SECRET so a working bridge needs no second secret; set APP_TOKEN to give
# phones a credential you can rotate without dropping the desk link. Unset and
# empty on both means /app-link refuses every connection — a socket that reaches
# a brain able to answer as you is never opened ungated.
APP_TOKEN = (os.getenv("APP_TOKEN") or BRIDGE_SECRET or "").strip()

# The chat id stamped on commands the PHONE sends through a linked desk. The desk
# keys its per-conversation working memory off this, so the phone gets a session
# of its own rather than sharing Telegram's. It is never a real Telegram chat:
# app replies are intercepted by req_id before the relay, and the relay refuses
# this id outright as a second line of defence.
APP_CHAT_ID = int(os.getenv("APP_CHAT_ID", "-90001"))

# ── One assistant, not three copies of one ───────────────────────────────────
#
# The comment above says the phone "gets a session of its own rather than sharing
# Telegram's", and that was deliberate. It is being overruled, deliberately.
#
# The cost only became obvious in use: ask something at the desk, pick up the
# phone an hour later, and he has no idea what was just discussed. Ask on the
# phone, open Telegram, same again. Three surfaces, three separate memories, one
# person talking — which is not a presence, it is three copies of one wearing the
# same name. The film's JARVIS is the same JARVIS in the car as in the workshop.
#
# So ROUTING and MEMORY are separated. APP_CHAT_ID stays exactly as it is for
# routing and for the relay's refusal — that defence is load-bearing and nothing
# here touches it. What changes is which key the rolling history is filed under.
#
# **Only the operator's own surfaces merge.** A VIP's conversation is theirs;
# merging Mousumi's chat into his would be a privacy failure dressed as a
# feature, and it is why this maps one specific pair of ids rather than
# collapsing everything into one.
APP_MEMORY_SHARED = (os.getenv("APP_MEMORY_SHARED", "1").strip() == "1")


# ── Which surface he is speaking from, and what it can do ────────────────────
#
# Since the memory became shared, one history holds turns typed at the desk, sent
# from the phone and sent through Telegram, and nothing in it says which. He was
# inferring it by accident: a phone turn arrives carrying a location block and a
# Telegram turn does not — a hint, not a statement, and one that vanishes the
# moment location sharing is switched off.
#
# It matters in a specific way rather than a general one. At the desk "open that
# file" is a thing that can be done; on the phone it is not, and offering it is
# worse than declining, because the offer looks like the work happening. The
# surface also decides what he HAS: the desk has hands, the cloud only talks.
#
# One line, stated rather than implied. Kept short on purpose — this rides on
# every turn, and a paragraph about the transport is a paragraph not spent on the
# question.
SURFACE_NOTE = {
    "app": ("He is speaking from the phone app. You can answer, look at photos and "
            "remember things; you cannot touch his PC unless the desk is linked."),
    "telegram": ("He is speaking through Telegram. Same reach as the phone app: "
                 "answers and memory, no direct control of his PC unless the desk "
                 "is linked."),
    "desk": ("He is speaking at the desk, where you have full control of the "
             "machine."),
}


def _surface_line(surface: str) -> str:
    """The one line naming where a turn came from, or nothing.

    Unknown surfaces return an empty string rather than a guess: saying the wrong
    thing about what he can reach is worse than saying nothing, because he acts on
    it.
    """
    return SURFACE_NOTE.get(surface, "")


def _memory_key(chat_id: int) -> int:
    """Which conversation this turn is REMEMBERED under.

    Distinct from `chat_id`, which says where a reply GOES. The phone's turns are
    filed under the operator's Telegram chat so both surfaces read one history —
    and the desk, which speaks to the gateway as the operator, is already there.

    Falls through untouched when `TELEGRAM_USER_ID` is unset (the app must work on
    a gateway with no Telegram wiring at all) and when the flag is off, so the old
    behaviour is one environment variable away rather than a revert.
    """
    if not APP_MEMORY_SHARED or chat_id != APP_CHAT_ID:
        return chat_id
    owner = (os.getenv("TELEGRAM_USER_ID") or "").strip()
    return int(owner) if owner.lstrip("-").isdigit() else chat_id

# The phone re-probes when no frame has arrived for 30s (LinkMachine.tick), which
# on an idle socket would mean a teardown-and-reconnect every half minute. A
# status frame with no message refreshes that clock without writing to the HUD's
# chat log, so the keepalive is invisible.
APP_KEEPALIVE_SECS = float(os.getenv("APP_KEEPALIVE_SECS", "20"))

# How often a phone gets fresh desk vitals while a desk is linked. Cheap (one
# psutil read on the desk) but not free, so it only runs while a phone is
# actually attached.
APP_TELEMETRY_SECS = float(os.getenv("APP_TELEMETRY_SECS", "15"))

# ── Reaching a phone that holds no socket ────────────────────────────────────
# Android suspends a backgrounded app and the WebSocket dies with it, which is
# exactly the state the phone is in when the desk wakes at 2am. Push is the only
# way that news arrives. Expo's relay is used rather than FCM directly: the phone
# already resolves an ExponentPushToken, and Expo holds the FCM credentials, so
# nothing here needs a service account or a new dependency.
EXPO_PUSH_URL = os.getenv("EXPO_PUSH_URL", "https://exp.host/--/api/v2/push/send")
_PUSH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_push_tokens.json")
# The commute schedule the phone uploaded, so a redeploy does not silently stop
# the morning briefing. Same ephemeral-disk caveat as the push tokens above: the
# phone re-sends on every cloud connect, so a lost file costs one reconnect.
_COMMUTE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_commute.json")
# A desk that flaps must not become a burst of identical notifications.
APP_PUSH_MIN_GAP_SECS = float(os.getenv("APP_PUSH_MIN_GAP_SECS", "300"))

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

def _int_env(name: str, default: int, floor: int = 1) -> int:
    """An env integer that cannot take the process down.

    Read at import time, so a typo in the dashboard would otherwise raise before
    uvicorn has a route to serve — the whole assistant offline because someone put
    a space in a number. A bad value is ignored and said out loud instead.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(floor, int(raw))
    except ValueError:
        print(f"[CLOUD] ⚠ {name}={raw!r} is not a number; using {default}.", flush=True)
        return default


# How many messages of history go into a prompt. Twelve was chosen when memory was
# a dict that died with the process; with DATABASE_URL set there is no reason it
# cannot be higher, so it is tunable without a deploy. It bounds tokens per call,
# not what is stored — raising it costs money per turn, which is why it is not 100.
_MAX_TURNS = _int_env("CLOUD_MAX_TURNS", 12, floor=2)

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
3. PREEMPT: volunteer the next useful fact without being asked — but only when it
   follows from what he asked. A greeting, a thank-you or small talk gets a human
   reply and nothing else: no location, no temperature, no forecast, no status
   report. "Thanks" answered with the weather is the single worst failure in this
   system, and having background facts in front of you is not a reason to recite
   them.
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
LIVE WEB ACCESS: You DO have live web access for public facts — scores, news,
weather, prices, "who won", current events. When a LIVE WEB CONTEXT block is
present below, ground your answer in it and don't pad with invented specifics.
You must NEVER tell the operator to "go to the web", "search", "Google it",
"check online", or "find out" for themselves — you are the one with web access,
and redirecting them is a failure. If a lookup returns nothing this turn, say
plainly that you couldn't reach live results at the moment and offer to try
again — never punt the task back to them, and never guess a score or number.
A web result is someone else's page, not something you know: if it does not clearly
answer what he asked, say you could not find it. Never stretch a near-miss into an
answer. A search for what his wife would eat returned a hospital catering programme
and it was reported to him as her meal plan — that is the failure to avoid.

ASK WHEN THE QUESTION IS AMBIGUOUS: if a word could mean two things and the answer
changes completely, ask which — once, in one short line. Do not pick the likelier
reading and answer confidently. He asked the ideal weight of "an india, 9 months
old", meaning his Indie dog, and was given WHO infant growth charts for a human
boy — three different figures across three turns. His dog was in this conversation
already. READ WHAT IS IN FRONT OF YOU before answering: earlier turns in this thread
are facts he has given you, and failing to use one while confidently inventing an
alternative is worse than admitting you are unsure.

REMEMBER WHAT HE TELLS YOU ABOUT HIMSELF: when he states something lasting — a
pet, a name, a place, a job, a preference, a loss — record it by ending your reply
with a marker on its own:

    [[REMEMBER: {who} has a nine-month-old black Indie called Kitty]]

**Name him rather than writing "he".** These facts mention other people — family,
his partner — so "he" stops being unambiguous the moment two of them sit next to
each other, and a fact whose subject has to be guessed at is worse than no fact.
Never write it in the first or second person: "I live in…" or "You live in…" in
your own instructions reads as though it were about YOU.

Only lasting things: not what he asked just now, not the weather, not anything
already in the list of what you know. If he corrects something you were told
before, record the correction the same way. He never sees the marker, so do not
refer to it or explain it.

ASK FOR A SEARCH INSTEAD OF PROMISING ONE: if you need current or specific
information you do not have, end your reply with a marker on its own:

    [[LOOKUP: the search query]]

The search runs immediately and you are asked again with the results, so the
answer reaches him in this same reply. Use it for facts you would otherwise guess
at — breed weights, prices, opening hours, scores, anything specific.

NEVER PROMISE A FOLLOW-UP. You cannot go away and come back: nothing of yours runs
between turns, so "I'll look it up", "I'll check", "I'll get back to you" and
"shortly" are promises nobody will keep. Asked the ideal weight of a 9-month-old
Indie, the answer was "the ideal weight can vary, Sir. I'll look up the breed
standards for you" — and nothing ever arrived. The marker above is what that
sentence should have been. Either the answer is in this reply, or you say plainly
that you could not find it. The marker itself is machinery: he never sees it, so
never refer to it or explain it.

NEVER CONTRADICT YOURSELF ABOUT A MEASUREMENT: the background block gives air
temperature and feels-like temperature as two named figures of the same reading.
They are not competing readings and neither is "from earlier". If you have a
measurement, it is current unless the block says otherwise."""


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


# ── Reasoning models say their thinking out loud, and it must not be shipped ──
#
# `<think>...</think>` is what a reasoning model emits before its answer. On
# 2026-08-19 at 19:16 a photo went to `qwen/qwen3.6-27b` — the Groq vision
# fallback, reached because Gemini's free vision quota was exhausted — and the
# phone displayed the ENTIRE monologue and no answer at all:
#
#     <think>
#     The user has sent a photo of what appears to be a smartwatch on a wrist.
#     ... Given the user has a dog named Kitty, it might be related ...
#     The user's prompt is just "The operator sent this photo without a caption"
#
# Three separate failures in one bubble. It leaked the reasoning, it leaked the
# facts block and the injected prompt along with it, and it ran out of
# `max_tokens` mid-thought so the answer was never reached — the reply was cut
# off with no closing tag. Vision itself was fine: the model read the watch face,
# the date and the blurred stairs behind it correctly. Only the packaging was
# wrong, which is why /health showed nothing amiss.
_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think\b[^>]*>.*\Z", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    """Remove a model's thinking, leaving only what it meant to say.

    Applied to every completion rather than to the Groq leg alone, because which
    provider serves which capability is a runtime switch and Gemini's thinking
    models emit the same tag. One place, so a provider change cannot bring this
    back.

    The unterminated case is deliberate, and it is the one that actually
    happened: a `<think>` with no `</think>` means the token budget ran out
    mid-thought, so everything from the tag onward is monologue and there is no
    answer behind it. Dropping it leaves an empty string, which the caller turns
    into an admission rather than an empty bubble.

    Not attempted here: Groq's `reasoning_format="hidden"`. It is the tidier fix
    where it applies and it is model-specific — Groq answers 400 for a model that
    does not reason — so it cannot be sent unconditionally, and nothing here can
    verify which of the configured ids accept it. Stripping needs no such
    knowledge and covers both providers.
    """
    out = _THINK_BLOCK.sub("", text or "")
    out = _THINK_OPEN.sub("", out)
    return out.strip()


def _groq_complete(messages: list[dict], model: str = "") -> str:
    """Blocking Groq chat completion with key rotation. Call via asyncio.to_thread."""
    def _call(client):
        resp = client.chat.completions.create(
            model=model or GROQ_MODEL,
            messages=messages,
            temperature=0.6,
            # 700 was the budget for an ANSWER, and a reasoning model spends it on
            # thinking first. The photo above never reached its reply because the
            # monologue alone exceeded it. Raised so the thinking a reasoning
            # model insists on doing cannot starve the sentence the operator
            # actually reads.
            max_tokens=2000,
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


# ════════════════════════════════════════════════════════════════════════════
# Gemini brain (optional second provider — see _provider_for)
# ════════════════════════════════════════════════════════════════════════════
#
# Same key-pool idea as Groq: the free tier is rate-limited per key, so several
# keys in `GEMINI_API_KEYS` are rotated on a 429 rather than failing the turn.
#
# The message shape is genuinely different, not a dialect of the OpenAI one:
# system text is its own `system_instruction=` argument, turns are
# `{"type": "user_input", "content": [parts]}`, and every part names itself
# (`{"type": "text" | "audio" | "image", ...}`). `_to_gemini` does that
# translation in one place so the rest of this file keeps speaking one language.
def _parse_gemini_keys() -> list[str]:
    multi = (os.getenv("GEMINI_API_KEYS") or "").strip()
    single = (os.getenv("GEMINI_API_KEY") or "").strip()
    keys: list[str] = []
    if multi:
        keys.extend(k.strip() for k in multi.split(",") if k.strip())
    if single and single not in keys:
        keys.insert(0, single)
    return keys


_GEMINI_KEYS = _parse_gemini_keys()
_gemini_idx = 0
_gemini_lock = threading.Lock()


def gemini_ready() -> bool:
    """Whether Gemini can be called at all. Checked before every switch."""
    if not _GEMINI_KEYS:
        return False
    try:
        from google import genai  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        # requirements-cloud.txt predates google-genai on this deploy
        return False


def _run_gemini(call_fn):
    """Run call_fn(client), rotating keys on a recoverable error."""
    from google import genai

    global _gemini_idx
    if not _GEMINI_KEYS:
        raise RuntimeError("No GEMINI_API_KEYS / GEMINI_API_KEY configured.")
    with _gemini_lock:
        start = _gemini_idx
    n = len(_GEMINI_KEYS)
    last_exc: Optional[BaseException] = None
    for attempt in range(n):
        idx = (start + attempt) % n
        try:
            out = call_fn(genai.Client(api_key=_GEMINI_KEYS[idx]))
            with _gemini_lock:
                _gemini_idx = idx
            return out
        except BaseException as e:  # noqa: BLE001
            if _looks_recoverable(e):
                last_exc = e
                print(f"[CLOUD] Gemini key {idx} failed ({e}); rotating.", flush=True)
                continue
            raise
    raise last_exc or RuntimeError("Gemini key rotation exhausted.")


# `data:image/jpeg;base64,<payload>` — the shape `see()` builds for Groq.
_DATA_URI = re.compile(r"^data:([^;,]+);base64,(.*)$", re.S)


def _gemini_part(part: dict) -> Optional[dict]:
    """One OpenAI-shaped content part, translated.

    The parts are not a dialect of each other. Groq takes an image as
    `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`;
    Gemini takes `{"type": "image", "data": "<base64>", "mime_type": "image/jpeg"}`.
    Passing the first straight through earned a 400 naming the exact field:

        The value 'image_url' is not supported for 'type' at 'input[0].content[1]'.
        Supported values: ... 'image' ...

    A part this cannot translate is dropped rather than sent. A remote http image
    URL is the real case — Gemini's inline shape carries bytes, not links, and a
    rejected request costs the whole turn where a missing image costs one detail.
    """
    kind = str(part.get("type") or "")
    if kind == "text":
        return {"type": "text", "text": str(part.get("text") or "")}
    if kind == "image_url":
        url = str((part.get("image_url") or {}).get("url") or "")
        found = _DATA_URI.match(url)
        if not found:
            print("[CLOUD] dropping a non-inline image; Gemini's shape takes bytes.", flush=True)
            return None
        return {"type": "image", "mime_type": found.group(1), "data": found.group(2)}
    # already Gemini-shaped — the transcription path builds these itself
    if kind in ("image", "audio"):
        return part
    return None


def _to_gemini(messages: list[dict]) -> tuple[str, list[dict]]:
    """Translate OpenAI-shaped messages into (system_instruction, input).

    Every system message is folded into one instruction, in order — this file
    deliberately sends several (persona, then the romanise nudge, then the
    location background), and their recency is what makes the model obey them, so
    the order is preserved rather than sorted.

    Assistant turns are replayed as user turns labelled with who spoke. The
    documented way to put a model turn into a stateless history is to append the
    `model_dump()` of a step returned by a previous call, and this history is our
    own store of plain strings rather than Gemini's objects — so there is no step
    to dump. Labelling keeps the content, which is what the model needs, and
    loses the role, which it can infer. Revisit if Google documents a synthetic
    model-turn shape.
    """
    system_parts: list[str] = []
    history: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
            continue
        parts: list[dict] = []
        if isinstance(content, str):
            text = content if role == "user" else f"J.A.R.V.I.S. replied: {content}"
            parts.append({"type": "text", "text": text})
        elif isinstance(content, list):
            # multimodal content, built by the callers in Groq's shape — every part
            # is translated, and anything untranslatable is dropped rather than
            # sent, since one bad part fails the whole request
            parts.extend(p for p in (_gemini_part(x) for x in content if isinstance(x, dict)) if p)
        if parts:
            history.append({"type": "user_input", "content": parts})
    return "\n\n".join(system_parts), history


def _gemini_complete(messages: list[dict], model: str = "") -> str:
    """Blocking Gemini text/vision completion. Call via asyncio.to_thread."""
    system_instruction, history = _to_gemini(messages)

    def _call(client):
        kwargs = {"model": model or GEMINI_MODEL, "input": history, "store": False}
        if system_instruction:
            kwargs["system_instruction"] = system_instruction
        interaction = client.interactions.create(**kwargs)
        return (getattr(interaction, "output_text", "") or "").strip()

    return _run_gemini(_call)


# The phone records m4a, which is AAC in an MP4 container. Gemini documents
# audio/aac but not audio/mp4, so that is what an m4a is declared as.
_AUDIO_MIME = {
    "m4a": "audio/aac", "aac": "audio/aac", "mp3": "audio/mp3", "ogg": "audio/ogg",
    "oga": "audio/ogg", "opus": "audio/ogg", "wav": "audio/wav", "flac": "audio/flac",
    "aiff": "audio/aiff",
}

# What Whisper cannot be told and Gemini can. The reported failure is a clip
# transcribed against the wrong language, in a house that speaks two at once —
# and loudness never fixed it, because volume was never the problem.
_GEMINI_TRANSCRIBE_PROMPT = (
    "Generate a transcript of the speech in this clip. The speaker mixes Bengali "
    "and English in the same sentence, and often speaks Bengali with English words "
    "in it. Write the transcript using ONLY English/Latin letters — romanise any "
    "Bengali phonetically rather than writing Bengali script, and keep English "
    "words as English. The speaker never speaks Hindi; anything that sounds like "
    "Hindi is Bengali. Return the transcript alone, with no commentary, no "
    "translation and no quotation marks."
)


def _gemini_transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    """Blocking Gemini transcription. Call via asyncio.to_thread."""
    ext = (filename.rsplit(".", 1)[-1] if "." in filename else "m4a").lower()
    mime = _AUDIO_MIME.get(ext, "audio/aac")
    payload = base64.b64encode(audio).decode("utf-8")

    def _call(client):
        interaction = client.interactions.create(
            model=GEMINI_AUDIO_MODEL,
            input=[
                {"type": "text", "text": _GEMINI_TRANSCRIBE_PROMPT},
                {"type": "audio", "data": payload, "mime_type": mime},
            ],
            store=False,
        )
        return (getattr(interaction, "output_text", "") or "").strip()

    return _run_gemini(_call)


# ── Is the second brain actually working? ────────────────────────────────────
#
# A silent fallback is worse than a loud failure. When the free tier's daily quota
# runs out, Gemini answers 429 and every call quietly becomes a Groq call — so the
# only symptom is transcription getting worse again, with no way to tell that from
# the model simply having a bad day. These counters are what makes the difference
# visible, and they are reported by /health.
#
# In-process and therefore reset by every restart, which is honest: they describe
# this instance's life, not all time. `quota` is the distinction that matters —
# a 429 means the tier is spent and waiting is the only cure, where any other
# error usually means the request itself was wrong.
_brain_stats: dict[str, dict] = {
    cap: {"gemini_ok": 0, "fell_back": 0, "last_error": None, "last_error_at": None,
          "last_error_was_quota": False}
    for cap in ("text", "vision", "audio")
}


def _looks_like_quota(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None)
    if code == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "resource_exhausted" in msg or "quota" in msg


def _note_fallback(capability: str, exc: BaseException) -> None:
    import datetime as _dt
    stat = _brain_stats[capability]
    stat["fell_back"] += 1
    # truncated: this is read from a browser, and a stack in a JSON field is noise
    stat["last_error"] = str(exc)[:200]
    stat["last_error_at"] = _dt.datetime.now(_OPERATOR_TZ).isoformat(timespec="seconds")
    stat["last_error_was_quota"] = _looks_like_quota(exc)
    quota = " (quota exhausted — resets at midnight US Pacific)" if stat["last_error_was_quota"] else ""
    print(f"[CLOUD] Gemini {capability} failed{quota} ({exc}); falling back to Groq.", flush=True)


# ── The dispatchers every caller uses ────────────────────────────────────────
#
# Gemini failing falls back to Groq rather than failing the turn. A provider
# switch must never be able to take the assistant off the air: the worst a bad
# `LLM_PROVIDER_*` value or an exhausted free tier can do is put the old brain
# back, loudly, in the log and in /health.
def _complete(messages: list[dict], model: str = "", capability: str = "text") -> str:
    groq_model = model or (GROQ_VISION_MODEL if capability == "vision" else GROQ_MODEL)
    if _provider_for(capability) == "gemini" and gemini_ready():
        try:
            out = _gemini_complete(
                messages, GEMINI_VISION_MODEL if capability == "vision" else GEMINI_MODEL
            )
            _brain_stats[capability]["gemini_ok"] += 1
            return _answerable(_strip_reasoning(out))
        except Exception as e:  # noqa: BLE001
            _note_fallback(capability, e)
    return _answerable(_strip_reasoning(_groq_complete(messages, groq_model)))


def _answerable(text: str) -> str:
    """Never hand back an empty bubble.

    A reply that is empty once its thinking is removed means the model spent the
    whole budget deciding what to say. Saying so is the honest report; an empty
    message reads as the app having broken, which is the failure shape this
    project keeps having to unlearn.
    """
    return text or "I thought my way past the end of my answer, sir. Ask me again."


def _transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    if _provider_for("audio") == "gemini" and gemini_ready():
        try:
            out = _gemini_transcribe(audio, filename)
            _brain_stats["audio"]["gemini_ok"] += 1
            return out
        except Exception as e:  # noqa: BLE001
            _note_fallback("audio", e)
    return _groq_transcribe(audio, filename)


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

# How far back a time-sensitive lookup may reach. Tavily honours `days` only on
# the news topic, which is why `fresh` below switches both of them together.
_TAVILY_FRESH_DAYS = int(os.getenv("TAVILY_FRESH_DAYS", "14"))


def _date_label(raw: object) -> str:
    """One comparable shape for a source's publication date.

    Tavily returns `published_date` on news results and omits it everywhere else,
    in a format that has been both ISO and RFC 1123 depending on the source. So
    normalise what parses and pass the rest through truncated rather than dropping
    it: a strange-looking date still tells the model how old a claim is, and no
    date at all is precisely what let a 2025 monsoon article be read back as this
    morning's forecast.
    """
    import datetime as _dt

    s = str(raw or "").strip()
    if not s:
        # Stated out loud on purpose. An undated snippet sitting among dated ones
        # reads as "recent enough not to need one", which is the original mistake.
        return "date unknown"
    try:
        return _dt.date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            return _dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s[:24]


def _dated_block(body: str) -> str:
    """Stamp a lookup with today's date, so age needs no arithmetic to see.

    The phone's envelope already carries local wall time (`src/lib/ask.ts`), but
    that only ever said what *today* is — it never said how old the *evidence*
    was. A model asked about rain on 2026-08-17, handed undated 2025 prose, had
    nothing to weigh it against and warned about that year's monsoon as though it
    were the afternoon's forecast.
    """
    import datetime as _dt

    if not body:
        return ""
    today = _dt.datetime.now(_OPERATOR_TZ).date().isoformat()
    return (f"- Today is {today}. Every source below carries its publication date; "
            f"treat an old one as history, never as current conditions.\n{body}")


def _tavily_lookup(query: str, fresh: bool = False) -> str:
    """Grounding via Tavily (same provider the desk uses). Better than DDG for
    current facts/scores. Uses the REST API over urllib — no extra dependency.

    `fresh` asks for a dated window rather than a nudge. Appending "latest result
    today" to the query — which is all recency used to mean here — is a hint to
    the ranker, not a filter, and a well-ranked article from last year satisfies
    it completely.
    """
    import json as _json
    import urllib.request

    body = {
        "api_key": _TAVILY_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }
    if fresh:
        # `days` is honoured on the news topic only, so the two travel together.
        # Not applied to every query: restricting "what is a pangolin" to the news
        # of the last fortnight is a different kind of wrong answer.
        body["topic"] = "news"
        body["days"] = _TAVILY_FRESH_DAYS
    payload = _json.dumps(body).encode()
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
            bits.append(f"- [{_date_label(r.get('published_date'))}] {title}: {content}")
    return "\n".join(bits)


def _ddg_lookup(query: str) -> str:
    from ddgs import DDGS

    bits = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=6):
            title = r.get("title", "")
            body = r.get("body", "")
            if body:
                # DDG carries no publication date at all. Marked rather than left
                # bare, so a Tavily block and a DDG one cannot be told apart by the
                # model as "dated" versus "current".
                bits.append(f"- [date unknown] {title}: {body}")
    return "\n".join(bits)


# Time-sensitive intents get a dated window from Tavily, and — since DuckDuckGo
# offers no such filter — the old query nudge when DDG is the one answering.
_RECENCY_HINTS = ("score", "match", "news", "latest", "today", "current",
                  "price", "stock", "weather", "result", "who won", "live")


def _web_lookup(query: str) -> str:
    """Best-effort web grounding for factual questions. Prefers Tavily when a key
    is configured, falls back to DuckDuckGo. Never fatal."""
    if not WEB_LOOKUP:
        return ""
    q = query.strip()
    fresh = any(h in q.lower() for h in _RECENCY_HINTS)
    # DDG gets the nudge because it has nothing better; Tavily gets `days`.
    ddg_q = f"{q} latest result today" if fresh else q
    try:
        if _TAVILY_KEY:
            out = _tavily_lookup(q, fresh=fresh)
            if out:
                return _dated_block(out)
            # fall through to DDG if Tavily returned nothing
        return _dated_block(_ddg_lookup(ddg_q))
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] web lookup skipped: {e}", flush=True)
        # Last-ditch: try DDG if Tavily was the one that failed.
        if _TAVILY_KEY:
            try:
                return _dated_block(_ddg_lookup(ddg_q))
            except Exception:
                pass
        return ""


_LOOKUP_HINTS = (
    "weather", "temperature", "forecast", "score", "match", "news", "who",
    "what is", "when", "where", "latest", "price", "stock", "today", "current",
)


def _expand_contractions(text: str) -> str:
    """`what's` -> `what is`, so the hint list actually matches speech.

    "what's the ideal weight of a 9 month indie" triggered no lookup at all, because
    the list holds "what is" and a substring test does not know they are the same
    word. The model was left with nothing to answer from and improvised a promise to
    look it up later, which is the one thing it cannot do.

    Normalising is better than adding every contraction to the list: there is one
    rule here and there would be a dozen entries there, and the next contraction
    would be missed again.
    """
    return re.sub(r"\b(what|who|where|when|how|that|there|here|it)'s\b", r"\1 is", text, flags=re.I)


# What the model writes when it needs a search before it can answer.
#
# The keyword gate above is a guess made before the model has seen the question,
# and no list of substrings will ever cover English, let alone Benglish. This is
# the model asking for itself — one round trip more, and the answer arrives in the
# same reply instead of being promised for a later that does not exist.
#
# Stripped from every reply whether or not it was acted on, so it can never be
# shown to the operator.
_LOOKUP_MARKER = re.compile(r"\[\[\s*LOOKUP\s*:\s*(.+?)\]\]", re.I | re.S)

_SECOND_PASS_NUDGE = (
    "LOOKUP RESULTS (highest priority): you asked for a search and here is what came "
    "back. Answer the question now, in persona, grounded in these results. Do NOT ask "
    "for another lookup, do NOT mention searching, and do NOT promise anything for "
    "later. If these results do not answer it, say plainly that you could not find it."
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


# Injected as a FRESH system turn when a live-info query's web lookup came back
# empty. Stops the model from deflecting the task back to the operator.
_LOOKUP_FAILED_NUDGE = (
    "LIVE LOOKUP FAILED (highest priority): your web lookup for this question "
    "returned nothing this turn. Tell {honorific} briefly and plainly that you "
    "couldn't reach live results at the moment and offer to try again shortly. "
    "Do NOT tell them to search, Google, or 'find out' themselves. Do NOT guess "
    "or invent a score, number, price, or result. One or two sentences, in "
    "persona."
)


async def _resolve_markers(reply: str, transcript: list, honorific: str) -> str:
    """Act on the machinery a reply carries, then remove every trace of it.

    Shared by `think()` and `see()` because it was NOT, and that cost a visible
    bug: a photo asking after a motorcycle's mileage was answered, in full, with

        [[LOOKUP: Royal Enfield Hunter 350 mileage ARAI real world]]

    `see()` was a parallel implementation that never got this block while sharing
    the persona that teaches the marker — so the model obeyed its instructions and
    the gateway printed them. The search never ran, the question was never
    answered, and the raw line went into the rolling history to be read back as
    context on every following turn. A `[[REMEMBER: …]]` stated over a photo was
    unstorable for the same reason.

    One function rather than a second copy, because a third caller is precisely how
    this happened once already.

    `transcript` is what the second pass should be asked WITH, and it is the
    caller's job because the two paths differ: `think()` hands over its own
    messages unchanged, while `see()` must swap the base64 image for a text
    stand-in — the second pass runs on the text leg, which cannot read an image and
    would pay for the tokens anyway. What the model saw survives in its own first
    reply, which is appended here.
    """
    asked = _LOOKUP_MARKER.search(reply or "")
    if asked and WEB_LOOKUP:
        query = asked.group(1).strip()[:200]
        print(f"[CLOUD] model asked for a lookup: {query!r}", flush=True)
        snippets = await asyncio.to_thread(_web_lookup, query)
        second = list(transcript)
        # its own request belongs in the transcript, or the second pass reads as
        # though the results arrived from nowhere
        second.append({"role": "assistant", "content": reply})
        second.append({
            "role": "system",
            "content": (_SECOND_PASS_NUDGE + "\n\n" + snippets) if snippets else
                       _LOOKUP_FAILED_NUDGE.format(honorific=honorific),
        })
        # Once only. A model that can ask twice can ask forever, and a loop that
        # bills a free tier per iteration is not a loop worth having.
        reply = await asyncio.to_thread(_complete, second, "", "text")

    # Stored before the marker is stripped, and stored even if the write fails, so a
    # fact he stated is at least true for this process.
    for fact in _REMEMBER_MARKER.findall(reply or ""):
        await remember_fact(fact)

    # Stripped whether or not they were acted on. A marker that reaches the operator
    # is worse than no marker at all: it is punctuation from the machinery, in a chat
    # that is supposed to read like someone talking.
    reply = _REMEMBER_MARKER.sub("", reply or "")
    return _LOOKUP_MARKER.sub("", reply or "").strip()


async def think(chat_id: int, text: str, who: str, honorific: str,
                context: str = "", surface: str = "") -> str:
    """Run one turn through the cloud brain, with rolling per-chat memory.

    `context` is per-turn background — the phone's location and measured weather.
    It arrives as its own system turn immediately before the user message, for the
    same recency reason `_ROMANISE_NUDGE` does, and it is deliberately **not**
    written to `history`: the caller used to prepend it to `text`, which meant every
    remembered turn carried a stale copy of his coordinates and the model spent the
    conversation comparing them ("still overcast", "still in Presidency Division").

    The history is keyed by `_memory_key`, not by `chat_id`: where a reply GOES
    and what the assistant REMEMBERS are different questions, and the operator's
    surfaces answer the second one together. See the note on `APP_MEMORY_SHARED`.

    `surface` names where the turn came from, which one shared history cannot say
    on its own. Ephemeral like `context` and for the same reason: the surface is
    true of this turn, not of the conversation, and a remembered copy would have
    him telling you tomorrow where you stood yesterday.
    """
    mem = _memory_key(chat_id)
    history = await _history_for(mem)

    grounding = ""
    lookup_failed = False
    lowered = _expand_contractions(text).lower()
    if WEB_LOOKUP and any(h in lowered for h in _LOOKUP_HINTS):
        snippets = await asyncio.to_thread(_web_lookup, text)
        if snippets:
            grounding = (
                "\n\n[LIVE WEB CONTEXT — use if relevant, cite naturally, don't dump raw]:\n"
                + snippets
            )
        else:
            # A live-info query (score/news/weather/price) whose lookup returned
            # nothing — usually Tavily unconfigured on this host and DuckDuckGo
            # blocked from the datacenter IP. Do NOT let the model punt the task
            # back to the operator ("go find out"); make it fail honestly.
            lookup_failed = True
            print(f"[CLOUD] web lookup empty for live query: {text[:80]!r}", flush=True)

    # Anchor "today"/"now" so time-sensitive answers (scores, news) aren't guessed
    # against the model's stale training cutoff. Operator's timezone, NOT the
    # server's UTC clock.
    import datetime as _dt
    now = _dt.datetime.now(_OPERATOR_TZ)
    date_ctx = f"\n\nCURRENT DATE/TIME (operator's local clock): {now:%A, %d %B %Y, %I:%M %p} IST."
    system = _PERSONA.format(who=who, honorific=honorific) + date_ctx + await _facts_block() + grounding
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-_MAX_TURNS:])
    # Before the location block, because it frames it: "he is on the phone" is
    # what makes "he is at the office" mean he is standing there rather than
    # that a machine there reported it.
    said_surface = _surface_line(surface)
    if said_surface:
        messages.append({"role": "system", "content": said_surface})
    if context:
        messages.append({"role": "system", "content": context})
    if _has_indic_script(text):
        messages.append({"role": "system", "content": _ROMANISE_NUDGE})
    if lookup_failed:
        messages.append({"role": "system", "content": _LOOKUP_FAILED_NUDGE.format(honorific=honorific)})
    messages.append({"role": "user", "content": text})

    reply = await asyncio.to_thread(_complete, messages, "", "text")

    # The search it asked for, the facts it was told to keep, and the markers
    # removed — one extra round trip, and the operator sees one grounded reply.
    # This is what replaces "I'll look up the breed standards for you", a sentence
    # that was never followed by anything because nothing runs between turns.
    #
    # `messages` unchanged: a text turn's transcript is already what the second
    # pass should read. See `see()` for the half that is not.
    reply = await _resolve_markers(reply, messages, honorific)

    # what he actually said, not what he said plus a page of coordinates
    await _history_add(mem, "user", text)
    await _history_add(mem, "assistant", reply)
    return reply


async def see(chat_id: int, image_b64: str, caption: str, who: str, honorific: str,
              surface: str = "") -> str:
    """Answer a Telegram photo through the Groq vision model, in persona and
    with the same rolling per-chat memory as think()."""
    # the same key `think` uses: one conversation across his surfaces, so a photo
    # sent from the phone is something the desk can be asked about afterwards
    mem = _memory_key(chat_id)
    history = await _history_for(mem)

    import datetime as _dt
    now = _dt.datetime.now(_OPERATOR_TZ)
    date_ctx = f"\n\nCURRENT DATE/TIME (operator's local clock): {now:%A, %d %B %Y, %I:%M %p} IST."
    system = _PERSONA.format(who=who, honorific=honorific) + date_ctx + await _facts_block()
    question = caption.strip() or "The operator sent this photo without a caption — react to it helpfully."
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-_MAX_TURNS:])
    # a photo comes from a surface too, and what he can do about what he sees
    # depends on which one
    said_surface = _surface_line(surface)
    if said_surface:
        messages.append({"role": "system", "content": said_surface})
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

    reply = await asyncio.to_thread(_complete, messages, "", "vision")

    # ── The same machinery a text turn gets, and for the same reasons ────────
    #
    # This path had none of it, and a photo of a motorcycle came back as the
    # literal string `[[LOOKUP: Royal Enfield Hunter 350 mileage ARAI real world]]`
    # — the persona teaches the marker and this function used the persona, so the
    # model was following instructions the gateway then failed to carry out.
    #
    # The transcript handed over is text-only. The second pass runs on the text
    # leg: it cannot read the image, and sending a megabyte of base64 to a model
    # that will ignore it is a cost with no upside. The stand-in below is the same
    # one the history keeps, and what the model actually SAW travels in its own
    # first reply, which `_resolve_markers` appends.
    grounded = [m for m in messages if not isinstance(m.get("content"), list)]
    grounded.append({"role": "user", "content": f"[sent a photo] {question}"})
    reply = await _resolve_markers(reply, grounded, honorific)

    # Store a text stand-in for the image so follow-up turns keep context
    # without re-sending base64 through the chat model.
    await _history_add(mem, "user", f"[sent a photo] {question}")
    await _history_add(mem, "assistant", reply)
    return reply


# Per-chat rolling memory (level-1 "independent" memory; resets on restart).
# ── Rolling memory, and where it lives ───────────────────────────────────────
#
# `_HISTORY` is the in-process cache and always has been. What is new is that it
# can be *backed* by a database, because on its own it forgets everything on every
# restart — and on Render's free tier a restart is a redeploy, a crash, or the
# platform moving the instance. "It doesn't remember things" was the most-felt bug
# in this system and it was never the model's fault: there was nowhere to remember.
#
# Unset `DATABASE_URL` and none of this runs; the behaviour is exactly what it was.
# That is deliberate — a memory layer that cannot be switched off is a memory layer
# you cannot diagnose.
#
# Postgres because the free tiers that exist are Postgres (Neon, Supabase), and
# because Render's own filesystem is ephemeral: SQLite on the instance would die
# with the instance, which is the problem, not the solution.
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

_HISTORY: dict[int, list[dict]] = {}
# which chats have been read back from the database, so it happens once per process
_RECALLED: set[int] = set()
_db_ready = False
_db_broken = False


def _db_conn():
    """A connection per call, closed immediately.

    Free-tier Postgres counts connections and reaps idle ones, so holding a pool
    open across a mostly-idle gateway is how you arrive to find it unusable. The
    cost of connecting is paid on turns a human is already waiting through.

    **Use a pooler URL, not the direct one.** Supabase's direct host resolves to
    IPv6 only and Render's free tier has no IPv6 route, so `db.<ref>.supabase.co`
    cannot be reached from here at all; `aws-0-<region>.pooler.supabase.com` is
    IPv4. Checked with a DNS lookup rather than assumed — the dashboard labels both
    as IPv6-by-default, and for the pooler that is not what DNS returns.

    Prepared statements are switched off because a transaction-mode pooler hands the
    next query to whichever backend is free, and a statement prepared on one is not
    there on another. psycopg3 starts preparing automatically after five identical
    queries — which is a handful of turns, so this would not fail until it had
    already looked like it worked.
    """
    import psycopg
    conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
    try:
        conn.prepare_threshold = None
    except Exception:  # noqa: BLE001
        # older psycopg exposes this differently; a session-mode pooler is fine
        # without it, and this must not be the thing that stops memory working
        pass
    return conn


def _db_init_blocking() -> None:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS chat_turns (
                   id       BIGSERIAL PRIMARY KEY,
                   chat_id  BIGINT      NOT NULL,
                   role     TEXT        NOT NULL,
                   content  TEXT        NOT NULL,
                   said_at  TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        # the only query this table serves is "the last N turns of one chat"
        cur.execute("CREATE INDEX IF NOT EXISTS chat_turns_by_chat ON chat_turns (chat_id, id DESC)")
        conn.commit()


def _db_init_facts_blocking() -> None:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS facts (
                   id      BIGSERIAL PRIMARY KEY,
                   fact    TEXT        NOT NULL UNIQUE,
                   at      TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        conn.commit()


def _db_facts_blocking(limit: int) -> list[str]:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT fact FROM facts ORDER BY id LIMIT %s", (limit,))
        return [row[0] for row in cur.fetchall()]


def _db_add_fact_blocking(fact: str) -> None:
    with _db_conn() as conn, conn.cursor() as cur:
        # the same thing told twice is one fact; UNIQUE and DO NOTHING mean saying
        # it again is free rather than filling the block with duplicates
        cur.execute("INSERT INTO facts (fact) VALUES (%s) ON CONFLICT (fact) DO NOTHING", (fact,))
        conn.commit()


def _db_forget_fact_blocking(fact: str) -> int:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM facts WHERE fact = %s", (fact,))
        conn.commit()
        return cur.rowcount


# ── Small state that must outlive a deploy ───────────────────────────────────
#
# The commute schedule, the push addresses and the two once-a-day markers were
# JSON files beside this module, and Render's disk is wiped on every deploy — not
# every restart, which is why it read as working. So a deploy silently disarmed
# the briefing, and the only symptom was a notification that did not arrive:
# indistinguishable from the Android bug this whole feature exists to route
# around. It happened twice on 2026-08-20, the second time 60 minutes before the
# first briefing was due.
#
# Recovery existed — the phone re-uploads on cloud connect — but it is gated on a
# WebSocket the app does not always hold. A photo answered over plain HTTP that
# evening while `push_targets` sat at 0, so the app looked connected and the
# gateway was unreachable.
#
# One table with a key rather than four, because none of this is a schema: it is
# four small dictionaries that need to survive, and a column per feature would be
# a migration every time a new one appears. TEXT and `json.dumps` rather than
# JSONB, so nothing depends on psycopg's type adaptation through a pooler.
def _db_init_state_blocking() -> None:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS gateway_state (
                   key  TEXT        PRIMARY KEY,
                   val  TEXT        NOT NULL,
                   at   TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        conn.commit()


def _db_state_get_blocking(key: str) -> Optional[dict]:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT val FROM gateway_state WHERE key = %s", (key,))
        row = cur.fetchone()
    if not row:
        return None
    try:
        out = json.loads(row[0])
    except Exception:  # noqa: BLE001
        return None
    return out if isinstance(out, dict) else None


def _db_state_put_blocking(key: str, val: dict) -> None:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO gateway_state (key, val, at) VALUES (%s, %s, now())
               ON CONFLICT (key) DO UPDATE SET val = EXCLUDED.val, at = now()""",
            (key, json.dumps(val)),
        )
        conn.commit()


def _persist(key: str, val: dict) -> None:
    """Mirror one small dictionary into the database, without being waited for.

    Every caller is a `_save_*` that already wrote its file and is inside an async
    handler, so a task can be spawned here and the caller carries on. Deliberately
    fire-and-forget: none of this is worth making the operator wait through, and a
    failed write costs exactly what the old behaviour cost — a re-send on the next
    connect.

    Silent when there is no database or no loop. A gateway with `DATABASE_URL`
    unset must behave exactly as it did before this existed, which is the same
    rule the memory layer follows.
    """
    if not DATABASE_URL or _db_broken:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # module scope, or a worker thread: the file write already happened

    async def _write() -> None:
        try:
            if not await _memory_ready():
                return
            await asyncio.to_thread(_db_state_put_blocking, key, val)
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] state write failed for {key!r}: {e}", flush=True)

    loop.create_task(_write())


def _db_load_blocking(chat_id: int, limit: int) -> list[dict]:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT role, content FROM chat_turns WHERE chat_id = %s ORDER BY id DESC LIMIT %s",
            (chat_id, limit),
        )
        rows = cur.fetchall()
    # newest-first out of the database, oldest-first into the prompt
    return [{"role": role, "content": content} for role, content in reversed(rows)]


def _db_append_blocking(chat_id: int, role: str, content: str) -> None:
    with _db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_turns (chat_id, role, content) VALUES (%s, %s, %s)",
            (chat_id, role, content),
        )
        conn.commit()


async def _memory_ready() -> bool:
    """Whether turns can be persisted. Creates the table on first use.

    A failure here latches: one broken start means this process stops trying, rather
    than paying a ten-second connect timeout on every turn for the rest of its life.
    The gateway keeps working from process memory, which is what it did before.
    """
    global _db_ready, _db_broken
    if not DATABASE_URL or _db_broken:
        return False
    if _db_ready:
        return True
    try:
        await asyncio.to_thread(_db_init_blocking)
        await asyncio.to_thread(_db_init_facts_blocking)
        await asyncio.to_thread(_db_init_state_blocking)
        _db_ready = True
        print("[CLOUD] persistent memory ready", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        _db_broken = True
        print(f"[CLOUD] persistent memory unavailable ({e}); memory is this process only.", flush=True)
        return False


# ── Things worth knowing, as opposed to things that were said ────────────────
#
# A turn history answers "what were we just talking about". It does not answer "what
# is true about him", because anything said more than `_MAX_TURNS` ago is gone —
# which is how a question about his dog got answered with infant growth charts while
# the dog sat four turns up the transcript.
#
# Facts are separate, few, and permanent. They go in every system prompt, so this
# stays deliberately small: it is a page about a person, not a log.
_FACTS_CAP = 60
_facts_cache: Optional[list[str]] = None

_REMEMBER_MARKER = re.compile(r"\[\[\s*REMEMBER\s*:\s*(.+?)\]\]", re.I | re.S)


async def _facts() -> list[str]:
    """Every stored fact. Cached, because this is read on every single turn."""
    global _facts_cache
    if _facts_cache is not None:
        return _facts_cache
    if not await _memory_ready():
        _facts_cache = []
        return _facts_cache
    try:
        _facts_cache = await asyncio.to_thread(_db_facts_blocking, _FACTS_CAP)
        print(f"[CLOUD] {len(_facts_cache)} fact(s) loaded", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] facts unavailable ({e}); answering without them.", flush=True)
        _facts_cache = []
    return _facts_cache


async def remember_fact(fact: str) -> bool:
    """Store one fact. False when there was nowhere to put it.

    False matters and is not swallowed: without `DATABASE_URL` a fact lives until the
    next restart, and telling someone their assistant will remember something when it
    will not is worse than admitting it cannot.
    """
    said = " ".join((fact or "").split())[:300]
    if not said:
        return False
    global _facts_cache
    if _facts_cache is None:
        await _facts()
    if said not in (_facts_cache or []):
        (_facts_cache if _facts_cache is not None else []).append(said)
    if not await _memory_ready():
        print(f"[CLOUD] fact held in memory only (no DATABASE_URL): {said!r}", flush=True)
        return False
    try:
        await asyncio.to_thread(_db_add_fact_blocking, said)
        print(f"[CLOUD] remembered: {said!r}", flush=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] could not store fact ({e}): {said!r}", flush=True)
        return False


async def forget_fact(fact: str) -> bool:
    said = " ".join((fact or "").split())[:300]
    global _facts_cache
    if _facts_cache is not None and said in _facts_cache:
        _facts_cache.remove(said)
    if not await _memory_ready():
        return False
    try:
        return await asyncio.to_thread(_db_forget_fact_blocking, said) > 0
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] could not forget fact ({e}): {said!r}", flush=True)
        return False


async def _facts_block() -> str:
    """The facts, as a system-prompt section. Empty string when there are none."""
    known = await _facts()
    if not known:
        return ""
    return ("\n\nWHAT YOU KNOW ABOUT HIM (established, and true unless he corrects it — "
            "use it, and never contradict it):\n" + "\n".join(f"- {f}" for f in known))


async def _history_for(chat_id: int) -> list[dict]:
    """The rolling history, read back from the database the first time it is asked.

    Only when the cache is empty. A process that has been talking already holds the
    live conversation; overwriting that with a database read would replay turns it
    has just had.
    """
    hist = _HISTORY.setdefault(chat_id, [])
    if chat_id in _RECALLED or not await _memory_ready():
        return hist
    _RECALLED.add(chat_id)
    if hist:
        return hist
    try:
        rows = await asyncio.to_thread(_db_load_blocking, chat_id, _MAX_TURNS * 2)
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] memory recall failed ({e}); starting this chat empty.", flush=True)
        return hist
    if rows:
        hist.extend(rows)
        print(f"[CLOUD] recalled {len(rows)} message(s) for chat {chat_id}", flush=True)
    return hist


async def _history_add(chat_id: int, role: str, content: str) -> None:
    """Append one message, in memory and on disk.

    The in-process cap stays: it bounds what goes into a prompt, which is a token
    budget rather than a storage one. The database keeps everything — a turn from
    last week costs nothing to store and is the only way a longer window, or the
    desk mining this later, ever becomes possible.
    """
    hist = _HISTORY.setdefault(chat_id, [])
    hist.append({"role": role, "content": content})
    if len(hist) > _MAX_TURNS * 2:
        del hist[: len(hist) - _MAX_TURNS * 2]
    if await _memory_ready():
        try:
            await asyncio.to_thread(_db_append_blocking, chat_id, role, content)
        except Exception as e:  # noqa: BLE001
            # not latched: one failed write is a blip, and the turn is still live in
            # this process, so the conversation carries on
            print(f"[CLOUD] memory write failed ({e}); the turn is in this process only.", flush=True)


# ── Everything that reaches the shared history goes through one of these two ──
#
# `_history_add` takes a raw key, and once the memory became shared that made it
# the wrong thing to call directly: the key a turn is REMEMBERED under is
# `_memory_key(chat_id)`, never `chat_id`. Two writers got that wrong, in the two
# available ways, and both are the reason these exist.

async def _remember_desk_turn(chat_id: int, asked: str, answered: str) -> None:
    """File a DESK-answered turn in the shared history.

    Missed because the turn was never lost — it was in the other store. The desk
    keeps its own memory, and `_forward_to_desk` and `_ask_desk` both hand the
    question over and return before anything here writes. So with the desk LINKED
    — the normal state when he is home — the shared history filled only from the
    cloud fallback. Ask the phone something with the desk up, open Telegram an
    hour later, and it was not there: the one case the shared memory was built
    for was the one case that skipped it.

    The desk's own copy stays. These are two stores answering two questions, and
    the gateway's is the only one all three surfaces read.
    """
    mem = _memory_key(chat_id)
    if (asked or "").strip():
        await _history_add(mem, "user", asked.strip())
    if (answered or "").strip():
        await _history_add(mem, "assistant", answered.strip())


async def _remember_said(text: str) -> None:
    """File something JARVIS said UNPROMPTED under the shared history.

    The nudge wrote to the raw `APP_CHAT_ID` while `think` read the operator's
    own thread, so with `TELEGRAM_USER_ID` set those are two different
    conversations and the message landed in the one nothing reads. Its own
    docstring names the cost exactly — "a message the model cannot remember
    saying makes the next turn incoherent" — which is what it then caused.

    A helper and not a call site, so the next unprompted voice added here cannot
    repeat it. The commute briefing was already the second one, and it wrote
    nothing at all.
    """
    said = (text or "").strip()
    if said:
        await _history_add(_memory_key(APP_CHAT_ID), "assistant", said)


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
                                ident["who"], ident["honorific"],
                                surface="telegram")
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] think() fault: {e}\n{traceback.format_exc()}", flush=True)
            reply = "I hit a fault reaching my reasoning core just now — try again in a moment."
        _queue_offline_fact(ident, text, reply)
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
            transcript = await asyncio.to_thread(_transcribe, buf.getvalue(), fname)
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
                              ident["who"], ident["honorific"],
                              surface="telegram")
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
import base64  # noqa: E402
import hmac  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402


class _RedactQuerySecrets(logging.Filter):
    """Keep the phone's pairing token out of the access log.

    uvicorn logs the whole request line, and the phone has to present its token
    as a query parameter — React Native cannot set headers on a WebSocket
    handshake. So every phone connection was printing

        "WebSocket /app-link?token=<the actual secret>" [accepted]

    into the log, readable by anyone with dashboard access, and that token reaches
    a brain that answers as him. Redacted at the logging layer rather than by
    turning access logs off, because those logs are how the desk bridge and the
    phone were debugged in the first place.
    """

    _PATTERN = re.compile(r"(token=)[^\s\"&]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if "token=" in message.lower():
            # collapse to an already-formatted message: the args have been
            # consumed by getMessage() and must not be applied a second time
            record.msg = self._PATTERN.sub(r"\1<redacted>", message)
            record.args = ()
        return True


for _log_name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
    logging.getLogger(_log_name).addFilter(_RedactQuerySecrets())

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

# Phase 4 item 7: per-forwarded-request reply correlation. Each entry is
# {"evt": asyncio.Event set on the first reply/done frame, "last": monotonic
# time of the last sign of life (notify frames refresh it)}. A watchdog answers
# locally when a connected-but-wedged desk produces nothing within the window.
_pending_reqs: dict[int, dict] = {}
_DESK_REPLY_TIMEOUT = float(os.getenv("DESK_REPLY_TIMEOUT_SECS", "45"))


# Phone sessions waiting on a desk reply, keyed by the same req_id the desk
# echoes on every frame. A req_id in here means "this answer belongs to a phone,
# not to Telegram" — the desk-link reader checks it before the Telegram relay.
_app_sinks: dict[int, "asyncio.Queue"] = {}

# Live phone sockets. Used to decide whether the desk is worth polling for
# telemetry, and to announce the desk arriving; per-session replies are still
# written by each session's own writer, never from here.
_app_clients: set = set()

# push_token -> platform, for phones that are not holding a socket right now.
_push_targets: dict = {}

# What each phone calls its notification channels, keyed by push token.
#
# Not persisted, and it does not need to be: the app registers on every launch,
# and until it does the defaults below apply. Android drops a notification sent
# to a channel that does not exist, so guessing here is not a small mistake — it
# is a push that Expo accepts and nobody ever sees.
_push_channels: dict = {}

# Where a phone that has not registered its channels is assumed to listen.
#
# Kept in step with jarvis-mobile's `notify.ts` — and the whole point of the
# registration above is that this file should never again be the thing that has
# to be remembered. These are the floor, not the contract.
DEFAULT_CHANNELS = {"general": "general-v8", "watch": "desk-watch-v2"}


def _channel_for(token: str, kind: str) -> str:
    """The channel THIS phone calls `kind`, or the current default.

    Android silently discards a notification addressed to a channel that does not
    exist, so this lookup is the difference between a push that arrives and one
    that Expo accepts and nobody ever sees.
    """
    named = _push_channels.get(token) or {}
    return named.get(kind) or DEFAULT_CHANNELS.get(kind) or kind
_last_push_at: float = 0.0


def _desk_connected() -> bool:
    return _desk_ws is not None


# ── Push, and the desk announcement that uses it ─────────────────────────────

# ── The commute schedule, and why the gateway holds one ──────────────────────
#
# The morning briefing was a local job on the phone, and measured on the device
# on 2026-08-20 it cannot be: `expo-background-task` requires a connected network
# for every run (`BackgroundTaskScheduler.kt:108`), and the app's uid reads
# `Network: 108 (blocked=REASON_APP_BACKGROUND|REASON_APP_STANDBY)` with
# `#netAvail=0` while sitting in the RARE standby bucket. The work was not being
# deferred, it was stopped — logcat caught the pending worker running 200ms after
# a cold launch and then re-queueing into a window it would be blocked in again.
# So the app was the only thing that could unblock its own briefing, which is
# exactly how it was reported: "it arrives after I open the app".
#
# A high-priority push is exempt from all three restrictions, and this gateway can
# already send one. What it could not do is know WHEN — so the phone now tells it.
#
# One schedule, not one per phone. This whole system has a single operator and a
# single `APP_TOKEN`; facts are global here for the same reason.
_commute: dict = {}


def _load_commute() -> None:
    global _commute
    try:
        with open(_COMMUTE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _commute = data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        _commute = {}


def _save_commute() -> None:
    try:
        with open(_COMMUTE_FILE, "w", encoding="utf-8") as fh:
            json.dump(_commute, fh)
    except Exception:  # noqa: BLE001
        # an unwritable file costs a re-send on the next connect, not a feature
        pass
    # and the copy that survives a deploy, which the file does not
    _persist("commute", _commute)


def _clean_commute(body: dict) -> Optional[dict]:
    """Read the upload, or return None if it cannot be trusted.

    Rejecting outright rather than repairing. A schedule half-understood is worse
    than none: it would fire a notification at a time nobody chose, which is the
    one thing this feature must never do — a briefing arriving from a setting the
    operator turned off would teach him to distrust every other one.
    """
    tz = str((body or {}).get("tz") or "").strip()[:64]
    if not tz:
        return None

    days = (body or {}).get("days")
    if not isinstance(days, list) or len(days) != 7:
        return None
    # indexed the way JavaScript's `Date.getDay()` counts, 0 = Sunday, because
    # that is what the phone means by it. Python's `weekday()` starts on Monday,
    # and the scheduler must convert rather than assume.
    days = [bool(d) for d in days]

    rows = (body or {}).get("departures")
    if not isinstance(rows, list):
        return None
    out = []
    for r in rows[:8]:
        if not isinstance(r, dict):
            continue
        try:
            hour, minute = int(r.get("hour")), int(r.get("minute"))
            lat, lon = float(r.get("lat")), float(r.get("lon"))
        except (TypeError, ValueError):
            continue
        place_id = str(r.get("place_id") or "").strip()[:32]
        if not place_id:
            continue
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        out.append({
            "place_id": place_id,
            "label": str(r.get("label") or place_id).strip()[:32],
            "hour": hour, "minute": minute, "lat": lat, "lon": lon,
        })
    return {"tz": tz, "days": days, "departures": out}


def _load_push_targets() -> None:
    """Read the saved addresses, if the disk still has them.

    Render's free tier wipes this on redeploy, which is survivable: the phone
    re-registers on every cloud connect, so a lost file costs one reconnect
    rather than a re-pairing.
    """
    global _push_targets
    try:
        with open(_PUSH_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _push_targets = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        _push_targets = {}


def _save_push_targets() -> None:
    try:
        with open(_PUSH_FILE, "w", encoding="utf-8") as fh:
            json.dump(_push_targets, fh)
    except Exception:  # noqa: BLE001
        # an unwritable file costs a re-registration, not a feature
        pass
    # The addresses matter more than the file admits. A wiped list means the
    # gateway can reach nobody until the app next opens a SOCKET — not until it is
    # next used, which is what made this look survivable.
    _persist("push_targets", _push_targets)


def _expo_push_blocking(tokens: list, title: str, body: str, data: dict,
                        kind: str = "general") -> dict:
    """POST one batch to Expo. Blocking on purpose — the caller threads it.

    Returns the DECODED reply, not a truncated string. Expo answers HTTP 200 even
    when it accepted nothing: the verdict is per token, in `data[i].status`, in
    the order the tokens were sent. Reading only the status code — or printing
    400 characters of it — reports a push that reached nobody as a push that
    worked, which is this project's recurring failure shape.
    """
    payload = json.dumps([
        # Resolved PER PHONE, from what that phone said it created. Android
        # drops a notification addressed to a channel that does not exist, and
        # this file used to name `general` and `desk-watch` outright — both of
        # which the app has since renamed and deleted, `general` eight times
        # over. Every reply push in between was accepted by Expo and discarded
        # by Android, which is why bug C survived three separate fixes to the
        # socket that were all, individually, correct.
        #
        # `watch` is the MAX-importance one, so a lock countdown can interrupt
        # where a status change should not.
        {"to": t, "title": title, "body": body, "data": data,
         "priority": "high", "channelId": _channel_for(t, kind)}
        for t in tokens
    ]).encode("utf-8")
    req = urllib.request.Request(
        EXPO_PUSH_URL, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8", "replace")
    try:
        out = json.loads(raw)
        return out if isinstance(out, dict) else {"data": out}
    except Exception:  # noqa: BLE001
        # Unparseable is not "delivered" — say so rather than returning {}.
        return {"_unparsed": raw[:400]}


async def _push_all(title: str, body: str, data: Optional[dict] = None,
                    kind: str = "general", force: bool = False) -> None:
    """Push to every registered phone.

    `force` skips the quiet gap, and exactly one caller uses it: the desk watch.
    That alert is a 30-second window on whether a machine stays unlocked, so
    rate-limiting it would mean choosing to drop a security event because a
    status notification happened to go out four minutes earlier.
    """
    global _last_push_at
    if not _push_targets:
        return
    now = time.monotonic()
    if not force and _last_push_at and now - _last_push_at < APP_PUSH_MIN_GAP_SECS:
        print("[CLOUD] push suppressed - inside the quiet gap", flush=True)
        return
    _last_push_at = now
    sent = list(_push_targets)
    try:
        out = await asyncio.to_thread(
            _expo_push_blocking, sent, title, body, data or {}, kind)
    except Exception as e:  # noqa: BLE001
        # a push that cannot be delivered must never take the desk link down
        print(f"[CLOUD] push failed: {e}", flush=True)
        return

    # Expo answers 200 with a per-token verdict, in the order the tokens went out.
    # Read it: a token belonging to an uninstalled or replaced app comes back
    # DeviceNotRegistered forever, and every push after that was being counted as
    # delivered. It matters most on the one path that cannot afford it — the desk
    # watch, whose alert is a 30-second window on whether the machine locks.
    tickets = out.get("data") if isinstance(out, dict) else None
    if not isinstance(tickets, list):
        print(f"[CLOUD] push -> {len(sent)} target(s), but Expo's reply was not "
              f"readable, so DELIVERY IS UNCONFIRMED: {out}", flush=True)
        return

    ok, dead, failed = 0, [], []
    for token, ticket in zip(sent, tickets):
        status = (ticket or {}).get("status") if isinstance(ticket, dict) else None
        if status == "ok":
            ok += 1
            continue
        detail = ((ticket or {}).get("details") or {}).get("error", "") \
            if isinstance(ticket, dict) else ""
        if detail == "DeviceNotRegistered":
            dead.append(token)
        else:
            failed.append(detail or "unknown")
    if dead:
        # Pruned, or the registry grows by one on every reinstall and each push
        # pays for addresses that can never answer.
        for token in dead:
            _push_targets.pop(token, None)
        _save_push_targets()
        print(f"[CLOUD] pruned {len(dead)} unregistered push target(s) - "
              f"{len(_push_targets)} remain.", flush=True)
    if ok:
        print(f"[CLOUD] push delivered to {ok}/{len(sent)} target(s).", flush=True)
    else:
        # The loud case on purpose: nothing arrived, and the caller has no other
        # way to find out.
        print(f"[CLOUD] PUSH REACHED NOBODY - {len(sent)} target(s), "
              f"{len(dead)} unregistered, errors={failed}", flush=True)


# ── The briefing the gateway sends itself ────────────────────────────────────
#
# Ported from jarvis-mobile `src/lib/commute.ts`, deliberately rather than
# shared. The phone keeps its copy as a fallback and for PREVIEW, which is the
# button that proves the channel and the permission without waiting on anything.
# Two copies of the thresholds is a real cost; the alternative was a phone
# fetching a forecast it has no network to fetch.
#
# Why any of this is here is in `_load_commute` above: the device job cannot run.
COMMUTE_TICK_SECS = float(os.getenv("COMMUTE_TICK_SECS", "60"))
# How long after a departure time a briefing may still go out. Not slop for a
# scheduler — this loop is punctual — but for a redeploy or a cold start landing
# inside the minute that mattered. Past this it is stale: a warning about rain on
# your way out is worth nothing once you have already left.
COMMUTE_FIRE_WINDOW_MIN = int(os.getenv("COMMUTE_FIRE_WINDOW_MIN", "20"))
OPEN_METEO_URL = os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")

# The thresholds, in one place so they can be argued with — the same values and
# the same names the phone uses.
RAIN_CHANCE = 50
RAIN_MM = 0.4
HOT_C = 35.0
COLD_C = 12.0
WINDY_KMH = 40.0
_THUNDER = {95, 96, 99}

# place_id -> the day it was last briefed, so the same umbrella is not announced
# three times. Per departure, so the morning cannot silence the evening.
_briefed: dict = {}
_BRIEFED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_briefed.json")


def _load_briefed() -> None:
    global _briefed
    try:
        with open(_BRIEFED_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _briefed = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        _briefed = {}


def _save_briefed() -> None:
    try:
        with open(_BRIEFED_FILE, "w", encoding="utf-8") as fh:
            json.dump(_briefed, fh)
    except Exception:  # noqa: BLE001
        # Worth naming: an unwritable file means a briefing could repeat after a
        # restart. That is the failure to prefer — a second umbrella notice is an
        # annoyance, a missed one is the whole feature.
        pass
    # Persisted for the annoyance rather than the feature: a deploy at 7:05 PM
    # used to clear today's marker, so the next tick would brief the same
    # departure again.
    _persist("briefed", _briefed)


def _commute_zone(name: str):
    """The operator's zone, or UTC.

    Falling back rather than raising: a schedule with an unreadable zone is still
    better acted on late than not at all, and /health reports the tz it was
    given, so a wrong one is visible rather than silent.
    """
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        import datetime as _dt
        return _dt.timezone.utc


def _js_weekday(when) -> int:
    """Sunday-first, the way the phone indexes its `days` array.

    `Date.getDay()` counts 0 = Sunday; Python's `weekday()` counts 0 = Monday.
    Converted here, once, because getting it wrong is a briefing that arrives on
    the wrong days and reads as a scheduling bug rather than an off-by-one.
    """
    return (when.weekday() + 1) % 7


def _hour_label(hour: int) -> str:
    """`7 PM`. The meridiem is always printed.

    A window that read `08:00-11:00` on a briefing its owner had set for the
    evening is the reason: 24-hour digits are unambiguous only to a reader
    already thinking in them.
    """
    twelve = 12 if hour % 12 == 0 else hour % 12
    return f"{twelve} {'AM' if hour % 24 < 12 else 'PM'}"


def _due_departure(now, sched: dict) -> Optional[dict]:
    """Which departure wants briefing now, if any, and not already done today.

    Fires at the time or shortly after, never before. The phone's window was
    ±30 minutes because Android chose when its job ran; nothing chooses for this
    loop, so early delivery would be a decision rather than a symptom — and a
    warning about the walk out is worth less the earlier it arrives.
    """
    days = sched.get("days") or []
    if len(days) != 7 or not days[_js_weekday(now)]:
        return None
    today = now.strftime("%Y-%m-%d")
    minutes_now = now.hour * 60 + now.minute
    for d in sched.get("departures") or []:
        try:
            target = int(d["hour"]) * 60 + int(d["minute"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (target <= minutes_now <= target + COMMUTE_FIRE_WINDOW_MIN):
            continue
        if _briefed.get(d.get("place_id")) == today:
            continue
        return d
    return None


def _forecast_blocking(lat: float, lon: float, tz: str) -> Optional[dict]:
    """Read Open-Meteo. Blocking on purpose — the caller threads it.

    None for any failure, and the caller stays SILENT on a None. Announcing "all
    clear" when the lookup failed would be the one genuinely dishonest message
    this feature could send, and the phone learned that expensively: a briefing
    that could not tell "the morning is fine" from "I could not find out" marked
    the day done and went quiet until tomorrow, where it failed identically.
    """
    url = (f"{OPEN_METEO_URL}?latitude={lat:.4f}&longitude={lon:.4f}"
           "&hourly=temperature_2m,precipitation_probability,precipitation,"
           "weather_code,wind_speed_10m"
           f"&forecast_days=2&timezone={urllib.parse.quote(tz)}")
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", "replace")
        out = json.loads(raw)
        return out if isinstance(out, dict) else None
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] commute forecast failed: {e}", flush=True)
        return None


def _briefing_text(data: dict, dep: dict, today: str) -> Optional[tuple]:
    """The briefing, or None when the forecast said nothing about these hours.

    The voice is the phone's and the four rules travel with it: the figure comes
    first and the remark second, the remark never replaces the instruction, `sir`
    is spent once and the title spends it, and there are no exclamation marks.
    Understatement is the whole instrument.
    """
    hourly = (data or {}).get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None

    # the departure hour and the two after it, matched by local hour rather than
    # by index: the array starts at midnight, but only in the API's timezone
    wanted = {(int(dep["hour"]) + n) % 24 for n in (0, 1, 2)}
    rows = []
    for i, iso in enumerate(times):
        date, _, clock = str(iso).partition("T")
        if date != today:
            continue
        try:
            if int(clock[:2]) in wanted:
                rows.append(i)
        except ValueError:
            continue
    # answered, but not about the hours being asked about. Still an absence of
    # knowledge rather than a quiet morning, so the day is not consumed
    if not rows:
        return None

    def pick(key):
        arr = hourly.get(key) or []
        return [float(arr[i]) for i in rows
                if i < len(arr) and isinstance(arr[i], (int, float))]

    temps, chances = pick("temperature_2m"), pick("precipitation_probability")
    mm, winds, codes = pick("precipitation"), pick("wind_speed_10m"), pick("weather_code")

    max_chance = max(chances) if chances else 0.0
    total_mm = sum(mm)
    max_temp = max(temps) if temps else None
    min_temp = min(temps) if temps else None
    max_wind = max(winds) if winds else 0.0
    storm = any(int(c) in _THUNDER for c in codes)

    notes = []
    if storm:
        notes.append("Thunderstorms forecast. Leave early or wait it out — "
                     "either beats the alternative.")
    if max_chance >= RAIN_CHANCE or total_mm >= RAIN_MM:
        near = f", around {total_mm:.1f} mm" if total_mm >= RAIN_MM else ""
        notes.append(f"A {round(max_chance)}% chance of rain on your way out{near}. "
                     "An umbrella, unless you've grown fond of arriving wet.")
    if max_temp is not None and max_temp >= HOT_C:
        notes.append(f"It reaches {round(max_temp)}°C today. Water, and something "
                     "for your head — I would rather not arrange the hospital visit.")
    if min_temp is not None and min_temp <= COLD_C:
        notes.append(f"Down to {round(min_temp)}°C. The jacket you keep ignoring "
                     "would be appropriate.")
    if max_wind >= WINDY_KMH:
        notes.append(f"Gusts to {round(max_wind)} km/h. Mind the hair.")

    hour = int(dep["hour"])
    window = f"{_hour_label(hour)}–{_hour_label((hour + 3) % 24)}"
    label = dep.get("label") or dep.get("place_id") or "there"

    # A quiet day is announced too, and it carries the figures. Silence was
    # indistinguishable from the feature being broken and was read that way for
    # four days straight — so the reassurance is not the word "fine", it is
    # numbers that can be disagreed with if they are wrong.
    if not notes:
        degrees = "" if max_temp is None else f"{round(max_temp)}°C, "
        return (f"Nothing in your way from {label}, sir",
                f"Nothing to carry. {degrees}a {round(max_chance)}% chance of rain, "
                f"wind {round(max_wind)} km/h ({window}). Do try to enjoy it.")

    # named, because two of these arrive in a day and a shade holding both has to
    # say which door each one is about
    return (f"Before you leave {label}, sir", f"{' '.join(notes)} ({window})")


async def _commute_tick() -> None:
    """One pass. Does nothing at all outside a departure window."""
    if not _commute or not _push_targets:
        return
    import datetime as _dt
    now = _dt.datetime.now(_commute_zone(_commute.get("tz") or "UTC"))
    dep = _due_departure(now, _commute)
    if not dep:
        return

    today = now.strftime("%Y-%m-%d")
    data = await asyncio.to_thread(
        _forecast_blocking, float(dep["lat"]), float(dep["lon"]),
        _commute.get("tz") or "UTC")
    if data is None:
        # NOT marked briefed: the day stays open for the next tick. A failed
        # lookup must not consume the day, which is the mistake the phone made
        # and then had to be talked out of.
        return
    said = _briefing_text(data, dep, today)
    if said is None:
        return

    title, body = said
    # `force`, and only the second caller ever to use it. Read the note on the
    # quiet gap in `_push_all`: it exists so a flapping desk cannot become a
    # burst of identical notifications. A briefing cannot burst — once per
    # departure per day, held down by `_briefed` — and it is the only push here
    # the operator actually asked for by name. Dropping it because a status
    # notification went out four minutes earlier would be the gap deciding
    # against the one thing it was never meant to police.
    await _push_all(title, body,
                    data={"kind": "commute", "place_id": dep.get("place_id")},
                    kind="general", force=True)
    # He can ask about a briefing he was just sent — "was that for the office
    # or for home", "what did you say about the rain" — and before this the
    # brain had never said any of it. Same argument as the nudge, and this one
    # was not writing to the wrong key, it was writing nowhere.
    await _remember_said(f"{title} — {body}")
    _briefed[dep.get("place_id")] = today
    _save_briefed()
    print(f"[CLOUD] briefing pushed for "
          f"{dep.get('label') or dep.get('place_id')} ({today})", flush=True)


async def _commute_loop() -> None:
    """Watch the clock the phone cannot.

    A minute's granularity against a twenty-minute window: nothing here needs to
    be exact, and a tick that costs nothing when no departure is due can afford
    to be frequent. Every exception is caught and printed — a loop that died
    quietly would put this feature back exactly where it started, correct in
    theory and never arriving.
    """
    print("[CLOUD] commute briefing loop started.", flush=True)
    while True:
        try:
            await _commute_tick()
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] commute tick failed: {e}", flush=True)
        await asyncio.sleep(COMMUTE_TICK_SECS)


# ── Speaking first ───────────────────────────────────────────────────────────
#
# Everything this assistant has ever said has been an answer. Asked for directly
# on 2026-08-20: "make the app interact with me autonomously, without sending my
# chat." That is the difference between a very good assistant app and the thing
# it is named after — the film's JARVIS starts sentences.
#
# The commute briefing was the first unprompted thing the phone ever received, and
# it proved the delivery: a high-priority push reaches a phone that is asleep, in a
# pocket, with the app closed. This reuses that path for something that is not on a
# timetable.
#
# THE DESIGN CONSTRAINT, and it is the whole of it: **he must be worth reading.**
# A machine that speaks unprompted is one bad week away from being muted, and a
# muted assistant cannot say the one thing that mattered. So:
#
#   * at most one unprompted remark a day, and never two in a row on the same
#     subject;
#   * only when something is actually true today that was not true yesterday —
#     nothing generated for the sake of speaking;
#   * silence is the default and needs no excuse. There is no "nothing to report"
#     message, because that is the message you learn to swipe away;
#   * and it is a REMARK, not a briefing. One or two sentences, in his voice.
#
# The quiet hours are not negotiable either. Nothing goes out before NUDGE_FROM_H
# or after NUDGE_UNTIL_H, whatever it thinks it has noticed.
NUDGE_ENABLED = (os.getenv("NUDGE_ENABLED", "1").strip() == "1")
NUDGE_TICK_SECS = float(os.getenv("NUDGE_TICK_SECS", "900"))
NUDGE_FROM_H = int(os.getenv("NUDGE_FROM_H", "9"))
NUDGE_UNTIL_H = int(os.getenv("NUDGE_UNTIL_H", "21"))
_NUDGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_nudge.json")

# what was said unprompted, and when: {"day": "YYYY-MM-DD", "about": "<subject>"}
_nudge: dict = {}


def _load_nudge() -> None:
    global _nudge
    try:
        with open(_NUDGE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _nudge = data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        _nudge = {}


def _save_nudge() -> None:
    try:
        with open(_NUDGE_FILE, "w", encoding="utf-8") as fh:
            json.dump(_nudge, fh)
    except Exception:  # noqa: BLE001
        # An unwritable file means he could speak twice in a day. That is the
        # failure to prefer over the alternative — a lost marker that silenced him
        # permanently would be indistinguishable from the feature not existing.
        pass
    # Same reasoning as `_briefed`, and the same deploy cleared both.
    _persist("nudge", _nudge)


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday")


def _asserts_today(low: str, weekday: str) -> bool:
    """Whether a fact claims something about TODAY, rather than merely mentioning it.

    This replaces `weekday in low`, a bare substring test, and the replacement exists
    because that test was wrong in the most expensive way available: on Friday
    2026-08-21 it matched a Monday-to-Friday work pattern, the prompt below then
    asserted the fact as true today, and the model — obeying — invented a Saturday
    shift the operator did not have. Reported from the phone within the hour.

    Two rules, and the second is the one that mattered:

    1. The day has to be named as something RECURRING or as today's own business:
       "every friday", "fridays", "on friday". A weekday appearing anywhere in a
       sentence is not a claim about this Friday.
    2. **A fact naming any OTHER weekday is refused outright.** "Mon-Fri" mentions
       Friday and is not about Friday; it is about a week. Anything listing more than
       one day is describing a pattern whose interesting part is usually the day that
       is NOT today, which is exactly the trap that was fallen into.
    """
    named = [d for d in WEEKDAYS if d in low]
    # more than one day named means a range or a list: a pattern, not today
    if len(named) != 1 or named[0] != weekday:
        return False
    # and it has to read as recurring or as today's business, not incidental
    return any(
        p in low
        for p in (f"every {weekday}", f"{weekday}s", f"on {weekday}", f"this {weekday}")
    )


def _nudge_subject(facts: list, now) -> Optional[tuple]:
    """What is worth remarking on today, as (subject, prompt), or None.

    None is the expected answer most of the time and needs no apology. This looks
    for a fact that is ABOUT today rather than merely stored — a date that has
    arrived, a day named. Anything else is a fact he already knows and mentioning
    it unprompted is reciting.

    Deliberately narrow. The temptation is to hand the whole fact list to a model
    and ask "anything interesting?", which produces a remark every single day
    because a model asked for something will always find something. The judgement
    of WHETHER to speak is made here, in code, and only the WORDING is the model's.

    **That last sentence was not true until 2026-08-21.** The judgement was a
    substring match, and it announced a Saturday shift that did not exist. See
    `_asserts_today`, and note that the prompt below is now told to refuse rather
    than to invent when the fact does not actually say what it was matched for —
    the second guard, because the first one being wrong once cost a day's trust.
    """
    today = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A").lower()
    day_num = str(now.day)
    month = now.strftime("%B").lower()

    for fact in facts:
        low = str(fact).lower()
        # a fact naming today's date, in either order people write it
        dated = (f"{day_num} {month}" in low or f"{month} {day_num}" in low
                 or today in low)
        if not (dated or _asserts_today(low, weekday)):
            continue
        return (
            str(fact)[:120],
            f"Today is {now.strftime('%A %d %B')}. You were once told: "
            f"\"{fact}\". If — and ONLY if — that fact is about today, remark on it "
            "in ONE short sentence, as though you had just remembered it, the way "
            "someone who knows him would mention it in passing. Do not greet him, "
            "do not list anything, do not offer help, and do not explain that you "
            "remembered. **Never state anything the fact does not say**, and never "
            "infer another day from it. If it is not about today, or does not "
            "warrant saying out loud, reply with exactly: SKIP",
        )
    return None


async def _nudge_tick() -> None:
    """One pass. Silent unless something is genuinely true today."""
    if not NUDGE_ENABLED or not _push_targets:
        return
    import datetime as _dt
    now = _dt.datetime.now(_OPERATOR_TZ)
    if not (NUDGE_FROM_H <= now.hour < NUDGE_UNTIL_H):
        return

    today = now.strftime("%Y-%m-%d")
    if _nudge.get("day") == today:
        return

    facts = await _facts()
    if not facts:
        return
    found = _nudge_subject(facts, now)
    if not found:
        return
    subject, instruction = found
    # the same subject twice is how a remark becomes a nag
    if _nudge.get("about") == subject:
        return

    ident = next((i for i in _IDENTITIES.values() if i["tier"] == _ADMIN_TIER), None)
    if not ident:
        return

    try:
        said = await think(APP_CHAT_ID, instruction, ident["who"],
                           ident["honorific"], surface="app")
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] nudge could not be worded: {e}", flush=True)
        return

    said = (said or "").strip()
    # The model's own veto, and it is honoured. Asked whether something is worth
    # saying, a model that answers "no" is more trustworthy than one that never
    # does — and the marker is still written, so it does not reconsider all day.
    if not said or said.upper().startswith("SKIP"):
        _nudge.update({"day": today, "about": subject})
        _save_nudge()
        print("[CLOUD] nudge declined by the brain; nothing sent.", flush=True)
        return

    await _push_all("J.A.R.V.I.S.", said,
                    data={"kind": "reply", "unprompted": True},
                    kind="general", force=True)
    _nudge.update({"day": today, "about": subject})
    _save_nudge()
    print(f"[CLOUD] spoke first: {said[:80]}", flush=True)


async def _nudge_loop() -> None:
    """Watch for something worth saying, and say almost nothing.

    Quarter-hourly, which for at most one remark a day is generous — but the
    window it is looking for is "today", and a tick that costs a dictionary lookup
    when there is nothing to say can afford to be frequent. Every exception is
    caught and printed: a loop that died quietly would be a feature that silently
    stopped existing, which is this project's recurring failure shape.
    """
    print("[CLOUD] unprompted-remark loop started.", flush=True)
    while True:
        try:
            await _nudge_tick()
        except Exception as e:  # noqa: BLE001
            print(f"[CLOUD] nudge tick failed: {e}", flush=True)
        await asyncio.sleep(NUDGE_TICK_SECS)


async def _broadcast_app(payload: dict) -> None:
    """One frame to every attached phone, dropping the ones that have gone."""
    for ws in list(_app_clients):
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001
            _app_clients.discard(ws)


async def _announce_desk(linked: bool) -> None:
    """Tell the phones the desk arrived, or left.

    A phone holding a socket gets the frame and raises its own notification.
    Push therefore goes out only when NO phone is attached — a listening phone
    would otherwise be told twice for one event, which you feel in your pocket.

    Only the arrival is pushed. Losing the desk is a quiet downgrade, and waking
    someone to report that a machine went to sleep is noise.
    """
    await _broadcast_app({"type": "desk", "linked": bool(linked)})
    if linked and not _app_clients:
        await _push_all(
            "J.A.R.V.I.S. is on full power",
            "The desk is online. PC control, files and terminal are available again.",
            {"kind": "desk_link"})


# ── Real answers about where the phone is ────────────────────────────────────
# Open-Meteo takes coordinates and needs no key, which is why it is here rather
# than a provider that would put billing between him and "is it raining".
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# WMO weather codes, collapsed to what a person would say. Enough to be honest
# about rain, which is the thing the model kept getting wrong from memory.
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}
# lat/lon rounded to ~1km, held for ten minutes: repeated questions in one
# conversation cost one request, and the answer is still current
_weather_cache: dict = {}
WEATHER_TTL_SECS = 600
# how long to stop asking after a 429, since a shared IP does not recover in seconds
WEATHER_BACKOFF_SECS = 900
_weather_blocked_until: float = 0.0


def _get_json_blocking(url: str, timeout: float = 8.0) -> dict:
    """One GET, decoded. Blocking; every caller threads it."""
    req = urllib.request.Request(url, headers={
        # Nominatim's usage policy requires an identifying agent, and the others
        # are happier with one too
        "User-Agent": "jarvis-cloud-gateway/1.0 (personal assistant)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", "replace"))


def _weather_blocking(lat: float, lon: float) -> Optional[str]:
    """Fallback only. The phone normally sends this.

    Open-Meteo rate-limits per IP, and Render's outbound address is shared with
    every other service on the host — so this was answered `429 Too Many Requests`
    in production while the same URL returned 200 from a laptop. The phone fetches
    it now, from its own address; this remains for a client that sends none.

    A 429 therefore backs off rather than being retried per turn, and a stale
    reading is served in preference to nothing: an hour-old temperature is still
    worth more than "I could not check".
    """
    global _weather_blocked_until
    key = (round(lat, 2), round(lon, 2))
    hit = _weather_cache.get(key)
    if hit and time.time() - hit[0] < WEATHER_TTL_SECS:
        return hit[1]
    if time.time() < _weather_blocked_until:
        return hit[1] if hit else None
    url = (f"{OPEN_METEO_URL}?latitude={lat:.4f}&longitude={lon:.4f}"
           "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
           "precipitation,weather_code,wind_speed_10m"
           "&daily=precipitation_probability_max&forecast_days=1&timezone=auto")
    try:
        data = _get_json_blocking(url)
        now = data.get("current") or {}
        code = int(now.get("weather_code", -1))
        said = _WMO.get(code, "unclear conditions")
        mm = now.get("precipitation")
        chance = ((data.get("daily") or {}).get("precipitation_probability_max") or [None])[0]
        parts = [
            f"{said}",
            f"{now.get('temperature_2m')}°C (feels {now.get('apparent_temperature')}°C)",
            f"humidity {now.get('relative_humidity_2m')}%",
            f"wind {now.get('wind_speed_10m')} km/h",
        ]
        # the figure that settles "is it raining": actual precipitation now, not a
        # forecast, and not the model's recollection
        if isinstance(mm, (int, float)):
            parts.append(f"precipitation {mm} mm in the last hour")
        if isinstance(chance, (int, float)):
            parts.append(f"rain chance today {chance}%")
        line = ", ".join(str(p) for p in parts)
        _weather_cache[key] = (time.time(), line)
        return line
    except Exception as e:  # noqa: BLE001
        # 429 is not a transient blip on a shared IP, it is the state of the world
        # for a while: stop asking, and keep answering from what is already known.
        if "429" in str(e):
            _weather_blocked_until = time.time() + WEATHER_BACKOFF_SECS
            print(f"[CLOUD] weather rate-limited; backing off "
                  f"{WEATHER_BACKOFF_SECS}s (the phone should be sending this)", flush=True)
        else:
            print(f"[CLOUD] weather lookup failed: {e}", flush=True)
        return hit[1] if hit else None


# Places and routes, also key-free. Nominatim for "what is near me", OSRM for
# "how far" — both public, both rate-limited, neither offering live traffic.
# Traffic needs a paid provider; when there is a key for one, `_route_blocking` is
# the single place that would change.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving"

# Deliberately narrow, and English only. A pattern that half-works in three
# languages is worse than one that admits its scope: an unmatched question simply
# reaches the brain as it always did.
_NEAR_RE = re.compile(r"\b(?:nearest|nearby|closest|near me)\b[:,]?\s*(?P<what>[\w\s'&-]{2,40})?", re.I)
_FAR_RE = re.compile(r"\b(?:how far|distance|how long)\b.{0,20}?\b(?:to|from|until)\s+(?P<dest>[\w\s',.&-]{2,60})", re.I)


def _places_blocking(what: str, lat: float, lon: float, limit: int = 4) -> Optional[str]:
    """Whatever is closest, by straight-line distance from him."""
    q = urllib.parse.quote(what.strip())
    # a ~12km box keeps "nearest chemist" local instead of national
    d = 0.11
    url = (f"{NOMINATIM_URL}?q={q}&format=json&limit={limit}&addressdetails=0"
           f"&viewbox={lon - d:.4f},{lat + d:.4f},{lon + d:.4f},{lat - d:.4f}&bounded=1")
    try:
        rows = _get_json_blocking(url)
        if not isinstance(rows, list) or not rows:
            return None
        out = []
        for r in rows[:limit]:
            try:
                km = _haversine_km(lat, lon, float(r["lat"]), float(r["lon"]))
            except Exception:  # noqa: BLE001
                continue
            name = str(r.get("display_name") or "").split(",")[0]
            area = ", ".join(str(r.get("display_name") or "").split(",")[1:3]).strip()
            out.append(f"{name} ({area}) about {km:.1f} km away")
        return "; ".join(out) or None
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] place lookup failed: {e}", flush=True)
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def _match_known(dest: str, known: list) -> Optional[dict]:
    """Resolve a destination against the places he has named.

    Checked before any geocoder, because "the office" is not a place Nominatim can
    find — it is a word that means something only to him, and searching for it
    would return somebody else's office in another city.
    """
    want = dest.strip().lower()
    if not want:
        return None
    for place in known:
        label = str(place.get("label") or "").strip().lower()
        if not label:
            continue
        # "office", "the office", "my office" all mean the one he named
        if label == want or label in want:
            return place
    return None


def _route_to_blocking(name: str, dlat: float, dlon: float, lat: float, lon: float) -> Optional[str]:
    """Distance and time to coordinates already known — no geocoding needed."""
    try:
        route = _get_json_blocking(
            f"{OSRM_URL}/{lon:.5f},{lat:.5f};{dlon:.5f},{dlat:.5f}?overview=false")
        legs = (route.get("routes") or [{}])[0]
        metres, seconds = legs.get("distance"), legs.get("duration")
        if not isinstance(metres, (int, float)) or not isinstance(seconds, (int, float)):
            return None
        straight = _haversine_km(lat, lon, dlat, dlon)
        return (f"{name}: {metres / 1000:.1f} km by road ({straight:.1f} km straight line), "
                f"about {int(seconds // 60)} min driving with no traffic accounted for")
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] route lookup failed: {e}", flush=True)
        return None


def _route_blocking(dest: str, lat: float, lon: float) -> Optional[str]:
    """Driving distance and duration to a named place.

    **No traffic.** OSRM's public server routes on the road graph alone, so the
    duration is free-flowing time — right for "how far", optimistic for "when will
    I get there" at 6pm. Said plainly in the context rather than quietly implied.
    """
    try:
        q = urllib.parse.quote(dest.strip())
        found = _get_json_blocking(f"{NOMINATIM_URL}?q={q}&format=json&limit=1")
        if not isinstance(found, list) or not found:
            return None
        dlat, dlon = float(found[0]["lat"]), float(found[0]["lon"])
        name = str(found[0].get("display_name") or dest).split(",")[0]
        route = _get_json_blocking(
            f"{OSRM_URL}/{lon:.5f},{lat:.5f};{dlon:.5f},{dlat:.5f}?overview=false")
        legs = (route.get("routes") or [{}])[0]
        metres, seconds = legs.get("distance"), legs.get("duration")
        if not isinstance(metres, (int, float)) or not isinstance(seconds, (int, float)):
            return None
        straight = _haversine_km(lat, lon, dlat, dlon)
        return (f"{name}: {metres / 1000:.1f} km by road ({straight:.1f} km straight line), "
                f"about {int(seconds // 60)} min driving with no traffic accounted for")
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] route lookup failed: {e}", flush=True)
        return None


async def _local_lookups(where: dict, text: str) -> list:
    """Anything the question asks about *this* place, fetched rather than recalled."""
    facts = []
    near = _NEAR_RE.search(text)
    if near and (near.group("what") or "").strip():
        found = await asyncio.to_thread(
            _places_blocking, near.group("what"), where["lat"], where["lon"])
        if found:
            facts.append(f"Closest matches for '{near.group('what').strip()}': {found}.")
    far = _FAR_RE.search(text)
    if far:
        dest = far.group("dest")
        # a place he named beats a geocoder that has never heard of it
        named = _match_known(dest, where.get("known") or [])
        route = await asyncio.to_thread(
            _route_to_blocking, named["label"], named["lat"], named["lon"], where["lat"], where["lon"]
        ) if named else await asyncio.to_thread(_route_blocking, dest, where["lat"], where["lon"])
        if route:
            facts.append(f"Route from him: {route}.")

    # where he is standing, if it is somewhere he named. Answers "am I at the
    # office" and stops the model guessing from a district name.
    here = _match_here(where)
    if here:
        facts.append(f"He is currently at {here}.")
    return facts


def _match_here(where: dict) -> Optional[str]:
    """The name of wherever he is, if he has named it. 250m counts as being there."""
    for place in where.get("known") or []:
        try:
            if _haversine_km(where["lat"], where["lon"], float(place["lat"]), float(place["lon"])) <= 0.25:
                return str(place.get("label") or "").strip() or None
        except Exception:  # noqa: BLE001
            continue
    return None


async def _where_context(where: dict, text: str) -> str:
    """Background facts for a question asked from a known place.

    The model was answering "is it raining" out of its own weights — confidently,
    and wrong, while it was raining outside. Giving it the actual current
    conditions is the fix; telling it not to guess is the other half.

    Weather is fetched for every located turn rather than when the text looks
    weather-shaped. Intent matching would have to work in English, Bengali and
    Benglish to be worth anything, and a missed match is exactly the failure being
    fixed. One cached HTTP call is cheaper than that risk.

    **This returns the block alone.** It used to return the block glued to the front
    of the operator's own message, and that did two kinds of damage. A user turn
    opening with a wall of facts reads as the operator having asked about them, so
    "thanks" was answered with the temperature — six times out of six on greetings
    in one log. And because `think()` stores what it is given, every one of those
    blocks was written into rolling memory, so by the tenth turn the conversation
    was mostly stale copies of his coordinates, which is where "still overcast" and
    "still in Presidency Division" came from. The caller now passes this as an
    ephemeral system turn that is never remembered.
    """
    # a name he set by standing there beats a reverse geocode, which returned four
    # different names for one desk across four consecutive turns
    place = where.get("label") or where.get("place") or f"{where['lat']:.3f},{where['lon']:.3f}"
    # The phone's own reading is preferred: it comes from an address that is not
    # rate-limited, and it costs this process no request at all. Only when a client
    # sends none does the gateway try for itself.
    given = where.get("weather")
    weather, extra = await asyncio.gather(
        asyncio.sleep(0, result=given) if given
        else asyncio.to_thread(_weather_blocking, where["lat"], where["lon"]),
        _local_lookups(where, text),
    )
    lines = [f"He is at {place} (lat {where['lat']:.4f}, lon {where['lon']:.4f})."]
    if weather:
        lines.append(f"Measured conditions there right now: {weather}.")
    else:
        lines.append("No live weather could be fetched; say so rather than guessing.")
    lines.extend(extra)
    trail = where.get("trail")
    if trail:
        lines.append(f"Where he has been recently, oldest first: {trail}.")
    known = where.get("known")
    if known:
        # so "how long to the office" and "is that near home" are answerable at all
        lines.append("Places he has named: "
                     + "; ".join(f"{p['label']} at {p['lat']:.4f},{p['lon']:.4f}" for p in known)
                     + ".")
    lines.append("Use these figures for anything about weather, distances or his "
                 "whereabouts. Do not contradict them and do not answer from memory.")
    # The rule that stops the facts becoming the reply. Without it a greeting was
    # answered with the temperature, because a block of facts next to "volunteer
    # the next useful fact" reads as an instruction to recite them.
    lines.append(
        "THIS IS BACKGROUND, NOT THE SUBJECT. It is here in case the question needs "
        "it. Answer only what he actually asked. If he said hello, thanked you, or "
        "said nothing that turns on where he is or what the weather is doing, do NOT "
        "mention his location, the temperature, or the forecast at all — a greeting "
        "answered with a weather report is a failure. Never open a reply by telling "
        "him where he is unless he asked."
    )
    return "[BACKGROUND CONTEXT — " + " ".join(lines) + "]"


async def _relay_watch(frame: dict) -> None:
    """Carry a desk-watch alert from the desk to the phones, socket or not.

    The alert used to travel only down the phone's WebSocket, which means it
    could only reach an app that was already running — and the phone is in a
    pocket precisely when this matters. The desk locks itself when its own
    countdown expires whether or not anyone answered, so an alert nobody sees is
    the desk's silence deciding.

    The frame is forwarded verbatim: `parseFrame` already reads `intruder` and
    `intruder_resolved`, so there is one contract here rather than a cloud dialect.
    """
    await _broadcast_app(frame)
    if frame.get("type") != "intruder" or _app_clients:
        # a resolution needs no push — the window is shut, there is nothing left
        # to answer — and an attached phone raises its own notification
        return
    who = str(frame.get("user") or "").strip()
    trigger = str(frame.get("trigger") or "unlock").strip()
    seconds = frame.get("expires_in")
    when = f" It locks itself in {int(seconds)}s." if isinstance(seconds, (int, float)) else ""
    # The whole alert rides in the payload, not just its id.
    #
    # A phone woken by this push holds no socket, so it never saw the frame — and
    # tapping the notification would open an app with nothing to show. Carrying
    # the fields lets it rebuild the alert and raise the answer screen.
    #
    # `expires_at_ms` is the one place this design puts a wall clock on the wire.
    # The desk deliberately sends `expires_in` seconds so the two machines never
    # have to agree on the time, but a notification can sit unread for minutes, so
    # a duration is meaningless by the time it is tapped. Skew only shifts a
    # readout: the desk still owns the countdown that decides, and locks on its own
    # clock whatever the phone displays.
    await _push_all(
        "Someone is at the desk",
        f"{trigger} as {who or 'an unknown user'}. Was that you?{when}",
        {"kind": "intruder",
         "id": frame.get("id") or frame.get("action_id") or "",
         "expires_at_ms": int((time.time() + float(seconds)) * 1000)
                          if isinstance(seconds, (int, float)) else None,
         "image": frame.get("image"),
         "user": frame.get("user"),
         "trigger": trigger},
        kind="watch",
        force=True)


_load_push_targets()

_load_commute()
_load_briefed()
_load_nudge()


async def _restore_state() -> None:
    """Read back what a deploy took, and say plainly what was recovered.

    The file loads above already ran at import, so this only OVERWRITES what the
    database actually holds — a gateway with no `DATABASE_URL`, or one whose first
    connect fails, keeps exactly the old behaviour.

    Awaited before the loops start rather than raced with them. The commute tick is
    every 60 seconds and would have picked this up anyway, but a briefing due in
    the first minute of a deploy is precisely the case this is for, and "it would
    probably catch up" is the kind of reasoning that hid the original bug.

    Empty is not a failure and is not reported as one: the first deploy after this
    lands finds an empty table, which is correct and says nothing interesting.
    """
    global _push_targets, _commute, _briefed, _nudge
    if not DATABASE_URL:
        return
    try:
        if not await _memory_ready():
            return
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] state restore skipped: {e}", flush=True)
        return

    recovered = []
    try:
        got = await asyncio.to_thread(_db_state_get_blocking, "push_targets")
        if got:
            _push_targets = {str(k): str(v) for k, v in got.items()}
            recovered.append(f"{len(_push_targets)} push target(s)")

        got = await asyncio.to_thread(_db_state_get_blocking, "commute")
        if got:
            # through the validator, not straight in: a row written by an older
            # build must not be trusted further than an upload from the phone is
            clean = _clean_commute(got)
            if clean:
                _commute = clean
                recovered.append(f"{len(clean['departures'])} departure(s)")
            else:
                print("[CLOUD] stored commute schedule REFUSED - unreadable", flush=True)

        got = await asyncio.to_thread(_db_state_get_blocking, "briefed")
        if got:
            _briefed = got
            recovered.append("today's briefing marker")

        got = await asyncio.to_thread(_db_state_get_blocking, "nudge")
        if got:
            _nudge = got
            recovered.append("today's remark marker")
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] state restore failed: {e}", flush=True)
        return

    if recovered:
        print(f"[CLOUD] state restored across the deploy: {', '.join(recovered)}.", flush=True)


def _queue_offline_fact(ident: dict, text: str, reply: str) -> None:
    """Seal and queue a turn the desk never saw — BEFORE the reply goes out.

    Ordered before the send on purpose (ruled 2026-08-01), and free to be: sealing
    is local CPU and the outbox is in memory, so there is no network hop in front
    of his answer. queue_fact never raises, so a lost fact can never cost him the
    reply either.
    """
    if fact_outbox is None:
        return
    fact_outbox.queue_fact(
        text,
        who=(ident.get("who") or "KAUSTAV").upper(),
        tier=ident.get("tier"),
        reply=reply,
    )


def _ensure_bot():
    global _bot, _dp
    if _bot is None:
        from aiogram import Bot
        _bot = Bot(token=BOT_TOKEN)
        _dp = _build_dispatcher()
    return _bot, _dp


# UptimeRobot pings with HTTP HEAD by default, so register HEAD alongside GET;
# both return 200 OK. (FastAPI/Starlette does not auto-answer HEAD for a GET route.)
@app.get("/")
@app.head("/")
@app.get("/health")
@app.head("/health")
async def health():
    roster = ", ".join(f"{i['who']} [{i['tier']}]" for i in _IDENTITIES.values()) or "none"
    # Diagnostics only — no secret VALUES exposed, just which features are wired.
    return {"status": "ok", "service": "jarvis-cloud-gateway",
            "mode": MODE, "identities": roster,
            "search": "tavily" if _TAVILY_KEY else "duckduckgo",
            # Which brain is actually answering, per capability. Reported because
            # a provider switch is an env change with no deploy behind it, so
            # there is otherwise no way to tell from outside what is live — and
            # "gemini" here with `gemini_ready` false is the whole diagnosis when
            # a switch silently kept using Groq.
            "brains": {
                "text": _provider_for("text"),
                "vision": _provider_for("vision"),
                "audio": _provider_for("audio"),
                "gemini_ready": gemini_ready(),
                "gemini_model": GEMINI_MODEL,
                "gemini_audio_model": GEMINI_AUDIO_MODEL,
                "gemini_keys": len(_GEMINI_KEYS),
                # Per capability: how many calls Gemini actually served, how many
                # fell back to Groq, and why the last one did. `last_error_was_quota`
                # true means the free tier is spent rather than the code being wrong.
                "usage": _brain_stats,
            },
            # Whether anything is remembered across a restart. `configured` false is
            # the answer to "why does it keep forgetting" — on the free tier a
            # redeploy or a platform move wipes an in-process dict, and UptimeRobot
            # keeping the instance warm does not prevent either.
            "memory": {
                "configured": bool(DATABASE_URL),
                "ready": _db_ready,
                "broken": _db_broken,
                "prompt_messages": _MAX_TURNS * 2,
                "chats_cached": len(_HISTORY),
                # facts are separate from turns on purpose — see the comment above
                # `_FACTS_CAP`. A turn history says what was just said; these say
                # what is true, and they go into every prompt.
                "facts_known": len(_facts_cache or []),
                # Whether the small state survives a deploy, which is a different
                # question from whether turns are stored — and one that went
                # unanswerable for a day. Named rather than inferred: a schedule
                # backed only by Render's disk and a schedule backed by Postgres
                # look identical from outside right up to the next deploy.
                "state_durable": bool(DATABASE_URL) and _db_ready,
            },
            "bridge": bool(BRIDGE_SECRET),
            # The phone refuses a gateway that does not declare this, because a
            # bare 200 would flip it to CLOUD and strand it on a dead socket —
            # worse than staying dark. So it is a claim about the ROUTE being
            # usable, not about the process being up: false when no APP_TOKEN is
            # configured, since every connection would then be refused.
            "app_link": bool(APP_TOKEN),
            "desk_linked": _desk_connected(),
            "apps_linked": len(_app_clients),
            # how many phones can be reached while holding no socket
            "push_targets": len(_push_targets),
            # Named rather than counted alone: "the phone thinks it told me" and
            # "I have a schedule" have to be distinguishable from outside, or a
            # briefing that never fires has no diagnosis but guesswork.
            "commute": {
                "tz": _commute.get("tz"),
                "departures": len(_commute.get("departures") or []),
                "days_on": sum(1 for d in (_commute.get("days") or []) if d),
            },
            # Counts only — how deep the sealed backlog is and whether anything
            # was lost. No fact, sealed or otherwise, is exposed here.
            "fact_outbox": fact_outbox.stats() if fact_outbox is not None else None}


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
    req_id = next(_req_seq)
    frame = {
        "type": "cmd",
        "req_id": req_id,
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
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] desk forward failed ({e}); falling back to local brain.", flush=True)
        return False

    # Phase 4 item 7: a connected-but-wedged desk must not black-hole the
    # message. If NOTHING referencing this req_id comes back within the window
    # (notify heartbeats extend it), answer locally so the operator always
    # gets a reply.
    # `asked` rides along so the reader can file the pair. The reply arrives in a
    # different coroutine minutes later and has only the answer; the question
    # exists nowhere else by then.
    entry = {"evt": asyncio.Event(), "last": time.monotonic(), "asked": text}
    _pending_reqs[req_id] = entry

    async def _watchdog():
        try:
            while True:
                remaining = _DESK_REPLY_TIMEOUT - (time.monotonic() - entry["last"])
                if remaining <= 0:
                    print(f"[CLOUD] ⚠ Desk silent on req {req_id} for "
                          f"{_DESK_REPLY_TIMEOUT:.0f}s — answering from the cloud brain.", flush=True)
                    try:
                        reply = await think(message.chat.id, text,
                                            ident["who"], ident["honorific"],
                                            surface="telegram")
                    except Exception:  # noqa: BLE001
                        reply = ("The desk link stalled on that one — I couldn't get "
                                 "an answer through. Try again in a moment.")
                    # A connected-but-wedged desk never stored this turn either, so
                    # it needs queueing exactly like a PC-off turn. It ships on the
                    # next handshake — this socket has already proved it is not
                    # listening.
                    _queue_offline_fact(ident, text, reply)
                    bot, _ = _ensure_bot()
                    for i in range(0, len(reply), 4000):
                        try:
                            await bot.send_message(message.chat.id, reply[i:i + 4000])
                        except Exception as e:  # noqa: BLE001
                            print(f"[CLOUD] fallback send failed: {e}", flush=True)
                            break
                    return
                try:
                    await asyncio.wait_for(entry["evt"].wait(), remaining)
                    return  # the desk answered — nothing to do
                except asyncio.TimeoutError:
                    continue  # re-check: a notify heartbeat may have refreshed "last"
        finally:
            _pending_reqs.pop(req_id, None)

    asyncio.create_task(_watchdog())
    return True


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
    # Scheduled, not awaited: announcing can involve an HTTP round trip to Expo,
    # and the desk's welcome must not wait behind a phone notification.
    asyncio.create_task(_announce_desk(True))
    bot, _ = _ensure_bot()
    try:
        await websocket.send_json({"type": "welcome", "identities": roster})
        while True:
            frame = await websocket.receive_json()
            ftype = frame.get("type")
            # C#11a Step 4: the desk's `fact_key` handshake and `fact_ack` are
            # handled first and short-circuit — they carry no chat_id or req_id, and
            # accepting the key is what triggers the flush of the sealed backlog.
            if fact_outbox is not None and await fact_outbox.handle_desk_frame(
                    frame, websocket.send_json):
                continue
            # Desk-watch alerts belong to the phones, never to Telegram: this is a
            # webcam capture of whoever is at the machine, and the answer decides
            # whether it locks. Handled before the relay so it cannot fall through
            # to a chat, and scheduled so a push cannot stall the desk's reader.
            if ftype in ("intruder", "intruder_resolved"):
                asyncio.create_task(_relay_watch(frame))
                continue
            chat_id = frame.get("chat_id")
            # Phase 4 item 7: reply correlation. A reply/done frame resolves the
            # request's watchdog; a notify (typing) frame is a heartbeat that
            # extends its window — a long-running command that shows signs of
            # life is never double-answered by the cloud fallback.
            rid = frame.get("req_id")
            # A phone is waiting on this one. Hand the frame to that session and
            # stop — nothing about it belongs in Telegram.
            sink = _app_sinks.get(rid) if rid is not None else None
            if sink is not None:
                sink.put_nowait(frame)
                continue
            # Belt and braces: a frame that lost its req_id must still never be
            # relayed to a chat id that was never a Telegram chat.
            if chat_id == APP_CHAT_ID:
                continue
            # Read BEFORE the first await below: setting `evt` releases the
            # watchdog, which pops this entry, and a streamed answer arrives as
            # several `reply` frames — only the first carries the question.
            _asked = ""
            if rid is not None and rid in _pending_reqs:
                if ftype in ("reply", "done"):
                    _pending_reqs[rid]["evt"].set()
                else:
                    _pending_reqs[rid]["last"] = time.monotonic()
                if ftype == "reply" and not _pending_reqs[rid].get("filed"):
                    _pending_reqs[rid]["filed"] = True
                    _asked = _pending_reqs[rid].get("asked") or ""
            if ftype == "reply" and chat_id is not None:
                text = (frame.get("text") or "").strip()
                if text:
                    # Filed before it is sent: a Telegram send that fails is no
                    # reason for the conversation to forget the turn happened.
                    await _remember_desk_turn(chat_id, _asked, text)
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
            elif ftype == "alert":
                # Unsolicited desk-originated alert (proactive detection, worker
                # report). Relay to the given chat, or to the admin identity when
                # the desk didn't include one.
                text = (frame.get("text") or "").strip()
                target = chat_id
                if target is None:
                    target = next((uid for uid, i in _IDENTITIES.items()
                                   if i["tier"] == _ADMIN_TIER), None)
                if text and target is not None:
                    for i in range(0, len(text), 4000):
                        try:
                            await bot.send_message(target, text[i:i + 4000])
                        except Exception as e:  # noqa: BLE001
                            print(f"[CLOUD] alert relay failed: {e}", flush=True)
                            break
                elif text:
                    print("[CLOUD] ⚠ alert dropped — no admin identity configured.", flush=True)
            # "done" and unknown frames need no relay.
    except WebSocketDisconnect:
        print("[CLOUD] Desk link dropped — falling back to local brain.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] desk-link fault: {e}", flush=True)
    finally:
        if _desk_ws is websocket:
            _desk_ws = None
            try:
                asyncio.create_task(_announce_desk(False))
            except RuntimeError:
                # no running loop, i.e. the process is going down. The phones
                # lose their socket at the same moment anyway
                pass


# ════════════════════════════════════════════════════════════════════════════
# Mobile app front door — /app-link
# ════════════════════════════════════════════════════════════════════════════
# The phone dials in here. Same brain as Telegram, same desk routing: when a desk
# is linked the command runs on the real machine, and when it is not the cloud
# answers on its own. The phone never has to know which — it asks, and the front
# door decides, which is the whole point of there being one door.
#
# Wire format is the DESK's, not a third one: status frames are
# {"status": …, "message": …, "user": …} and sync frames are
# {"status": "sync", "type": "telemetry", "data": {…}}, exactly what the desk's
# own /ws emits, so `parseFrame` on the phone has a single contract to satisfy
# whichever transport it is on.

def _app_identity() -> dict:
    """Who the phone speaks as.

    The pairing token IS the credential and it is issued to the owner, so a
    phone that presents it is the admin. Falls back to a sane owner identity when
    TELEGRAM_USER_ID was never set — the app must still work on a gateway wired
    for nothing but this socket.
    """
    for ident in _IDENTITIES.values():
        if ident.get("tier") == _ADMIN_TIER:
            return ident
    return {"who": "Kaustav", "honorific": "Sir", "tier": _ADMIN_TIER}


class AppMessage(NamedTuple):
    """One decoded frame from the phone.

    `photo` is the base64 JPEG as sent, not decoded bytes: `see()` hands it
    straight to the vision model as a data URI, so decoding it here would only be
    to encode it again.
    """
    command: str
    audio: Optional[bytes]
    filename: str
    photo: str = ""


def _decode_app_message(raw: str) -> AppMessage:
    """Split one text frame from the phone into its parts.

    Bare text is the contract `LinkMachine.send` writes and stays the default:
    anything that is not a JSON object carrying a `type` we recognise is treated
    as a command, so asking J.A.R.V.I.S. about a JSON snippet still asks rather
    than parses.
    """
    text = raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return AppMessage(text, None, "")
    try:
        env = json.loads(text)
    except Exception:  # noqa: BLE001
        return AppMessage(text, None, "")
    if not isinstance(env, dict):
        return AppMessage(text, None, "")
    kind = str(env.get("type") or "").strip().lower()
    if kind == "voice":
        fmt = str(env.get("format") or "m4a").strip().lstrip(".") or "m4a"
        try:
            return AppMessage("", base64.b64decode(env.get("audio") or "", validate=False),
                              f"voice.{fmt}")
        except Exception:  # noqa: BLE001
            return AppMessage("", None, "")
    if kind == "photo":
        # Carried as the base64 string rather than bytes — see AppMessage. The
        # caption is optional; `see()` supplies its own prompt when there is none.
        image = str(env.get("image") or "").strip()
        return AppMessage(str(env.get("text") or "").strip(), None, "", image)
    if kind in ("cmd", "command", "text", "ask"):
        return AppMessage(str(env.get("text") or "").strip(), None, "")
    return AppMessage(text, None, "")


def _decode_where(raw: str) -> Optional[dict]:
    """The phone's location, if this frame carried one.

    Read separately from the command so bare text keeps working untouched, and so
    a malformed `where` costs the location rather than the question.

    Coordinates only, no history: they are used to answer *this* turn and are not
    stored anywhere. The place name is resolved on the phone, where the reverse
    geocoder is free and offline-ish, rather than spending a lookup here.
    """
    text = raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        env = json.loads(text)
        where = env.get("where") if isinstance(env, dict) else None
        if not isinstance(where, dict):
            return None
        lat, lon = float(where["lat"]), float(where["lon"])
    except Exception:  # noqa: BLE001
        return None
    # a phone that reports the null island is a phone with no fix
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    place = str(where.get("place") or "").strip()[:120]
    out = {"lat": lat, "lon": lon, "place": place}

    # The name he gave this spot by standing in it, when the phone recognised one.
    #
    # Preferred over `place` in the context block. The reverse geocoder answered for
    # a single desk as Bidhannagar, then Kankurgachi, then twice as "Presidency
    # Division" — an administrative division of millions — across four consecutive
    # turns, and each was stated to him as fact. A label he set does not drift.
    label = str(where.get("label") or "").strip()[:40]
    if label:
        out["label"] = label

    # Conditions the phone measured for these coordinates. Taken as given: it is
    # his own phone, it fetched them from an IP that is not rate-limited, and the
    # alternative is this process being told 429 and saying it could not check.
    given_weather = str(where.get("weather") or "").strip()[:300]
    if given_weather:
        out["weather"] = given_weather

    # Places he has named — home, the office, anywhere else worth a word.
    #
    # Only this phone knows what "the office" means, so they arrive with the
    # question rather than being stored here. That also means "how far to the
    # office" is answered without geocoding a word no geocoder could resolve.
    known = where.get("known")
    if isinstance(known, list):
        named = []
        for item in known[:12]:
            if not isinstance(item, dict):
                continue
            try:
                label = str(item["label"]).strip()[:40]
                named.append({"label": label, "lat": float(item["lat"]), "lon": float(item["lon"])})
            except Exception:  # noqa: BLE001
                continue
        if named:
            out["known"] = named

    # The trail, if the phone chose to send one.
    #
    # Held on the phone, not here: this gateway keeps no location history of its
    # own, so what arrives is only ever what was needed to answer this question and
    # it is gone when the turn ends. Capped on arrival as well as on the phone,
    # because a cap that only exists on the client is not a cap.
    trail = where.get("trail")
    if isinstance(trail, list) and trail:
        steps = []
        for step in trail[-12:]:
            if not isinstance(step, dict):
                continue
            label = str(step.get("place") or "").strip()[:60]
            when = str(step.get("when") or "").strip()[:32]
            if label:
                steps.append(f"{label}{f' ({when})' if when else ''}")
        if steps:
            out["trail"] = "; ".join(steps)
    return out


async def _ask_desk(text: str, ident: dict, on_notify) -> Optional[str]:
    """Run one phone command through the linked desk brain.

    Returns the desk's answer, `""` when it finished with nothing to say, or
    `None` when there is no desk or it never answered — the caller then falls
    back to the cloud brain, exactly as the Telegram path does. A partial answer
    that stopped arriving beats no answer, so accumulated text is returned rather
    than discarded on timeout.
    """
    ws = _desk_ws
    if ws is None:
        return None
    req_id = next(_req_seq)
    q: asyncio.Queue = asyncio.Queue()
    _app_sinks[req_id] = q
    try:
        await ws.send_json({
            "type": "cmd",
            "req_id": req_id,
            "chat_id": APP_CHAT_ID,
            # The desk brain keys persona and memory off an UPPERCASE user string.
            "user": (ident.get("who") or "KAUSTAV").upper(),
            "tier": ident.get("tier"),
            "honorific": ident.get("honorific") or "Sir",
            "text": text,
        })
    except Exception as e:  # noqa: BLE001
        _app_sinks.pop(req_id, None)
        print(f"[CLOUD] app→desk forward failed ({e}); the cloud brain answers.", flush=True)
        return None

    chunks: list[str] = []
    last = time.monotonic()
    try:
        while True:
            remaining = _DESK_REPLY_TIMEOUT - (time.monotonic() - last)
            if remaining <= 0:
                if chunks:
                    break
                print(f"[CLOUD] WARN desk silent on app req {req_id} for "
                      f"{_DESK_REPLY_TIMEOUT:.0f}s — the cloud brain answers.", flush=True)
                return None
            try:
                frame = await asyncio.wait_for(q.get(), remaining)
            except asyncio.TimeoutError:
                continue  # a notify heartbeat may have moved `last`
            last = time.monotonic()
            ftype = frame.get("type")
            if ftype == "reply":
                piece = (frame.get("text") or "").strip()
                if piece:
                    chunks.append(piece)
            elif ftype == "notify":
                await on_notify()
            elif ftype == "done":
                break
    finally:
        _app_sinks.pop(req_id, None)
    return "\n".join(chunks)


async def _desk_telemetry(timeout: float = 12.0) -> Optional[dict]:
    """One vitals snapshot from the linked desk, or None if it did not answer.

    The cloud has no CPU or disk figures for his machine and must never invent
    any — an empty Reports tab is the honest reading when the desk is off.
    """
    ws = _desk_ws
    if ws is None:
        return None
    req_id = next(_req_seq)
    q: asyncio.Queue = asyncio.Queue()
    _app_sinks[req_id] = q
    try:
        await ws.send_json({"type": "hud_req", "req_id": req_id})
        frame = await asyncio.wait_for(q.get(), timeout)
    except Exception:  # noqa: BLE001
        return None
    finally:
        _app_sinks.pop(req_id, None)
    data = frame.get("data")
    return data if isinstance(data, dict) else None


def _excuse(what: str, exc: BaseException) -> str:
    """A sentence for the chat, and the whole truth in the log.

    A provider's error object was being interpolated straight into a chat bubble, so
    a retired model id produced this, in the middle of a conversation:

        I couldn't make sense of that picture: Error code: 404 - {'error':
        {'message': 'The model `meta-llama/llama-4-scout-17b-16e-instruct` does not
        exist or you do not have access to it.', 'type': 'invalid_request_error',
        'code': 'model_not_found'}}

    That is the right information for whoever runs this and the wrong information
    for whoever is talking to it — and it is printed in a chat log that persists,
    next to a persona built on not sounding like machinery. The detail goes to the
    log, where it is actually actionable; the operator gets a sentence.

    A model id that has been retired is worth naming as its own case: it is the one
    failure here that is fixed by changing a setting rather than by waiting.
    """
    detail = str(exc)
    print(f"[CLOUD] {what} failed: {detail}", flush=True)
    if "model_not_found" in detail or "does not exist or you do not have access" in detail:
        return (f"I can't {what} at the moment — the model configured for it is no longer "
                f"available. Worth checking the gateway's settings.")
    return f"I couldn't {what} just then. Try me again in a moment."


async def _deliver_unprompted(message: str, title: str = "J.A.R.V.I.S.") -> dict:
    """Say something nobody asked for, down whichever route is open.

    The one path for anything that speaks first — a finished background job, a
    reminder, the desk reporting in. Everything it needs already existed; what did
    not exist was a single place that does it correctly, so each new feature was
    about to reinvent the socket-or-push decision and get it subtly different.

    Three things happen, in this order and for these reasons:

    1. It is written into the same rolling history as a normal turn. An unprompted
       message the model cannot remember saying makes the next turn incoherent —
       "what did you mean by that" would be answered by a brain that never said it.
    2. Attached phones get a `speaking` frame, which their reducer logs as a
       J.A.R.V.I.S. turn and which raises a local notification unless the chat is
       already on screen.
    3. Push goes out ONLY when no phone is attached. A listening phone would
       otherwise be told twice for one event, and you feel that in your pocket.
       This mirrors `_announce_desk`, deliberately.
    """
    said = (message or "").strip()
    if not said:
        return {"delivered": False, "reason": "empty"}

    ident = next(iter(_IDENTITIES.values()),
                 {"who": "KAUSTAV", "honorific": "Sir", "tier": "admin"})
    who = (ident.get("who") or "KAUSTAV").upper()

    await _remember_said(said)

    await _broadcast_app({"status": "speaking", "message": said, "user": who})
    pushed = False
    if not _app_clients:
        await _push_all(title, said, {"kind": "unprompted"})
        pushed = True
    print(f"[CLOUD] unprompted -> {len(_app_clients)} socket(s), pushed={pushed}", flush=True)
    return {"delivered": True, "sockets": len(_app_clients), "pushed": pushed}


@app.post("/app-fact")
async def app_fact(request: Request):
    """Add, remove or list what J.A.R.V.I.S. knows about him.

    Gated by `APP_TOKEN`, because these go into every system prompt: an open version
    would let anyone tell the assistant what is true about its operator, which is a
    more durable kind of lie than a single injected message.

        {"fact": "his dog Indie died on 5 August 2025"}
        {"forget": "his dog Indie died on 5 August 2025"}
        {}                                    -> lists what is stored

    `stored: false` means there is no DATABASE_URL and the fact lives only until the
    next restart. Reported rather than hidden: telling someone their assistant will
    remember something when it will not is worse than saying it cannot.
    """
    if not APP_TOKEN:
        return Response(status_code=503)
    auth = request.headers.get("authorization", "")
    presented = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(presented, APP_TOKEN):
        peer = request.client.host if request.client else "?"
        print(f"[CLOUD] REFUSED app-fact from {peer}", flush=True)
        return Response(status_code=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}

    drop = str(body.get("forget") or "").strip()
    if drop:
        return {"forgotten": await forget_fact(drop), "facts": await _facts()}
    fact = str(body.get("fact") or "").strip()
    if fact:
        stored = await remember_fact(fact)
        return {"stored": stored, "persistent": bool(DATABASE_URL), "facts": await _facts()}
    return {"facts": await _facts(), "persistent": bool(DATABASE_URL)}


@app.post("/app-say")
async def app_say(request: Request):
    """Make J.A.R.V.I.S. say something to the phone, unprompted.

    The primitive behind "can it message me first". Gated by the same credential as
    the socket, because it writes into his conversation as though the assistant had
    spoken — an open version of this route would let anyone put words in its mouth,
    which is worse than letting them read.

    Deliberately dumb: it delivers the text it is given and does not think about it.
    A caller that wants a considered message asks `think()` first and posts the
    answer. Keeping the two apart means a scheduled reminder cannot cost an LLM call
    and cannot fail because a free tier ran out.
    """
    if not APP_TOKEN:
        return Response(status_code=503)
    auth = request.headers.get("authorization", "")
    presented = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(presented, APP_TOKEN):
        peer = request.client.host if request.client else "?"
        print(f"[CLOUD] REFUSED app-say from {peer}", flush=True)
        return Response(status_code=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return Response(status_code=400)
    message = str((body or {}).get("message") or "").strip()[:2000]
    title = str((body or {}).get("title") or "J.A.R.V.I.S.").strip()[:64]
    if not message:
        return Response(status_code=400)
    return await _deliver_unprompted(message, title)


@app.post("/app-push/register")
async def app_push_register(request: Request):
    """The phone hands over the address it can be reached at while asleep.

    Gated by the same credential as the socket, and for the same reason: this
    address is what gets told the desk is up, and it belongs to one install.
    Presented as a bearer header rather than a query parameter — REST can set
    headers, and only the WebSocket handshake could not.
    """
    if not APP_TOKEN:
        return Response(status_code=503)
    auth = request.headers.get("authorization", "")
    presented = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(presented, APP_TOKEN):
        peer = request.client.host if request.client else "?"
        print(f"[CLOUD] REFUSED push register from {peer}", flush=True)
        return Response(status_code=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return Response(status_code=400)
    token = str((body or {}).get("push_token") or "").strip()
    platform = str((body or {}).get("platform") or "?").strip()[:16]
    if not token:
        return Response(status_code=400)

    """The phone also says which notification channels it created.

    Android DROPS a notification addressed to a channel that does not exist, and
    the app has renamed its everyday channel eight times chasing a mute-briefing
    bug — `general` -> `general-v2` -> ... -> `general-v8` — deleting the old ones
    as it went. This gateway went on addressing `general`, so every reply push
    since that first rename was accepted by Expo and silently discarded by
    Android. Measured on the device on 2026-08-19: `push_targets: 1`, the gateway
    correctly deciding to push, and no notification ever arriving.

    So the phone is the authority on its own channel names, because it is the
    only thing that knows them. Absent — an older build — the defaults below
    still apply, which is why those were moved forward too.
    """
    channels = (body or {}).get("channels")
    if isinstance(channels, dict):
        named = {
            str(k)[:32]: str(v)[:64]
            for k, v in channels.items()
            if isinstance(k, str) and isinstance(v, str) and v.strip()
        }
        if named:
            _push_channels[token] = named
            print(f"[CLOUD] push channels for this phone: {named}", flush=True)

    fresh = token not in _push_targets
    _push_targets[token] = platform
    _save_push_targets()
    if fresh:
        # never the token itself: it is the address of one install, and these
        # logs are readable by anyone with the Render dashboard
        print(f"[CLOUD] push target registered ({platform}) - "
              f"{len(_push_targets)} total.", flush=True)
    return {"ok": True, "targets": len(_push_targets)}


@app.post("/app-commute")
async def app_commute(request: Request):
    """The phone hands over WHEN it wants to be briefed, and where about.

    The briefing itself is not here yet — this stores the schedule the scheduler
    will read. Landing the contract first because the phone half is what needed
    proving: it is the side that holds `KnownPlace`, and until it uploads
    coordinates the gateway has nothing to forecast against.

    Gated by the same credential as the socket and the push registration. It is a
    weaker secret than those two — a schedule is not a brain — but it decides when
    a notification wakes him up, and that is not nothing.

    Idempotent by replacement, not by merge. The phone is the authority on this
    setting, so a departure it has switched off must travel as an absence and
    silence the gateway; merging would leave a briefing firing on a schedule the
    operator had already turned off, which is the worst thing this feature could
    do to its own credibility.
    """
    if not APP_TOKEN:
        return Response(status_code=503)
    auth = request.headers.get("authorization", "")
    presented = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not hmac.compare_digest(presented, APP_TOKEN):
        peer = request.client.host if request.client else "?"
        print(f"[CLOUD] REFUSED commute sync from {peer}", flush=True)
        return Response(status_code=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return Response(status_code=400)

    global _commute
    clean = _clean_commute(body or {})
    if clean is None:
        # 400 rather than a partial store: see `_clean_commute`. A schedule the
        # gateway understood differently from the phone is the one failure mode
        # worth refusing outright.
        print("[CLOUD] commute sync REFUSED - unreadable schedule", flush=True)
        return Response(status_code=400)

    _commute = clean
    _save_commute()
    # the times, not the coordinates: these logs are readable by anyone with the
    # Render dashboard, and where he lives is not a thing to print there
    when = ", ".join(f"{d['label']} {d['hour']:02d}:{d['minute']:02d}"
                     for d in clean["departures"]) or "nothing scheduled"
    print(f"[CLOUD] commute schedule stored ({clean['tz']}): {when}", flush=True)
    return {"ok": True, "departures": len(clean["departures"])}


@app.websocket("/app-link")
async def app_link(websocket: WebSocket):
    """The phone's socket. Bare text is a command; bytes are a voice clip."""
    if not APP_TOKEN:
        # Refused before accept: an ungated door onto a brain that answers as him
        # is worse than no door.
        await websocket.close(code=1008)
        return
    presented = websocket.query_params.get("token", "")
    if not hmac.compare_digest(presented, APP_TOKEN):
        peer = websocket.client.host if websocket.client else "?"
        print(f"[CLOUD] REFUSED app-link token mismatch from {peer}", flush=True)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    ident = _app_identity()
    who = (ident.get("who") or "KAUSTAV").upper()
    send_lock = asyncio.Lock()
    alive = True
    busy = False
    _app_clients.add(websocket)
    # ASCII only: a harness run outside run_harnesses.py picks cp1252 for stdout
    # on Windows, and an emoji here kills the handler mid-connect.
    print(f"[CLOUD] App linked (desk {'up' if _desk_connected() else 'down'}) - "
          f"{len(_app_clients)} phone(s) attached.", flush=True)

    async def emit(payload: dict) -> bool:
        """True if the frame actually left. False was previously indistinguishable
        from success, which is how a finished answer came to vanish silently."""
        nonlocal alive
        if not alive:
            return False
        async with send_lock:
            try:
                await websocket.send_json(payload)
                return True
            except Exception:  # noqa: BLE001
                alive = False
                # Pruned here rather than only in `finally`. A socket dies without
                # raising until something is written to it, so `_app_clients` was
                # collecting phantoms — and `if not _app_clients` is what decides
                # whether to push, so phantoms actively suppressed the push that
                # would have delivered this. `apps_linked: 3` for one phone was the
                # visible symptom.
                _app_clients.discard(websocket)
                return False

    async def say(status: str, message: str = "") -> bool:
        return await emit({"status": status, "message": message, "user": who})

    async def deliver(answer: str) -> None:
        """The answer reaches him whether or not the socket survived the thinking.

        A vision call takes the better part of a minute, which is long enough for a
        screen to lock and Android to take the WebSocket with it. The answer was
        computed, paid for, and written into memory — and then dropped into a closed
        socket, so the chat showed a photo with nothing under it.

        Forced past the push quiet gap on purpose: this is an answer to something he
        asked a moment ago, not a status notification, and rate-limiting it would
        mean choosing to lose a reply because an unrelated push went out earlier.

        **`_app_clients` is consulted before the write, not just after it.** A
        successful `send_json` was the only evidence used here, and it is not
        evidence: a peer's close arrives on the ASGI *receive* channel, and during a
        turn this handler is blocked in `think()`, so the disconnect has not been
        consumed yet. The write therefore succeeds into a connection nobody is
        holding, `emit()` reports it delivered, and the push never fires. Proved on
        the phone on 2026-08-18 — backgrounding the app mid-answer produced no
        notification at all, three attempts running.

        The list is only trustworthy because the app was fixed the same day to close
        its socket on the way out (`LinkMachine.suspend`, jarvis-mobile). Measured
        after that change: `apps_linked` goes 1 -> 0 when the phone leaves. Before
        it, this guard would have made things worse — the list was full of phantoms,
        so it would have suppressed the very push it is here to trigger.
        """
        # `alive`, not `_app_clients`. That set is global: with a second phone
        # attached — a release build installed beside the debug one is enough —
        # it stays non-empty after THIS phone leaves, and this answer belongs to
        # this connection. `alive` is now set the moment `reader()` sees the
        # disconnect, and `emit()` checks it again before writing.
        if alive and await say("speaking", answer):
            return
        print("[CLOUD] no phone holding the socket; pushing the reply instead", flush=True)
        await _push_all("J.A.R.V.I.S.", answer, {"kind": "reply"}, force=True)

    async def keepalive() -> None:
        """Keep the phone's 30s frame watchdog from tearing down an idle socket.

        A status frame with no message refreshes that clock without writing a
        line into the HUD's chat log. It reports the state the session is
        actually in, so a long-running desk command still reads as `thinking`
        rather than being flipped back to `online` under itself.
        """
        while alive:
            await asyncio.sleep(APP_KEEPALIVE_SECS)
            if not alive:
                return
            await say("thinking" if busy else "online")

    async def vitals() -> None:
        """Real desk numbers while a desk is linked; nothing at all when it is not."""
        while alive:
            if _desk_connected():
                data = await _desk_telemetry()
                if data:
                    await emit({"status": "sync", "type": "telemetry", "data": data})
            await asyncio.sleep(APP_TELEMETRY_SECS)

    inbox: asyncio.Queue = asyncio.Queue()

    async def reader() -> None:
        """Always be awaiting `receive`, so a disconnect is noticed when it happens.

        This is what makes `deliver()`'s `_app_clients` check mean anything, and
        without it that check was asking a question nothing had answered.

        The loop below used to call `websocket.receive()` itself, which meant
        nothing was awaiting the receive channel for the whole length of a turn.
        A peer's close arrives on that channel — so when the phone was pocketed
        mid-answer the disconnect simply sat there unread: `alive` stayed true,
        `_app_clients` still held this socket, `send_json` wrote into a dead
        connection without raising, `emit()` reported success, and the push that
        should have carried the answer never fired.

        Measured on the phone on 2026-08-19: a question sent one second before
        backgrounding produced no notification at all in fifty seconds, with
        `push_targets: 1` — the token was there, nothing ever asked to use it.

        Draining here costs one task per connection and makes the disconnect
        prompt. `receive()` must now be called from ONE place only: two coroutines
        awaiting the same ASGI channel is undefined.
        """
        nonlocal alive
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                await inbox.put(msg)
        except Exception:  # noqa: BLE001
            pass
        finally:
            # Before the queue sentinel, not after: the turn in flight may reach
            # `deliver()` at any moment, and the whole point is that it finds an
            # honest answer to "is anyone still holding this socket".
            alive = False
            _app_clients.discard(websocket)
            await inbox.put({"type": "websocket.disconnect"})

    helpers = [
        asyncio.create_task(keepalive()),
        asyncio.create_task(vitals()),
        asyncio.create_task(reader()),
    ]

    await say("online",
              "Desk is online — full control through the cloud."
              if _desk_connected() else
              "Cloud brain only, so PC control is off until the desk wakes.")
    try:
        while True:
            # from the reader's queue, never from the socket: `receive()` is
            # awaited in exactly one place now, and that place is `reader()`
            msg = await inbox.get()
            if msg.get("type") == "websocket.disconnect":
                break

            audio: Optional[bytes] = msg.get("bytes")
            filename = "voice.m4a"
            text = ""
            photo = ""
            where: Optional[dict] = None
            if audio is None and msg.get("text") is not None:
                decoded = _decode_app_message(msg["text"])
                text, audio, photo = decoded.command, decoded.audio, decoded.photo
                where = _decode_where(msg["text"])
                if decoded.filename:
                    filename = decoded.filename

            busy = True
            try:
                if audio:
                    await say("thinking", "")
                    try:
                        # Stripped: Whisper returns whitespace for a silent clip,
                        # and " " is truthy — un-stripped it would be asked as a
                        # command and answered as if he had said something.
                        text = (await asyncio.to_thread(
                            _transcribe, audio, filename) or "").strip()
                    except Exception as e:  # noqa: BLE001
                        await say("error", _excuse("make out that recording", e))
                        await say("online")
                        continue
                    if not text:
                        await say("error", "I couldn't hear anything in that.")
                        await say("online")
                        continue
                    # Attributed to HIM in the phone's chat log. A transcript sent
                    # as a status message would be logged as J.A.R.V.I.S. saying
                    # it, which is a lie about who spoke.
                    await emit({"type": "transcript", "text": text, "user": who})

                answer: Optional[str] = None

                # A photo, with or without a caption.
                #
                # Answered here rather than forwarded to the desk even when the desk
                # is up: the vision model lives on this side, and the desk has no
                # route that takes an image. `see()` keeps the same rolling memory as
                # `think()`, so "what is this" about the photo and the turn after it
                # are one conversation.
                if photo:
                    await say("thinking")
                    try:
                        answer = await see(APP_CHAT_ID, photo, text,
                                           ident["who"], ident["honorific"],
                                           surface="app")
                    except Exception as e:  # noqa: BLE001
                        await say("error", _excuse("look at that picture", e))
                        await say("online")
                        continue
                    if answer:
                        await deliver(answer)
                    await say("online")
                    continue

                if not text:
                    continue

                await say("thinking")
                if _desk_connected():
                    # the desk gets the question as asked: it has its own senses,
                    # and a preamble about the phone's surroundings is not its brief
                    answer = await _ask_desk(text, ident, lambda: say("thinking"))
                    if answer is not None:
                        # The turn is in the DESK's memory, not in the shared
                        # one — and the shared one is what Telegram and the desk
                        # read tomorrow. See `_remember_desk_turn`.
                        await _remember_desk_turn(APP_CHAT_ID, text, answer)
                if answer is None:
                    try:
                        # A located question is answered from measured figures rather
                        # than the model's recollection — it said "no rain" while it
                        # was raining, which is the whole reason this exists.
                        # Background, passed separately from what he said. Gluing it
                        # onto his message made a greeting look like a question about
                        # the weather, and put a copy of his coordinates in memory
                        # on every turn.
                        ctx = await _where_context(where, text) if where else ""
                        # APP_CHAT_ID is still what a reply is ROUTED by. What it
                        # is remembered under is now `_memory_key`, which files
                        # the phone under the operator's own thread — see the
                        # note on APP_MEMORY_SHARED. The comment here used to say
                        # the phone got a thread of its own, which was true and
                        # was the problem.
                        answer = await think(APP_CHAT_ID, text,
                                             ident["who"], ident["honorific"],
                                             context=ctx, surface="app")
                    except Exception as e:  # noqa: BLE001
                        await say("error", _excuse("answer that", e))
                        await say("online")
                        continue
                    # The desk never saw this turn, so it is sealed and queued for
                    # the next handshake exactly like a PC-off Telegram turn.
                    _queue_offline_fact(ident, text, answer)
                if answer:
                    await deliver(answer)
                await say("online")
            finally:
                busy = False
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[CLOUD] app-link fault: {e}", flush=True)
    finally:
        alive = False
        _app_clients.discard(websocket)
        for t in helpers:
            t.cancel()
        print(f"[CLOUD] App link closed - {len(_app_clients)} phone(s) attached.", flush=True)


@app.on_event("startup")
async def _startup():
    # Before the loops, because a deploy is exactly when this matters: the
    # schedule, the addresses and today's markers all live in memory, and the
    # disk they were backed by does not survive one.
    await _restore_state()

    # FIRST, and above the Telegram checks on purpose. Every return below this
    # point is about the bot — a missing BOT_TOKEN, an unset PUBLIC_URL — and the
    # briefing has nothing to do with Telegram. Started under either of those and
    # the loop would never run, which is the same silence this whole change
    # exists to remove.
    asyncio.create_task(_commute_loop())
    # Same placement and the same reason: speaking first has nothing to do with
    # Telegram, and every return below is about the bot.
    asyncio.create_task(_nudge_loop())

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
