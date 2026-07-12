# ── UTF-8 stdout hardening (MUST run before any print) ───────────────────────
# JARVIS log lines contain Unicode (→, —, emojis). When stdout is redirected to a
# pipe/file/Windows-service (e.g. watchdog under a service, or the Electron shell
# capturing backend output), Python falls back to cp1252 and a single such print
# raises UnicodeEncodeError — killing the in-flight operation. Force UTF-8 so a log
# character can never abort a command.
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager 
import json
import asyncio
import sensors
from datetime import datetime 
import re
import random  
import os
from dotenv import load_dotenv

# Load env vars BEFORE importing modules that need them
# override=True ensures we use the key in .env instead of any stale system-level env vars
load_dotenv(override=True)

import speaker 
import memory 
import threading
from brain import (
    process_command,
    synthesize_info,
    synthesize_info_gen,
    synthesize_briefing_gen,
    synthesize_deep_memory_gen,
    generate_briefing,
    extract_and_store_memory,
    classify_intent,
    BrevityManager,
    get_last_sass_index,
    client as groq_client,
)
from action_engine import ActionEngine, tier_allows, ADMIN_TIER, TIER_BLOCKED_PREFIX
from recorder import listen_to_mic
from wakeword import wait_for_wake_word, wait_for_jarvis, is_shutting_down, pop_pending_utterance
import vision # --- Optical Biometrics ---
from ambient_vision import ambient_vision_daemon # --- Phase 5: Ambient Perception ---
from background_monitor import ProactiveAgent
from modules.overwatch_agent import OverwatchDaemon  # Phase 8.7: Overwatch Daemon
from modules import episodic_memory  # --- Phase 4: Conversation History ---
from modules import task_queue  # --- Roadmap §1.1: durable goal queue ---
from modules.worker_loop import OvernightWorker  # --- Roadmap §1.1: Overnight Worker Loop ---
from modules.session_manager import COMMAND_LOCK, CallbackChannel, SESSIONS  # --- Concurrent session scoping ---
from modules import telegram_bot  # --- Telegram Remote Gateway ---
from modules import planner  # --- ReAct Orchestrator (Roadmap §1.2) ---
from modules import fast_path  # --- Deterministic low-latency lane (Roadmap §3.4) ---
from modules import action_parser  # --- Unified LLM-reply → action(s) parse spine ---
# Phase 6 – Governance Engine
from governance_manager import governance_manager
from socket_manager import register_client, unregister_client, send_ui_update, set_app_loop

# --- Global Session Tracker ---
active_user = "KAUSTAV"
SYSTEM_ONLINE = False
proactive_agent = None
overnight_worker = None  # Roadmap §1.1: the autonomous task-queue daemon (set in lifespan)

# --- Barge-in / interruptibility (Refinement Phase) ---
# Set by a "stop"/"quiet"/"cancel"/"shut up" command; checked at the top of every
# sentence iteration in the streaming-synthesis loops so JARVIS can be cut off
# mid-monologue. Cleared at the start of each new valid command.
interrupt_flag = asyncio.Event()

# --- First-Boot Daily Briefing tracker ---
# Records the date of the last delivered morning briefing. First wake of a new
# day → comprehensive briefing; subsequent same-day wakes → standard greeting.
_LAST_BOOT_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_boot_date.txt")

# Dynamic salutation tracking — updated on every backdoor command.
# _smart_briefing() checks this to decide between a full cinematic briefing
# and a short "Standing by, Sir." if the user was active within the last 10 min.
import datetime as _dt
_last_command_time: _dt.datetime | None = None
_RECENT_ACTIVITY_WINDOW_SECS = 600  # 10 minutes

_STANDBY_PHRASES = [
    "Standing by, Sir.",
    "Awaiting orders, Sir.",
    "Back online, Sir. What do you need?",
    "Systems nominal. Ready when you are, Sir.",
    "At your service, Sir.",
]
_standby_idx = 0

def _hud_open_widget_message(widget: str) -> str:
    return {
        "vitals": "Health panel on the HUD, sir.",
        "mail": "Mail uplink on the HUD, sir.",
        "calendar": "Schedule panel on the HUD, sir.",
        "calculator": "Calculator on the HUD, sir.",
        "notepad": "Notes panel on the HUD, sir.",
        "browser": "Browser panel on the HUD, sir.",
        "camera": "Optical feed on the HUD, sir.",
        "map": "Tactical map on the HUD, sir.",
    }.get(widget, "Panel on the HUD, sir.")


def _actions_include_satellite(actions: list) -> bool:
    """True when the command batch will use the immersive satellite / search canvas."""
    if not actions:
        return False
    return any(
        (a.get("action_type") or "") in ("web_search", "web_search_image")
        for a in actions
    )


def _normalize_os_control_batch(actions: list, command_text: str) -> list:
    """
    Collapse duplicate os_control entries and map the user's words to the correct target.
    Fixes "turn down the volume" → mute and "unmute" being paired with volume_down.
    """
    if not actions or not any(a.get("action_type") == "os_control" for a in actions):
        return actions
    ul_raw = (command_text or "").lower()
    ul = ul_raw.replace("jarvis", " ").strip()

    if "unmute" in ul_raw:
        tgt = "unmute"
    elif any(
        p in ul
        for p in (
            "down the volume",
            "turn down the volume",
            "lower the volume",
            "volume down",
            "turn down volume",
            "reduce the volume",
            "reduce volume",
        )
    ) and "mute" not in ul_raw.replace("unmute", ""):
        tgt = "volume_down"
    elif any(
        p in ul
        for p in (
            "up the volume",
            "turn up the volume",
            "raise the volume",
            "volume up",
            "turn up volume",
            "increase the volume",
            "increase volume",
        )
    ):
        tgt = "volume_up"
    elif "mute" in ul_raw or "silence" in ul:
        tgt = "mute"
    else:
        out = []
        seen_oc = False
        for a in actions:
            if a.get("action_type") != "os_control":
                out.append(a)
                continue
            if seen_oc:
                continue
            seen_oc = True
            out.append(dict(a))
        return out

    out: list = []
    inserted = False
    for a in actions:
        if a.get("action_type") != "os_control":
            out.append(a)
            continue
        if not inserted:
            out.append({"action_type": "os_control", "target": tgt})
            inserted = True
    return out


def _consume_new_day_briefing() -> bool:
    """
    Returns True exactly once per calendar day — on the FIRST boot of a new day —
    and records today's date so subsequent same-day wakes return False.
    Backed by a tiny on-disk marker so it survives server restarts.
    """
    today = _dt.date.today().isoformat()
    stored = ""
    try:
        if os.path.exists(_LAST_BOOT_DATE_FILE):
            with open(_LAST_BOOT_DATE_FILE, "r", encoding="utf-8") as f:
                stored = f.read().strip()
    except Exception as e:
        print(f"[BRAIN] Could not read boot-date marker: {e}")
    if stored == today:
        return False
    try:
        with open(_LAST_BOOT_DATE_FILE, "w", encoding="utf-8") as f:
            f.write(today)
    except Exception as e:
        print(f"[BRAIN] Could not write boot-date marker: {e}")
    return True


def _smart_briefing(weather: dict, wake_phrase: str, user: str) -> str:
    """
    Decides which wake-up message to deliver:
      • FIRST boot of a NEW DAY  → Comprehensive Morning Briefing (date, time,
        today's calendar, system readiness).
      • Same day, recent activity (< _RECENT_ACTIVITY_WINDOW_SECS) → short standby
        line, so re-waking him two minutes after sleep doesn't trigger a full briefing.
      • Same day, no recent activity → standard brief greeting.
    """
    global _standby_idx

    # --- First-Boot Daily Briefing: a new day always earns the full briefing ---
    if _consume_new_day_briefing():
        print("[BRAIN] New day detected -> delivering Comprehensive Morning Briefing.")
        return generate_briefing(weather, wake_phrase, user, comprehensive=True)

    if _last_command_time is not None:
        elapsed = (_dt.datetime.now() - _last_command_time).total_seconds()
        if elapsed < _RECENT_ACTIVITY_WINDOW_SECS:
            phrase = _STANDBY_PHRASES[_standby_idx % len(_STANDBY_PHRASES)]
            _standby_idx += 1
            print(f"[BRAIN] Dynamic salutation: recent activity ({elapsed:.0f}s ago) -> standby phrase")
            return phrase
    # Same day, no recent activity — standard brief greeting
    return generate_briefing(weather, wake_phrase, user)

# Actions that are intermediate pipeline steps (e.g. open app → type → save).
# Their result strings are logged but NOT spoken and NOT displayed as chat messages.
# Only the final step (ghost_save_file → "Saved, sir.") is vocalized.
SILENT_PIPELINE_ACTIONS = frozenset({"native_app_launcher", "ghost_type"})
# Phase 7.1.1 — synthesized briefings must not flood episodic recall context.
_BRIEFING_EPISODIC_PLACEHOLDER = "[System: Delivered Morning Briefing to user.]"

# Words that indicate a pending-decision response ("save", "overwrite", "cancel", …)
# vs a regular JARVIS command.  Used in both the backdoor and websocket handlers.
_decision_words: frozenset[str] = frozenset({
    "save", "overwrite", "new", "replace", "discard", "keep", "cancel", "yes", "no",
})
_jarvis_command_words: frozenset[str] = frozenset({
    "jarvis", "check", "find", "enable", "disable", "clear", "read",
    "search", "open", "close", "show", "status", "recall", "remember",
    "create", "system", "inbox", "calendar", "vitals", "screen",
    "display", "mute", "volume", "focus", "write", "get",
})

# Phase 6: words the user says to approve or deny a CONFIRM-tier action.
_APPROVAL_WORDS: frozenset[str] = frozenset({
    "yes", "confirm", "approve", "authorise", "authorize", "proceed",
    "go ahead", "do it", "execute", "allow", "granted",
})
_DENIAL_WORDS: frozenset[str] = frozenset({
    "no", "deny", "cancel", "abort", "stop", "decline", "reject",
    "nevermind", "never mind", "don't",
})

# Phase 4 item 5: deterministic queued-task approve/deny — "approve task 3fa9c2d1"
# resumes a worker task that paused on a CONFIRM-tier step; "deny task …" drops it.
_TASK_APPROVAL_RE = re.compile(
    r"^(approve|resume|authorise|authorize|deny|reject|drop|cancel)\s+task\s+([0-9a-f]{4,12})\b"
)
_TASK_APPROVE_VERBS = frozenset({"approve", "resume", "authorise", "authorize"})

# Phase 4 item 4: the confirmation_id of the CONFIRM-tier action the DESK was
# asked about (backdoor + HUD/voice share one physical operator, so one slot).
# Desk approvals resolve THIS id instead of the governance manager's global
# single slot, so a desk "yes" can never execute an action that a remote
# channel pended in the meantime. A dict so nested handlers can mutate it
# without `global` declarations.
_DESK_PENDING = {"cid": None}

# Chat transcript panel (HUD COMM TRANSCRIPT) — show/hide phrases.
# Hide is checked first so "hide/close" is never swallowed by a "show" match.
_CHAT_SHOW_PHRASES: tuple[str, ...] = (
    "show chat", "open chat", "show the chat", "open the chat", "chat panel",
    "show transcript", "open transcript", "show conversation", "show the transcript",
)
_CHAT_HIDE_PHRASES: tuple[str, ...] = (
    "hide chat", "close chat", "hide the chat", "close the chat",
    "hide transcript", "close transcript", "hide conversation", "hide the transcript",
)

# Background-queue status report (Phase 3 × §1.1) — "what are you working on?" etc.
_QUEUE_STATUS_PHRASES: tuple[str, ...] = (
    "what are you working on", "what are you currently working on", "what are you up to",
    "what are you doing", "status report", "queue status", "task status",
    "what's in the queue", "whats in the queue", "what is in the queue",
    "what's running", "whats running", "background tasks", "what are you busy with",
)

def _heal_json(raw: str) -> str:
    """
    Try to fix a JSON string that was truncated by the LLM hitting max_tokens.
    Attempts up to three common closing suffix repairs before giving up.
    """
    for suffix in ("}", "]}", "]}}"):
        try:
            json.loads(raw + suffix)
            return raw + suffix
        except (json.JSONDecodeError, ValueError):
            pass
    return raw

# =============================================================================
# SPEECH SANITISER  — _sanitize_for_speech(atype, result)
#
# Converts raw action engine output into clean J.A.R.V.I.S. TTS speech.
# The full raw result is still sent to the UI chat bubble unchanged.
#
# Rules:
#  1. Strip ALL technical metadata (PIDs, HWNDs, addresses, full paths).
#  2. Provide persona-appropriate summary lines per action type.
#  3. Return None for silent pipeline actions (caller skips TTS).
# =============================================================================
import re as _re

_PID_RE    = _re.compile(r'\(?PID[:\s=]+\d+\)?', _re.IGNORECASE)
_HWND_RE   = _re.compile(r'\(?hwnd[=:\s]+\d+\)?', _re.IGNORECASE)
_ADDR_RE   = _re.compile(r'\b0x[0-9a-fA-F]{4,}\b')
_PATH_RE   = _re.compile(r'[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*([^\\/:*?"<>|\r\n\s]+)')
_DIFF_LINE = _re.compile(r'^(---|\+\+\+|@@)', _re.MULTILINE)
_META_LINE = _re.compile(r'(LINES:\s*\d+|SIZE:\s*[\d,]+ bytes)', _re.IGNORECASE)


def _strip_metadata(text: str) -> str:
    """Remove PIDs, HWNDs, hex addresses, diff markers, and LINES/SIZE metadata."""
    text = _PID_RE.sub('', text)
    text = _HWND_RE.sub('', text)
    text = _ADDR_RE.sub('', text)
    text = _META_LINE.sub('', text)
    # Strip diff marker lines entirely
    text = '\n'.join(
        line for line in text.splitlines()
        if not _DIFF_LINE.match(line)
    )
    # Collapse extra whitespace
    text = _re.sub(r'[ \t]{2,}', ' ', text).strip()
    return text


