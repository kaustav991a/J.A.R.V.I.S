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

* Strict owner firewall. Every update is validated against TELEGRAM_USER_ID from
  the environment. Any other user gets a cold rejection and is logged; their
  message never reaches the brain or the engine.

* Graceful when unconfigured. If TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID are
  absent, `start_bot()` logs and no-ops, so the rest of J.A.R.V.I.S. boots
  normally without Telegram.

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


# ════════════════════════════════════════════════════════════════════════════
# Output channel
# ════════════════════════════════════════════════════════════════════════════
class TelegramChannel(OutputChannel):
    """Delivers J.A.R.V.I.S.'s replies back to a specific Telegram chat."""

    kind = "telegram"

    def __init__(self, chat_id: int, user: str = _OWNER_USER) -> None:
        # Channel id is scoped to the telegram chat/user — this is the unit of
        # concurrent session isolation for the phone.
        super().__init__(channel_id=f"telegram:{chat_id}", user=user)
        self.chat_id = chat_id

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
# Owner firewall
# ════════════════════════════════════════════════════════════════════════════
def _is_owner(message) -> bool:
    user = getattr(message, "from_user", None)
    return bool(user and _OWNER_ID is not None and user.id == _OWNER_ID)


async def _reject(message) -> None:
    """Cold firewall rejection for any non-owner. Logged; brain never invoked."""
    user = getattr(message, "from_user", None)
    uid = getattr(user, "id", "?")
    uname = getattr(user, "username", "?")
    print(f"[TELEGRAM] ⛔ Unauthorized access attempt — id={uid} username=@{uname}", flush=True)
    try:
        await message.answer("⛔ Access denied. This is a private system. The attempt has been logged.")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# Handlers
# ════════════════════════════════════════════════════════════════════════════
def _build_dispatcher():
    from aiogram import Dispatcher, Router, F
    from aiogram.filters import Command

    router = Router()

    @router.message(Command("start"))
    async def cmd_start(message):
        if not _is_owner(message):
            return await _reject(message)
        await message.answer(
            "J.A.R.V.I.S. online and at your service, Sir.\n\n"
            "• Just text me to issue a command.\n"
            "• /task <goal> — queue a background task (e.g. \"/task build figma key abc123\").\n"
            "• /tasks — list queued/finished tasks.\n"
            "• /status — system status.\n"
            "• /offline <token> — gracefully take the system offline (watchdog)."
        )

    @router.message(Command("status"))
    async def cmd_status(message):
        if not _is_owner(message):
            return await _reject(message)
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
        if not _is_owner(message):
            return await _reject(message)
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
        if not _is_owner(message):
            return await _reject(message)
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
        if not _is_owner(message):
            return await _reject(message)
        token = (message.text or "").partition(" ")[2].strip()
        ok, msg = _request_watchdog_shutdown(token)
        await message.answer(msg)

    @router.message(F.text)
    async def on_text(message):
        if not _is_owner(message):
            return await _reject(message)
        if _process_fn is None:
            return await message.answer("My reasoning core isn't wired up yet, Sir.")
        channel = TelegramChannel(message.chat.id)
        await channel.notify("typing")
        try:
            await _process_fn(message.text, channel)
        except Exception as e:
            print(f"[TELEGRAM] process_fn fault: {e}\n{traceback.format_exc()}", flush=True)
            await channel.reply("I encountered a fault processing that, Sir.")

    @router.message()
    async def on_other(message):
        if not _is_owner(message):
            return await _reject(message)
        await message.answer("I can only act on text commands for now, Sir.")

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
    print(f"[TELEGRAM] ✅ Gateway online — owner id {_OWNER_ID}. Polling started.", flush=True)
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
