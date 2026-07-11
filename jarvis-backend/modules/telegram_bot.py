"""
telegram_bot.py — The Telegram Remote Gateway (Untether from the desk)
======================================================================

A long-polling Telegram bot that runs as a background task on the FastAPI event
loop, giving J.A.R.V.I.S. a remote mouth and ear on the operator's phone.

DESIGN
------
* Transport only. This module never reasons or acts on its own — it hands every
  text command to an injected `process_fn(command_text, channel)` (main.py's
  `run_remote_command`), which routes through the exact same brain
  (llm_router/process_command) and ActionEngine as the voice/HUD path. Responses
  come back through a `TelegramChannel` (an OutputChannel) so a phone reply never
  leaks to the desk speakers or the HUD.

* Voice notes and photos are reduced to TEXT here (Groq Whisper transcript /
  Groq vision description — env GROQ_WHISPER_MODEL, GROQ_VISION_MODEL) and then
  handed to the same process_fn, so the real brain still does all the reasoning
  and permission tiers apply unchanged.

* Multi-user firewall + permission tiers (Phase 4.5). Every update is matched
  against a small registry of recognised identities, each loaded from the
  environment:
      - TELEGRAM_USER_ID     → Kaustav (ADMIN tier, "Sir", unrestricted)
      - TELEGRAM_GF_ID       → Mousumi (VIP GUEST tier, "Madam")
      - TELEGRAM_BROTHER_ID  → Kinshuk (VIP GUEST tier, "Mr. Kinshuk")
  Any unrecognised id hits a cold, silent firewall: it is logged and dropped —
  no reply, no brain, no engine. Recognised callers carry their identity on the
  TelegramChannel (`user` + `permission_tier`), which the shared command core
  uses to greet them correctly and to enforce what they may do. VIP guests may
  chat and run fast read-only searches (scores, weather, general knowledge) but
  are refused every structural action (OS, files, terminal, Autopilot, Gmail,
  Git, telemetry) with a polite, fixed rejection phrase.

* Graceful when unconfigured. If TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID are
  absent, `start_bot()` logs and no-ops, so the rest of J.A.R.V.I.S. boots
  normally without Telegram. The VIP ids are optional — absent ones simply mean
  that guest cannot connect.

Background tasks (e.g. "Build this Figma key …") are queued via an injected
`queue_goal_fn`, and J.A.R.V.I.S. can push files back to the chat via
`send_document_to_owner()` (exposed to action_engine as the `telegram_send_file`
action).
"""

from __future__ import annotations

import os
import asyncio
import traceback
from typing import Awaitable, Callable, Optional

from modules.session_manager import OutputChannel

# ── Configuration (read at start_bot time, not import time) ──────────────────
_BOT_TOKEN: Optional[str] = None
_OWNER_ID: Optional[int] = None

# Phase 4.5: recognised-identity registry, keyed by numeric Telegram user id.
# Populated in start_bot() from TELEGRAM_USER_ID / TELEGRAM_GF_ID /
# TELEGRAM_BROTHER_ID. Each value is an identity dict (see _make_identity).
# Anyone NOT in this map is an unrecognised intruder → silent firewall.
_IDENTITIES: dict[int, dict] = {}

# Lazily-created aiogram objects + the running poll task.
_bot = None                       # aiogram.Bot
_dispatcher = None                # aiogram.Dispatcher
_poll_task: Optional[asyncio.Task] = None

# Injected from main.py at startup (keeps this module transport-only).
_process_fn: Optional[Callable[[str, OutputChannel], Awaitable[None]]] = None
_queue_goal_fn: Optional[Callable[[str, str], Awaitable[tuple]]] = None
_list_tasks_fn: Optional[Callable[[], Awaitable[list]]] = None
_status_fn: Optional[Callable[[], Awaitable[str]]] = None

_OWNER_USER = "KAUSTAV"  # the admin identity remote commands run as

# ── Permission tiers (mirror action_engine.ADMIN_TIER / VIP_GUEST_TIER) ──────
# Kept as bare literals so this transport module stays free of the engine's
# heavy import chain. The strings MUST match action_engine, which is the
# authoritative enforcement point.
_ADMIN_TIER = "admin"
_VIP_GUEST_TIER = "vip_guest"

# Fixed phrase shown to a VIP guest who attempts a privileged/blocked action.
_VIP_REJECTION = "I'm afraid I cannot perform that action without direct authorization from Sir."