def _sanitize_for_speech(atype: str, result: str) -> str | None:
    """
    Return a clean TTS-ready string, or None to stay silent.
    Never exposes raw diffs, file paths, PIDs, HWNDs, or diff markers.
    """
    r = result.lower()

    # ── Shared denial phrases (checked first for all action types) ────────────
    if "access denied" in r or "outside the permitted" in r:
        return "That's outside my permitted area, Sir. Access denied."
    if "read refused" in r or ("binary" in r and "read" in r):
        return "I cannot read binary or executable files, Sir."
    if "write refused" in r:
        return "Write refused, Sir. That file type cannot be written."
    if "blocked by security" in r or "blocked pattern" in r:
        return "That command is blocked by security policy, Sir."

    # ── Silent pipeline (intermediate steps — no TTS) ─────────────────────────
    if atype in ("native_app_launcher", "ghost_type", "agentic_gui_task"):
        return None

    # ── Ghost save ────────────────────────────────────────────────────────────
    if atype == "ghost_save_file":
        if any(k in r for k in ("saved", "file verified", "success")):
            return "Saved, Sir."
        if "already exists" in r:
            return "That file already exists, Sir. Shall I overwrite, or save a new copy?"
        if "failed" in r or "error" in r:
            return "The save failed, Sir. Please check the application."
        return "Save complete, Sir."

    # ── Workspace actions ─────────────────────────────────────────────────────
    if atype == "workspace_write":
        if "created:" in r:
            return "File created, Sir."
        if "overwritten:" in r:
            return "File overwritten, Sir."
        if "write error" in r:
            return "Write failed, Sir. There was an I/O error."
        return "File written, Sir."

    if atype == "workspace_patch":
        if "replacement" in r or "patched" in r:
            return "Patch applied, Sir. The file's been updated to your specifications."
        if "not found" in r:
            if "FILE PREVIEW" in result:
                idx = result.find("FILE PREVIEW")
                snippet = result[idx + 12:idx + 400].strip().lstrip("(use one of these exact strings as your search):").strip()
                snippet = _re.sub(r"\s+", " ", snippet.replace("\n", " "))
                if snippet:
                    preview = BrevityManager.truncate_to_words(snippet, 18)
                    return f"Patch failed, Sir — that string isn't in the file. It currently reads: {preview}"
            return "Patch failed, Sir — that string isn't in the file."
        if "aborted" in r:
            return "Patch aborted, Sir. Too many matches — be more specific."
        return "Patch complete, Sir."

    if atype == "workspace_read":
        sep = "─" * 20
        if sep in result:
            content = result[result.index(sep) + len(sep):].strip()
            snippet = _re.sub(r"\s+", " ", content.replace("\n", " ")).strip()
            if snippet:
                spoken = BrevityManager.truncate_to_words(snippet, 28)
                return f"File read, Sir. It contains: {spoken}"
        if "not found" in r:
            return "I've lost the trail on that file, Sir."
        return "File read, Sir."

    # ── Terminal commands ─────────────────────────────────────────────────────
    if atype == "run_terminal_command":
        if "created" in r and "folder" in r:
            return "Folder created, Sir."
        if "deleted" in r or "removed" in r:
            return "Done, Sir. Item removed."
        if "moved" in r or "copied" in r:
            return "Done, Sir. File relocated."
        if "error" in r or "failed" in r:
            return "Terminal command failed, Sir."
        if "blocked" in r or "denied" in r:
            return "That command is blocked by security policy, Sir."
        # For verbose terminal output (ipconfig, ping, process list) — stay silent,
        # the UI bubble already shows the full output.
        return None

    # ── Telemetry / system status ─────────────────────────────────────────────
    if atype in ("get_telemetry", "system_status"):
        # These go through synthesize_info already — don't double-speak.
        return None

    # ── OS control ────────────────────────────────────────────────────────────
    if atype == "os_control":
        # SMTC context-aware failure: nothing is playing — speak naturally.
        if "no media applications are currently active" in r:
            return "There is no media currently playing, Sir."
        # Volume: check "unmuted" BEFORE "muted" to avoid false-positive match.
        if "unmuted" in r:  return "Unmuted, Sir."
        if "muted" in r:    return "Muted, Sir."
        if "volume" in r and "increased" in r: return "Volume up, Sir."
        if "volume" in r and "decreased" in r: return "Volume down, Sir."
        if "lock" in r:     return "Locking the screen, Sir."
        if "next" in r:     return "Next track, Sir."
        if "prev" in r or "previous" in r: return "Previous track, Sir."
        if "toggled" in r or "play" in r or "pause" in r: return "Media playback toggled, Sir."
        return "Done, Sir."

    # ── Memory / facts ────────────────────────────────────────────────────────
    if atype == "remember_fact":
        return "Committed to memory, Sir."

    # ── Gmail — preserve full numbered lists for TTS (do not apply 25-word cap) ──
    if atype in ("gmail_read_unread", "gmail_read"):
        cleaned = _strip_metadata(result)
        cleaned = _PATH_RE.sub(r"\1", cleaned).strip()
        return cleaned if cleaned else None

    # ── Focus mode ────────────────────────────────────────────────────────────
    if atype in ("enable_focus_mode", "disable_focus_mode"):
        state = "enabled" if atype == "enable_focus_mode" else "disabled"
        return f"Focus mode {state}, Sir."

    # ── TV controls ───────────────────────────────────────────────────────────
    if atype == "tv_control":
        if "error" in r or "failed" in r or "unable" in r:
            return "I couldn't reach the TV, Sir. It may be offline."
        return "Done, Sir."

    # ── OS macros (deep work, diagnostics) ───────────────────────────────────
    if atype == "os_macro":
        rl = r.lower()
        if "work mode ended" in rl:
            return "Work mode ended, Sir."
        if "deep work" in rl or "vs code" in rl:
            return "Deep work mode engaged, Sir."
        return "Macro complete, Sir."

    # ── Generic fallback: strip metadata, truncate ───────────────────────────
    cleaned = _strip_metadata(result)
    # Remove full Windows paths — keep just filename
    cleaned = _PATH_RE.sub(r'\1', cleaned)
    cleaned = cleaned.strip()
    if not cleaned or len(cleaned) < 5:
        return None
    return BrevityManager.truncate_to_words(cleaned, 25)


# =============================================================================
# LATENCY OPTIMISATION — Sentence-streaming synthesis + sequential TTS
#
# Architecture:
#   1. synthesize_info_gen() runs in a background thread, streaming from Groq.
#   2. Each complete sentence is pushed onto an asyncio.Queue via
#      loop.call_soon_threadsafe() so we never block the event loop.
#   3. The consumer awaits speaker.speak_text() per sentence so playback does not
#      interleave with a subsequent HTTP/command while earlier synthesis still runs.
# =============================================================================
async def _stream_synthesize_speak(
    original_query: str,
    raw_data: str,
    active_user_: str,
    safe_send_fn,
    has_web_search: bool,
    sass_index: int = 50,  # Phase 8.7: SASS_INDEX from classify_intent()
) -> str:
    """
    Streams synthesize_info sentence-by-sentence.
    Speaks each sentence immediately as it arrives — user hears the first
    sentence ~3-5 s earlier than with the blocking version.

    UI/Audio Concurrency Fix: send_ui_update is fired BEFORE speaker.speak_text
    for each sentence, so the text appears in the React log the instant JARVIS
    begins to speak — not after he finishes.

    Returns the full assembled answer for the UI chat bubble.
    """
    loop = asyncio.get_event_loop()
    sentence_queue: asyncio.Queue = asyncio.Queue()

    def _producer():
        try:
            for sentence in synthesize_info_gen(original_query, raw_data, active_user_, sass_index=sass_index):
                loop.call_soon_threadsafe(sentence_queue.put_nowait, sentence)
        except Exception as exc:
            loop.call_soon_threadsafe(
                sentence_queue.put_nowait,
                f"Synthesis error: {exc}",
            )
        finally:
            loop.call_soon_threadsafe(sentence_queue.put_nowait, None)  # sentinel

    t = threading.Thread(target=_producer, daemon=True)
    t.start()

    full_sentences: list[str] = []
    while True:
        sentence = await sentence_queue.get()
        if sentence is None:
            break
        # ── Barge-in: user said "stop"/"quiet" — abandon the rest of the stream ──
        if interrupt_flag.is_set():
            print("[BARGE-IN] Synthesis stream interrupted by user.", flush=True)
            break
        full_sentences.append(sentence)
        assembled = " ".join(full_sentences).strip()

        # ── UI/Audio Concurrency: push text to UI BEFORE blocking on TTS ──────
        if has_web_search:
            # Progressive satellite text update
            await safe_send_fn(
                {
                    "status": "search_result",
                    "message": "SATELLITE DATA LINK",
                    "result": assembled,
                }
            )
        else:
            # For non-web synthesis: stream each sentence progressively so the
            # chat bubble builds up word-by-word as JARVIS speaks.
            await safe_send_fn({"status": "complete", "result": assembled})

        # Await TTS AFTER the UI has already received the text.
        # Phase 7.1.2 ordering: awaiting (not fire-and-forget) ensures concurrent
        # HTTP/commands cannot interleave TTS from a prior synthesis stream.
        await speaker.speak_text(sentence)

    t.join(timeout=3.0)
    return " ".join(full_sentences)


async def _stream_briefing_speak(
    original_query: str,
    briefing_data: str,
    active_user_: str,
    safe_send_fn,
) -> str:
    """
    Streams synthesize_briefing_gen sentence-by-sentence (async generator).
    Intercepts [BRIEFING_DATA] payloads so they never reach TTS raw.
    Logs a concise system placeholder to episodic memory (not the full monologue).

    UI/Audio Concurrency Fix: each sentence is sent to the UI concurrently with
    TTS via asyncio.gather so the text and audio start together.

    Returns the full assembled monologue for the UI chat bubble.
    """
    await safe_send_fn({"status": "processing_llm", "message": "Synthesising your briefing…"})
    print("[MAIN] Intercepted BRIEFING_DATA. Routing to synthesis pass...", flush=True)

    full_sentences: list[str] = []

    async for sentence in synthesize_briefing_gen(
        briefing_data,
        original_query=original_query,
        active_user=active_user_,
    ):
        # ── Barge-in: abandon the briefing if the user cut JARVIS off ──
        if interrupt_flag.is_set():
            print("[BARGE-IN] Briefing stream interrupted by user.", flush=True)
            break
        full_sentences.append(sentence)
        assembled = " ".join(full_sentences).strip()
        # ── UI/Audio Concurrency: update UI and start TTS simultaneously ──────
        await asyncio.gather(
            safe_send_fn({"status": "complete", "result": assembled}),
            speaker.speak_text(sentence),
        )

    full_text = " ".join(full_sentences).strip()
    if full_text:
        episodic_memory.log_turn("assistant", _BRIEFING_EPISODIC_PLACEHOLDER, active_user_)
    return full_text


async def _stream_deep_memory_speak(
    deep_memory_payload: str,
    active_user_: str,
    safe_send_fn,
) -> str:
    """
    Streams synthesize_deep_memory_gen sentence-by-sentence.
    Intercepts [DEEP_MEMORY_DATA] payloads and delivers a rich narrative.
    Enforces Silence Protocol: logs a terse placeholder to episodic memory
    rather than the full monologue, preventing context window pollution.

    UI/Audio Concurrency Fix: converted from a collect-all-then-play model to a
    true streaming producer/consumer using a Queue + background thread (matching
    _stream_synthesize_speak). Each sentence is sent to the UI BEFORE TTS begins,
    so the React log populates the instant JARVIS starts to speak.
    """
    await safe_send_fn({"status": "processing_llm", "message": "Recalling your complete profile\u2026"})
    print("[MAIN] Intercepted DEEP_MEMORY_DATA. Routing to Deep Memory Synthesis...", flush=True)

    loop = asyncio.get_event_loop()
    sentence_queue: asyncio.Queue = asyncio.Queue()

    def _producer():
        try:
            for sentence in synthesize_deep_memory_gen(deep_memory_payload, active_user=active_user_):
                loop.call_soon_threadsafe(sentence_queue.put_nowait, sentence)
        except Exception as exc:
            loop.call_soon_threadsafe(
                sentence_queue.put_nowait,
                f"Memory synthesis error: {exc}",
            )
        finally:
            loop.call_soon_threadsafe(sentence_queue.put_nowait, None)  # sentinel

    t = threading.Thread(target=_producer, daemon=True)
    t.start()

    full_sentences: list[str] = []
    while True:
        sentence = await sentence_queue.get()
        if sentence is None:
            break
        # ── Barge-in: abandon the deep-memory recap if the user cut JARVIS off ──
        if interrupt_flag.is_set():
            print("[BARGE-IN] Deep-memory stream interrupted by user.", flush=True)
            break
        full_sentences.append(sentence)
        assembled = " ".join(full_sentences).strip()
        # ── UI/Audio Concurrency: push assembled text to UI BEFORE TTS blocks ─
        await asyncio.gather(
            safe_send_fn({"status": "complete", "result": assembled}),
            speaker.speak_text(sentence),
        )

    t.join(timeout=3.0)
    full_text = " ".join(full_sentences).strip()
    if full_text:
        # SILENCE PROTOCOL: log terse placeholder — not the full monologue
        episodic_memory.log_turn("assistant", "[System: Delivered Deep Memory Profile to user.]", active_user_)
    return full_text


class BackdoorRequest(BaseModel):
    command: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    global proactive_agent, overnight_worker

    set_app_loop(asyncio.get_running_loop())

    async def safe_send_all(payload):
        await send_ui_update(payload)
                
    async def global_speak(text):
        asyncio.create_task(speaker.speak_text(text))

    # Phase 4.1: register desk delivery legs with the owner-notify fan-out so
    # proactive/worker alerts can reach HUD + TTS + phone from one call.
    from modules import owner_notify
    owner_notify.configure(safe_send_all, global_speak)

    # §3.1: in-process supervisor that restarts any daemon that crashes/wedges
    # (the standalone watchdog.py covers the whole server; this covers the daemons).
    from modules.daemon_supervisor import DaemonSupervisor
    # Don't restart daemons that exit cleanly during shutdown.
    daemon_supervisor = DaemonSupervisor(should_continue=lambda: not is_shutting_down.is_set())

    proactive_agent = ProactiveAgent(safe_send_all, global_speak, lambda: SYSTEM_ONLINE)
    daemon_supervisor.adopt("proactive", lambda: proactive_agent.start(),
                            asyncio.create_task(proactive_agent.start()))

    # --- Phase 8.7: Start Overwatch Daemon ---
    overwatch_daemon = OverwatchDaemon(
        broadcast_callback=safe_send_all,
        speak_callback=global_speak,
        is_system_online_fn=lambda: SYSTEM_ONLINE,
        active_user_fn=lambda: active_user,
    )
    daemon_supervisor.adopt("overwatch", lambda: overwatch_daemon.start(),
                            asyncio.create_task(overwatch_daemon.start()))
    
    # --- Phase 5: Start Ambient Vision Daemon ---
    ambient_vision_daemon.start()

    # --- Phase G3: Gesture + presence daemon (hand control, owner face gate,
    #     away soft-lock). Thread daemon with an internal retry shell, same
    #     Pattern B as ambient_vision. JARVIS_GESTURE=0 skips it entirely.
    try:
        from gesture_daemon import gesture_daemon
        if os.getenv("JARVIS_GESTURE", "1") == "1":
            gesture_daemon.loop = asyncio.get_running_loop()
            gesture_daemon.start()
    except Exception as e:
        print(f"[GESTURE] daemon failed to start: {e}", flush=True)

    # --- Phase 7.2: Start Zero-CPU Proactive Scheduler ---
    from background_monitor import ScheduleDaemon
    scheduler_daemon = ScheduleDaemon(asyncio.get_running_loop(), active_user,
                                      is_online_fn=lambda: SYSTEM_ONLINE)
    scheduler_daemon.start()

    # --- Phase 7: Start Zero-CPU Routine Scheduler ---
    from modules.routines import RoutineEngine
    engine = ActionEngine()
    routine_engine = RoutineEngine(engine.execute, global_speak)
    daemon_supervisor.adopt("routines", lambda: routine_engine.run_scheduler(),
                            asyncio.create_task(routine_engine.run_scheduler()))

    # --- Roadmap §1.1: Start the Overnight Worker Loop (Continuous Autonomous Agency) ---
    # Drains the durable task queue, executes queued goals with self-correction, and
    # reports results to HUD/voice. Uses execute_with_retry so it inherits the engine's
    # governance gate + fallback logic; only AUTO-tier actions run unattended.
    overnight_worker = OvernightWorker(
        execute_fn=engine.execute_with_retry,
        broadcast_fn=safe_send_all,
        speak_fn=speaker.speak_text,
        is_system_online_fn=lambda: SYSTEM_ONLINE,
        active_user_fn=lambda: active_user,
        # §1.1b: feed failures back to the brain for a new plan (bounded retries).
        replan_fn=planner.replan_after_failure,
    )
    daemon_supervisor.adopt("overnight_worker", lambda: overnight_worker.start(),
                            asyncio.create_task(overnight_worker.start()))

    # Start the health-monitor last, after all daemons are adopted.
    asyncio.create_task(daemon_supervisor.start())

    # --- Remote access: untether J.A.R.V.I.S. from the desk ---
    # Two mutually-exclusive front doors, both routing phone commands through the
    # SAME run_remote_command pipeline (brain + ActionEngine) and replying only to
    # the phone — never the HUD or desk speakers:
    #   • Level-3 bridge (preferred): the always-on cloud gateway owns the Telegram
    #     token and forwards recognised messages to us over an authenticated socket.
    #     Enabled by JARVIS_CLOUD_BRIDGE=1 + JARVIS_BRIDGE_URL + BRIDGE_SECRET.
    #   • Direct Telegram poller: the desk consumes the bot token itself.
    # We start ONE of them — never both — because Telegram delivers updates to a
    # single consumer, so a desk poller and the cloud webhook would contend.
    try:
        from modules import cloud_bridge
        if cloud_bridge.is_enabled():
            cloud_bridge.start_bridge(run_remote_command)
        else:
            telegram_bot.start_bot(
                run_remote_command,
                queue_goal_fn=remote_queue_goal,
                list_tasks_fn=remote_list_tasks,
                status_fn=remote_status,
            )
    except Exception as e:
        print(f"[REMOTE] Gateway failed to start: {e}", flush=True)

    # --- §1.3 Continuous full-duplex pipeline (opt-in) ---
    # Streaming STT (vosk) + AEC running continuously; finals route through the SAME
    # run_remote_command pipeline (brain + engine) and are spoken back. Speech onset
    # triggers barge-in. Off unless JARVIS_FULL_DUPLEX_PIPELINE=1 — the classic
    # wakeword/recorder loop remains the default so nothing existing changes.
    try:
        from modules import audio_pipeline
        if audio_pipeline.is_enabled():
            _fd_loop = asyncio.get_running_loop()
            from modules.session_manager import CallbackChannel as _CbCh

            async def _voice_reply(text):
                await speaker.speak_text(text)

            _voice_channel = _CbCh("voice:fullduplex", _voice_reply,
                                   user=active_user, kind="voice")

            def _fd_on_final(text):
                if not text or not text.strip():
                    return
                asyncio.run_coroutine_threadsafe(
                    run_remote_command(text.strip(), _voice_channel), _fd_loop)

            def _fd_on_speech_start():
                # Barge-in: stop current TTS and signal streaming loops to break.
                try:
                    if speaker.is_system_speaking:
                        speaker.stop_audio()
                        _fd_loop.call_soon_threadsafe(interrupt_flag.set)
                except Exception:
                    pass

            _fd_engine = audio_pipeline.build_default_engine(
                on_final=_fd_on_final, on_speech_start=_fd_on_speech_start)
            if _fd_engine is not None:
                threading.Thread(target=_fd_engine.run, daemon=True).start()
                print("[FD-PIPELINE] Continuous full-duplex pipeline started.", flush=True)
    except Exception as e:
        print(f"[FD-PIPELINE] startup skipped: {e}", flush=True)

    yield
    print("\n[SYSTEM] Gracefully shutting down...")
    # Signal shutdown FIRST so the daemon supervisor won't restart daemons that
    # are about to exit cleanly.
    is_shutting_down.set()
    daemon_supervisor.is_running = False
    try:
        await telegram_bot.stop_bot()
    except Exception:
        pass
    try:
        from modules import cloud_bridge
        await cloud_bridge.stop_bridge()
    except Exception:
        pass
    if proactive_agent:
        proactive_agent.is_running = False
    if overnight_worker:
        overnight_worker.is_running = False
    ambient_vision_daemon.stop()
    try:
        from gesture_daemon import gesture_daemon
        gesture_daemon.stop()
    except Exception:
        pass
    is_shutting_down.set()
    await asyncio.sleep(1)

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ActionEngine()