def _make_identity(user: str, tier: str, honorific: str, label: str, greeting: str) -> dict:
    """Build a recognised-identity record stored in _IDENTITIES."""
    return {
        "user": user,            # active_user string the brain keys persona off
        "tier": tier,            # _ADMIN_TIER | _VIP_GUEST_TIER
        "honorific": honorific,  # how logs/fallbacks address them
        "label": label,          # human label for logs
        "greeting": greeting,    # /start welcome text
    }


def _identify(message) -> Optional[dict]:
    """Map an incoming update to a recognised identity, or None for intruders."""
    user = getattr(message, "from_user", None)
    uid = getattr(user, "id", None)
    if uid is None:
        return None
    return _IDENTITIES.get(uid)


# ════════════════════════════════════════════════════════════════════════════
# Output channel
# ════════════════════════════════════════════════════════════════════════════
class TelegramChannel(OutputChannel):
    """Delivers J.A.R.V.I.S.'s replies back to a specific Telegram chat."""

    kind = "telegram"

    def __init__(self, chat_id: int, user: str = _OWNER_USER,
                 permission_tier: str = _ADMIN_TIER, honorific: str = "Sir") -> None:
        # Channel id is scoped to the telegram chat/user — this is the unit of
        # concurrent session isolation for the phone. `user` selects the brain's
        # persona; `permission_tier` gates what the command core will run for
        # this caller; `honorific` lets generic fallback strings address the
        # caller correctly. All ride on the channel so a guest's session,
        # working memory, and tool stream stay isolated from the desk HUD's.
        super().__init__(channel_id=f"telegram:{chat_id}", user=user)
        self.chat_id = chat_id
        self.permission_tier = permission_tier
        self.honorific = honorific

    async def reply(self, text: str) -> None:
        if not text or not text.strip() or _bot is None:
            return
        # Telegram hard-limits messages to 4096 chars; chunk long answers.
        for chunk in _chunk(text.strip(), 4000):
            try:
                await _bot.send_message(self.chat_id, chunk)
            except Exception as e:
                print(f"[TELEGRAM] reply send failed: {e}", flush=True)
                break

    async def notify(self, status: str, message: str = "") -> None:
        # Use a typing indicator as the only "status" signal — no chat spam.
        if _bot is None:
            return
        try:
            await _bot.send_chat_action(self.chat_id, "typing")
        except Exception:
            pass

    async def send_document(self, path: str, caption: str = "") -> bool:
        return await _send_document(self.chat_id, path, caption)


def _chunk(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [text]


# ════════════════════════════════════════════════════════════════════════════
# Inbound media → text (keeps this module transport-only: voice notes become a
# transcript and photos become a factual description, and BOTH are then handed
# to the same injected process_fn as any typed command — the brain still does
# all the reasoning).
# ════════════════════════════════════════════════════════════════════════════
async def _download_media(media) -> bytes:
    """Pull a Telegram file (voice/audio/photo size) into memory."""
    import io
    buf = io.BytesIO()
    await _bot.download(media, destination=buf)
    return buf.getvalue()


# Whisper auto-detect frequently mistakes Bengali speech for Hindi and emits
# Devanagari. This prompt is example text in the operator's actual languages —
# it biases the decoder toward Bengali script / Benglish / English instead.
_WHISPER_PROMPT = (
    "আজকের আবহাওয়া কেমন? Ajker khabar ki? Weather ta kemon aaj? "
    "Play some music. PC ta ki obostha e ache?"
)


def _transcribe_sync(audio: bytes, filename: str) -> str:
    """Blocking Groq Whisper transcription (multilingual — Bengali/Benglish
    speech included). Run via asyncio.to_thread."""
    from modules.groq_key_manager import run_with_key_rotation

    model = (os.getenv("GROQ_WHISPER_MODEL") or "whisper-large-v3").strip()
    language = (os.getenv("GROQ_WHISPER_LANGUAGE") or "").strip()  # e.g. "bn" to force

    def _call(client):
        kwargs = {"prompt": _WHISPER_PROMPT}
        if language:
            kwargs["language"] = language
        resp = client.audio.transcriptions.create(
            file=(filename, audio), model=model, **kwargs)
        return (getattr(resp, "text", "") or "").strip()

    return run_with_key_rotation(_call)


def _describe_image_sync(image: bytes) -> str:
    """Blocking Groq vision call that turns a photo into a factual description
    the desk brain can reason over. Run via asyncio.to_thread."""
    import base64
    from modules.groq_key_manager import run_with_key_rotation

    model = (os.getenv("GROQ_VISION_MODEL")
             or "meta-llama/llama-4-scout-17b-16e-instruct").strip()
    b64 = base64.b64encode(image).decode()

    def _call(client):
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    "Describe this image factually in 2-4 sentences: subjects, "
                    "any visible text (quote it verbatim), setting, and anything "
                    "notable. No preamble, no opinions."
                )},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            temperature=0.2,
            max_tokens=400,
        )
        return (resp.choices[0].message.content or "").strip()

    return run_with_key_rotation(_call)


# ════════════════════════════════════════════════════════════════════════════
# Multi-user firewall
# ════════════════════════════════════════════════════════════════════════════
def _is_admin(message) -> bool:
    ident = _identify(message)
    return bool(ident and ident["tier"] == _ADMIN_TIER)


def _channel_for(message) -> "TelegramChannel":
    """Build an identity-scoped channel for a recognised caller.

    Only call after _identify() has confirmed the caller is recognised.
    """
    ident = _identify(message) or _IDENTITIES.get(_OWNER_ID, {})
    return TelegramChannel(
        message.chat.id,
        user=ident.get("user", _OWNER_USER),
        permission_tier=ident.get("tier", _ADMIN_TIER),
        honorific=ident.get("honorific", "Sir"),
    )


async def _firewall(message) -> None:
    """Cold, SILENT firewall for any unrecognised id. Logged; never answered.

    A reply would confirm the bot exists and is listening; an intruder gets
    nothing. The brain and engine are never invoked.
    """
    user = getattr(message, "from_user", None)
    uid = getattr(user, "id", "?")
    uname = getattr(user, "username", "?")
    print(f"[TELEGRAM] ⛔ Silent firewall — unrecognised id={uid} username=@{uname}", flush=True)


async def _deny_privileged(channel: "TelegramChannel") -> None:
    """Polite refusal sent to a recognised VIP guest who lacks the privilege."""
    await channel.reply(_VIP_REJECTION)