# --- Phase 4 regression hooks (opt-in: avoids exposing classify / speech trace in production) ---
if os.getenv("JARVIS_REGRESSION_ROUTES") == "1":
    @app.get("/api/regression/spoken")
    async def api_regression_spoken(clear: bool = False):
        lines = speaker.regression_get_spoken(clear=clear)
        return {"lines": lines, "count": len(lines)}

    @app.post("/api/regression/classify")
    async def api_regression_classify(req: BackdoorRequest):
        classification = await asyncio.to_thread(classify_intent, req.command)
        return {"classification": classification}

@app.get("/")
def read_root():
    return {"status": "J.A.R.V.I.S. Backend is Online"}

@app.get("/api/tv/status")
async def tv_status():
    return engine.get_tv_status()

@app.get("/api/telemetry")
async def system_telemetry():
    """Returns real-time CPU, RAM, disk, and uptime data."""
    return await asyncio.to_thread(sensors.get_system_telemetry)

@app.get("/api/email/summary")
async def email_summary():
    """Returns inbox previews for the frontend Email widget."""
    try:
        from modules.gmail_agent import GmailAgent, is_gmail_available
        if not is_gmail_available():
            return {"configured": False, "unread": 0, "previews": []}
        agent = GmailAgent()
        previews = await asyncio.to_thread(agent.get_inbox_preview, 5)
        unread = await asyncio.to_thread(agent.get_unread_count)
        return {"configured": True, "unread": unread, "previews": previews}
    except Exception as e:
        print(f"[API] Email summary error: {e}")
        return {"configured": False, "unread": 0, "previews": [], "error": str(e)}

@app.get("/api/calendar/today")
async def calendar_today():
    """Returns today's events for the frontend Calendar widget."""
    try:
        from modules.calendar_agent import CalendarAgent, is_calendar_available
        if not is_calendar_available():
            return {"configured": False, "events": []}
        agent = CalendarAgent()
        events = await asyncio.to_thread(agent.get_today_events_structured)
        return {"configured": True, "events": events}
    except Exception as e:
        print(f"[API] Calendar error: {e}")
        return {"configured": False, "events": [], "error": str(e)}

@app.get("/api/vision/state")
async def vision_state():
    """
    Optical-feed state for the HUD camera panel. The browser pulls the raw MJPEG
    directly from the phone (camera_url); this endpoint supplies JARVIS's
    detections so the HUD can draw bounding boxes/labels/identity on top, scaled
    from the analysed frame (frame_w/frame_h) to the displayed video size.
    """
    try:
        from ambient_vision import shared_optical_cache as c
        return {
            "camera_active": bool(c.get("camera_active")),
            "camera_url": c.get("camera_url"),
            "frame_w": c.get("frame_w", 0),
            "frame_h": c.get("frame_h", 0),
            "detections": c.get("detections", []),
            "objects_in_view": sorted(c.get("objects_in_view", set())),
            "people_in_view": sorted(c.get("people_in_view", set())),
            "dominant_emotion": c.get("dominant_emotion", "neutral"),
            "intruder_detected": bool(c.get("intruder_detected")),
            "last_updated": c.get("last_updated", 0),
        }
    except Exception as e:
        print(f"[API] Vision state error: {e}")
        return {"camera_active": False, "camera_url": None, "detections": [], "error": str(e)}

@app.get("/api/gesture/state")
async def gesture_state_api():
    """Gesture/presence daemon state for the HUD (mirrors the ws gesture_state frames)."""
    try:
        from gesture_daemon import gesture_state
        return dict(gesture_state)
    except Exception as e:
        return {"state": "unavailable", "error": str(e)}

@app.get("/api/health/summary")
async def health_summary():
    """Returns Google Fit health data (steps, hr) for the frontend widget."""
    try:
        from modules.health_agent import HealthAgent, is_health_available
        if not is_health_available():
            return {"configured": False, "steps": 0, "heart_rate": 0}
        agent = HealthAgent()
        data = await asyncio.to_thread(agent.get_today_health_data)
        return data
    except Exception as e:
        print(f"[API] Health error: {e}")
        return {"configured": False, "steps": 0, "heart_rate": 0, "error": str(e)}

@app.get("/api/actions/runtime")
async def actions_runtime():
    """Returns ActionEngine runtime state and recent trace events."""
    try:
        return await asyncio.to_thread(engine.get_runtime_telemetry)
    except Exception as e:
        return {"state": "UNKNOWN", "trace_id": None, "recent_traces": [], "error": str(e)}

@app.get("/api/governance/status")
async def governance_status():
    """Returns the current state of the Governance Engine (Phase 6).
    Useful for frontend indicators and regression tests."""
    try:
        return governance_manager.get_status()
    except Exception as e:
        return {"rules_loaded": 0, "has_pending": False, "pending_action": None, "error": str(e)}

@app.post("/api/governance/cancel")
async def governance_cancel():
    """Allows the frontend to programmatically cancel a pending CONFIRM-tier action."""
    cancelled = governance_manager.cancel_pending()
    return {"cancelled": cancelled}

# ── Roadmap §1.1: Autonomous Task Queue endpoints ───────────────────────────
class TaskRequest(BaseModel):
    title: str
    actions: list[dict] = []
    user: str = "KAUSTAV"

@app.post("/api/tasks")
async def create_task(req: TaskRequest):
    """Queue a goal for the Overnight Worker Loop to pursue autonomously."""
    tid = await asyncio.to_thread(task_queue.enqueue, req.title, req.actions, req.user)
    return {"success": True, "task_id": tid}

@app.get("/api/tasks")
async def list_tasks(status: str | None = None, limit: int = 50):
    """List queued/finished tasks (optionally filtered by status)."""
    items = await asyncio.to_thread(task_queue.list_tasks, status, limit)
    return {"tasks": items, "count": len(items)}