# ════════════════════════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════════════════════════
def _build_dispatcher():
    from aiogram import Dispatcher, Router, F
    from aiogram.filters import Command

    router = Router()

    @router.message(Command("start"))
    async def cmd_start(message):
        ident = _identify(message)
        if ident is None:
            return await _firewall(message)
        await message.answer(ident["greeting"])

    @router.message(Command("status"))
    async def cmd_status(message):
        if _identify(message) is None:
            return await _firewall(message)
        if not _is_admin(message):
            return await _deny_privileged(_channel_for(message))
        await TelegramChannel(message.chat.id).notify("typing")
        if _status_fn is not None:
            try:
                text = await _status_fn()
            except Exception as e:
                text = f"Status unavailable: {e}"
        else:
            text = "All systems nominal, Sir."
        await message.answer(text)

    @router.message(Command("task"))
    async def cmd_task(message):
        if _identify(message) is None:
            return await _firewall(message)
        if not _is_admin(message):
            return await _deny_privileged(_channel_for(message))
        goal = (message.text or "").partition(" ")[2].strip()
        if not goal:
            return await message.answer("Give me a goal, Sir — e.g. \"/task build figma key abc123\".")
        if _queue_goal_fn is None:
            return await message.answer("Task queue is not available right now, Sir.")
        await TelegramChannel(message.chat.id).notify("typing")
        try:
            task_id, n_actions = await _queue_goal_fn(goal, _OWNER_USER)
        except Exception as e:
            print(f"[TELEGRAM] queue_goal failed: {e}", flush=True)
            return await message.answer(f"I couldn't queue that, Sir: {e}")
        if task_id:
            await message.answer(
                f"Queued, Sir. Task `{task_id}` with {n_actions} action(s). "
                f"I'll pursue it in the background and report when it's done.",
                parse_mode=None,
            )
        else:
            await message.answer("I couldn't form an action plan for that goal, Sir.")

    @router.message(Command("tasks"))
    async def cmd_tasks(message):
        if _identify(message) is None:
            return await _firewall(message)
        if not _is_admin(message):
            return await _deny_privileged(_channel_for(message))
        if _list_tasks_fn is None:
            return await message.answer("Task list is not available right now, Sir.")
        try:
            tasks = await _list_tasks_fn()
        except Exception as e:
            return await message.answer(f"Task list unavailable: {e}")
        if not tasks:
            return await message.answer("No tasks on record, Sir.")
        lines = []
        for t in tasks[:15]:
            lines.append(f"• [{t.get('status','?')}] {t.get('title','(untitled)')} ({t.get('id','')[:8]})")
        await message.answer("Tasks:\n" + "\n".join(lines))

    @router.message(Command("offline"))
    async def cmd_offline(message):
        if _identify(message) is None:
            return await _firewall(message)
        if not _is_admin(message):
            return await _deny_privileged(_channel_for(message))
        token = (message.text or "").partition(" ")[2].strip()
        ok, msg = _request_watchdog_shutdown(token)
        await message.answer(msg)

    @router.message(F.text)
    async def on_text(message):
        ident = _identify(message)
        if ident is None:
            return await _firewall(message)
        if _process_fn is None:
            return await message.answer("My reasoning core isn't wired up yet, Sir.")
        # Identity rides on the channel: `user` drives the brain's persona/
        # honorifics, `permission_tier` is enforced by run_remote_command +
        # the ActionEngine before any tool runs. Each guest's session is keyed
        # by their own telegram chat id, isolating their working memory and
        # tool stream from the desk HUD and from each other.
        channel = TelegramChannel(
            message.chat.id, user=ident["user"], permission_tier=ident["tier"],
            honorific=ident["honorific"],
        )
        await channel.notify("typing")
        try:
            await _process_fn(message.text, channel)
        except Exception as e:
            print(f"[TELEGRAM] process_fn fault: {e}\n{traceback.format_exc()}", flush=True)
            await channel.reply("I encountered a fault processing that.")

    @router.message(F.voice | F.audio)
    async def on_voice(message):
        """Voice note → Whisper transcript → the exact same brain path as text."""
        ident = _identify(message)
        if ident is None:
            return await _firewall(message)
        if _process_fn is None:
            return await message.answer("My reasoning core isn't wired up yet, Sir.")
        channel = TelegramChannel(
            message.chat.id, user=ident["user"], permission_tier=ident["tier"],
            honorific=ident["honorific"],
        )
        await channel.notify("typing")
        media = message.voice or message.audio
        try:
            audio = await _download_media(media)
            fname = getattr(media, "file_name", None) or "voice.ogg"
            transcript = await asyncio.to_thread(_transcribe_sync, audio, fname)
        except Exception as e:
            print(f"[TELEGRAM] voice transcription fault: {e}\n{traceback.format_exc()}", flush=True)
            return await channel.reply(
                f"I couldn't make out that voice note, {ident['honorific']} — mind typing it?")
        if not transcript:
            return await channel.reply(
                f"That voice note came through empty, {ident['honorific']}.")
        print(f"[TELEGRAM] 🎤 voice → \"{transcript[:80]}\"", flush=True)
        try:
            await _process_fn(transcript, channel)
        except Exception as e:
            print(f"[TELEGRAM] process_fn fault: {e}\n{traceback.format_exc()}", flush=True)
            await channel.reply("I encountered a fault processing that.")

    @router.message(F.photo)
    async def on_photo(message):
        """Photo → vision description → the brain answers with full persona/memory."""
        ident = _identify(message)
        if ident is None:
            return await _firewall(message)
        if _process_fn is None:
            return await message.answer("My reasoning core isn't wired up yet, Sir.")
        channel = TelegramChannel(
            message.chat.id, user=ident["user"], permission_tier=ident["tier"],
            honorific=ident["honorific"],
        )
        await channel.notify("typing")
        try:
            image = await _download_media(message.photo[-1])  # largest size
            desc = await asyncio.to_thread(_describe_image_sync, image)
        except Exception as e:
            print(f"[TELEGRAM] photo vision fault: {e}\n{traceback.format_exc()}", flush=True)
            return await channel.reply(
                f"My visual cortex faltered on that one, {ident['honorific']} — send it again in a moment.")
        caption = (message.caption or "").strip()
        command = (
            f"[I've sent you a photo over Telegram. What the image shows: {desc}] "
            + (caption or "What do you make of it?")
        )
        try:
            await _process_fn(command, channel)
        except Exception as e:
            print(f"[TELEGRAM] process_fn fault: {e}\n{traceback.format_exc()}", flush=True)
            await channel.reply("I encountered a fault processing that.")

    @router.message()
    async def on_other(message):
        ident = _identify(message)
        if ident is None:
            return await _firewall(message)
        await message.answer("Text, voice notes, and photos I can work with — that one I can't, yet.")

    dp = Dispatcher()
    dp.include_router(router)
    return dp