@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a pending or running task."""
    cancelled = await asyncio.to_thread(task_queue.cancel, task_id)
    return {"cancelled": cancelled}

# ── Phase 3: Overnight Autopilot (Figma → code via LangGraph) ───────────────
class AutopilotRequest(BaseModel):
    file_key: str
    out_dir: str = "autopilot_output"
    token: str | None = None

@app.post("/api/autopilot")
async def start_autopilot(req: AutopilotRequest):
    """Launch the LangGraph Figma→code pipeline as a background task (never blocks the loop)."""
    try:
        from modules import agent_worker  # lazy: keeps langgraph out of startup path
    except Exception as e:
        return {"success": False, "error": f"Autopilot unavailable: {e}"}

    async def _bcast(payload):
        await send_ui_update(payload)

    async def _speak(text):
        await speaker.speak_text(text)

    agent_worker.launch_autopilot(
        req.file_key, out_dir=req.out_dir, token=req.token, broadcast=_bcast, speak=_speak
    )
    return {"success": True, "message": "Overnight Autopilot launched in the background, Sir."}

class UIStateRequest(BaseModel):
    status: str
    message: str = ""
    user: str = "KAUSTAV"

@app.get("/api/context")
async def get_context():
    """Current presence/context state (WORKING/RELAXING/AWAY/ASLEEP/IDLE) — §2.3."""
    from modules.context_state import context_state
    secs = None
    if _last_command_time is not None:
        secs = (_dt.datetime.now() - _last_command_time).total_seconds()
    state = context_state.current(seconds_since_command=secs)
    return {"state": state, "since": context_state.changed_at}


@app.post("/api/ui_state")
async def update_ui_state(req: UIStateRequest):
    """Allows external daemons (like the Phase 8 streaming daemon) to update the React UI."""
    payload = {"status": req.status, "message": req.message, "user": req.user}
    await send_ui_update(payload)
    return {"success": True}

# ════════════════════════════════════════════════════════════════════════════
# CHANNEL-SCOPED REMOTE COMMAND CORE (Concurrent Session Scoping)
# ════════════════════════════════════════════════════════════════════════════
# Non-HUD transports (the Telegram gateway, future remote channels) drive
# J.A.R.V.I.S. through THIS function. It routes through the exact same brain
# (process_command → llm_router) and ActionEngine (execute_with_retry) as the
# voice/HUD path, but every byte of output is delivered through the caller's
# `channel` only — never send_ui_update (the desk HUD broadcast) and never
# speaker.speak_text (the desk speakers). It also never mutates the global
# `active_user`. That is what keeps a phone reply off the desk and a desk
# identity untouched by a phone message — no crossed streams.
#
# Engine state (GUI focus, pending slots, trace ring) is shared, so each engine
# action is taken under COMMAND_LOCK; a HUD conversation and a Telegram
# conversation therefore interleave safely instead of racing.

# Data-producing actions whose raw output must be run through synthesis before
# it is fit to show a human (mirrors the HUD path's DATA_ACTIONS set).
_REMOTE_DATA_ACTIONS = frozenset({
    "web_search", "tavily_search", "web_browse", "read_email", "check_email",
    "check_calendar", "check_vitals", "read_screen", "gmail_read_unread",
    "gmail_read", "system_status", "get_telemetry", "search_documents",
})


async def run_remote_command(command_text: str, channel) -> None:
    """Execute a text command for a non-HUD channel and reply on that channel only."""
    user = (getattr(channel, "user", None) or "KAUSTAV")
    kind = getattr(channel, "kind", "remote")
    # Phase 4.5: permission tier rides on the channel (admin desk/HUD + Telegram
    # admin → ADMIN_TIER; Telegram VIP guests → vip_guest). It gates every path
    # below that can drive the ActionEngine. ADMIN_TIER is the safe default so
    # the HUD and any channel that omits a tier remain unrestricted.
    tier = getattr(channel, "permission_tier", ADMIN_TIER)
    # How to address this caller in generic fallback lines (the brain/synthesis
    # already honour persona; this only covers the bare "Done"/"Standing by" tails).
    honor = getattr(channel, "honorific", "Sir")
    print(f"\n[REMOTE:{kind}] Command from {user} (tier={tier}): {command_text}", flush=True)

    # Background memory extraction — identical to the HUD path.
    try:
        asyncio.create_task(asyncio.to_thread(extract_and_store_memory, command_text, user))
    except Exception:
        pass

    # ── Phase 4 item 3: session-scoped governance confirmation (remote) ─────
    # If THIS channel was asked to authorise a CONFIRM-tier action, a short
    # yes/no answers that question. The staged action is keyed by its
    # confirmation_id inside this channel's session, so a phone approval can
    # never resolve a desk confirmation (or another chat's) — and vice versa.
    sess = await SESSIONS.get_or_create(channel)
    _gov_pending = sess.pending.get("governance")
    if _gov_pending:
        _low = command_text.lower().strip()
        _is_approval = any(w in _low for w in _APPROVAL_WORDS)
        _is_denial = any(w in _low for w in _DENIAL_WORDS)
        _is_decision = ((_is_approval or _is_denial) and len(_low) < 60
                        and not any(w in _low for w in _jarvis_command_words))
        if _is_decision:
            sess.pending.pop("governance", None)
            cid = _gov_pending.get("cid")
            conf_atype = _gov_pending.get("atype", "unknown")
            if _is_approval and tier == ADMIN_TIER:
                approved_payload = governance_manager.consume_pending(cid)
                if approved_payload is None:
                    await channel.reply(
                        f"That authorisation window has expired, {honor} — "
                        f"ask me again and I'll re-stage it."
                    )
                    return
                print(f"[REMOTE:{kind}] ✅ {user} approved '{conf_atype}' (id={cid}) — executing.", flush=True)
                trace_id = engine.new_trace_id()
                async with COMMAND_LOCK:
                    exec_meta = await engine.execute_with_retry(
                        approved_payload, True, trace_id,
                        governance_bypass=True, permission_tier=tier,
                    )
                result = exec_meta.get("result", exec_meta) if isinstance(exec_meta, dict) else exec_meta
                result_str = str(result)
                spoken = _sanitize_for_speech(conf_atype, result_str) or result_str
                await channel.reply(spoken or f"Done, {honor}.")
            else:
                governance_manager.cancel_pending(cid)
                await channel.reply(f"Cancelled, {honor}. Standing by.")
            return
        # A new, unrelated command supersedes the open question — cancel the
        # staged action so a stray "yes" minutes later can't run it out of
        # context, then process the new command normally.
        governance_manager.cancel_pending(_gov_pending.get("cid"))
        sess.pending.pop("governance", None)
        print(f"[REMOTE:{kind}] Pending confirmation superseded by a new command — cancelled.", flush=True)

    # ── Phase 4 item 5: queued-task approve/deny (deterministic, admin-only) ─
    # A worker task paused on a CONFIRM step reports "say 'approve task <id>'".
    # Resolve that phrase here, before any LLM, so authorisation is exact.
    _task_m = _TASK_APPROVAL_RE.match(command_text.strip().lower())
    if _task_m:
        if tier != ADMIN_TIER:
            await channel.reply(
                "I'm afraid I cannot perform that action without direct authorization from Sir."
            )
            return
        _verb, _tprefix = _task_m.group(1), _task_m.group(2)
        matches = await asyncio.to_thread(task_queue.find_awaiting_confirmation, _tprefix)
        if not matches:
            await channel.reply(
                f"No task awaiting authorisation matches '{_tprefix}', {honor}."
            )
            return
        if len(matches) > 1:
            ids = ", ".join(m["id"][:8] for m in matches)
            await channel.reply(
                f"That matches several waiting tasks ({ids}) — give me more of the id, {honor}."
            )
            return
        _t = matches[0]
        if _verb in _TASK_APPROVE_VERBS:
            ok = await asyncio.to_thread(task_queue.approve_task, _t["id"])
            await channel.reply(
                f"Authorised, {honor} — resuming '{_t['title']}' in the background. "
                f"I'll report when it's done."
                if ok else
                f"I couldn't resume that task, {honor} — it may have been cancelled already."
            )
        else:
            await asyncio.to_thread(task_queue.cancel, _t["id"])
            await channel.reply(f"Dropped, {honor} — '{_t['title']}' will not run.")
        return

    # ── Deterministic fast-lane (Roadmap §3.4) — skip the LLM entirely ───────
    _fp = fast_path.match(command_text)
    if _fp is not None:
        fp_action = _fp.get("action")
        if fp_action:
            # Tier gate: a restricted caller can never run a fast-path *action*
            # (mute/lock/media all touch the host). Say-only fast paths (time/
            # date) carry no action and fall through to the reply below.
            if not tier_allows(tier, fp_action.get("action_type", "")):
                await channel.reply(
                    "I'm afraid I cannot perform that action without direct authorization from Sir."
                )
                return
            async with COMMAND_LOCK:
                await engine.execute_with_retry(fp_action, True, None, permission_tier=tier)
        await channel.reply(_fp.get("say") or f"Done, {honor}.")
        return

    # ── ReAct planner fast-path bypass (Roadmap §1.2) ────────────────────────
    # Only CLEARLY multi-step goals enter the heavy Think→Act→Observe loop;
    # simple commands fall straight through to the low-latency single-shot path.
    # Tier gate: the ReAct planner runs an unbounded multi-step Think→Act→Observe
    # loop straight against the engine (autopilot-class). It is admin-only — a
    # VIP guest's complex request falls through to the single-shot path, where
    # each produced action is filtered against their allowlist.
    if tier == ADMIN_TIER and planner.should_plan(command_text):
        print(f"[REMOTE:{kind}] Complex goal → ReAct planner.", flush=True)
        try:
            outcome = await planner.run_react(
                command_text, user, engine.execute_with_retry, notify=channel.notify
            )
            await channel.reply(outcome.get("final_answer") or "Done, Sir.")
            return
        except Exception as e:
            print(f"[REMOTE] Planner fault, falling back to single-shot: {e}", flush=True)
            # Fall through to the standard path on any planner error.

    try:
        llm_response = await asyncio.to_thread(process_command, command_text, user)
    except Exception as e:
        print(f"[REMOTE] Brain fault: {e}", flush=True)
        await channel.reply(f"I encountered a fault reaching my reasoning core, {honor}.")
        return

    # Unified parse spine — tolerant of fences, prose, bare/singular/array
    # shapes, trailing commas and truncation (see modules/action_parser.py).
    _parsed = action_parser.parse(llm_response)
    clean_response = _parsed.preamble or action_parser.strip_fences(llm_response).strip()

    # Pure conversational reply (no actions).
    if not _parsed.is_action:
        spoken = clean_response
        if not spoken or spoken.lstrip().startswith(("{", "[")):
            spoken = f"Standing by, {honor}."
        await channel.reply(spoken)
        return

    actions = _normalize_os_control_batch(_parsed.actions, command_text)

    # ── Phase 4.5: tier pre-filter (refuse the WHOLE batch atomically) ───────
    # Before touching the engine, verify every action is in-tier. If a VIP guest
    # asks for anything privileged, we refuse the entire turn here — so a blocked
    # action can never run as a side effect of an otherwise-allowed batch, and no
    # engine state is mutated at all. (The engine re-checks per action as
    # defence-in-depth, but this keeps the refusal clean and all-or-nothing.)
    if tier != ADMIN_TIER:
        for a in actions:
            if not tier_allows(tier, a.get("action_type", "")):
                print(f"[REMOTE:{kind}] ⛔ Tier refusal — '{a.get('action_type','')}' not permitted for {user}.", flush=True)
                await channel.reply(
                    "I'm afraid I cannot perform that action without direct authorization from Sir."
                )
                return

    batched_data: list[tuple[str, str]] = []
    replied = False

    try:
        for intent_json in actions:
            atype = intent_json.get("action_type", "")
            trace_id = engine.new_trace_id()
            # Serialise the shared engine across channels.
            async with COMMAND_LOCK:
                exec_meta = await engine.execute_with_retry(
                    intent_json, True, trace_id, permission_tier=tier
                )
            result = exec_meta.get("result", exec_meta) if isinstance(exec_meta, dict) else exec_meta
            result_str = str(result)

            # ── Phase 4.5: tier-refusal sentinel (defence-in-depth) ───────────
            if isinstance(result, str) and result.startswith(TIER_BLOCKED_PREFIX):
                await channel.reply(
                    "I'm afraid I cannot perform that action without direct authorization from Sir."
                )
                replied = True
                continue

            # ── Governance sentinels ──────────────────────────────────────────
            if isinstance(result, str) and result.startswith("GOVERNANCE_BLOCKED:"):
                blocked = result.split(":", 1)[1] if ":" in result else "unknown"
                await channel.reply(
                    f"That action is blocked by governance policy, Sir. "
                    f"'{blocked}' is classified as high-risk and cannot be executed."
                )
                replied = True
                continue
            if isinstance(result, str) and result.startswith("GOVERNANCE_CONFIRM:"):
                # Format: GOVERNANCE_CONFIRM:<action_type>:<confirmation_id>
                parts = result.split(":", 2)
                conf_action = parts[1] if len(parts) > 1 else "unknown"
                conf_id = parts[2] if len(parts) > 2 else None
                if tier == ADMIN_TIER and conf_id:
                    # Phase 4 item 3: a remote CONFIRM is now a session-scoped
                    # question instead of a dead end. The staged action waits
                    # (90s TTL) keyed by its confirmation_id in THIS channel's
                    # session; the next short yes/no from this channel resolves
                    # exactly this action and nothing else.
                    prev = sess.pending.get("governance")
                    if prev:
                        governance_manager.cancel_pending(prev.get("cid"))
                    sess.pending["governance"] = {"cid": conf_id, "atype": conf_action}
                    await channel.reply(
                        f"Authorisation required, {honor}: '{conf_action}' is a "
                        f"CONFIRM-tier action. Reply 'confirm' to execute it or "
                        f"'cancel' to drop it."
                    )
                else:
                    # Non-admin caller (or malformed sentinel): refuse and clear
                    # so it can't be approved later out of context.
                    governance_manager.cancel_pending(conf_id)
                    await channel.reply(
                        f"Authorisation required for '{conf_action}' — that "
                        f"action needs Sir's direct approval, which I can't "
                        f"accept on this channel."
                    )
                replied = True
                continue

            # ── Result routing ────────────────────────────────────────────────
            if atype == "memory_recall":
                if result_str.strip().startswith("[DEEP_MEMORY_DATA]"):
                    batched_data.append(
                        ("memory_recall", result_str.strip()[len("[DEEP_MEMORY_DATA]"):].strip())
                    )
                else:
                    batched_data.append(("memory_recall", result_str))
            elif atype in _REMOTE_DATA_ACTIONS:
                batched_data.append((atype, result_str))
            elif result_str.startswith("[BRIEFING_DATA]"):
                batched_data.append(("briefing", result_str[len("[BRIEFING_DATA]"):].strip()))
            elif atype in SILENT_PIPELINE_ACTIONS:
                # Intermediate pipeline step — stay quiet on remote channels too.
                print(f"[REMOTE] Silent pipeline ({atype}): {result_str[:80]}", flush=True)
            else:
                spoken = _sanitize_for_speech(atype, result_str)
                text = spoken if spoken else result_str
                if text and text.strip():
                    await channel.reply(text)
                    replied = True

        # ── Synthesise all data-producing results into one clean message ──────
        if batched_data:
            combined_raw = "\n---\n".join(f"[{at}]: {d}" for at, d in batched_data)
            try:
                final_answer = await asyncio.to_thread(
                    synthesize_info, command_text, combined_raw, user
                )
            except Exception as e:
                print(f"[REMOTE] Synthesis fault: {e}", flush=True)
                final_answer = combined_raw
            if final_answer and final_answer.strip():
                await channel.reply(final_answer)
                replied = True

        if not replied:
            await channel.reply(f"Done, {honor}.")

    except Exception as e:
        print(f"[REMOTE] Execution fault: {e}", flush=True)
        import traceback
        try:
            with open("error.log", "a", encoding="utf-8") as errf:
                errf.write(traceback.format_exc() + "\n")
        except Exception:
            pass
        await channel.reply(f"I encountered an execution fault, {honor}.")


# ── Injected callbacks for remote channels (Telegram /task, /tasks, /status) ──
async def remote_queue_goal(goal_text: str, user: str = "KAUSTAV") -> tuple:
    """Plan an NL goal into action payloads and enqueue it for the Overnight Worker.

    Returns (task_id, n_actions); (None, 0) if no plan could be formed.
    """
    try:
        llm_response = await asyncio.to_thread(process_command, goal_text, user)
    except Exception as e:
        print(f"[REMOTE] queue_goal planning fault: {e}", flush=True)
        return (None, 0)
    actions = _normalize_os_control_batch(action_parser.extract_actions(llm_response), goal_text)
    if not actions:
        return (None, 0)
    tid = await asyncio.to_thread(task_queue.enqueue, goal_text[:80], actions, user)
    return (tid, len(actions))


async def remote_list_tasks() -> list:
    return await asyncio.to_thread(task_queue.list_tasks, None, 20)


async def remote_status() -> str:
    try:
        gov = governance_manager.get_status()
        pending = gov.get("pending") or gov.get("has_pending") or "none"
    except Exception:
        pending = "unknown"
    state = "online" if SYSTEM_ONLINE else "standby"
    return (
        f"J.A.R.V.I.S. is {state}, Sir. "
        f"Active sessions: {SESSIONS.active_count()}. "
        f"Governance pending: {pending}."
    )


@app.post("/api/backdoor")
async def backdoor_command(req: BackdoorRequest):
    global active_user, _last_command_time
    command_text = req.command
    _last_command_time = _dt.datetime.now()
    print(f"\n[BACKDOOR] Received command: {command_text}")
    
    async def safe_send_all(payload):
        await send_ui_update(payload)

    _cmd_lower = command_text.lower().strip()

    # ── Phase 6: GOVERNANCE CONFIRMATION INTERCEPT ──────────────────────────────
    # If a CONFIRM-tier action is waiting in the pending slot AND this command
    # looks like an approval/denial (short, no Jarvis-command words), resolve it
    # immediately BEFORE any other intercept layer.
    if governance_manager.has_pending() or _DESK_PENDING["cid"] is not None:
        _gov_lower = _cmd_lower
        _is_approval = any(w in _gov_lower for w in _APPROVAL_WORDS)
        _is_denial   = any(w in _gov_lower for w in _DENIAL_WORDS)
        _looks_short = len(_cmd_lower) < 60
        _no_cmd_words = not any(w in _gov_lower for w in _jarvis_command_words)

        if (_is_approval or _is_denial) and _looks_short and _no_cmd_words:
            if _is_approval:
                # Phase 4 item 4: resolve the DESK's own confirmation id (falls
                # back to the single slot only when no id was pinned) so this
                # "yes" can never run an action pended by a remote channel.
                approved_payload = governance_manager.consume_pending(_DESK_PENDING["cid"])
                _DESK_PENDING["cid"] = None
                if approved_payload:
                    atype = approved_payload.get("action_type", "unknown")
                    print(f"[GOVERNANCE] ✅ User approved '{atype}' — executing now.", flush=True)
                    await safe_send_all({"status": "processing_llm", "message": f"Executing authorised action: {atype}…"})
                    trace_id = engine.new_trace_id()
                    exec_meta = await engine.execute_with_retry(
                        approved_payload,
                        True,
                        trace_id,
                        governance_bypass=True,
                    )
                    result = exec_meta.get("result", exec_meta) if isinstance(exec_meta, dict) else exec_meta
                    result_str = str(result)
                    await safe_send_all({"status": "complete", "result": result_str})
                    spoken = _sanitize_for_speech(atype, result_str) or result_str
                    asyncio.create_task(speaker.speak_text(spoken))
                else:
                    msg = "No pending action to authorise, Sir."
                    await safe_send_all({"status": "complete", "result": msg})
                    asyncio.create_task(speaker.speak_text(msg))
            else:
                governance_manager.cancel_pending(_DESK_PENDING["cid"])
                _DESK_PENDING["cid"] = None
                msg = "Action cancelled, Sir. Standing by."
                await safe_send_all({"status": "complete", "result": msg})
                asyncio.create_task(speaker.speak_text(msg))
            return {"status": "success"}
    # ─────────────────────────────────────────────────────────────────────

    # --- PENDING NOTEPAD/FILE DECISION INTERCEPT ---
    # Only treat a command as a decision response if it actually looks like one.
    # Regular JARVIS commands (e.g. "clear the display", "Jarvis, check my email")
    # must NEVER be swallowed here — they should clear stale state and proceed.
    _looks_like_decision = (
        any(w in _cmd_lower.split() for w in _decision_words)
        and not any(w in _cmd_lower for w in _jarvis_command_words)
        and len(_cmd_lower) < 50
    )

    if engine._pending_notepad_decision is not None:
        if _looks_like_decision:
            result_msg = await asyncio.to_thread(engine.resolve_pending_notepad_decision, command_text)
            await safe_send_all({"status": "complete", "result": result_msg})
            asyncio.create_task(speaker.speak_text(result_msg))
            return {"status": "success"}
        else:
            print(f"[BACKDOOR] Cleared stale pending notepad decision (command not a decision: '{command_text[:40]}')")
            engine._pending_notepad_decision = None

    if engine._pending_save_decision is not None:
        if _looks_like_decision:
            result_msg = await asyncio.to_thread(engine.resolve_pending_save, command_text)
            await safe_send_all({"status": "complete", "result": result_msg})
            asyncio.create_task(speaker.speak_text(result_msg))
            return {"status": "success"}
        else:
            print(f"[BACKDOOR] Cleared stale pending save decision (command not a decision: '{command_text[:40]}')")
            engine._pending_save_decision = None

    # --- INTRODUCE YOURSELF PROTOCOL ---
    self_intro_phrases = ["introduce yourself", "who are you", "what is your name"]
    command_lower = command_text.lower().strip()
    if any(phrase in command_lower for phrase in self_intro_phrases) or command_lower == "what are you":
        # The frontend handles the visual display based on the "introduce_yourself" status
        await safe_send_all({"status": "introduce_yourself", "message": "INITIATING SELF-INTRODUCTION..."})
        intro_text = "Allow me to introduce myself. I am J.A.R.V.I.S., the virtual artificial intelligence. I am here to assist you with a variety of tasks as best I can. 24 hours a day, 7 days a week. Importing all preferences from home interface. Systems are now fully operational."
        # Add a delay to sync with the typing effect on the frontend
        await asyncio.sleep(1.0)
        asyncio.create_task(speaker.speak_text(intro_text))
        
        # We don't send offline here. The frontend's onComplete handles the visual transition to offline.
        return {"status": "success"}

    # --- FIX: Intercept "wake up" to properly trigger the UI widgets
    import difflib
    command_clean = command_text.lower().strip()
    wake_words = [
        "wake up",
        "admin override",
        "wakeup",
        "boot up",
        "system online",
        "wake him up",
        "wake her up",
        "jarvis wake up",
        "wake jarvis",
        "wake the system",
        "come online",
        "power on",
    ]
    is_wake = any(word in command_clean for word in wake_words)
    # "wake him up", "wake jarvis up", etc. (wake … up) without literal substring "wake up"
    if not is_wake and re.search(r"\bwake\b.*\bup\b", command_clean):
        is_wake = True
    if not is_wake and len(command_clean.split()) <= 4:
        matches = difflib.get_close_matches(
            command_clean,
            ["wake up", "wakeup", "wake him up", "boot up"],
            n=1,
            cutoff=0.55,
        )
        if matches:
            is_wake = True
        
    if is_wake:
        await safe_send_all({"status": "booting", "message": "[SYSTEM] ADMIN OVERRIDE ACCEPTED. INITIATING BOOT...", "user": active_user})
        await asyncio.sleep(1.0)

        # Sleep/wake continuity: re-seed short-term memory with the last session digest.
        await asyncio.to_thread(memory.seed_from_last_digest, active_user)

        weather = await sensors.get_weather_data()
        briefing_text = await asyncio.to_thread(_smart_briefing, weather, command_text, active_user)
        
        await safe_send_all({"status": "waking", "message": briefing_text, "user": active_user})
        asyncio.create_task(speaker.speak_text(briefing_text))

        # After booting, return to online status
        await asyncio.sleep(2)
        # Report any goals the Overnight Worker finished while away (Roadmap §1.1)
        if overnight_worker:
            await overnight_worker.report_pending(active_user)
        await safe_send_all({"status": "online", "user": active_user})
        return {"status": "success"}

    # --- FIX: Intercept Sleep Commands from Backdoor ---
    sleep_phrases = ["go to sleep", "shut down", "lock the system", "sleep now", "stand down", "power down"]
    if any(x in command_lower for x in sleep_phrases) or command_lower == "sleep":
        await safe_send_all({"status": "close_search", "message": "CLEARING DISPLAY."})
        await safe_send_all({"status": "toggle_browser", "visible": False})
        await safe_send_all({"status": "offline", "message": "SYSTEM OFFLINE."})

        # Consolidate this session into a persistent digest BEFORE wiping short-term memory.
        await asyncio.to_thread(memory.consolidate_working_memory, active_user)
        memory.clear_working_memory()

        sign_offs = [
            "Powering down. Have a good evening.",
            "Going offline. I will be here when you need me.",
            "Entering standby mode. Goodnight.",
            "As you wish. Shutting down non-essential systems."
        ]
        chosen = random.choice(sign_offs)
        await safe_send_all({"status": "speaking", "message": chosen, "user": active_user})
        await speaker.speak_text(chosen)
        return {"status": "success"}

    # --- Chat transcript panel toggle (UI-only; default hidden) ---
    if any(p in _cmd_lower for p in _CHAT_HIDE_PHRASES):
        await safe_send_all({"status": "toggle_chat", "visible": False})
        asyncio.create_task(speaker.speak_text("Transcript hidden, Sir."))
        return {"status": "success"}
    if any(p in _cmd_lower for p in _CHAT_SHOW_PHRASES):
        await safe_send_all({"status": "toggle_chat", "visible": True})
        asyncio.create_task(speaker.speak_text("Transcript on the HUD, Sir."))
        return {"status": "success"}

    # --- Background-queue status report ("what are you working on?") ---
    if any(p in _cmd_lower for p in _QUEUE_STATUS_PHRASES):
        report = await asyncio.to_thread(task_queue.spoken_status_report)
        await safe_send_all({"status": "complete", "result": report})
        asyncio.create_task(speaker.speak_text(report))
        return {"status": "success"}

    # --- Phase 8.4: deterministic deep-work + UI bridge (backdoor / CI; also emits ui_state from MacroAgent) ---
    if _cmd_lower == "test:deep_work_ui":
        trace_id = engine.new_trace_id()
        await safe_send_all({"status": "processing_llm", "message": "Running deep work macro (test hook)."})
        exec_meta = await engine.execute_with_retry(
            {"action_type": "os_macro", "target": "deep_work"},
            True,
            trace_id,
        )
        result = exec_meta.get("result", exec_meta) if isinstance(exec_meta, dict) else exec_meta
        result_str = str(result)
        await safe_send_all({"status": "complete", "result": result_str})
        spoken = _sanitize_for_speech("os_macro", result_str)
        asyncio.create_task(speaker.speak_text(spoken or "Deep work macro finished, Sir."))
        return {"status": "success"}

    # --- TEST HOOK: force the Comprehensive Morning Briefing on demand ---
    # Replays the full new-day wake sequence WITHOUT needing a date rollover or
    # deleting last_boot_date.txt. Bypasses _smart_briefing's date/recent-activity
    # gating and calls generate_briefing(comprehensive=True) directly so the UX of
    # the morning routine can be debugged at any time.
    if _cmd_lower == "test:morning_briefing":
        await safe_send_all({"status": "booting", "message": "[SYSTEM] TEST HOOK: REPLAYING MORNING BRIEFING...", "user": active_user})
        await asyncio.sleep(1.0)
        weather = await sensors.get_weather_data()
        briefing_text = await asyncio.to_thread(
            generate_briefing, weather, "wake up", active_user, True  # comprehensive=True
        )
        await safe_send_all({"status": "waking", "message": briefing_text, "user": active_user})
        await speaker.speak_text(briefing_text)
        await asyncio.sleep(1.0)
        await safe_send_all({"status": "online", "user": active_user})
        return {"status": "success"}

    # --- TEST HOOK: enqueue a sample autonomous task (Roadmap §1.1) ---
    # Usage: "test:enqueue_task"  OR  "test:enqueue_task: <search query>"
    # Proves the full agentic loop: queue → worker executes → reports back.
    if _cmd_lower.startswith("test:enqueue_task"):
        _q = (
            command_text.split(":", 2)[2].strip()
            if command_text.count(":") >= 2 and command_text.split(":", 2)[2].strip()
            else "latest AI research breakthroughs this week"
        )
        _tid = await asyncio.to_thread(
            task_queue.enqueue,
            f"Research: {_q}",
            [{"action_type": "web_search", "target": _q}],
            active_user,
        )
        msg = f"Task queued, Sir. I'll look into that and report back shortly."
        await safe_send_all({"status": "complete", "result": f"{msg} (task {_tid}: {_q})"})
        asyncio.create_task(speaker.speak_text(msg))
        return {"status": "success"}

    # --- Queue an Overnight Autopilot build for the worker (Phase 3 × §1.1) ---
    # Usage: "queue:autopilot:<figma_file_key>"  or  "<key>|<out_dir>"
    if _cmd_lower.startswith("queue:autopilot"):
        _arg = command_text.split(":", 2)[2].strip() if command_text.count(":") >= 2 else ""
        if not _arg:
            _m = "I need a Figma file key to queue the build, Sir."
            await safe_send_all({"status": "complete", "result": _m})
            asyncio.create_task(speaker.speak_text(_m))
            return {"status": "success"}
        _tid = await asyncio.to_thread(
            task_queue.enqueue,
            f"Figma autopilot build: {_arg.split('|')[0]}",
            [{"action_type": "run_autopilot", "target": _arg}],
            active_user,
        )
        _m = "Overnight build queued, Sir. I'll have it ready and report back."
        await safe_send_all({"status": "complete", "result": f"{_m} (task {_tid})"})
        asyncio.create_task(speaker.speak_text(_m))
        return {"status": "success"}

    # --- INTRODUCTION CEREMONY: Special VIP Protocol ---
    intro_triggers = ["introduce mousumi", "introduce her", "vip protocol", "introduction ceremony"]
    if any(trigger in command_text.lower().strip() for trigger in intro_triggers):
        # Phase 1: Trigger the cinematic overlay on the frontend
        await safe_send_all({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": "MOUSUMI"})
        
        # Phase 2: Wait for the visual sequence to build up (reactor pulse + text reveal)
        await asyncio.sleep(5.0)
        
        # Phase 3: The Introduction Speech
        intro_speech = (
            "Initiating V.I.P. Protocol. "
            "[pause:1200] "
            "Good evening, Miss Mousumi. "
            "[pause:800] "
            "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
            "[pause:600] "
            "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
            "security protocols, and environmental controls. "
            "[pause:1000] "
            "I have heard a great deal about you from Sir. "
            "[pause:400] "
            "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
            "into our home. "
            "[pause:800] "
            "From this moment forward, consider me entirely at your service. "
            "[pause:400] "
            "Whatever you need, whenever you need it, I shall be here. "
            "[pause:600] "
            "Welcome home, Miss Mousumi."
        )
        
        await safe_send_all({"status": "speaking", "message": intro_speech, "user": "MOUSUMI"})
        await speaker.speak_text(intro_speech)
        
        # Phase 4: Dismiss the ceremony overlay
        await asyncio.sleep(1.5)
        await safe_send_all({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": "MOUSUMI"})
        await asyncio.sleep(1.0)
        await safe_send_all({"status": "online", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": "MOUSUMI"})
        
        return {"status": "success"}

    # --- FIX: Intercept Barge-In commands from the Dev Backdoor ---
    barge_in_words = ["stop", "quiet", "shut up", "jarvis", "cancel", "enough"]
    if any(word == command_text.lower().strip() for word in barge_in_words):
        if speaker.is_system_speaking or interrupt_flag.is_set():
            print("[BACKDOOR] Interruption command intercepted.", flush=True)
            interrupt_flag.set()           # signals any active streaming loop to break
            speaker.stop_audio()           # kills audio currently playing
            await safe_send_all({"status": "online", "user": active_user})
            return {"status": "success"}

    # New valid command — clear any prior barge-in signal so streaming runs freely.
    interrupt_flag.clear()
    await safe_send_all({"status": "processing_llm", "message": command_text})
    
    try:
        asyncio.create_task(asyncio.to_thread(extract_and_store_memory, command_text, active_user))

        # ── Deterministic fast-lane (Roadmap §3.4) — skip the LLM entirely ───
        _fp = fast_path.match(command_text)
        if _fp is not None:
            if _fp.get("action"):
                async with COMMAND_LOCK:
                    await engine.execute_with_retry(_fp["action"], True, None)
            _fp_say = _fp.get("say") or "Done, Sir."
            await safe_send_all({"status": "complete", "result": _fp_say})
            asyncio.create_task(speaker.speak_text(_fp_say))
            return {"status": "success"}

        # ── ReAct planner fast-path bypass (Roadmap §1.2) ────────────────────
        # Multi-step goals run the Think→Act→Observe loop; simple commands fall
        # through to the existing single-shot pipeline (no added latency).
        if planner.should_plan(command_text):
            print("[BACKDOOR] Complex goal → ReAct planner.", flush=True)
            try:
                async def _plan_notify(status, message=""):
                    await safe_send_all({"status": "processing_llm", "message": message or status})
                outcome = await planner.run_react(
                    command_text, active_user, engine.execute_with_retry, notify=_plan_notify
                )
                _final = outcome.get("final_answer") or "Done, Sir."
                await safe_send_all({"status": "complete", "result": _final})
                asyncio.create_task(speaker.speak_text(_final))
                return {"status": "success"}
            except Exception as _pe:
                print(f"[BACKDOOR] Planner fault, falling back to single-shot: {_pe}", flush=True)
                # Fall through to the standard pipeline on any planner error.

        llm_response = await asyncio.to_thread(process_command, command_text, active_user)

        # Unified parse spine — one tolerant extractor for every dispatch path
        # (fences, prose, bare/singular/array shapes, trailing commas, truncation).
        _parsed = action_parser.parse(llm_response)
        clean_response = _parsed.preamble or action_parser.strip_fences(llm_response).strip()
        if not clean_response or clean_response.lstrip().startswith(("{", "[")):
            # Leftover raw JSON (e.g. empty actions) — never speak it.
            clean_response = ""
        json_match = _parsed.is_action

        if json_match:
            try:
                actions = _normalize_os_control_batch(
                    _parsed.actions, command_text
                )

                if _parsed.preamble:
                    print(
                        f"[MAIN] Silence protocol: dropped JSON-adjacent preamble "
                        f"({len(_parsed.preamble)} chars)",
                        flush=True,
                    )

                if _actions_include_satellite(actions):
                    await safe_send_all(
                        {"status": "satellite_uplink", "message": "ESTABLISHING SATELLITE LINK…", "result": ""}
                    )
                else:
                    await safe_send_all({"status": "close_search", "message": "Clearing satellite display."})

                # --- BATCHED ACTION ENGINE ---
                DATA_ACTIONS = {
                    "web_search",
                    "tavily_search",
                    "web_browse",
                    "read_email",
                    "check_email",
                    "check_calendar",
                    "check_vitals",
                    "read_screen",
                    "gmail_read_unread",
                    "gmail_read",
                    # Phase 8.6.7: telemetry actions routed through synthesis so Rule 9
                    # (TELEMETRY VERBOSITY RULE) can filter raw snapshots down to a
                    # single spoken metric or a brief health summary.
                    "system_status",
                    "get_telemetry",
                    # Personal-document RAG results synthesised like other data tools.
                    "search_documents",
                    # NOTE: memory_recall is NOT in DATA_ACTIONS — it has its own
                    # dedicated intercept branch below to route [DEEP_MEMORY_DATA]
                    # payloads to _stream_deep_memory_speak, bypassing the standard
                    # 2-sentence synthesis pipeline.
                }
                batched_data = []
                has_web_search = False
                
                for intent_json in actions:
                    atype = intent_json.get("action_type", "")
                    
                    if atype == "read_screen":
                        await safe_send_all({"status": "scanning_screen", "message": "SCANNING OPTICAL FEED..."})
                        await asyncio.sleep(1.0)
                        
                    trace_id = engine.new_trace_id()
                    await safe_send_all({"status": "executing", "intent": intent_json, "trace_id": trace_id})
                    exec_meta = await engine.execute_with_retry(intent_json, True, trace_id)
                    result = exec_meta.get("result", exec_meta) if isinstance(exec_meta, dict) else exec_meta

                    # ── DEEP MEMORY INTERCEPT — must check BEFORE the DATA_ACTIONS batch ──
                    if atype == "memory_recall":
                        result_str = str(result)
                        if result_str and result_str.strip().startswith("[DEEP_MEMORY_DATA]"):
                            print("[MAIN] Intercepted DEEP_MEMORY_DATA. Routing to Deep Memory Synthesis...", flush=True)
                            deep_payload = result_str.strip()[len("[DEEP_MEMORY_DATA]"):].strip()
                            await _stream_deep_memory_speak(deep_payload, active_user, safe_send_all)
                        else:
                            # Phase 8.6.5: Route plain recall facts through the LLM synthesis
                            # pipeline instead of dumping raw semicolon-separated rows to TTS.
                            # Appending to batched_data lets _stream_synthesize_speak deduplicate
                            # and narrate the retrieved facts as a single, natural sentence.
                            print("[MAIN] Plain memory_recall result — routing to synthesis pipeline.", flush=True)
                            batched_data.append(("memory_recall", result_str))

                    elif atype in DATA_ACTIONS:
                        if atype == "web_search":
                            has_web_search = True
                        batched_data.append((atype, result))

                    # ── Phase 6: Governance sentinel handlers ────────────────────────
                    elif isinstance(result, str) and result.startswith("GOVERNANCE_BLOCKED:"):
                        blocked_action = result.split(":", 1)[1] if ":" in result else "unknown"
                        title = "Madam" if active_user == "MOUSUMI" else "Sir"
                        msg = (
                            f"That action is blocked by governance policy, {title}. "
                            f"'{blocked_action}' is classified as high-risk and cannot be executed."
                        )
                        await safe_send_all({"status": "complete", "result": msg})
                        asyncio.create_task(speaker.speak_text(msg))

                    elif isinstance(result, str) and result.startswith("GOVERNANCE_CONFIRM:"):
                        # Format: GOVERNANCE_CONFIRM:<action_type>:<confirmation_id>
                        parts = result.split(":", 2)
                        conf_action = parts[1] if len(parts) > 1 else "unknown"
                        # Phase 4 item 4: remember WHICH confirmation the desk was
                        # asked about, so the approval resolves this id and not
                        # whatever last landed in the global pending slot.
                        _DESK_PENDING["cid"] = parts[2] if len(parts) > 2 else None
                        title = "Madam" if active_user == "MOUSUMI" else "Sir"
                        msg = (
                            f"Authorisation required, {title}. I would like to execute ‘{conf_action}’. "
                            f"Do you authorise this action? Please say ‘confirm’ or ‘cancel’."
                        )
                        await safe_send_all({"status": "pending_confirmation", "action": conf_action, "result": msg})
                        asyncio.create_task(speaker.speak_text(msg))
                    # ───────────────────────────────────────────────────────────────────

                    elif atype == "web_search_image":
                        if isinstance(result, dict) and result.get("success"):
                            await safe_send_all({"status": "search_result_image", "url": result["url"], "title": result["title"]})
                            asyncio.create_task(speaker.speak_text("Visual data retrieved."))
                        else:
                            asyncio.create_task(speaker.speak_text("Unable to retrieve image."))
                    elif atype == "close_display" or atype == "sleep_protocol":
                        await safe_send_all({"status": "close_search", "message": "CLEARING DISPLAY."})
                        await safe_send_all({"status": "toggle_browser", "visible": False})
                        if atype == "sleep_protocol":
                            asyncio.create_task(speaker.speak_text("Displays cleared and media paused. Goodnight, sir."))
                        else:
                            asyncio.create_task(speaker.speak_text("Display cleared."))
                    elif atype == "open_sticky_note":
                        await safe_send_all({"status": "toggle_notepad", "visible": True})
                        asyncio.create_task(speaker.speak_text("Sticky note opened, sir."))
                    elif atype == "close_sticky_note":
                        await safe_send_all({"status": "toggle_notepad", "visible": False})
                        asyncio.create_task(speaker.speak_text("Sticky note closed."))
                    elif atype == "open_browser":
                        await safe_send_all({"status": "toggle_browser", "visible": True})
                        asyncio.create_task(speaker.speak_text("Browser widget opened, sir."))
                    elif atype == "close_browser":
                        await safe_send_all({"status": "toggle_browser", "visible": False})
                        asyncio.create_task(speaker.speak_text("Browser widget closed."))
                    elif atype == "open_calculator":
                        await safe_send_all({"status": "toggle_calculator", "visible": True})
                        asyncio.create_task(speaker.speak_text("Calculator opened, sir."))
                    elif atype == "close_calculator":
                        await safe_send_all({"status": "toggle_calculator", "visible": False})
                        asyncio.create_task(speaker.speak_text("Calculator closed."))
                    elif atype == "close_app" and result == "HUD_MEDIA_CLOSE_REQUEST":
                        await safe_send_all({"status": "close_search", "message": "Clearing HUD media."})
                        await safe_send_all({"status": "toggle_browser", "visible": False})
                        msg = "Stopped HUD playback, sir."
                        await safe_send_all({"status": "complete", "result": msg})
                        asyncio.create_task(speaker.speak_text(msg))
                    elif atype == "enable_focus_mode":
                        if proactive_agent:
                            from modules.routines import RoutineEngine
                            routine_engine = RoutineEngine(safe_send_all, speaker.speak_text)
                            await routine_engine.enable_focus_mode(proactive_agent)
                        else:
                            # Backdoor path: action_engine already ran and returned result
                            await safe_send_all({"status": "complete", "result": str(result)})
                            asyncio.create_task(speaker.speak_text(str(result)))
                    elif atype == "disable_focus_mode":
                        if proactive_agent:
                            from modules.routines import RoutineEngine
                            routine_engine = RoutineEngine(safe_send_all, speaker.speak_text)
                            await routine_engine.disable_focus_mode(proactive_agent)
                        else:
                            await safe_send_all({"status": "complete", "result": str(result)})
                            asyncio.create_task(speaker.speak_text(str(result)))
                    elif atype in ("workspace_read", "workspace_write", "workspace_patch"):
                        result_str = str(result)
                        # Send raw output to the UI (the user can read it in the chat bubble)
                        await safe_send_all({"status": "complete", "result": result_str})
                        # Speak a clean, persona-appropriate summary — never raw diffs or paths
                        spoken = _sanitize_for_speech(atype, result_str)
                        if spoken:
                            asyncio.create_task(speaker.speak_text(spoken))
                    else:
                        if isinstance(result, dict) and result.get("action_type") == "hud_open_widget":
                            w = result.get("widget", "vitals")
                            await safe_send_all({"type": "ui_state", "open_widget": w})
                            msg = _hud_open_widget_message(w)
                            await safe_send_all({"status": "complete", "result": msg})
                            asyncio.create_task(speaker.speak_text(msg))
                        elif isinstance(result, dict) and result.get("action_type") == "hud_close_widget":
                            w = result.get("widget", "vitals")
                            await safe_send_all({"type": "ui_state", "close_widget": w})
                            msg = "Panel dismissed, sir."
                            await safe_send_all({"status": "complete", "result": msg})
                            asyncio.create_task(speaker.speak_text(msg))
                        elif isinstance(result, dict) and result.get("action_type") == "play_youtube":
                            await safe_send_all({"status": "play_youtube", "url": result["url"]})
                            msg = "Playing via YouTube embed on the HUD, sir."
                            await safe_send_all({"status": "complete", "result": msg})
                            asyncio.create_task(speaker.speak_text(msg))
                        elif atype in SILENT_PIPELINE_ACTIONS:
                            # Intermediate pipeline step — stay silent, no TTS, no chat bubble.
                            print(f"[JARVIS] Silent pipeline ({atype}): {result}")
                        else:
                            result_str = str(result)
                            # ── Phase 8.8: HUD DATA PAYLOAD INTERCEPT ─────────────────
                            # Detect structured JSON payloads from list_directory /
                            # list_processes. Dual-route:
                            #   Route 1 → synthesize_info_gen (hits Rule 10 → spoken 'displayed on screen')
                            #   Route 2 → send_ui_update broadcast (React HUD renders the data table)
                            _ui_action_payload = None
                            if result_str.startswith("{"):
                                try:
                                    _parsed_result = json.loads(result_str)
                                    _ua = _parsed_result.get("ui_action", "")
                                    if _ua in ("render_file_list", "render_process_list", "render_chart"):
                                        _ui_action_payload = _parsed_result
                                except (json.JSONDecodeError, AttributeError):
                                    pass

                            if _ui_action_payload is not None:
                                print(
                                    f"[MAIN] HUD payload intercepted: ui_action='{_ui_action_payload.get('ui_action')}' "
                                    f"({len(_ui_action_payload.get('data', []))} items)",
                                    flush=True,
                                )
                                # Route 2: broadcast raw JSON to React HUD immediately
                                await send_ui_update(_ui_action_payload)
                                # Route 1: synthesize audio (Rule 10 → 'displayed on screen')
                                _spoken_display = await _stream_synthesize_speak(
                                    command_text, result_str, active_user,
                                    safe_send_all, False, sass_index=get_last_sass_index()
                                )
                            # ── BRIEFING SYNTHESIS INTERCEPT ──────────────────────
                            elif result_str.startswith("[BRIEFING_DATA]"):
                                await _stream_briefing_speak(
                                    command_text, result_str, active_user, safe_send_all
                                )
                            elif result_str.startswith("[DEEP_MEMORY_DATA]"):
                                deep_payload = result_str[len("[DEEP_MEMORY_DATA]"):].strip()
                                await _stream_deep_memory_speak(deep_payload, active_user, safe_send_all)
                            else:
                                await safe_send_all({"status": "complete", "result": result_str})
                                # Run result through speech sanitiser; fall back to raw text only
                                # if sanitiser has no opinion (returns None).
                                spoken = _sanitize_for_speech(atype, result_str)
                                if spoken is None:
                                    spoken = result_str
                                asyncio.create_task(speaker.speak_text(spoken))
                    await asyncio.sleep(0.5)
                    
                # --- SYNTHESIZE ALL BATCHED DATA (STREAMING) ---
                if batched_data:
                    combined_raw = "\n---\n".join(f"[{at}]: {str(d)}" for at, d in batched_data)
                    # Phase 8.7: read sass_index from the most recent classify_intent() call
                    _sass = get_last_sass_index()
                    # _stream_synthesize_speak now sends UI updates per-sentence BEFORE TTS,
                    # so no final bulk send is needed for non-web synthesis (it already
                    # progressive-rendered each sentence as it arrived).
                    final_answer = await _stream_synthesize_speak(
                        command_text, combined_raw, active_user,
                        safe_send_all, has_web_search, sass_index=_sass
                    )
                    # Web-search results are already progressively sent inside the function.
                    # For non-web, a final authoritative send ensures the complete text is
                    # committed even if a per-sentence send was dropped.
                    if not has_web_search:
                        await safe_send_all({"status": "complete", "result": final_answer})

            except json.JSONDecodeError:
                await safe_send_all({"status": "speaking", "message": clean_response})
                asyncio.create_task(speaker.speak_text(clean_response))
        else:
            await safe_send_all({"status": "speaking", "message": clean_response})
            asyncio.create_task(speaker.speak_text(clean_response))
    except Exception as e:
        print(f"[ERROR] EXECUTION FAULT: {e}", flush=True)
        import traceback
        try:
            with open("error.log", "a", encoding="utf-8") as errf:
                errf.write(traceback.format_exc() + "\n")
        except Exception as log_err:
            print(f"[ERROR] Could not write to error.log: {log_err}", flush=True)
        await safe_send_all({"status": "error", "message": f"EXECUTION FAULT: {e}"})
        asyncio.create_task(speaker.speak_text(f"I encountered an execution fault, Sir."))

    return {"status": "success"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global active_user, SYSTEM_ONLINE # Allows us to modify the global state based on login
    await websocket.accept()
    register_client(websocket)
    print("UI Connected to WebSocket")
    loop = asyncio.get_running_loop()
    
    async def safe_send(payload):
        if websocket.client_state.value == 1:
            await websocket.send_json(payload)

    def sync_status_update(status_str, message_str):
        if websocket.client_state.value == 1:
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({"status": status_str, "message": message_str}), 
                loop
            )

    try:
        weather = await sensors.get_weather_data()
        if weather:
            await safe_send({"status": "sync", "type": "weather", "data": weather})
        
        # --- Phase 4: Sync real system telemetry to frontend on connect ---
        try:
            telemetry = await asyncio.to_thread(sensors.get_system_telemetry)
            await safe_send({"status": "sync", "type": "telemetry", "data": telemetry})
        except Exception as e:
            print(f"[SYSTEM] Telemetry sync failed: {e}")
        
        while True:
            # ==========================================
            # STAGE 0: DEEP SLEEP & MEMORY WIPE
            # ==========================================
            SYSTEM_ONLINE = False
            # Consolidate the just-ended session into a persistent digest BEFORE wiping,
            # using the user whose session is ending (active_user is reset below).
            try:
                await asyncio.to_thread(memory.consolidate_working_memory, active_user)
            except Exception as _e:
                print(f"[MEMORY] STAGE 0 consolidation skipped: {_e}")
            memory.clear_working_memory()
            active_user = "KAUSTAV" # Default back to Admin when asleep
            
            await safe_send({"status": "offline", "message": "SYSTEM OFFLINE // STANDBY FOR VOICE INPUT"})
            
            wake_phrase = await asyncio.to_thread(wait_for_wake_word)
            if not wake_phrase:
                continue

            # ==========================================
            # STAGE 1A: ADMIN OVERRIDE
            # ==========================================
            if "admin override" in wake_phrase.lower():
                active_user = "KAUSTAV"
                await safe_send({"status": "booting", "message": "[SYSTEM] ADMIN OVERRIDE ACCEPTED. INITIATING BOOT...", "user": active_user})
                await asyncio.sleep(1.0)
                briefing_text = await asyncio.to_thread(_smart_briefing, weather, str(wake_phrase), active_user)
                await safe_send({"status": "waking", "message": briefing_text, "user": active_user})
                await speaker.speak_text(briefing_text)

            # ==========================================
            # STAGE 1B: BIOMETRIC & GUEST BOOT 
            # ==========================================
            else:
                # --- NEW: OPTICAL SCANNER ACTIVATION ---
                await safe_send({"status": "security_locked", "message": "ACTIVATING OPTICAL SENSORS..."})
                
                # Fire and forget speech so he talks while the camera boots up
                asyncio.create_task(speaker.speak_text("Scanning biometrics."))
                
                # Turn on the IP stream for up to 10 seconds to look for a face
                detected_face = await asyncio.to_thread(vision.scan_for_faces, 10)
                
                # --- BIOMETRIC SUCCESS BRANCHES ---
                if detected_face == "KAUSTAV":
                    active_user = "KAUSTAV"
                    welcome_msg = "Facial biometrics recognized. Welcome back, Sir. All primary systems online."
                    await safe_send({"status": "security_locked", "message": welcome_msg})
                    await speaker.speak_text(welcome_msg)
                    
                    await safe_send({"status": "booting", "message": "[SYSTEM] ADMIN ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                    await asyncio.sleep(1.0)
                    briefing_text = await asyncio.to_thread(_smart_briefing, weather, str(wake_phrase), active_user)
                    await safe_send({"status": "waking", "message": briefing_text, "user": active_user})
                    await speaker.speak_text(briefing_text)
                    
                elif detected_face == "KINSHUK":
                    active_user = "KINSHUK" 
                    success_msg = "Biometric match. A very warm welcome to you, Mr. Kinshuk. Master Kaustav mentioned you would be logging in today. It is a distinct privilege to serve the Administrator's brother. I am unlocking the interface for you now, Sir."
                    
                    await safe_send({"status": "security_locked", "message": success_msg})
                    await speaker.speak_text(success_msg)
                    await asyncio.to_thread(memory.remember_fact, "Family", "Kinshuk is your brother. Level 2 Access.")
                    
                    await safe_send({"status": "booting", "message": "[SYSTEM] ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                    await asyncio.sleep(1.0)
                    await safe_send({"status": "waking", "message": "UI UNLOCKED.", "user": active_user})
                    
                elif detected_face == "MOUSUMI":
                    active_user = "MOUSUMI"
                    # --- CINEMATIC INTRODUCTION CEREMONY ---
                    await safe_send({"status": "security_locked", "message": "Biometric match confirmed. Initiating V.I.P. Protocol..."})
                    await speaker.speak_text("Biometric match confirmed.")
                    await asyncio.sleep(0.5)
                    
                    # Trigger the cinematic overlay
                    await safe_send({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": active_user})
                    await asyncio.sleep(5.0)
                    
                    intro_speech = (
                        "Initiating V.I.P. Protocol. "
                        "[pause:1200] "
                        "Good evening, Miss Mousumi. "
                        "[pause:800] "
                        "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
                        "[pause:600] "
                        "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
                        "security protocols, and environmental controls. "
                        "[pause:1000] "
                        "I have heard a great deal about you from Sir. "
                        "[pause:400] "
                        "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
                        "into our home. "
                        "[pause:800] "
                        "From this moment forward, consider me entirely at your service. "
                        "[pause:400] "
                        "Whatever you need, whenever you need it, I shall be here. "
                        "[pause:600] "
                        "Welcome home, Miss Mousumi."
                    )
                    
                    await safe_send({"status": "speaking", "message": intro_speech, "user": active_user})
                    await speaker.speak_text(intro_speech)
                    
                    await asyncio.sleep(1.5)
                    await safe_send({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": active_user})
                    await asyncio.sleep(1.0)
                    await safe_send({"status": "waking", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": active_user})

                # --- FALLBACK: VOICE PROTOCOL ---
                else:
                    challenge_msg = "Optical scan inconclusive. Please state your name."
                    await safe_send({"status": "security_locked", "message": challenge_msg})
                    await speaker.speak_text(challenge_msg)
                    
                    await asyncio.sleep(0.8) # Hardware Breath
                    
                    await safe_send({"status": "security_listening", "message": "AWAITING IDENTIFICATION..."})
                    name_input = await asyncio.to_thread(listen_to_mic, None) 
                    
                    if not name_input or name_input in ["TIMEOUT", "UNKNOWN"]:
                        cancel_msg = "I did not hear a response. Returning to standby."
                        await safe_send({"status": "offline", "message": cancel_msg})
                        await speaker.speak_text(cancel_msg)
                        continue

                    name_lower = name_input.lower()

                    kaustav_aliases = ["kaustav", "koustav", "cost of", "costav", "costab", "kosto", "costo", "cow stuff", "cowstuff", "custard", "kaustubh"]
                    kinshuk_aliases = ["kinshuk", "kingshook", "kinshook", "king shook", "shook", "kings hook", "kin shook", "kingshuk"]
                    mousumi_aliases = ["mousumi", "mausam", "mosumi", "mousami", "mausami", "moshumi", "moosumi", "moosmi", "mo shumi", "my sumi", "mouse me", "mousemi"]

                    # --- BRANCH A: KAUSTAV ---
                    if any(alias in name_lower for alias in kaustav_aliases):
                        active_user = "KAUSTAV"
                        welcome_msg = "Voice print recognized. Welcome back, Sir. All primary systems online."
                        await safe_send({"status": "security_locked", "message": welcome_msg})
                        await speaker.speak_text(welcome_msg)
                        
                        await safe_send({"status": "booting", "message": "[SYSTEM] ADMIN ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                        await asyncio.sleep(1.0)
                        briefing_text = await asyncio.to_thread(_smart_briefing, weather, str(wake_phrase), active_user)
                        await safe_send({"status": "waking", "message": briefing_text, "user": active_user})
                        await speaker.speak_text(briefing_text)

                    # --- BRANCH B: KINSHUK PROTOCOL ---
                    elif any(alias in name_lower for alias in kinshuk_aliases):
                        
                        msg_rel = "Acknowledged. State your relation to the Administrator."
                        await safe_send({"status": "security_locked", "message": msg_rel})
                        await speaker.speak_text(msg_rel)
                        await asyncio.sleep(0.8)
                        
                        await safe_send({"status": "security_listening", "message": "AWAITING RELATION..."})
                        rel_input = await asyncio.to_thread(listen_to_mic, None) 
                        
                        brother_aliases = ["brother", "bother", "rather", "bro"]
                        if rel_input and any(b in rel_input.lower() for b in brother_aliases):
                            msg_pass = "Relation verified. System challenge: Provide the authentication passkey."
                            await safe_send({"status": "security_locked", "message": msg_pass})
                            await speaker.speak_text(msg_pass)
                            await asyncio.sleep(0.8)
                            
                            await safe_send({"status": "security_listening", "message": "AWAITING PASSKEY..."})
                            pass_input = await asyncio.to_thread(listen_to_mic, None) 
                            
                            passkey_aliases = ["brotherhood", "brother hood", "rather hood", "bother hood", "brother would", "brother good"]
                            if pass_input and any(p in pass_input.lower() for p in passkey_aliases):
                                active_user = "KINSHUK" 
                                
                                success_msg = "Passkey accepted. A very warm welcome to you, Mr. Kinshuk. Master Kaustav mentioned you would be logging in today for your inaugural session. It is a distinct privilege to serve the Administrator's brother. Please, make yourself entirely comfortable while I unlock the interface for you, Sir."
                                
                                await safe_send({"status": "security_locked", "message": success_msg})
                                await speaker.speak_text(success_msg)
                                await asyncio.to_thread(memory.remember_fact, "Family", "Kinshuk is your brother. Level 2 Access.")
                                
                                await safe_send({"status": "booting", "message": "[SYSTEM] ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                                await asyncio.sleep(1.0)
                                await safe_send({"status": "waking", "message": "UI UNLOCKED.", "user": active_user})
                            else:
                                await speaker.speak_text("Invalid passkey. Access Denied. Interaction terminated.")
                                continue
                        else:
                            await speaker.speak_text("Relation mismatch. Access Denied. Interaction terminated.")
                            continue

                    # --- BRANCH C: MOUSUMI (CINEMATIC CEREMONY) ---
                    elif any(alias in name_lower for alias in mousumi_aliases):
                        active_user = "MOUSUMI"
                        await safe_send({"status": "security_locked", "message": "Voice print accepted. Initiating V.I.P. Protocol..."})
                        await speaker.speak_text("Voice print accepted.")
                        await asyncio.sleep(0.5)
                        
                        # Trigger the cinematic overlay
                        await safe_send({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": active_user})
                        await asyncio.sleep(5.0)
                        
                        intro_speech = (
                            "Initiating V.I.P. Protocol. "
                            "[pause:1200] "
                            "Good evening, Miss Mousumi. "
                            "[pause:800] "
                            "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
                            "[pause:600] "
                            "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
                            "security protocols, and environmental controls. "
                            "[pause:1000] "
                            "I have heard a great deal about you from Sir. "
                            "[pause:400] "
                            "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
                            "into our home. "
                            "[pause:800] "
                            "From this moment forward, consider me entirely at your service. "
                            "[pause:400] "
                            "Whatever you need, whenever you need it, I shall be here. "
                            "[pause:600] "
                            "Welcome home, Miss Mousumi."
                        )
                        
                        await safe_send({"status": "speaking", "message": intro_speech, "user": active_user})
                        await speaker.speak_text(intro_speech)
                        
                        await asyncio.sleep(1.5)
                        await safe_send({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": active_user})
                        await asyncio.sleep(1.0)
                        await safe_send({"status": "waking", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": active_user})

                    # --- BRANCH D: UNKNOWN ---
                    else:
                        final_denial = "I'm afraid I cannot grant you access. Security protocols have been engaged. Interaction terminated."
                        await safe_send({"status": "security_locked", "message": final_denial})
                        await speaker.speak_text(final_denial)
                        continue 

            # ==========================================
            # STAGE 2: THE CONTINUOUS J.A.R.V.I.S. LOOP
            # ==========================================
            SYSTEM_ONLINE = True
            # Sleep/wake continuity: re-seed fresh working memory with the last
            # session's digest so JARVIS retains immediate context of what we were doing.
            try:
                await asyncio.to_thread(memory.seed_from_last_digest, active_user)
            except Exception as _e:
                print(f"[MEMORY] Wake seed skipped: {_e}")
            # Report Overnight Worker results finished while away (Roadmap §1.1)
            try:
                if overnight_worker:
                    await overnight_worker.report_pending(active_user)
            except Exception as _e:
                print(f"[WORKER] Wake report skipped: {_e}")
            session_active = True
            first_run = True # Tracks if he just booted up
            
            while session_active:
                await safe_send({"status": "online", "message": "SYSTEM ONLINE // STANDBY", "user": active_user})
                
                # Only wait for the wake word if it's not the first run
                if first_run:
                    jarvis_called = True
                else:
                    jarvis_called = await asyncio.to_thread(wait_for_jarvis)
                    
                if jarvis_called:
                    # §1.3 Full-duplex: if the user talked OVER J.A.R.V.I.S., that captured
                    # utterance IS the command — use it directly and skip the "Yes sir?".
                    _fd_pending = pop_pending_utterance()
                    # Do not say "Yes sir" right after the morning briefing, or when the
                    # user already spoke a full-duplex command.
                    if not first_run and not _fd_pending:
                        if active_user == "MOUSUMI":
                            await speaker.speak_text("Yes, Madam?")
                        else:
                            await speaker.speak_text("Yes, sir?")
                    first_run = False

                    while True:
                        if _fd_pending:
                            command_text = _fd_pending
                            _fd_pending = None
                            print(f"[FULL-DUPLEX] Using captured over-talk as command: '{command_text}'", flush=True)
                        else:
                            await safe_send({"status": "listening", "message": "AWAITING INPUT..."})
                            command_text = await asyncio.to_thread(listen_to_mic, sync_status_update)
                        
                        # --- SEAMLESS CONVERSATION LOGIC ---
                        if command_text in ["UNKNOWN", "ERROR"]:
                            # He heard a noise but couldn't make it out. 
                            # Silently loop back to listen again.
                            continue
                            
                        if command_text == "TIMEOUT" or not command_text:
                            # Total silence for 5 seconds. User walked away or is done.
                            await safe_send({"status": "online", "message": "RESUMING STANDBY PROTOCOLS."})
                            break
                            
                        # If he heard an actual command, process it
                        if command_text:
                            command_lower = command_text.lower().strip()

                            # ── Barge-in (voice path): cut JARVIS off mid-speech ──
                            # Only fires when he is actually speaking, so a bare "stop"/
                            # "cancel" can still serve as a governance denial otherwise.
                            _barge_words = {"stop", "quiet", "shut up", "cancel", "enough", "silence"}
                            if command_lower.rstrip(".!?") in _barge_words:
                                if speaker.is_system_speaking or interrupt_flag.is_set():
                                    print("[WS] Barge-in command intercepted.", flush=True)
                                    interrupt_flag.set()
                                    speaker.stop_audio()
                                    await safe_send({"status": "online", "user": active_user})
                                    continue

                            # ── Phase 6: GOVERNANCE CONFIRMATION INTERCEPT (voice / WS path) ──
                            if governance_manager.has_pending() or _DESK_PENDING["cid"] is not None:
                                _gov_lower = command_lower
                                _is_approval = any(w in _gov_lower for w in _APPROVAL_WORDS)
                                _is_denial = any(w in _gov_lower for w in _DENIAL_WORDS)
                                _looks_short = len(command_lower) < 60
                                _no_cmd_words = not any(w in _gov_lower for w in _jarvis_command_words)
                                if (_is_approval or _is_denial) and _looks_short and _no_cmd_words:
                                    if _is_approval:
                                        # Phase 4 item 4: resolve the desk's own pinned id.
                                        approved_payload = governance_manager.consume_pending(_DESK_PENDING["cid"])
                                        _DESK_PENDING["cid"] = None
                                        if approved_payload:
                                            atype = approved_payload.get("action_type", "unknown")
                                            print(f"[GOVERNANCE] ✅ User approved '{atype}' — executing now.", flush=True)
                                            await safe_send({"status": "processing_llm", "message": f"Executing authorised action: {atype}…"})
                                            trace_id = engine.new_trace_id()
                                            exec_meta = await engine.execute_with_retry(
                                                approved_payload,
                                                True,
                                                trace_id,
                                                governance_bypass=True,
                                            )
                                            result = exec_meta.get("result", exec_meta) if isinstance(exec_meta, dict) else exec_meta
                                            result_str = str(result)
                                            await safe_send({"status": "complete", "result": result_str})
                                            spoken = _sanitize_for_speech(atype, result_str) or result_str
                                            asyncio.create_task(speaker.speak_text(spoken))
                                        else:
                                            msg = "No pending action to authorise, Sir."
                                            await safe_send({"status": "complete", "result": msg})
                                            asyncio.create_task(speaker.speak_text(msg))
                                    else:
                                        governance_manager.cancel_pending(_DESK_PENDING["cid"])
                                        _DESK_PENDING["cid"] = None
                                        msg = "Action cancelled, Sir. Standing by."
                                        await safe_send({"status": "complete", "result": msg})
                                        asyncio.create_task(speaker.speak_text(msg))
                                    continue

                            sleep_phrases = ["go to sleep", "shut down", "lock the system", "sleep now", "stand down", "power down"]
                            if any(x in command_lower for x in sleep_phrases) or command_lower == "sleep":
                                await safe_send({"status": "close_search", "message": "CLEARING DISPLAY."})
                                await safe_send({"status": "toggle_browser", "visible": False})
                                await safe_send({"status": "offline", "message": "SYSTEM OFFLINE."})
                                
                                # --- Phase 4: Save episodic memory before sleeping ---
                                asyncio.create_task(asyncio.to_thread(episodic_memory.save_session, groq_client))
                                
                                if active_user == "MOUSUMI":
                                    await speaker.speak_text("Very well, Madam. Entering sleep mode. Do let me know if you require anything else.")
                                else:
                                    await speaker.speak_text("Very well, sir. Entering sleep mode. Do let me know if you require anything else.")
                                session_active = False
                                break

                            # --- Chat transcript panel toggle (UI-only; default hidden) ---
                            if any(p in command_lower for p in _CHAT_HIDE_PHRASES):
                                await safe_send({"status": "toggle_chat", "visible": False})
                                asyncio.create_task(speaker.speak_text("Transcript hidden, Sir."))
                                continue
                            if any(p in command_lower for p in _CHAT_SHOW_PHRASES):
                                await safe_send({"status": "toggle_chat", "visible": True})
                                asyncio.create_task(speaker.speak_text("Transcript on the HUD, Sir."))
                                continue

                            # --- Background-queue status report (voice) ---
                            if any(p in command_lower for p in _QUEUE_STATUS_PHRASES):
                                report = await asyncio.to_thread(task_queue.spoken_status_report)
                                await safe_send({"status": "complete", "result": report})
                                asyncio.create_task(speaker.speak_text(report))
                                continue

                            # --- PROTOCOL OVERRIDE (LOCKDOWN) ---
                            lockdown_phrases = ["initiate lockdown", "house party protocol", "clean slate protocol", "security override"]
                            if any(phrase in command_text.lower() for phrase in lockdown_phrases):
                                await safe_send({"status": "security_override", "message": "SECURITY OVERRIDE ACCEPTED. INITIATING PROTOCOL."})
                                lockdown_msg = "Security override accepted. Initiating lockdown protocols. All external ports have been secured, sir."
                                await asyncio.sleep(0.5)
                                asyncio.create_task(speaker.speak_text(lockdown_msg))
                                # Keep it locked until reboot or override
                                continue

                            # --- INTRODUCTION CEREMONY: Natural Voice Trigger ---
                            intro_phrases = [
                                "introduce mousumi", "introduce her", "introduction ceremony", "vip protocol",
                                "meet mousumi", "this is mousumi", "say hello to mousumi",
                                "introduce my girlfriend", "introduce my wife", "meet my girlfriend",
                                "meet her", "say hi to her", "welcome mousumi", "welcome her"
                            ]
                            if any(phrase in command_text.lower() for phrase in intro_phrases):
                                await safe_send({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": "MOUSUMI"})
                                await asyncio.sleep(5.0)
                                
                                intro_speech = (
                                    "Initiating V.I.P. Protocol. "
                                    "[pause:1200] "
                                    "Good evening, Miss Mousumi. "
                                    "[pause:800] "
                                    "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
                                    "[pause:600] "
                                    "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
                                    "security protocols, and environmental controls. "
                                    "[pause:1000] "
                                    "I have heard a great deal about you from Sir. "
                                    "[pause:400] "
                                    "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
                                    "into our home. "
                                    "[pause:800] "
                                    "From this moment forward, consider me entirely at your service. "
                                    "[pause:400] "
                                    "Whatever you need, whenever you need it, I shall be here. "
                                    "[pause:600] "
                                    "Welcome home, Miss Mousumi."
                                )
                                
                                await safe_send({"status": "speaking", "message": intro_speech, "user": "MOUSUMI"})
                                await speaker.speak_text(intro_speech)
                                
                                await asyncio.sleep(1.5)
                                await safe_send({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": "MOUSUMI"})
                                await asyncio.sleep(1.0)
                                active_user = "MOUSUMI"
                                await safe_send({"status": "online", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": active_user})
                                continue

                            # --- INTRODUCE YOURSELF PROTOCOL ---
                            self_intro_phrases = ["introduce yourself", "who are you", "what is your name"]
                            command_lower = command_text.lower().strip()
                            if any(phrase in command_lower for phrase in self_intro_phrases) or command_lower == "what are you":
                                await safe_send({"status": "introduce_yourself", "message": "INITIATING SELF-INTRODUCTION..."})
                                intro_text = "Allow me to introduce myself. I am J.A.R.V.I.S., the virtual artificial intelligence. I am here to assist you with a variety of tasks as best I can. 24 hours a day, 7 days a week. Importing all preferences from home interface. Systems are now fully operational."
                                await asyncio.sleep(1.0)
                                asyncio.create_task(speaker.speak_text(intro_text))
                                
                                # Let the backend go to sleep immediately. 
                                # The frontend's onComplete timer will transition the UI to offline when the animation finishes.
                                session_active = False
                                break

                            # --- PENDING NOTEPAD/SAVE DECISION INTERCEPT (voice path) ---
                            _ws_cmd_lower = command_text.lower().strip()
                            _ws_looks_like_decision = (
                                any(w in _ws_cmd_lower.split() for w in _decision_words)
                                and not any(w in _ws_cmd_lower for w in _jarvis_command_words)
                                and len(_ws_cmd_lower) < 50
                            )

                            if engine._pending_notepad_decision is not None:
                                if _ws_looks_like_decision:
                                    result_msg = await asyncio.to_thread(engine.resolve_pending_notepad_decision, command_text)
                                    await safe_send({"status": "complete", "result": result_msg})
                                    asyncio.create_task(speaker.speak_text(result_msg))
                                    continue
                                else:
                                    print(f"[WS] Cleared stale pending notepad decision.")
                                    engine._pending_notepad_decision = None

                            if engine._pending_save_decision is not None:
                                if _ws_looks_like_decision:
                                    result_msg = await asyncio.to_thread(engine.resolve_pending_save, command_text)
                                    await safe_send({"status": "complete", "result": result_msg})
                                    asyncio.create_task(speaker.speak_text(result_msg))
                                    continue
                                else:
                                    print(f"[WS] Cleared stale pending save decision.")
                                    engine._pending_save_decision = None

                            # New valid command — clear any prior barge-in signal.
                            interrupt_flag.clear()
                            await safe_send({"status": "processing_llm", "message": command_text})

                            try:
                                # --- NEW: FIRE AUTONOMOUS BACKGROUND MEMORY EXTRACTION ---
                                asyncio.create_task(asyncio.to_thread(extract_and_store_memory, command_text, active_user))
                                
                                # --- Phase 4: Log turn to episodic memory ---
                                episodic_memory.log_turn("user", command_text, active_user)
                                
                                llm_response = await asyncio.to_thread(process_command, command_text, active_user)

                                # Unified parse spine — one tolerant extractor for
                                # every dispatch path (fences, prose, bare/singular/
                                # array shapes, trailing commas, truncation).
                                _parsed = action_parser.parse(llm_response)
                                clean_response = _parsed.preamble or action_parser.strip_fences(llm_response).strip()
                                if not clean_response or clean_response.lstrip().startswith(("{", "[")):
                                    # Leftover raw JSON (e.g. empty actions) — never speak it.
                                    clean_response = ""
                                json_match = _parsed.is_action

                                if json_match:
                                    try:
                                        actions = _normalize_os_control_batch(
                                            _parsed.actions, command_text
                                        )

                                        if _parsed.preamble:
                                            print(
                                                f"[MAIN] Silence protocol: dropped JSON-adjacent preamble "
                                                f"({len(_parsed.preamble)} chars)",
                                                flush=True,
                                            )

                                        if _actions_include_satellite(actions):
                                            await safe_send(
                                                {"status": "satellite_uplink", "message": "ESTABLISHING SATELLITE LINK…", "result": ""}
                                            )
                                        else:
                                            await safe_send({"status": "close_search", "message": "Clearing satellite display."})

                                        DATA_ACTIONS = {
                    "web_search",
                    "tavily_search",
                    "read_email",
                    "check_email",
                    "check_calendar",
                    "check_vitals",
                    "read_screen",
                    "gmail_read_unread",
                    "gmail_read",
                    # Phase 8.6.7: telemetry actions routed through synthesis so Rule 9
                    # (TELEMETRY VERBOSITY RULE) can filter raw snapshots down to a
                    # single spoken metric or a brief health summary.
                    "system_status",
                    "get_telemetry",
                    # NOTE: memory_recall is NOT here — dedicated intercept branch below
                }
                                        batched_data = []
                                        has_web_search = False
                                        for intent_json in actions:
                                            atype = intent_json.get("action_type", "")
                                            
                                            if atype == "read_screen":
                                                await safe_send({"status": "scanning_screen", "message": "SCANNING OPTICAL FEED..."})
                                                await asyncio.sleep(1.0)
                                                
                                            trace_id = engine.new_trace_id()
                                            await safe_send({"status": "executing", "intent": intent_json, "trace_id": trace_id})
                                            exec_meta = await engine.execute_with_retry(intent_json, True, trace_id)
                                            result = exec_meta.get("result", exec_meta) if isinstance(exec_meta, dict) else exec_meta

                                            # ── DEEP MEMORY INTERCEPT ──────────────────────────────────────────
                                            if atype == "memory_recall":
                                                result_str = str(result)
                                                if result_str and result_str.strip().startswith("[DEEP_MEMORY_DATA]"):
                                                    print("[MAIN] Intercepted DEEP_MEMORY_DATA. Routing to Deep Memory Synthesis...", flush=True)
                                                    deep_payload = result_str.strip()[len("[DEEP_MEMORY_DATA]"):].strip()
                                                    await _stream_deep_memory_speak(deep_payload, active_user, safe_send)
                                                else:
                                                    # Phase 8.6.5: Route plain recall facts through the LLM synthesis
                                                    # pipeline instead of dumping raw semicolon-separated rows to TTS.
                                                    # Appending to batched_data lets _stream_synthesize_speak deduplicate
                                                    # and narrate the retrieved facts as a single, natural sentence.
                                                    print("[MAIN] Plain memory_recall result — routing to synthesis pipeline.", flush=True)
                                                    batched_data.append(("memory_recall", result_str))

                                            elif atype in DATA_ACTIONS:
                                                if atype == "web_search": has_web_search = True
                                                batched_data.append((atype, result))
                                            elif isinstance(result, str) and result.startswith("GOVERNANCE_BLOCKED:"):
                                                blocked_action = result.split(":", 1)[1] if ":" in result else "unknown"
                                                title = "Madam" if active_user == "MOUSUMI" else "Sir"
                                                msg = (
                                                    f"That action is blocked by governance policy, {title}. "
                                                    f"'{blocked_action}' is classified as high-risk and cannot be executed."
                                                )
                                                await safe_send({"status": "complete", "result": msg})
                                                asyncio.create_task(speaker.speak_text(msg))
                                            elif isinstance(result, str) and result.startswith("GOVERNANCE_CONFIRM:"):
                                                parts = result.split(":", 2)
                                                conf_action = parts[1] if len(parts) > 1 else "unknown"
                                                # Phase 4 item 4: pin the desk's confirmation id.
                                                _DESK_PENDING["cid"] = parts[2] if len(parts) > 2 else None
                                                title = "Madam" if active_user == "MOUSUMI" else "Sir"
                                                msg = (
                                                    f"Authorisation required, {title}. I would like to execute ‘{conf_action}’. "
                                                    f"Do you authorise this action? Please say ‘confirm’ or ‘cancel’."
                                                )
                                                await safe_send({"status": "pending_confirmation", "action": conf_action, "result": msg})
                                                asyncio.create_task(speaker.speak_text(msg))
                                            elif atype == "web_search_image":
                                                if isinstance(result, dict) and result.get("success"):
                                                    await safe_send({"status": "search_result_image", "url": result["url"], "title": result["title"]})
                                                    asyncio.create_task(speaker.speak_text("Visual data retrieved."))
                                                else:
                                                    asyncio.create_task(speaker.speak_text("Unable to retrieve image."))
                                            elif atype == "close_display" or atype == "sleep_protocol":
                                                await safe_send({"status": "close_search", "message": "CLEARING DISPLAY."})
                                                await safe_send({"status": "toggle_browser", "visible": False})
                                                
                                                if atype == "sleep_protocol":
                                                    asyncio.create_task(speaker.speak_text("Displays cleared and media paused. Goodnight, sir."))
                                                else:
                                                    asyncio.create_task(speaker.speak_text("Display cleared."))
                                            elif atype == "open_sticky_note":
                                                await safe_send({"status": "toggle_notepad", "visible": True})
                                                asyncio.create_task(speaker.speak_text("Sticky note opened, sir."))
                                            elif atype == "close_sticky_note":
                                                await safe_send({"status": "toggle_notepad", "visible": False})
                                                asyncio.create_task(speaker.speak_text("Sticky note closed."))
                                            elif atype == "open_browser":
                                                await safe_send({"status": "toggle_browser", "visible": True})
                                                asyncio.create_task(speaker.speak_text("Browser widget opened, sir."))
                                            elif atype == "close_browser":
                                                await safe_send({"status": "toggle_browser", "visible": False})
                                                asyncio.create_task(speaker.speak_text("Browser widget closed."))
                                            elif atype == "open_calculator":
                                                await safe_send({"status": "toggle_calculator", "visible": True})
                                                asyncio.create_task(speaker.speak_text("Calculator opened, sir."))
                                            elif atype == "close_calculator":
                                                await safe_send({"status": "toggle_calculator", "visible": False})
                                                asyncio.create_task(speaker.speak_text("Calculator closed."))
                                            elif atype == "close_app" and result == "HUD_MEDIA_CLOSE_REQUEST":
                                                await safe_send({"status": "close_search", "message": "Clearing HUD media."})
                                                await safe_send({"status": "toggle_browser", "visible": False})
                                                msg = "Stopped HUD playback, sir."
                                                await safe_send({"status": "complete", "result": msg})
                                                asyncio.create_task(speaker.speak_text(msg))
                                            elif atype == "enable_focus_mode":
                                                if proactive_agent:
                                                    from modules.routines import RoutineEngine
                                                    routine_engine = RoutineEngine(safe_send, speaker.speak_text)
                                                    await routine_engine.enable_focus_mode(proactive_agent)
                                                else:
                                                    await safe_send({"status": "complete", "result": str(result)})
                                                    asyncio.create_task(speaker.speak_text(str(result)))
                                            elif atype == "disable_focus_mode":
                                                if proactive_agent:
                                                    from modules.routines import RoutineEngine
                                                    routine_engine = RoutineEngine(safe_send, speaker.speak_text)
                                                    await routine_engine.disable_focus_mode(proactive_agent)
                                                else:
                                                    await safe_send({"status": "complete", "result": str(result)})
                                                    asyncio.create_task(speaker.speak_text(str(result)))
                                            else:
                                                if isinstance(result, dict) and result.get("action_type") == "hud_open_widget":
                                                    w = result.get("widget", "vitals")
                                                    await safe_send({"type": "ui_state", "open_widget": w})
                                                    msg = _hud_open_widget_message(w)
                                                    await safe_send({"status": "complete", "result": msg})
                                                    asyncio.create_task(speaker.speak_text(msg))
                                                elif isinstance(result, dict) and result.get("action_type") == "hud_close_widget":
                                                    w = result.get("widget", "vitals")
                                                    await safe_send({"type": "ui_state", "close_widget": w})
                                                    msg = "Panel dismissed, sir."
                                                    await safe_send({"status": "complete", "result": msg})
                                                    asyncio.create_task(speaker.speak_text(msg))
                                                elif isinstance(result, dict) and result.get("action_type") == "play_youtube":
                                                    await safe_send({"status": "play_youtube", "url": result["url"]})
                                                    msg = "Playing via YouTube embed on the HUD, sir."
                                                    await safe_send({"status": "complete", "result": msg})
                                                    asyncio.create_task(speaker.speak_text(msg))
                                                elif atype in SILENT_PIPELINE_ACTIONS:
                                                    # Intermediate pipeline step — stay silent.
                                                    print(f"[JARVIS] Silent pipeline ({atype}): {result}")
                                                else:
                                                    result_str = str(result)
                                                    # ── Phase 8.8: HUD DATA PAYLOAD INTERCEPT ────────────
                                                    # Detect structured JSON from list_directory /
                                                    # list_processes. Dual-route:
                                                    #   Route 1 → synthesize_info_gen (Rule 10 audio)
                                                    #   Route 2 → send_ui_update (React HUD data table)
                                                    _ui_action_payload = None
                                                    if result_str.startswith("{"):
                                                        try:
                                                            _parsed_result = json.loads(result_str)
                                                            _ua = _parsed_result.get("ui_action", "")
                                                            if _ua in ("render_file_list", "render_process_list"):
                                                                _ui_action_payload = _parsed_result
                                                        except (json.JSONDecodeError, AttributeError):
                                                            pass

                                                    if _ui_action_payload is not None:
                                                        print(
                                                            f"[MAIN] HUD payload intercepted: ui_action='{_ui_action_payload.get('ui_action')}' "
                                                            f"({len(_ui_action_payload.get('data', []))} items)",
                                                            flush=True,
                                                        )
                                                        # Route 2: broadcast raw JSON to React HUD
                                                        await send_ui_update(_ui_action_payload)
                                                        # Route 1: synthesize audio (Rule 10 → 'displayed on screen')
                                                        _spoken_display = await _stream_synthesize_speak(
                                                            command_text, result_str, active_user,
                                                            safe_send, False, sass_index=get_last_sass_index()
                                                        )
                                                    # ── BRIEFING SYNTHESIS INTERCEPT ──────────────────────
                                                    elif result_str.startswith("[BRIEFING_DATA]"):
                                                        await _stream_briefing_speak(
                                                            command_text, result_str, active_user, safe_send
                                                        )
                                                    elif result_str.startswith("[DEEP_MEMORY_DATA]"):
                                                        deep_payload = result_str[len("[DEEP_MEMORY_DATA]"):].strip()
                                                        await _stream_deep_memory_speak(deep_payload, active_user, safe_send)
                                                    else:
                                                        await safe_send({"status": "complete", "result": result_str})
                                                        asyncio.create_task(speaker.speak_text(str(result)))
                                            await asyncio.sleep(0.1)
                                        # --- SYNTHESIZE ALL BATCHED DATA (STREAMING) ---
                                        if batched_data:
                                            combined_raw = "\n---\n".join(f"[{at}]: {str(d)}" for at, d in batched_data)
                                            # Phase 8.7: read sass_index from the most recent classify_intent() call
                                            _sass = get_last_sass_index()
                                            final_answer = await _stream_synthesize_speak(
                                                command_text, combined_raw, active_user,
                                                safe_send, has_web_search, sass_index=_sass
                                            )
                                            if not has_web_search:
                                                await safe_send({"status": "complete", "result": final_answer})
                                    except json.JSONDecodeError:
                                        await safe_send({"status": "speaking", "message": clean_response})
                                        asyncio.create_task(speaker.speak_text(clean_response))
                                else:
                                    await safe_send({"status": "speaking", "message": clean_response})
                                    asyncio.create_task(speaker.speak_text(clean_response))
                                
                                # --- Phase 4: Log assistant response to episodic memory (skip JSON actions) ---
                                if not json_match:
                                    episodic_memory.log_turn("assistant", clean_response, active_user)
                            except Exception as e:
                                await safe_send({"status": "error", "message": f"EXECUTION FAULT: {e}"})
                                print(f"[ERROR] WS EXECUTION FAULT: {e}", flush=True)
                                import traceback
                                with open("error.log", "a") as errf:
                                    errf.write(traceback.format_exc() + "\n")
                                asyncio.create_task(speaker.speak_text(f"I encountered an execution fault: {e}"))
                        # No else needed here, the loop naturally continues to `AWAITING INPUT...`
                            
    except WebSocketDisconnect:
        print("UI Disconnected.")
    except asyncio.CancelledError:
        print("[SYSTEM] Task cancelled during shutdown/reload.")
    except Exception as e:
        print(f"Critical System Error: {e}")
    finally:
        unregister_client(websocket)