# ════════════════════════════════════════════════════════════════════════════
# Outbound files (used by action_engine's telegram_send_file action)
# ════════════════════════════════════════════════════════════════════════════
async def _send_document(chat_id: int, path: str, caption: str = "") -> bool:
    if _bot is None:
        return False
    try:
        from aiogram.types import FSInputFile
        if not os.path.isfile(path):
            print(f"[TELEGRAM] send_document: file not found: {path}", flush=True)
            return False
        await _bot.send_document(chat_id, FSInputFile(path), caption=caption or None)
        return True
    except Exception as e:
        print(f"[TELEGRAM] send_document failed: {e}", flush=True)
        return False


async def send_document_to_owner(path: str, caption: str = "") -> bool:
    """Push a file/document to the owner's Telegram chat. Returns success bool."""
    if _OWNER_ID is None:
        return False
    return await _send_document(_OWNER_ID, path, caption)


async def send_text_to_owner(text: str) -> bool:
    """Push an unsolicited text message to the owner's Telegram chat (used by
    the owner-notify fan-out for proactive alerts). Returns True only if every
    chunk was accepted by Telegram."""
    if _bot is None or _OWNER_ID is None or not text or not text.strip():
        return False
    for chunk in _chunk(text.strip(), 4000):
        try:
            await _bot.send_message(_OWNER_ID, chunk)
        except Exception as e:  # noqa: BLE001
            print(f"[TELEGRAM] owner alert send failed: {e}", flush=True)
            return False
    return True


def is_configured() -> bool:
    return _bot is not None and _OWNER_ID is not None


# ════════════════════════════════════════════════════════════════════════════
# Watchdog graceful-shutdown bridge (optional)
# ════════════════════════════════════════════════════════════════════════════
def _request_watchdog_shutdown(token: str) -> tuple[bool, str]:
    """Ask the standalone watchdog to take the whole system offline (no restart)."""
    port = os.getenv("WATCHDOG_CONTROL_PORT", "8009")
    expected = os.getenv("WATCHDOG_TOKEN", "")
    if not token:
        return False, "Provide the shutdown token, Sir: /offline <token>."
    if expected and token != expected:
        return False, "Invalid shutdown token, Sir. Request denied."
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/shutdown?token={token}", method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        return True, "Acknowledged, Sir. Taking the system offline. Goodbye."
    except Exception as e:
        return False, f"Couldn't reach the watchdog, Sir: {e}"


# ════════════════════════════════════════════════════════════════════════════
# Lifecycle
# ════════════════════════════════════════════════════════════════════════════
def start_bot(
    process_fn: Callable[[str, OutputChannel], Awaitable[None]],
    *,
    queue_goal_fn: Optional[Callable[[str, str], Awaitable[tuple]]] = None,
    list_tasks_fn: Optional[Callable[[], Awaitable[list]]] = None,
    status_fn: Optional[Callable[[], Awaitable[str]]] = None,
) -> bool:
    """Configure and launch the Telegram poller as a background task.

    Returns True if the bot started, False if it is not configured. Safe to call
    from inside the FastAPI lifespan (must run on a live event loop).
    """
    global _BOT_TOKEN, _OWNER_ID, _bot, _dispatcher, _poll_task
    global _process_fn, _queue_goal_fn, _list_tasks_fn, _status_fn
    global _IDENTITIES

    _BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    owner_raw = os.getenv("TELEGRAM_USER_ID", "").strip()

    if not _BOT_TOKEN or not owner_raw:
        print("[TELEGRAM] Not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID missing) — gateway disabled.", flush=True)
        return False

    # TELEGRAM_USER_ID must be YOUR account's NUMERIC id (from @userinfobot) — NOT
    # the bot's @username and NOT the bot token. A non-numeric value here is the
    # single most common misconfiguration, and because it aborts start_bot() the
    # symptom is total silence (the poller never starts). Fail LOUD, not silent.
    if owner_raw.lstrip("-").isdigit():
        _OWNER_ID = int(owner_raw)
    else:
        hint = (
            "it looks like a username/handle — use your NUMERIC id"
            if owner_raw.startswith("@")
            else "it must be a numeric id"
        )
        print(
            "[TELEGRAM] ❌ TELEGRAM_USER_ID is invalid: "
            f"'{owner_raw}' ({hint}).\n"
            "[TELEGRAM]    Fix: open Telegram, message @userinfobot, copy the numeric 'Id' it\n"
            "[TELEGRAM]    returns (e.g. 123456789), and set TELEGRAM_USER_ID=that number in .env.\n"
            "[TELEGRAM]    Gateway DISABLED until this is corrected.",
            flush=True,
        )
        return False

    # ── Phase 4.5: build the recognised-identity registry ────────────────────
    # Admin is mandatory (validated above). The two VIP guests are optional —
    # a missing/invalid id simply means that person cannot connect. We never
    # let a malformed VIP id abort the whole gateway.
    _IDENTITIES = {}
    _IDENTITIES[_OWNER_ID] = _make_identity(
        user=_OWNER_USER,
        tier=_ADMIN_TIER,
        honorific="Sir",
        label="Kaustav (Admin)",
        greeting=(
            "J.A.R.V.I.S. online and at your service, Sir.\n\n"
            "• Just text me to issue a command.\n"
            "• /task <goal> — queue a background task (e.g. \"/task build figma key abc123\").\n"
            "• /tasks — list queued/finished tasks.\n"
            "• /status — system status.\n"
            "• /offline <token> — gracefully take the system offline (watchdog)."
        ),
    )

    def _register_vip(env_key: str, user: str, honorific: str, label: str, greeting: str) -> None:
        raw = os.getenv(env_key, "").strip()
        if not raw:
            return
        if not raw.lstrip("-").isdigit():
            print(f"[TELEGRAM] ⚠ {env_key} is not a numeric id ('{raw}') — {label} skipped.", flush=True)
            return
        vid = int(raw)
        if vid in _IDENTITIES:
            print(f"[TELEGRAM] ⚠ {env_key} collides with an existing identity (id={vid}) — {label} skipped.", flush=True)
            return
        _IDENTITIES[vid] = _make_identity(
            user=user, tier=_VIP_GUEST_TIER, honorific=honorific, label=label, greeting=greeting,
        )

    _register_vip(
        "TELEGRAM_GF_ID", "MOUSUMI", "Madam", "Mousumi (VIP Guest)",
        greeting=(
            "At your service, Madam. J.A.R.V.I.S. here.\n\n"
            "It is a pleasure to have you. You may chat with me freely, ask after the "
            "weather, the score of a match, or anything you're curious about, and I'll "
            "look it up at once. Some of the house controls are reserved for Sir, but "
            "for everything else — I am entirely at your disposal, Miss Mousumi."
        ),
    )
    _register_vip(
        "TELEGRAM_BROTHER_ID", "KINSHUK", "Mr. Kinshuk", "Kinshuk (VIP Guest)",
        greeting=(
            "J.A.R.V.I.S. online — good to see you, Mr. Kinshuk.\n\n"
            "Feel free to chat, ask for the weather, scores, news, or general "
            "knowledge and I'll fetch it straight away. The system controls and "
            "Sir's personal integrations are restricted, but for conversation and "
            "lookups I'm at your service."
        ),
    )

    try:
        from aiogram import Bot
    except Exception as e:
        print(f"[TELEGRAM] aiogram not installed ({e}) — gateway disabled.", flush=True)
        return False

    _process_fn = process_fn
    _queue_goal_fn = queue_goal_fn
    _list_tasks_fn = list_tasks_fn
    _status_fn = status_fn

    _bot = Bot(token=_BOT_TOKEN)
    _dispatcher = _build_dispatcher()

    async def _run():
        try:
            # handle_signals=False: uvicorn owns the process signals.
            await _dispatcher.start_polling(_bot, handle_signals=False)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[TELEGRAM] Polling loop crashed: {e}\n{traceback.format_exc()}", flush=True)

    _poll_task = asyncio.create_task(_run())
    _roster = ", ".join(f"{i['label']} [{i['tier']}]" for i in _IDENTITIES.values())
    print(
        f"[TELEGRAM] ✅ Gateway online — {len(_IDENTITIES)} recognised identit"
        f"{'y' if len(_IDENTITIES) == 1 else 'ies'}: {_roster}. Polling started.",
        flush=True,
    )
    return True


async def stop_bot() -> None:
    """Stop polling and close the bot session (call on shutdown)."""
    global _poll_task
    if _dispatcher is not None:
        try:
            await _dispatcher.stop_polling()
        except Exception:
            pass
    if _poll_task is not None:
        _poll_task.cancel()
        try:
            await _poll_task
        except (asyncio.CancelledError, Exception):
            pass
        _poll_task = None
    if _bot is not None:
        try:
            await _bot.session.close()
        except Exception:
            pass
    print("[TELEGRAM] Gateway stopped.", flush=True)
