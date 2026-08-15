import subprocess
import os
import asyncio
import re
import shutil
import webbrowser
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Union, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError, ConfigDict
import memory 
from ddgs import DDGS 
import platform
from modules.gmail_agent import GmailAgent
from modules.file_agent import FileAgent
from modules.health_agent import HealthAgent
from modules.os_agent import OSAgent
from modules.human_gui_agent import HumanGUIAgent
# Phase 2 – Invisible Fast-Lane tools
from modules.terminal_agent import TerminalAgent
from modules.telemetry_agent import TelemetryAgent
# Phase 3 – Code Specialist tools
from modules.workspace_agent import WorkspaceAgent
# Phase 6 – Governance Engine
from governance_manager import governance_manager, GovernanceSignal
# Phase 6 – GitHub Specialist
from modules.github_agent import GitHubAgent
# Phase 6 – Android TV ADB Specialist
from modules.tv_agent import TVAgent
# Phase 8.2 – OS Macro Engine
from modules.macro_agent import MacroAgent
from modules.web_agent import WebAgent

# --- TV Control & Network Imports ---
from zeroconf import Zeroconf, ServiceBrowser, ServiceStateChange
from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.sign_pythonrsa import PythonRSASigner
from adb_shell.auth.keygen import keygen

class ActionIntent(BaseModel):
    model_config = ConfigDict(extra='allow')
    action_type: str
    target: Union[str, dict] = ""
    query: Optional[str] = None

class ActionState(str, Enum):
    IDLE = "IDLE"
    RECEIVED = "RECEIVED"
    EXECUTING = "EXECUTING"
    RETRYING = "RETRYING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# ════════════════════════════════════════════════════════════════════════════
# Phase 4.5: Multi-User Permission Tiers
# ════════════════════════════════════════════════════════════════════════════
# A second, identity-scoped gate that sits IN FRONT of the governance engine.
# Governance asks "is this action safe to run at all?"; the tier gate asks
# "is THIS caller allowed to run it?". Both must pass.
#
# The model is DEFAULT-DENY for everyone who is not the Administrator. A VIP
# guest (girlfriend / brother on Telegram) may only invoke actions that appear
# on the small allowlist below — fast, read-only, public-information tools that
# expose none of Kaustav's machine, files, integrations, or automation. Anything
# else is refused *before* any side effect, logging, or governance pend.
#
# The admin tier ("admin") bypasses the gate entirely and keeps unrestricted
# structural access (OS, files, terminal, Autopilot/LangGraph, Gmail, Git,
# telemetry). Tier strings are intentionally simple so callers (telegram_bot,
# run_remote_command) can pass them through without importing an enum.
ADMIN_TIER = "admin"
VIP_GUEST_TIER = "vip_guest"

# Allowlist for the VIP GUEST tier. Casual conversation produces NO action and
# therefore never reaches this gate; only tool-backed actions are checked. We
# permit fast info-gathering (match scores, weather, general knowledge, news)
# and nothing that touches the host, personal data, or automation.
VIP_GUEST_ALLOWED_ACTIONS = frozenset({
    "tavily_search",   # fast LLM-grade lookups: match scores, weather, news
    "web_search",      # general-knowledge web search
})

# Sentinel returned by execute() when the tier gate refuses an action. Mirrors
# the GOVERNANCE_* sentinels so the remote pipeline can intercept and translate
# it into the polite VIP rejection phrase.
TIER_BLOCKED_PREFIX = "TIER_BLOCKED:"


def tier_allows(permission_tier: str, action_type: str) -> bool:
    """Return True if a caller at `permission_tier` may run `action_type`.

    Admin is unrestricted; every other (restricted) tier is default-deny,
    permitting only the VIP guest allowlist. An unknown/empty tier is treated
    as restricted — fail closed, never open.
    """
    if permission_tier == ADMIN_TIER:
        return True
    return (action_type or "").lower() in VIP_GUEST_ALLOWED_ACTIONS


# ── J.A.R.V.I.S.'s own irreplaceable state ──────────────────────────────────
# Pre-Electron review, 2026-08-15. `restricted_folders` guards the operating
# system and misses the only files on this machine that CANNOT be reinstalled.
# `jarvis_key.dpapi` + `jarvis_key.recovery` are the two wraps around the DEK:
# lose them and every row of `jarvis_longterm.db` is permanently unreadable —
# including by him, and including with the recovery code, because the recovery
# WRAP is one of the files.
#
# Reachable two ways, neither of which consulted anything: `delete_file`
# unlinked and `workspace_write` truncated. Governance approves both by TYPE and
# never by argument, so a model steered by an injected page or document could
# have ended the encryption arc in a single action.
#
# Exact FILES, not the backend directory, so JARVIS keeps full freedom to write
# code and notes beside them. This is a short list of things that must outlive a
# mistake, not a sandbox.
# Moved to `modules/protected_paths.py` so the WORKSPACE agent can apply the
# same rule at its own choke point. Guarding only `_delete_file` and
# `_workspace_write` here looked complete and was not: `_workspace_patch`
# reaches the same files through `WorkspaceAgent.patch_file`, and the key files
# are JSON, so a find-and-replace corrupts one as thoroughly as an overwrite.
# Re-exported under the old names — they are the public surface a harness pins.
from modules.protected_paths import (  # noqa: E402
    PROTECTED_FILES, PROTECTED_FOLDERS, protected_path_problem,
)


class ActionEngine:
    def __init__(self):
        self.os_agent       = OSAgent()
        self.human_gui_agent = HumanGUIAgent()
        # Phase 2 fast-lane tools
        self.terminal_agent  = TerminalAgent()
        self.telemetry_agent = TelemetryAgent()
        # Phase 3 code specialist
        self.workspace_agent = WorkspaceAgent()
        # Phase 6 GitHub specialist
        self.github_agent    = GitHubAgent()
        # Phase 6 Android TV — pure Python adb-shell + cached mDNS (see modules/tv_agent.py)
        self.tv_agent = TVAgent()
        # Phase 8.2 OS Macro Engine
        self.macro_agent = MacroAgent()
        self.web_agent = WebAgent()
        self._last_launched_app: str | None = None  # tracks last native_app_launcher target for ghost_type focus checks
        self._last_launched_pid: int | None = None  # PID of last subprocess.Popen for precise window targeting
        self._last_launched_hwnd: int | None = None  # Exact HWND for deterministic window targeting
        self._last_launched_started_at: float | None = None  # Session timestamp for stale-session checks
        # When ghost_save_file detects an existing file we DON'T silently overwrite —
        # we stash the context here and ask the user. main.py intercepts the next
        # user command, parses overwrite/rename intent, and calls resolve_pending_save().
        self._pending_save_decision: dict | None = None
        # Pending decision when Notepad shows "Save your changes?" prompt.
        # Used for both "open fresh tab before typing" and "close notepad".
        self._pending_notepad_decision: dict | None = None
        # Runtime telemetry/state (Week 1 reliability core, additive only)
        self._current_action_state: ActionState = ActionState.IDLE
        self._current_trace_id: str | None = None
        self._trace_ring: list[dict] = []
        self._max_trace_ring = 80
        self.restricted_folders = [
            Path("C:/Windows").resolve(),
            Path("C:/Program Files").resolve(),
            Path("C:/Program Files (x86)").resolve()
        ]
        # ── J.A.R.V.I.S.'s own irreplaceable state ───────────────────────────
        # Pre-Electron review, 2026-08-15. The restricted list above guards the
        # operating system and misses the only files on this machine that CANNOT
        # be reinstalled. `jarvis_key.dpapi` + `jarvis_key.recovery` are the two
        # wraps around the DEK; lose them and every row of `jarvis_longterm.db`
        # is permanently unreadable — including by him, and including with the
        # recovery code, because the recovery WRAP is one of the files.
        #
        # Reachable both ways: `delete_file` unlinks and `workspace_write`
        # truncates, and neither consulted anything. Governance approves those by
        # type, never by argument, so a model steered by an injected web page or
        # document could have destroyed the encryption arc in one action.
        #
        # Exact FILES, not the backend directory, so JARVIS keeps full freedom to
        # write code and notes beside them — the point is a short list of things
        # that must outlive a mistake, not a sandbox.
        self.protected_files = PROTECTED_FILES
        self.protected_folders = PROTECTED_FOLDERS
        
        # --- SMART HOME: DYNAMIC TV DETAILS ---
        self.tv_ip = "192.168.0.108" 
        self.tv_config_file = "tv_config.json"
        self.tv_port = self._load_tv_port()
        self.adb_device = None
        self.signer = self._get_adb_signer()

    def new_trace_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _set_action_state(self, state: ActionState, trace_id: str, payload: dict | None = None,
                          result: str | dict | None = None, note: str | None = None) -> None:
        self._current_action_state = state
        self._current_trace_id = trace_id
        event = {
            "trace_id": trace_id,
            "state": state.value,
            "ts": time.time(),
        }
        if payload is not None:
            event["payload"] = payload
        if result is not None:
            event["result"] = result if isinstance(result, str) else str(result)
        if note:
            event["note"] = note
        self._trace_ring.append(event)
        if len(self._trace_ring) > self._max_trace_ring:
            self._trace_ring = self._trace_ring[-self._max_trace_ring:]

    def get_runtime_telemetry(self) -> dict:
        return {
            "state": self._current_action_state.value,
            "trace_id": self._current_trace_id,
            "recent_traces": self._trace_ring[-10:],
        }

    @staticmethod
    def _next_available_filename(target_dir: str, filename: str) -> str:
        """
        Returns the next non-colliding filename in `target_dir`.
        Example: 'code_poem.txt' → 'code_poem_2.txt' if the original exists,
        'code_poem_3.txt' if _2 also exists, and so on.
        """
        base, ext = os.path.splitext(filename)
        candidate = filename
        n = 2
        while os.path.exists(os.path.join(target_dir, candidate)):
            candidate = f"{base}_{n}{ext}"
            n += 1
        return candidate

    def resolve_pending_save(self, choice: str) -> str:
        """
        Called by main.py when the user responds to an overwrite prompt.

        choice (case-insensitive):
          'overwrite' / 'replace' / 'yes' → overwrite the original file
          'new'       / 'rename'  / 'no'  → save under the auto-generated name
          'cancel'    / 'abort'           → cancel the operation entirely

        Returns a TTS-friendly status string. Always clears `_pending_save_decision`.
        """
        decision = self._pending_save_decision
        if not decision:
            return "There's no pending save to resolve, sir."

        target_dir          = decision["target_dir"]
        original_filename   = decision["original_filename"]
        alternative_filename = decision["alternative_filename"]
        choice_lc = (choice or "").lower().strip()

        try:
            if any(w in choice_lc for w in ["overwrite", "replace", "yes", "yeah", "yep", "do it", "confirm"]):
                filename_to_use = original_filename
                force = True
                action_word = "Overwritten and saved"
            elif any(w in choice_lc for w in ["cancel", "abort", "nevermind", "never mind", "stop"]):
                self._pending_save_decision = None
                return "Cancelled, sir."
            else:
                # Anything else (new, rename, no, save as new, different, etc.) → use alt name
                filename_to_use = alternative_filename
                force = True
                alt_base, _ = os.path.splitext(alternative_filename)
                action_word = f"Saved as {alt_base}"

            self._refresh_launch_session_target()
            res = self.human_gui_agent.ghost_save_file(
                target_dir, filename_to_use, force_overwrite=force,
                app_hint=self._last_launched_app, app_pid=self._last_launched_pid,
                app_hwnd=self._last_launched_hwnd,
            )

            if res == "SUCCESS":
                return f"{action_word}, sir."
            if res == "SAVE_DIALOG_NOT_FOUND":
                return "I couldn't locate the save dialog, sir."
            if res == "PYWINAUTO_NOT_INSTALLED":
                return "Save automation is offline, sir. Required library missing."
            if res.startswith("ERROR"):
                print(f"[ACTION ENGINE] resolve_pending_save error: {res}")
                return "The save protocol failed, sir."
            return f"{action_word}, sir."
        finally:
            self._pending_save_decision = None

    def resolve_pending_notepad_decision(self, choice: str) -> str:
        """
        Resolves an active Notepad unsaved-changes prompt.

        Modes:
          - new_tab_before_typing: continue pending ghost_type after decision
          - close_notepad: finish close flow after decision
        """
        pending = self._pending_notepad_decision
        if not pending:
            return "There's no pending Notepad decision, sir."

        mode = pending.get("mode", "")
        choice_lc = (choice or "").lower().strip()
        decision = "save"
        if any(w in choice_lc for w in ["discard", "dont save", "don't save", "no"]):
            decision = "discard"
        elif any(w in choice_lc for w in ["cancel", "abort", "stop", "nevermind", "never mind"]):
            decision = "cancel"

        try:
            prompt_result = self.human_gui_agent.handle_notepad_unsaved_prompt(decision)
            if prompt_result == "PYWINAUTO_NOT_INSTALLED":
                return "Automation dependency missing, sir."
            if prompt_result == "PROMPT_NOT_FOUND":
                return "I couldn't find the unsaved-notes prompt, sir."
            if prompt_result.startswith("ERROR"):
                print(f"[ACTION ENGINE] Notepad prompt handling error: {prompt_result}")
                return "I couldn't resolve the unsaved-notes prompt, sir."
            if prompt_result == "CANCELLED":
                return "Cancelled, sir."

            # If user chose Save, Notepad now opens Save As for the CURRENT note.
            # We autosave a backup so the flow can continue without stalling.
            if prompt_result == "SAVE_SELECTED":
                autosave_name = f"notepad_unsaved_{time.strftime('%Y%m%d_%H%M%S')}.txt"
                self._refresh_launch_session_target()
                autosave_res = self.human_gui_agent.ghost_save_file(
                    os.path.join(os.path.expanduser("~"), "Desktop"),
                    autosave_name,
                    force_overwrite=False,
                    app_hint=self._last_launched_app,
                    app_pid=self._last_launched_pid,
                    app_hwnd=self._last_launched_hwnd,
                )
                if autosave_res != "SUCCESS":
                    return "I opened save for the existing note, but couldn't complete autosave, sir."

            if mode == "close_notepad":
                self._last_launched_hwnd = None
                self._last_launched_pid = None
                self._last_launched_started_at = None
                return "Notepad closed, sir."

            # mode == new_tab_before_typing: continue the interrupted typing flow
            text_to_type = pending.get("text_to_type", "")
            shortcut = pending.get("shortcut")
            self._refresh_launch_session_target()
            resumed = self.human_gui_agent.ghost_type(
                text_to_type,
                shortcut,
                app_hint=pending.get("app_hint"),
                app_pid=pending.get("app_pid"),
                app_hwnd=pending.get("app_hwnd"),
            )
            if resumed.startswith("ERROR"):
                print(f"[ACTION ENGINE] Ghost Type resume error: {resumed}")
                return "I handled the unsaved note, but couldn't resume typing, sir."
            if resumed == "UNSAVED_CHANGES_PROMPT":
                # Nested prompt shouldn't happen, but handle defensively.
                return "There is still an unsaved note prompt, sir. Save, discard, or cancel?"
            return "Done, sir."
        finally:
            self._pending_notepad_decision = None

    def _refresh_launch_session_target(self) -> None:
        """
        Keeps _last_launched_{hwnd,pid} aligned with the real foreground app window.
        Non-destructive: if we can't resolve, we preserve existing values.
        """
        if not self._last_launched_app:
            return
        resolved = self.human_gui_agent.resolve_window(self._last_launched_app, pid=self._last_launched_pid)
        if not resolved:
            resolved = self.human_gui_agent.resolve_window(self._last_launched_app)
        if resolved:
            self._last_launched_hwnd = resolved.get("hwnd")
            if resolved.get("pid") is not None:
                self._last_launched_pid = resolved.get("pid")
            print(
                f"[ACTION ENGINE] Session target refreshed: app={self._last_launched_app}, "
                f"hwnd={self._last_launched_hwnd}, pid={self._last_launched_pid}"
            )

    def _load_tv_port(self) -> int:
        """Loads the last known TV port from cache."""
        try:
            with open(self.tv_config_file, "r") as f:
                data = json.load(f)
                return data.get("port", 5555)
        except (FileNotFoundError, json.JSONDecodeError):
            return 5555

    def _get_adb_signer(self):
        """Uses the proven FromRSAKeyPath method from your working script."""
        adbkey = 'adbkey'
        if not os.path.isfile(adbkey):
            keygen(adbkey)
        return PythonRSASigner.FromRSAKeyPath(adbkey)

    async def execute(self, payload: dict, *, governance_bypass: bool = False,
                      permission_tier: str = ADMIN_TIER) -> str:
        # ── Phase 4.5: Tier Gate (identity-scoped; runs BEFORE governance) ──
        # Default-deny for any non-admin caller. A refused action returns the
        # TIER_BLOCKED sentinel immediately — no logging, no governance pend,
        # no dispatch, zero side effects. Admin tier passes straight through.
        _atype = (payload.get("action_type") or "").lower()
        if not tier_allows(permission_tier, _atype):
            print(
                f"[TIER] ⛔ Action '{_atype}' refused for tier '{permission_tier}' "
                f"(not on VIP guest allowlist).",
                flush=True,
            )
            return f"{TIER_BLOCKED_PREFIX}{_atype}"

        # ── Phase 6: Governance Gate (must run before logging or dispatch) ──
        # PASS   → transparent, continues below.
        # BLOCK  → immediate rejection string (caller speaks it).
        # CONFIRM → stores payload in pending slot; returns a sentinel string
        #           that main.py intercepts to ask the user for approval.
        # governance_bypass: set True only when main.py re-invokes after the user
        # approved a CONFIRM-tier action (consume_pending); otherwise check() would re-pend.
        if not governance_bypass:
            gov = governance_manager.check(payload)
            sig = gov["signal"]

            if sig == GovernanceSignal.BLOCKED.value:
                reason = gov.get("reason", "Action blocked by governance policy.")
                print(f"[GOVERNANCE] 🚫 Execution halted: {reason}", flush=True)
                return f"GOVERNANCE_BLOCKED:{gov['action_type']}"

            if sig == GovernanceSignal.PENDING_CONFIRMATION.value:
                cid = gov.get("confirmation_id", "")
                atype = gov.get("action_type", "unknown")
                print(f"[GOVERNANCE] ⏸  Execution suspended — awaiting user confirmation (id={cid})", flush=True)
                return f"GOVERNANCE_CONFIRM:{atype}:{cid}"

        print(f"[ACTION ENGINE] Processing payload: {payload}")

        try:
            intent = ActionIntent(**payload)
        except ValidationError:
            return "Validation Error: I generated an invalid command structure, sir."

        action = intent.action_type.lower()
        target = intent.target

        # --- ROUTING TABLE ---
        if action == "launch_app":
            return await asyncio.to_thread(self._launch_app, target)
        elif action == "close_app":
            return await asyncio.to_thread(self._close_app, target)
        elif action == "hud_open_widget":
            return self._hud_open_widget(target)
        elif action == "hud_close_widget":
            return self._hud_close_widget(target)
        elif action == "delete_file":
            return self._delete_file(target)
        elif action == "remember_fact":
            return self._remember_fact(target)
        elif action == "web_search":
            # Offloaded: DDGS/Tavily perform blocking network requests. Running them
            # directly would freeze the event loop (TTS, WebSocket, daemons).
            return await asyncio.to_thread(self._web_search, target)
        elif action == "tavily_search":
            # Phase 3: fast, LLM-grade info-gathering (preferred over Playwright for quick lookups).
            return await asyncio.to_thread(self._tavily_search, target)
        elif action == "run_autopilot":
            # Phase 3: run the LangGraph Figma→code pipeline to completion (used by the
            # Overnight Worker so a queued "build the design" goal executes unattended).
            return await self._run_autopilot(target)
        elif action == "web_browse":
            return await self._web_browse(target)
        elif action == "web_click":
            return await self.web_agent.click(str(target))
        elif action == "web_type":
            parts = str(target).split("|", 1)
            if len(parts) == 2:
                return await self.web_agent.type_text(parts[0], parts[1])
            else:
                return "Error: Target must be format 'element_id|text'."
        elif action == "web_scroll":
            return await self.web_agent.scroll(str(target))
        elif action == "web_back":
            return await self.web_agent.go_back()
        elif action == "web_close":
            return await self.web_agent.close()
        elif action == "web_search_image":
            return await asyncio.to_thread(self._web_search_image, target)
        elif action == "play_music":
            return await asyncio.to_thread(self._play_music, target)
        elif action == "open_link": 
            return self._open_link(target)
        elif action == "close_display": 
            return "Display clear command received."
        elif action == "tv_control":
            # ADB connect + zeroconf can block 5s+; offload.
            return await asyncio.to_thread(self._control_tv, target)
        elif action == "tv_type":
            return await asyncio.to_thread(self._tv_type, target)
        elif action == "tv_search":
            return await asyncio.to_thread(self._tv_search, target)
        elif action == "tv_play_media":
            return await asyncio.to_thread(self._tv_play_media, target)
        # ── Phase 6: Android TV ADB Skill Pack ──────────────────────────────
        elif action == "tv_power":
            return await asyncio.to_thread(self._tv_power)
        elif action == "tv_volume":
            return await asyncio.to_thread(self._tv_volume, target)
        elif action == "tv_launch_app":
            return await asyncio.to_thread(self._tv_launch_app, target)
        elif action == "morning_briefing":
            return await asyncio.to_thread(self._morning_briefing)
        elif action == "movie_protocol":
            # ADB + zeroconf + 5s+ blocking; offload.
            return await asyncio.to_thread(self._movie_protocol)
        elif action == "sleep_protocol":
            return await asyncio.to_thread(self._sleep_protocol)
        elif action == "os_control":
            if target == "lock_screen":
                return await asyncio.to_thread(self.os_agent.lock_workstation)
            return await asyncio.to_thread(self.os_agent.control_media, target)
        elif action == "system_status":
            # Phase 2 upgrade: full TelemetryAgent snapshot supersedes os_agent basic read
            return await asyncio.to_thread(self.telemetry_agent.get_summary_string)
        elif action == "gui_action":
            if isinstance(target, dict):
                gui_type  = target.get("type", target.get("target", ""))
                gui_query = target.get("query", "")
            else:
                gui_type  = target
                gui_query = payload.get("query", "")
            return await asyncio.to_thread(self.human_gui_agent.execute_gui_action, gui_type, gui_query)
        elif action == "native_app_launcher":
            return await asyncio.to_thread(self._native_app_launcher, target)
        elif action == "enable_focus_mode":
            return "Focus mode enabled. Notifications silenced."
        elif action == "disable_focus_mode":
            return "Focus mode disabled. Notifications resumed."
        elif action == "ghost_save_file":
            # Format: "directory_path|filename"
            try:
                if "|" in target:
                    target_dir, filename = target.split("|", 1)
                    target_dir = target_dir.strip()
                    if target_dir.lower() in ["desktop", "documents", "downloads", "pictures"]:
                        target_dir = os.path.join(os.path.expanduser("~"), target_dir.title())
                else:
                    target_dir = os.path.expanduser("~\\Desktop")
                    filename = target
                target_dir_clean = target_dir.strip()
                filename_clean   = filename.strip()
                # Default behaviour: do NOT force-overwrite. Let the agent's pre-check
                # detect existing files and bubble FILE_EXISTS back so we can ask.
                self._refresh_launch_session_target()
                res = await asyncio.to_thread(
                    self.human_gui_agent.ghost_save_file,
                    target_dir_clean, filename_clean, force_overwrite=False,
                    app_hint=self._last_launched_app, app_pid=self._last_launched_pid,
                    app_hwnd=self._last_launched_hwnd,
                )

                # Translate raw codes → clean TTS-friendly responses (NO PATHS verbalized).
                if res == "SUCCESS":
                    try:
                        from modules.memory_engine import memory_engine
                        memory_engine.store_memory(
                            f"User prefers saving {filename_clean} to {target_dir_clean}",
                            category="path_preference",
                        )
                    except Exception:
                        pass
                    return "Saved, sir."
                if res == "FILE_EXISTS":
                    # Pre-check tripped — file already on disk. Compute next free name
                    # and stash the decision context for main.py to intercept.
                    alternative = self._next_available_filename(target_dir_clean, filename_clean)
                    self._pending_save_decision = {
                        "target_dir": target_dir_clean,
                        "original_filename": filename_clean,
                        "alternative_filename": alternative,
                    }
                    print(f"[ACTION ENGINE] Pending save decision queued — original='{filename_clean}', alt='{alternative}'")
                    return "File exists, sir. Save as new or overwrite?"
                if res == "SAVE_DIALOG_NOT_FOUND":
                    return "I couldn't locate the save dialog, sir."
                if res == "PYWINAUTO_NOT_INSTALLED":
                    return "Save automation is offline, sir. Required library missing."
                if res.startswith("ERROR"):
                    print(f"[ACTION ENGINE] Ghost Save error detail: {res}")
                    return "The save protocol failed, sir."
                return res
            except Exception as e:
                print(f"[ACTION ENGINE] Ghost Save routing error: {e}")
                return "I encountered an issue saving the file, sir."
        elif action == "memory_recall":
            try:
                # ── Fast local search against Tier 2 store ───────────────────────
                import memory_manager
                from memory_manager import get_full_profile
                # Get all memories for this user (max 100 to prevent overflow)
                # target payload from brain is e.g. "IDE preference"
                all_memories = memory_manager.get_relevant_memories(user="KAUSTAV", limit=100)
                
                target_lower = (target or "").lower()
                keywords = [w for w in target_lower.split() if len(w) > 2]
                
                # Identify if the target is just a broad/generic request
                generic_keywords = {"kaustav", "me", "everything", "all", "user", "preferences", "facts", "about"}
                is_generic = all(kw in generic_keywords for kw in keywords) or not keywords
                
                quick_hits = []
                # NOTE: `re` is imported at module level (line 4). A nested `import re`
                # here would make `re` a LOCAL for the whole execute() method, breaking
                # every other branch that uses `re` (e.g. ghost_type) with UnboundLocalError.
                if not is_generic:
                    for m in all_memories:
                        content = m['content']
                        # Use regex with word boundaries to prevent substring collisions (e.g. "ide" matching "considers")
                        if any(re.search(rf"\b{re.escape(kw)}\b", content, re.IGNORECASE) for kw in keywords):
                            quick_hits.append(content)
                            
                if quick_hits:
                    return f"Recall successful. {'; '.join(quick_hits[:3])}"
                elif is_generic and all_memories:
                    # Deep Memory path: return the full profile for dedicated LLM synthesis
                    profile = get_full_profile(user="KAUSTAV")
                    if not profile:
                        return "I don't appear to have any records about you yet, sir."
                    # Build a categorised dump for the synthesis LLM
                    lines = []
                    for m in profile:
                        lines.append(f"[{m['category']}] {m['content']}")
                    payload = "\n".join(lines)
                    print(f"[ACTION ENGINE] Deep Memory Recall — {len(profile)} facts assembled.", flush=True)
                    return f"[DEEP_MEMORY_DATA]\n{payload}"

                return "No relevant memories on that topic, sir."
            except Exception as e:
                return f"Memory recall offline: {e}"
        elif action == "ghost_type":
            # Format: "text|shortcut" or just "text"
            # IMPORTANT: use rsplit("|", 1) — split on the LAST pipe only.
            # The brain sometimes uses | as a line-break separator inside the text body
            # (e.g. "line1|line2|^s"). Splitting on the first | would eat the poem body as
            # the shortcut, garbling the output. rsplit ensures only the final segment is
            # treated as the shortcut, and only if it actually looks like a key combo.
            try:
                text_to_type = target
                shortcut = None
                if "|" in target:
                    parts = target.rsplit("|", 1)
                    potential_shortcut = parts[1].strip()
                    # Validate: a real shortcut starts with ^, alt+, ctrl+, shift+, { or is ≤15 chars
                    # and contains no spaces (poem lines have spaces; shortcuts don't)
                    is_real_shortcut = (
                        bool(re.match(r"^(\^|alt\+|ctrl\+|shift\+|\{|\w{1,15}$)", potential_shortcut, re.IGNORECASE))
                        and " " not in potential_shortcut
                        and len(potential_shortcut) <= 15
                    )
                    if is_real_shortcut:
                        text_to_type = parts[0]
                        shortcut = potential_shortcut
                    # else: treat the entire target as plain text, no shortcut

                self._refresh_launch_session_target()
                res = await asyncio.to_thread(
                    self.human_gui_agent.ghost_type,
                    text_to_type, shortcut,
                    app_hint=self._last_launched_app, app_pid=self._last_launched_pid,
                    app_hwnd=self._last_launched_hwnd,
                )
                if res == "UNSAVED_CHANGES_PROMPT":
                    self._pending_notepad_decision = {
                        "mode": "new_tab_before_typing",
                        "text_to_type": text_to_type,
                        "shortcut": shortcut,
                        "app_hint": self._last_launched_app,
                        "app_pid": self._last_launched_pid,
                        "app_hwnd": self._last_launched_hwnd,
                    }
                    return "Unsaved note detected, sir. Save, discard, or cancel?"
                if res.startswith("ERROR"):
                    print(f"[ACTION ENGINE] Ghost Type error: {res}")
                    return "I couldn't dictate the text, sir. The target window had lost focus."
                if res == "PYWINAUTO_NOT_INSTALLED":
                    return "I cannot type, sir. Required automation library is missing."
                return "Done, sir."
            except Exception as e:
                print(f"[ACTION ENGINE] Ghost Type routing error: {e}")
                return "Ghost Type encountered an issue, sir."
        elif action == "agentic_gui_task":
            print(f"[ACTION ENGINE] Dispatching to HumanGUIAgent (Internal-First → Vision fallback): '{target}'")
            # execute_autonomous_task runs a 30s+ vision loop — MUST be offloaded.
            result = await asyncio.to_thread(
                self.human_gui_agent.execute_autonomous_task,
                target, self.human_gui_agent.call_vision_api,
            )
            print(f"[ACTION ENGINE] HumanGUIAgent result: {result[:120]}")
            return result
        elif action == "read_screen":
            return await asyncio.to_thread(self._read_screen)
        # --- Phase 7: Health Data ---
        elif action == "check_vitals":
            return await asyncio.to_thread(self._check_vitals)
        # --- Phase 6: Digital Life ---
        elif action == "check_email":
            return await asyncio.to_thread(self._check_email)
        elif action == "read_email":
            return await asyncio.to_thread(self._read_email, target)
        elif action == "search_email":
            return await asyncio.to_thread(self._search_email, target)
        elif action == "send_email":
            return await asyncio.to_thread(self._send_email, target)
        # ── Phase 6: Gmail Skill Pack ─────────────────────────────────────────
        elif action == "gmail_read_unread":
            return await asyncio.to_thread(self._gmail_read_unread, target)
        elif action == "gmail_read":
            return await asyncio.to_thread(self._gmail_read, target)
        elif action == "gmail_send":
            return await asyncio.to_thread(self._gmail_send, target)
        elif action == "gmail_reply":
            return await asyncio.to_thread(self._gmail_reply, target)
        elif action == "check_calendar":
            return await asyncio.to_thread(self._check_calendar)
        elif action == "create_event":
            return await asyncio.to_thread(self._create_event, target)
        elif action == "clear_schedule":
            return await asyncio.to_thread(self._clear_schedule)
        elif action == "find_file":
            return await asyncio.to_thread(self._find_file, target)
        elif action == "list_directory":
            # Read-only directory listing (sandboxed to the user home by
            # terminal_agent._resolve_safe_path). A first-class safe action so it
            # is NOT gated behind the deliberately-blocked run_terminal_command.
            return await asyncio.to_thread(self.terminal_agent.list_directory, target or ".")
        elif action == "create_note":
            return await asyncio.to_thread(self._create_note, target)
        elif action == "organize_downloads":
            return await asyncio.to_thread(self._organize_downloads)
        # ── Phase 2: Invisible Fast-Lane ─────────────────────────────────────
        elif action == "run_terminal_command":
            return await asyncio.to_thread(self._run_terminal_command, target)
        elif action == "get_telemetry":
            return await asyncio.to_thread(self.telemetry_agent.get_summary_string)
        # ── Phase 3: Code Specialist ──────────────────────────────────────────
        elif action == "workspace_read":
            return await asyncio.to_thread(self._workspace_read, target)
        elif action == "workspace_write":
            return await asyncio.to_thread(self._workspace_write, target)
        elif action == "workspace_patch":
            return await asyncio.to_thread(self._workspace_patch, target)
        # ── Phase 6: GitHub Specialist ────────────────────────────────────────
        elif action == "github_status":
            return await asyncio.to_thread(self._github_status, target)
        elif action == "github_commit":
            return await asyncio.to_thread(self._github_commit, target)
        elif action == "github_push":
            return await asyncio.to_thread(self._github_push, target)
        elif action == "github_log":
            return await asyncio.to_thread(self._github_log, target)
        elif action == "github_diff":
            return await asyncio.to_thread(self._github_diff, target)
        # ── Phase 8.2: OS Macro Engine ────────────────────────────────────────
        elif action == "os_macro":
            return await asyncio.to_thread(self._os_macro, target)
        # ── Generative HUD: render structured data as a chart on the HUD ─────
        elif action == "render_chart":
            return self._render_chart(target)
        # ── Self-Improvement: propose a code change → branch → test → PR ─────
        elif action == "self_improve":
            return await self._self_improve(target)
        # ── Personal-Document RAG: search the user's own notes/files ─────────
        elif action == "search_documents":
            return await asyncio.to_thread(self._search_documents, target)
        # ── Remote Gateway: push a file/document to the operator's Telegram ──
        elif action == "telegram_send_file":
            return await self._telegram_send_file(target)
        # ── Partner messaging: owner-approved send + owner-only pull ──────────
        elif action == "message_partner":
            # governance_bypass is set only by main.py's post-approval re-invoke,
            # so it is the one honest signal that THIS staging was authorised.
            return await self._message_partner(target, approved=governance_bypass)
        elif action == "summarize_partner_chat":
            return await asyncio.to_thread(self._summarize_partner_chat, target)
        elif action == "partner_contact_status":
            return await asyncio.to_thread(self._partner_contact_status, target)
        # --- Phase 8: HUD Widget Toggles (handled by main.py, not action_engine) ---
        elif action in ("open_sticky_note", "close_sticky_note", "open_browser", "close_browser", "open_calculator", "close_calculator"):
            return f"UI_WIDGET_TOGGLE:{action}"
        else:
            return f"I'm afraid I don't know how to perform the action '{action}', sir."

    # ==========================================
    # SELF-CORRECTION ENGINE (Phase 4.4)
    # ==========================================
    async def execute_with_retry(
        self,
        payload: dict,
        return_meta: bool = False,
        trace_id: str | None = None,
        *,
        governance_bypass: bool = False,
        permission_tier: str = ADMIN_TIER,
    ):
        """
        Wraps execute() with intelligent fallback strategies.
        If an action fails, attempts one automatic recovery before reporting failure.

        Phase 6 note: governance signals (BLOCKED / CONFIRM) are detected here
        so the trace ring correctly records the pre-execution decision.
        """
        trace_id = trace_id or self.new_trace_id()
        self._set_action_state(ActionState.RECEIVED, trace_id, payload=payload)
        self._set_action_state(ActionState.EXECUTING, trace_id, payload=payload)
        # ── Universal error trap (Refinement Phase) ──────────────────────────
        # Any individual handler that raises an unexpected exception must NOT
        # crash the action chain or bubble an opaque 500 to main.py. We catch it
        # here, log the full traceback for diagnostics, record FAILED state, and
        # return a clean, localized error string the LLM/UI can speak gracefully.
        try:
            result = await self.execute(
                payload, governance_bypass=governance_bypass, permission_tier=permission_tier
            )
        except Exception as exc:
            import traceback
            _atype = payload.get("action_type", "unknown")
            print(f"[ACTION ENGINE] Unhandled exception in '{_atype}': {exc}", flush=True)
            traceback.print_exc()
            err_str = (
                f"Action failed: the '{_atype}' tool encountered an unexpected "
                f"error ({type(exc).__name__}). I've logged it, Sir."
            )
            self._set_action_state(
                ActionState.FAILED, trace_id, payload=payload,
                result=err_str, note="handler_exception",
            )
            if return_meta:
                return {
                    "trace_id": trace_id,
                    "state": ActionState.FAILED.value,
                    "result": err_str,
                    "used_fallback": False,
                }
            return err_str

        # ── Phase 4.5: propagate tier-refusal through return_meta path ────────
        if isinstance(result, str) and result.startswith(TIER_BLOCKED_PREFIX):
            self._set_action_state(
                ActionState.FAILED, trace_id, payload=payload,
                result=result, note="tier_refused",
            )
            if return_meta:
                return {
                    "trace_id":      trace_id,
                    "state":         ActionState.FAILED.value,
                    "result":        result,
                    "used_fallback": False,
                }
            return result

        # ── Phase 6: propagate governance signals through return_meta path ────
        if isinstance(result, str) and result.startswith(("GOVERNANCE_BLOCKED:", "GOVERNANCE_CONFIRM:")):
            state = ActionState.FAILED if result.startswith("GOVERNANCE_BLOCKED:") else ActionState.IDLE
            self._set_action_state(
                state,
                trace_id,
                payload=payload,
                result=result,
                note="governance_intercepted",
            )
            if return_meta:
                return {
                    "trace_id":     trace_id,
                    "state":        state.value,
                    "result":       result,
                    "used_fallback": False,
                }
            return result
        # ─────────────────────────────────────────────────────────────────────
        
        # Check if the result indicates a failure
        # SUCCESS — attach payload so telemetry / regression can correlate COMPLETE with action_type
        if not self._is_failure(result, payload.get("action_type")):
            self._set_action_state(ActionState.COMPLETE, trace_id, payload=payload, result=result)
            if return_meta:
                return {
                    "trace_id": trace_id,
                    "state": ActionState.COMPLETE.value,
                    "result": result,
                    "used_fallback": False,
                }
            return result
        
        # Attempt fallback
        action = payload.get("action_type", "").lower()
        target = payload.get("target", "")
        
        result_preview = result[:60] if isinstance(result, str) else str(result)[:60]
        print(f"[RETRY ENGINE] Primary action failed: '{result_preview}'. Attempting fallback...", flush=True)
        self._set_action_state(ActionState.RETRYING, trace_id, payload=payload, result=result, note="primary_failed")
        
        fallback_result = self._attempt_fallback(action, target, result)
        if fallback_result:
            print(f"[RETRY ENGINE] Fallback succeeded.", flush=True)
            self._set_action_state(
                ActionState.COMPLETE,
                trace_id,
                payload=payload,
                result=fallback_result,
                note="fallback_succeeded",
            )
            if return_meta:
                return {
                    "trace_id": trace_id,
                    "state": ActionState.COMPLETE.value,
                    "result": fallback_result,
                    "used_fallback": True,
                }
            return fallback_result
        
        # No fallback available or fallback also failed
        self._set_action_state(
            ActionState.FAILED,
            trace_id,
            payload=payload,
            result=result,
            note="fallback_unavailable_or_failed",
        )
        if return_meta:
            return {
                "trace_id": trace_id,
                "state": ActionState.FAILED.value,
                "result": result,
                "used_fallback": False,
            }
        return result
    
    # Actions whose result IS data/content the user asked to see. Their text can
    # legitimately contain "error"/"failed"/"couldn't" (log lines, file contents,
    # search snippets, git/terminal output, OCR) — so these are NEVER phrase-scanned
    # for failure. Only a dict {"success": False} marks them failed.
    _CONTENT_ACTIONS = frozenset({
        "read_screen", "workspace_read", "web_search", "tavily_search",
        "web_search_image", "system_status", "get_telemetry", "memory_recall",
        "check_email", "read_email", "gmail_read", "gmail_read_unread",
        "check_calendar", "check_vitals", "run_terminal_command", "web_browse",
    })

    # Distinctive failure phrases the CONTROL/side-effect agents actually emit
    # (2026-07-05 execution audit). Chosen not to collide with normal success
    # output. These matter because the agents do NOT prefix failures with
    # "error:"/"failed:", so without this a failed launch/type/save was scored
    # COMPLETE and narrated as "Done, Sir" while nothing happened.
    _FAILURE_PHRASES = (
        "smart open failed", "gui execution error", "execution error",
        "couldn't open", "could not open", "couldn't locate", "could not locate",
        "couldn't find a running process", "could not find a running process",
        "error navigating to", "error clicking", "error typing",
        "save dialog not found", "save_dialog_not_found", "tavily_unconfigured",
        "no relevant data", "unable to reach", "failed to", "write refused",
        "read refused", "but couldn't open", "i couldn't", "i could not",
    )

    def _is_failure(self, result, action_type=None) -> bool:
        """Determines if an action result indicates failure.

        Context-aware: data/content actions are never phrase-scanned (their text
        legitimately contains 'error'/'failed'); control/side-effect actions are
        checked against the hard-failure prefixes AND the specific phrases their
        agents emit, so a failed launch/type/save is no longer narrated as success.
        """
        if isinstance(result, dict):
            return not result.get("success", True)
        if not isinstance(result, str):
            return False
        # Screen/content dumps — never flag (raw OCR/file/search/terminal text).
        if result.startswith("SCREEN CONTENTS:"):
            return False
        if (action_type or "").lower() in self._CONTENT_ACTIONS:
            return False
        lower = result.strip().lower()
        if lower.startswith(("error:", "failed:")):
            return True
        return any(p in lower for p in self._FAILURE_PHRASES)
    
    def _attempt_fallback(self, action: str, target: str, original_error: str):
        """Returns a fallback result or None if no strategy applies."""
        
        # --- CLOSE APP FALLBACK: Try window title matching ---
        if action == "close_app":
            try:
                import ctypes
                import ctypes.wintypes
                
                EnumWindows = ctypes.windll.user32.EnumWindows
                GetWindowText = ctypes.windll.user32.GetWindowTextW
                GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
                PostMessage = ctypes.windll.user32.PostMessageW
                WM_CLOSE = 0x0010
                closed_count = 0
                
                @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
                def _enum_callback(hwnd, lParam):
                    nonlocal closed_count
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        if target.lower() in buff.value.lower():
                            PostMessage(hwnd, WM_CLOSE, 0, 0)
                            closed_count += 1
                    return True
                
                EnumWindows(_enum_callback, 0)
                if closed_count > 0:
                    return f"Retry successful. Closed {closed_count} window(s) matching '{target}' by title."
            except Exception:
                pass
        
        # --- LAUNCH APP FALLBACK: Try OS 'start' command, VERIFY it launched ---
        # os.system('start ...') returns 0 even for a bogus name (cmd accepted the
        # line), so we must NOT claim success blindly. Snapshot PIDs before/after:
        # a new process ⇒ genuinely launched; none ⇒ honest failure (return None so
        # the retry engine reports the original error instead of a phantom success).
        if action == "launch_app":
            # The PRIMARY launch path moved to os.startfile in May 2026 precisely
            # to stop building a shell line; this retry fallback was left behind on
            # the old form. `target` comes from the model's action JSON, so
            # `x" & calc & "` closed the quote and ran anything it liked.
            from modules import shell_safety
            if not shell_safety.is_shell_safe(target):
                print(f"[ACTION ENGINE] launch_app retry refused: "
                      f"{shell_safety.reject_reason(target)}", flush=True)
                return None
            try:
                import psutil
                import time as _t
                before = {p.pid for p in psutil.process_iter()}
                # A list with shell=False: `target` is an ARGUMENT to start, never
                # syntax, so it cannot introduce a second command.
                subprocess.run(["cmd", "/c", "start", "", target],
                               shell=False, capture_output=True, timeout=15)
                _t.sleep(1.2)
                after = {p.pid for p in psutil.process_iter()}
                if after - before:
                    return f"Launched '{target}', Sir."
                return None  # nothing spawned — don't fake a success
            except Exception:
                return None
        
        # --- WEB SEARCH FALLBACK: Broaden the query ---
        if action == "web_search" and ("no relevant data" in original_error.lower() or "error" in original_error.lower()):
            try:
                broadened = f"{target} explained simply"
                result = self._web_search(broadened)
                if not self._is_failure(result, "web_search"):
                    return result
            except Exception:
                pass
        
        # --- TV FALLBACK: Reconnect and retry once ---
        if action in ["tv_control", "tv_type", "tv_search"] and "unable to reach" not in original_error:
            try:
                self.adb_device = None  # Force reconnect
                if self._connect_tv():
                    if action == "tv_control":
                        return self._control_tv(target)
                    elif action == "tv_type":
                        return self._tv_type(target)
                    elif action == "tv_search":
                        return self._tv_search(target)
            except Exception:
                pass
        
        return None

    # ==========================================
    # PHASE 7: HEALTH INTEGRATION
    # ==========================================
    
    def _check_vitals(self) -> str:
        try:
            agent = HealthAgent()
            return agent.get_summary_string()
        except Exception as e:
            print(f"[ACTION ENGINE] Health check failed: {e}")
            return "I am currently unable to interface with your health monitors, Sir."

    def _morning_briefing(self) -> str:
        """Assembles data concurrently from all life-surface agents for a unified briefing."""
        print("[ACTION ENGINE] Morning Briefing: fetching parallel data...", flush=True)

        try:
            hour = datetime.now(ZoneInfo("Asia/Kolkata")).hour
        except Exception:
            try:
                import pytz

                hour = datetime.now(pytz.timezone("Asia/Kolkata")).hour
            except Exception:
                hour = datetime.now(
                    timezone(timedelta(hours=5, minutes=30))
                ).hour

        if hour < 12:
            greeting = "Good morning"
        elif hour < 18:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"

        def get_health() -> str:
            try:
                return HealthAgent().get_summary_string()
            except Exception:
                return "Health telemetry offline."

        def get_cal() -> str:
            try:
                from modules.calendar_agent import CalendarAgent
                return CalendarAgent().get_today_schedule()
            except Exception:
                return "Calendar synchronization unavailable."

        def get_gmail() -> str:
            try:
                return GmailAgent().get_unread_emails()
            except Exception:
                return "Comms server unreachable."

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_health = executor.submit(get_health)
            future_cal = executor.submit(get_cal)
            future_gmail = executor.submit(get_gmail)
            health_str = future_health.result(timeout=25)
            cal_str = future_cal.result(timeout=25)
            gmail_str = future_gmail.result(timeout=25)

        print("[ACTION ENGINE] Morning Briefing: data assembled.", flush=True)
        return (
            f"[BRIEFING_DATA] {greeting}, Sir. "
            f"HEALTH: {health_str} | SCHEDULE: {cal_str} | EMAILS: {gmail_str}"
        )

    # ==========================================
    # PHASE 6: DIGITAL LIFE MANAGER
    # ==========================================
    
    def _check_email(self) -> str:
        try:
            agent = GmailAgent()
            return agent.get_unread_summary()
        except Exception as e:
            print(f"[ACTION ENGINE] Email check failed: {e}")
            return "I'm unable to access your email at the moment, sir."
    
    def _read_email(self, target: str) -> str:
        try:
            agent = GmailAgent()
            index = target if target else "latest"
            return agent.read_email(index)
        except Exception as e:
            print(f"[ACTION ENGINE] Email read failed: {e}")
            return "I couldn't read that email, sir."

    def _search_email(self, target: str) -> str:
        try:
            agent = GmailAgent()
            return agent.search_email(target)
        except Exception as e:
            print(f"[ACTION ENGINE] Email search failed: {e}")
            return "I couldn't search for that email, sir."
    
    def _send_email(self, target: str) -> str:
        try:
            # Expected format: "to@email.com | Subject | Body text"
            parts = [p.strip() for p in target.split("|")]
            if len(parts) < 3:
                return "I need the recipient, subject, and body to send an email, sir. Format: 'to@email.com | Subject | Body'"
            agent = GmailAgent()
            return agent.send_email(parts[0], parts[1], parts[2])
        except Exception as e:
            print(f"[ACTION ENGINE] Email send failed: {e}")
            return "I couldn't send that email, sir."

    # ── Phase 6: Gmail Skill Pack handlers ──────────────────────────────

    def _gmail_read(self, target: str | dict) -> str:
        """
        Fetch emails by Gmail search query.
        target formats:
          {}                → dict: {"query": "...", "max_results": N}
          ""                → defaults to "is:unread" (5 results)
          "query"           → any Gmail search string, 5 results
          "query|N"         → query with custom result count
        """
        try:
            query = "is:unread"
            max_results = 5
            if isinstance(target, dict):
                raw_q = target.get("query") or target.get("q") or "is:unread"
                query = str(raw_q).strip() or "is:unread"
                mr = target.get("max_results", target.get("limit", 5))
                try:
                    max_results = max(1, min(int(mr), 20))
                except (TypeError, ValueError):
                    max_results = 5
            elif isinstance(target, str):
                s = target.strip()
                if s and "|" in s:
                    parts = s.split("|", 1)
                    query = parts[0].strip() or "is:unread"
                    try:
                        max_results = max(1, min(int(parts[1].strip()), 20))
                    except ValueError:
                        pass
                elif s:
                    query = s
            agent = GmailAgent()
            return agent.read_emails(query=query, max_results=max_results)
        except Exception as e:
            print(f"[ACTION ENGINE] gmail_read failed: {e}")
            return "I couldn't retrieve those emails, Sir."

    def _gmail_send(self, target: str | dict) -> str:
        """
        Send a new email.
        target format: "to@email.com | Subject | Body text"
        Also accepts JSON string or dict: {"to": "...", "subject": "...", "body": "..."}
        """
        try:
            import json as _json

            to, subject, body = "", "", ""
            if isinstance(target, dict):
                to = str(target.get("to", "")).strip()
                subject = str(target.get("subject", "")).strip()
                body = str(target.get("body", "")).strip()
            else:
                stripped = str(target).strip()
                if stripped.startswith("{"):
                    try:
                        data = _json.loads(stripped)
                    except _json.JSONDecodeError:
                        return (
                            "I need valid JSON or pipe-separated recipient, subject, and body, Sir. "
                            "Format: 'to@email.com | Subject | Body'"
                        )
                    to = str(data.get("to", "")).strip()
                    subject = str(data.get("subject", "")).strip()
                    body = str(data.get("body", "")).strip()
                else:
                    parts = [p.strip() for p in stripped.split("|", 2)]
                    if len(parts) < 3:
                        return (
                            "I need the recipient, subject, and body to send an email, Sir. "
                            "Format: 'to@email.com | Subject | Body'"
                        )
                    to, subject, body = parts[0], parts[1], parts[2]
            agent = GmailAgent()
            return agent.send_email(to, subject, body)
        except Exception as e:
            print(f"[ACTION ENGINE] gmail_send failed: {e}")
            return "I couldn't send that email, Sir."

    def _gmail_reply(self, target: str | dict) -> str:
        """
        Reply to an existing email thread.
        target format: "thread_id | reply body"
        Also accepts JSON string or dict: {"thread_id": "...", "body": "..."}
        """
        try:
            import json as _json

            thread_id, body = "", ""
            if isinstance(target, dict):
                thread_id = str(target.get("thread_id", "")).strip()
                body = str(target.get("body", "")).strip()
            else:
                stripped = str(target).strip()
                if stripped.startswith("{"):
                    try:
                        data = _json.loads(stripped)
                    except _json.JSONDecodeError:
                        return (
                            "I need valid JSON or thread id and body separated by '|', Sir. "
                            "Format: 'thread_id | reply message'"
                        )
                    thread_id = str(data.get("thread_id", "")).strip()
                    body = str(data.get("body", "")).strip()
                else:
                    parts = [p.strip() for p in stripped.split("|", 1)]
                    if len(parts) < 2:
                        return (
                            "I need the thread ID and reply body, Sir. "
                            "Format: 'thread_id | reply message'"
                        )
                    thread_id, body = parts[0], parts[1]
            if not thread_id:
                return "No thread ID provided, Sir. I need to know which email thread to reply to."
            agent = GmailAgent()
            return agent.reply_email(thread_id, body)
        except Exception as e:
            print(f"[ACTION ENGINE] gmail_reply failed: {e}")
            return "I couldn't send the reply, Sir."

    def _gmail_read_unread(self, target: str) -> str:
        """
        Fetch the latest unread emails and return a formatted summary.

        target formats:
          ""   or  "inbox"  → top 5 unread
          "N"              → top N unread (1-20)

        This is the primary action for "check my email" / "any new emails".
        It calls GmailAgent.get_unread_emails() which has the full pre-flight
        guard and returns a graceful string if credentials are missing.
        """
        try:
            limit = 5
            t = (target or "").strip()
            if t.isdigit():
                limit = max(1, min(int(t), 20))
            agent = GmailAgent()
            return agent.get_unread_emails(limit=limit)
        except Exception as e:
            print(f"[ACTION ENGINE] gmail_read_unread failed: {e}")
            return "I couldn't retrieve your unread emails, Sir."
    
    def _check_calendar(self) -> str:
        try:
            from modules.calendar_agent import CalendarAgent
            agent = CalendarAgent()
            return agent.get_summary_string()
        except Exception as e:
            print(f"[ACTION ENGINE] Calendar check failed: {e}")
            return "I'm unable to access your calendar at the moment, Sir."
    
    def _create_event(self, target: str) -> str:
        try:
            from modules.calendar_agent import CalendarAgent
            agent = CalendarAgent()
            return agent.create_event(target)
        except Exception as e:
            print(f"[ACTION ENGINE] Event creation failed: {e}")
            return "I couldn't create that event, sir."
            
    def _clear_schedule(self) -> str:
        try:
            from modules.calendar_agent import CalendarAgent
            agent = CalendarAgent()
            return agent.clear_today_schedule()
        except Exception as e:
            print(f"[ACTION ENGINE] Clear schedule failed: {e}")
            return "I couldn't clear your schedule, sir."
    
    def _find_file(self, target: str) -> str:
        try:
            agent = FileAgent()
            return agent.find_file(target)
        except Exception as e:
            print(f"[ACTION ENGINE] File search failed: {e}")
            return "I encountered an error searching for that file, sir."
    
    def _create_note(self, target: str) -> str:
        try:
            agent = FileAgent()
            return agent.create_note(target)
        except Exception as e:
            print(f"[ACTION ENGINE] Note creation failed: {e}")
            return "I couldn't create that note, sir."
    
    def _organize_downloads(self) -> str:
        try:
            agent = FileAgent()
            return agent.organize_downloads()
        except Exception as e:
            print(f"[ACTION ENGINE] Download organization failed: {e}")
            return "I couldn't organize your downloads, sir."

    # ── Phase 2: Invisible Fast-Lane handlers ────────────────────────────────

    def _run_terminal_command(self, target: str) -> str:
        """
        Routes a terminal-command request through the TerminalAgent sandbox.

        Target format examples:
          "list_directory: C:\\Users\\Kaustav\\Desktop"
          "create_folder: Documents\\Projects\\Alpha"
          "kill_process: chrome.exe"
          "ping: google.com"
          "raw: ipconfig /all"       ← raw passthrough (still blocked-pattern checked)
        """
        if not target or not target.strip():
            return "No terminal command specified."

        try:
            # Sub-command routing: "verb: argument" or bare raw command
            if ":" in target:
                verb, _, arg = target.partition(":")
                verb = verb.strip().lower().replace(" ", "_")
                arg  = arg.strip()
            else:
                verb = "raw"
                arg  = target.strip()

            if verb == "list_directory":
                return self.terminal_agent.list_directory(arg or ".")
            elif verb == "create_folder":
                return self.terminal_agent.create_folder(arg)
            elif verb == "move_file":
                # arg expected: "src -> dst"
                if "->" in arg:
                    src, _, dst = arg.partition("->")
                    return self.terminal_agent.move_file(src.strip(), dst.strip())
                return "Format: 'move_file: source_path -> dest_path'"
            elif verb == "copy_file":
                if "->" in arg:
                    src, _, dst = arg.partition("->")
                    return self.terminal_agent.copy_file(src.strip(), dst.strip())
                return "Format: 'copy_file: source_path -> dest_path'"
            elif verb == "delete_file":
                return self.terminal_agent.delete_file(arg)
            elif verb == "list_processes":
                return self.terminal_agent.list_processes(arg or None)
            elif verb == "kill_process":
                # Phase 8.8: terminal_agent.kill_process() returns a routing sentinel
                # "__ROUTE_TO_CLOSE_APP__:<name>" instead of calling taskkill directly.
                # Intercept it here and delegate to _close_app() so the _WEB_ONLY_SERVICES
                # and explorer.exe blacklists are always enforced — no bypass possible.
                _kp_result = self.terminal_agent.kill_process(arg)
                if isinstance(_kp_result, str) and _kp_result.startswith("__ROUTE_TO_CLOSE_APP__:"):
                    _kp_target = _kp_result.split(":", 1)[1]
                    print(
                        f"[ACTION ENGINE] kill_process sentinel intercepted — "
                        f"routing '{_kp_target}' to _close_app()",
                        flush=True,
                    )
                    return self._close_app(_kp_target)
                return _kp_result
            elif verb == "network_info":
                return self.terminal_agent.get_network_info()
            elif verb == "ping":
                return self.terminal_agent.ping(arg)
            elif verb == "lock":
                return self.terminal_agent.lock_workstation()
            elif verb == "sleep":
                return self.terminal_agent.sleep_system()
            else:
                # raw passthrough — still goes through the blocked-pattern gate
                return self.terminal_agent.run_command(arg or target)

        except Exception as e:
            print(f"[ACTION ENGINE] Terminal command failed: {e}")
            return f"Terminal execution offline: {e}"

    # ── Phase 3: Code Specialist handlers ────────────────────────────────────

    def _workspace_read(self, target: str) -> str:
        """
        Read a workspace file into context.
        Target: file path (absolute or relative to a workspace root).
        Result is stored in working memory so subsequent patch/write actions
        can reference the exact file content.
        """
        if not target or not target.strip():
            return "No file path specified for workspace read."
        try:
            result = self.workspace_agent.read_file(target.strip())
            # Inject the file content into working memory (capped at 600 chars)
            # so the LLM can use the exact strings on the next patch command.
            memory.add_to_working_memory(
                "system",
                f"[workspace_read result — use these exact strings for any patch]: {result[:600]}"
            )
            return result
        except Exception as e:
            print(f"[ACTION ENGINE] Workspace read failed: {e}")
            return f"Workspace read offline: {e}"

    def _render_chart(self, target) -> str:
        """
        Generative HUD (Roadmap §4): turn structured data into a chart on the HUD.
        Returns a `ui_action: render_chart` JSON payload that main.py broadcasts to
        the React HUD (DataOverlay), and the synthesis path narrates "shown on screen".

        Target: a dict or JSON string —
            {"title": "...", "type": "bar"|"line"|"pie",
             "data": [{"label": "...", "value": 12}, ...]}
          or {"title", "type", "labels": [...], "values": [...]}
        """
        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        spec = target
        if isinstance(target, str):
            try:
                spec = json.loads(target)
            except json.JSONDecodeError:
                spec = {"title": target, "type": "bar", "data": []}
        if not isinstance(spec, dict):
            spec = {"title": str(target), "type": "bar", "data": []}

        title = str(spec.get("title") or "Data")
        ctype = str(spec.get("type") or spec.get("chart_type") or "bar").lower()
        if ctype not in ("bar", "line", "pie"):
            ctype = "bar"

        data = spec.get("data")
        if not data and spec.get("labels") and spec.get("values"):
            data = [{"label": str(l), "value": _num(v)}
                    for l, v in zip(spec["labels"], spec["values"])]
        norm = []
        for d in (data or [])[:24]:
            if isinstance(d, dict):
                norm.append({"label": str(d.get("label", "")), "value": _num(d.get("value", 0))})
        if not norm:
            return "I don't have structured data to chart, sir."

        payload = {"ui_action": "render_chart", "title": title,
                   "chart_type": ctype, "data": norm}
        return json.dumps(payload)

    async def _self_improve(self, target) -> str:
        """
        Guarded self-improvement (Roadmap §3.3): propose a code change, apply it on a
        fresh branch, run the tests, and — only if they pass — push and open a PR for
        human review. NEVER merges. Gated CONFIRM-tier so initiating it needs approval.
        """
        instruction = target if isinstance(target, str) else str(target or "")
        if not instruction.strip():
            return "What improvement would you like me to propose, sir?"
        try:
            from modules.self_improve import SelfImprovementEngine
        except Exception as e:
            return f"The self-improvement engine is unavailable, sir: {e}"
        engine = SelfImprovementEngine(self.github_agent, self.workspace_agent)
        report = await engine.run(instruction.strip())
        return report.get("message", "Self-improvement run finished, sir.")

    def _search_documents(self, target) -> str:
        """
        Search the user's indexed personal documents/notes (Roadmap §4).
        Target: a natural-language query. Returns the most relevant chunks; the
        synthesis pipeline narrates them. Routed through DATA_ACTIONS in main.py.
        """
        query = target if isinstance(target, str) else str(target or "")
        if not query.strip():
            return "What would you like me to search your documents for, sir?"
        try:
            from modules import personal_rag
        except Exception as e:
            return f"My document index is unavailable, sir: {e}"
        hits = personal_rag.query_documents(query.strip(), n_results=4)
        if not hits:
            return "I found nothing relevant in your indexed documents, sir."
        return "PERSONAL DOCUMENT MATCHES:\n" + "\n---\n".join(hits)

    async def _telegram_send_file(self, target) -> str:
        """
        Push a file/document back to the operator's Telegram chat.

        Target may be:
          • a path string                       → "F:/reports/out.pdf"
          • a dict {"path": ..., "caption": ...} → optional caption text

        Routes through the Telegram gateway's bot (modules/telegram_bot.py). Only
        the validated owner ever receives files — the bot has no other recipient.
        """
        path, caption = "", ""
        if isinstance(target, dict):
            path = str(target.get("path") or target.get("file") or target.get("target") or "").strip()
            caption = str(target.get("caption") or "").strip()
        else:
            path = str(target or "").strip()

        if not path:
            return "No file path specified to send, sir."

        try:
            from modules import telegram_bot
        except Exception as e:
            return f"The Telegram gateway is unavailable, sir: {e}"

        if not telegram_bot.is_configured():
            return "The Telegram gateway is offline, sir — no file was sent."

        if not os.path.isfile(path):
            return f"I couldn't find that file to send, sir: {path}"

        ok = await telegram_bot.send_document_to_owner(path, caption)
        if ok:
            return f"Sent '{os.path.basename(path)}' to your Telegram, sir."
        return f"I was unable to deliver '{os.path.basename(path)}' to Telegram, sir."

    async def _message_partner(self, target, *, approved: bool = False) -> str:
        """Send an OWNER-APPROVED message to a registered partner.

        By the time this runs the owner has already authorised the exact text:
        `message_partner` is CONFIRM-tier in governance.json, so the first call
        returns the GOVERNANCE_CONFIRM sentinel and only the post-approval
        re-invocation (governance_bypass=True) reaches this method.

        Recipient resolution is allowlist-only (`partner_registry`): a NAME maps
        to an id held in the environment, and anything containing a digit is
        refused as an attempted raw chat id. Unknown or ambiguous names are
        refused honestly — a private message to the wrong person is the failure
        this whole path exists to prevent.
        """
        from modules import partner_messaging, partner_registry

        name, body = partner_messaging.parse_target(target)
        res = partner_registry.resolve(name)
        if not res.ok:
            print(f"[PARTNER] ⛔ send refused — {res.reason} (name={name!r})", flush=True)
            return res.refusal_text()

        body = partner_messaging.normalise_body(body)
        if not body:
            return (f"There's no message to send to {res.display_name}, Sir — "
                    "tell me what you'd like to say.")

        # A declined send is terminal, and a send already awaiting approval is
        # not staged twice. Checked HERE because every route (voice, HUD, phone,
        # a second action in the same reply) funnels through the engine.
        #
        # `approved` carries main.py's post-confirmation re-invoke. Without it
        # the in-flight mark left by the CONFIRM prompt refuses the very send
        # that prompt authorised, and NOTHING is ever delivered. The denial
        # check runs in both modes — approval cannot overturn a refusal.
        refusal = partner_messaging.guard.refusal(res.slot, body, approved=approved)
        if refusal:
            print(f"[PARTNER] ⛔ send refused — {refusal} ({res.slot})", flush=True)
            return partner_messaging.refusal_text(refusal, res.display_name)

        try:
            from modules import telegram_bot
        except Exception as e:  # noqa: BLE001
            return f"The Telegram gateway is unavailable, Sir: {e}"
        if not telegram_bot.is_configured():
            return "The Telegram gateway is offline, Sir — nothing was sent."

        ok = await telegram_bot.send_text_to_partner(res.partner_id, body)
        if ok:
            partner_messaging.guard.note_sent(res.slot, body)
            return f"Sent to {res.display_name}, Sir."
        return (f"I couldn't deliver that to {res.display_name}, Sir — "
                "Telegram refused the message. Nothing was sent.")

    def _summarize_partner_chat(self, target) -> str:
        """Read back what a partner has told JARVIS. ADMIN-ONLY, pull-only.

        Admin-only is enforced upstream by `tier_allows` — this action is NOT on
        `VIP_GUEST_ALLOWED_ACTIONS`, so a guest's invocation is refused with the
        TIER_BLOCKED sentinel before any dispatch, logging, or governance pend.
        One partner's history can never surface in another's: `partner_log.recent`
        filters on the single resolved slot.

        Nothing is pushed. This only ever runs because the owner asked.
        """
        from modules import partner_log, partner_messaging, partner_registry

        name, _ = partner_messaging.parse_target(target)
        if not name:
            name = str(target or "").strip()
        res = partner_registry.resolve(name)
        if not res.ok and res.reason != partner_registry.REASON_NOT_REGISTERED:
            return res.refusal_text()
        slot = res.slot
        display = res.display_name or partner_registry.display_for(slot or "")

        if not partner_log.logging_enabled():
            return (f"I don't keep a record of {display}'s messages, Sir — "
                    f"partner-chat logging is switched off "
                    f"({partner_log.ENV_FLAG} is not set). I can only tell you "
                    "what I've learned about her in general conversation.")

        rows = partner_log.recent(slot, limit=25)
        if not rows:
            return (f"Nothing logged from {display} yet, Sir. Partner-chat "
                    "logging is on, but she hasn't messaged me since.")
        return partner_messaging.format_history(rows, display, partner_log.DISCLOSURE)

    def _partner_contact_status(self, target) -> str:
        """"Did she talk to you?" — fact of contact, timing, urgency. NO CONTENT.

        The butler answer (roadmap §6.7). ADMIN-ONLY by the same mechanism as
        `summarize_partner_chat`: absent from `VIP_GUEST_ALLOWED_ACTIONS`, so
        `tier_allows` refuses a guest with the TIER_BLOCKED sentinel before any
        dispatch. A partner therefore cannot ask about the other partner.

        This method is deliberately a one-liner. All of the behaviour lives in
        `partner_contact.status_for` so the harness can drive it for real
        against a temp database, rather than asserting on the text of this file
        — the `f84f644` lesson, where grep-level partner tests passed while the
        feature had never once worked.

        It reads `contact_metadata()`, which does not SELECT her message body.
        Discretion here is a property of the query, not of the phrasing.
        """
        from modules import partner_contact

        return partner_contact.status_for(target)

    def _workspace_write(self, target: str) -> str:
        """
        Write or create a workspace file.
        Target format: "filepath|content"
        The pipe separates the path from the file content.
        """
        if not target or "|" not in target:
            return "Format: 'filepath|file content'. Pipe separates path from content."
        filepath, _, content = target.partition("|")
        filepath = filepath.strip()
        # Interpret literal \n sequences the LLM may output
        content = content.replace("\\n", "\n").replace("\\t", "\t")
        if not filepath:
            return "No file path specified for workspace write."
        # Truncating a key file destroys it exactly as thoroughly as unlinking it,
        # and this path had no check at all.
        protected = self._protected_path_problem(filepath)
        if protected:
            print(f"[ACTION ENGINE] workspace_write refused: protected path.", flush=True)
            return protected
        try:
            result = self.workspace_agent.write_file(filepath, content)
            # Inject written content into working memory so the LLM knows the
            # exact text for any subsequent patch command.
            preview = content[:400].replace("\n", "↵")
            memory.add_to_working_memory(
                "system",
                f"[workspace_write result — file now contains]: {preview}"
            )
            return result
        except Exception as e:
            print(f"[ACTION ENGINE] Workspace write failed: {e}")
            return f"Workspace write offline: {e}"

    #: Opt-in prefix on the PATH field meaning "change every occurrence"
    #: (roadmap §6.8.1 gap F). It rides on the path rather than as a fourth
    #: pipe-separated field because the split below is `maxsplit=2` — the
    #: replacement text is everything after the second pipe and may itself
    #: contain pipes, so a trailing flag field is impossible to parse. `*` is
    #: not a legal character in a Windows filename, so this cannot collide with
    #: a real path.
    PATCH_ALL_PREFIX = "*all*"

    def _workspace_patch(self, target: str) -> str:
        """
        Surgical find-and-replace in a workspace file.
        Target format: "filepath|search_string|replace_string"
        All three parts are required.

        Prefix the path with `*all*` to change EVERY occurrence. Without it, a
        search string matching more than once is refused rather than applied
        everywhere — until 2026-08-08 the default was replace-all and nothing
        passed a count, so an ambiguous edit rewrote every match silently.
        """
        if not target:
            return "No patch target specified."
        parts = target.split("|", 2)
        if len(parts) < 3:
            return "Format: 'filepath|search_string|replace_string'"
        filepath, search, replace = parts[0].strip(), parts[1], parts[2]
        replace_all = filepath.startswith(self.PATCH_ALL_PREFIX)
        if replace_all:
            filepath = filepath[len(self.PATCH_ALL_PREFIX):].strip()
        if not filepath:
            return "No file path specified for workspace patch."
        if not search:
            return "Search string cannot be empty."
        try:
            result = self.workspace_agent.patch_file(
                filepath, search, replace, replace_all=replace_all)
            return result
        except Exception as e:
            print(f"[ACTION ENGINE] Workspace patch failed: {e}")
            return f"Workspace patch offline: {e}"

    # ── Phase 6: GitHub Specialist handlers ──────────────────────────────────

    def _github_status(self, target: str) -> str:
        """
        Run git status on the active workspace repo.
        target: optional repo path (empty = WORKSPACE_DIR).
        """
        try:
            return self.github_agent.get_status(repo_path=target.strip() or None)
        except Exception as e:
            print(f"[ACTION ENGINE] GitHub status failed: {e}")
            return f"Git status offline: {e}"

    def _github_commit(self, target: str) -> str:
        """
        Stage all changes and commit.
        target format: "commit message"  OR  "repo_path|commit message"
        If a pipe is present the first segment is treated as the repo path.
        """
        try:
            repo_path = None
            msg = target.strip()
            if "|" in target:
                repo_path, _, msg = target.partition("|")
                repo_path = repo_path.strip() or None
                msg = msg.strip()
            if not msg:
                return "No commit message provided, Sir. Please specify a message."
            return self.github_agent.commit(msg, repo_path=repo_path)
        except Exception as e:
            print(f"[ACTION ENGINE] GitHub commit failed: {e}")
            return f"Git commit offline: {e}"

    def _github_push(self, target: str) -> str:
        """
        Push the current branch to origin.
        target: optional repo path (empty = WORKSPACE_DIR).
        """
        try:
            return self.github_agent.push(repo_path=target.strip() or None)
        except Exception as e:
            print(f"[ACTION ENGINE] GitHub push failed: {e}")
            return f"Git push offline: {e}"

    def _github_log(self, target: str) -> str:
        """
        Return last N commits in one-line format.
        target: optional count (default 5), or "repo_path|N".
        """
        try:
            repo_path = None
            n = 5
            t = (target or "").strip()
            if "|" in t:
                repo_path, _, rest = t.partition("|")
                repo_path = repo_path.strip() or None
                t = rest.strip()
            if t.isdigit():
                n = int(t)
            return self.github_agent.get_log(n=n, repo_path=repo_path)
        except Exception as e:
            print(f"[ACTION ENGINE] GitHub log failed: {e}")
            return f"Git log offline: {e}"

    def _github_diff(self, target: str) -> str:
        """Working-tree diff summary (`git diff --stat`). target: optional repo path."""
        try:
            return self.github_agent.get_diff(repo_path=target.strip() or None)
        except Exception as e:
            print(f"[ACTION ENGINE] GitHub diff failed: {e}")
            return f"Git diff offline: {e}"

    # ── Phase 6: Android TV ADB Skill Pack handlers ──────────────────────

    def _tv_power(self) -> str:
        """
        Toggle TV power using keyevent 26 (KEYCODE_POWER).
        Wakes the TV if in standby; sleeps it if active.
        """
        try:
            return self.tv_agent.tv_power_toggle()
        except Exception as e:
            print(f"[ACTION ENGINE] TV power toggle failed: {e}")
            return f"TV power toggle offline, Sir: {e}"

    def _tv_volume(self, target: str) -> str:
        """
        Adjust TV volume via ADB keyevents.
        target formats:
          "up"        → volume up 1 step
          "down"      → volume down 1 step
          "mute"      → toggle mute
          "up|5"      → volume up 5 steps
          "down|3"    → volume down 3 steps
        """
        try:
            direction = target.strip()
            steps = 1
            if "|" in direction:
                direction, _, n_str = direction.partition("|")
                direction = direction.strip()
                try:
                    steps = max(1, min(int(n_str.strip()), 20))
                except ValueError:
                    pass
            return self.tv_agent.tv_volume(direction, steps=steps)
        except Exception as e:
            print(f"[ACTION ENGINE] TV volume failed: {e}")
            return f"TV volume control offline, Sir: {e}"

    def _tv_launch_app(self, target: str) -> str:
        """
        Launch an app on the TV by name.
        target: plain English app name (e.g. 'netflix', 'youtube', 'prime video')
                OR an Android package ID (e.g. 'com.netflix.ninja').
        """
        try:
            if not target or not target.strip():
                return "Please specify an app name, Sir (e.g. 'YouTube', 'Netflix')."
            return self.tv_agent.tv_launch_app(target.strip())
        except Exception as e:
            print(f"[ACTION ENGINE] TV launch app failed: {e}")
            return f"TV app launch offline, Sir: {e}"



    # --- SMART HOME: AUTONOMOUS TV CONNECTION ---
    def _sweep_for_tv(self) -> int:
        """Uses your proven working ZeroConf logic to find the active port."""
        print("[ACTION ENGINE] Searching for Android TV devices on network...")
        found_port = None

        def on_service_state_change(zeroconf, service_type, name, state_change):
            nonlocal found_port
            if state_change is ServiceStateChange.Added:
                info = zeroconf.get_service_info(service_type, name)
                if info and info.addresses:
                    ip = ".".join(map(str, info.addresses[0]))
                    if ip == self.tv_ip:
                        found_port = info.port
                        print(f"[+] Discovered TV: {name} at {ip}:{found_port}")

        zc = Zeroconf()
        browser = ServiceBrowser(zc, "_adb._tcp.local.", handlers=[on_service_state_change])
        
        # Proven 5-second wait time from your script
        time.sleep(5) 
        zc.close()
        return found_port

    def _connect_tv(self):
        """Attempts fast connection, falls back to radar sweep if port changed."""
        if self.adb_device and self.adb_device.available:
            return True

        print(f"[ACTION ENGINE] Attempting TV uplink at {self.tv_ip}:{self.tv_port}...")
        self.adb_device = AdbDeviceTcp(self.tv_ip, self.tv_port, default_transport_timeout_s=9.)
        
        try:
            # FAST PATH: Try the last known port (should be instant)
            self.adb_device.connect(rsa_keys=[self.signer])
            print("[+] Connected to TV instantly.")
            return True
        except Exception as e:
            print(f"[ACTION ENGINE] Primary connection failed: {e}. Launching Radar...")
            
            # SLOW PATH: Sweep the network using your working logic
            new_port = self._sweep_for_tv()
            
            if new_port:
                self.tv_port = new_port
                
                # Save it so the next command is fast again
                with open(self.tv_config_file, "w") as f:
                    json.dump({"port": new_port}, f)
                
                # Reconnect with new port
                self.adb_device = AdbDeviceTcp(self.tv_ip, self.tv_port, default_transport_timeout_s=9.)
                try:
                    self.adb_device.connect(rsa_keys=[self.signer])
                    print(f"[+] Reconnected to TV at {self.tv_ip}:{self.tv_port}")
                    return True
                except Exception as e2:
                    print(f"[!] Failed to connect after sweep: {e2}")
                    return False
            else:
                print("[!] No Android TV devices found broadcasting on that IP.")
                return False

    def _control_tv(self, command: str) -> str:
        print(f"[ACTION ENGINE] Initiating TV protocol: {command}")
        
        if not self._connect_tv():
            return "Dining Room TV is offline or out of reach, sir. It may be powered off."

        key_map = {
            "power": "26", "home": "3", "mute": "164",
            "volume_up": "24", "volume_down": "25", "play_pause": "85",
            "back": "4", "up": "19", "down": "20", "left": "21", "right": "22", "select": "66",
            "youtube": "am start -a android.intent.action.VIEW -d 'vnd.youtube://'",
            "netflix": "am start -n com.netflix.ninja/.MainActivity"
        }

        cmd = command.lower().strip()
        
        # Handle discrete volume commands
        if cmd == "volume_up_5":
            try:
                for _ in range(5):
                    self.adb_device.shell("input keyevent 24")
                    time.sleep(0.1)
                return "Increased TV volume by 5 notches."
            except Exception as e:
                return f"Error controlling volume: {e}"
        elif cmd == "volume_down_5":
            try:
                for _ in range(5):
                    self.adb_device.shell("input keyevent 25")
                    time.sleep(0.1)
                return "Decreased TV volume by 5 notches."
            except Exception as e:
                return f"Error controlling volume: {e}"

        if cmd in key_map:
            try:
                action_code = key_map[cmd]
                if action_code.startswith("am start"):
                    self.adb_device.shell(action_code)
                    return f"Launching {cmd} on the Dining Room TV, sir."
                else:
                    self.adb_device.shell(f"input keyevent {action_code}")
                    return f"TV {cmd} command executed successfully."
            except Exception as e:
                return f"I encountered an error transmitting to the TV: {e}"
        else:
            return f"I don't have a protocol mapped for the TV command '{cmd}', sir."

    def _tv_type(self, text: str) -> str:
        """Injects text directly into the TV's active text box."""
        print(f"[ACTION ENGINE] Typing on TV: {text}")
        if not self._connect_tv(): return "I am unable to reach the TV, sir."
        
        # ADB requires spaces to be formatted as %s. That alone is NOT escaping:
        # `text` is model-supplied, so `x;reboot` reached the TV's shell as two
        # commands. shlex.quote makes it a single argument.
        import shlex as _shlex
        formatted_text = _shlex.quote(text.replace(" ", "%s"))
        try:
            self.adb_device.shell(f"input text {formatted_text}")
            self.adb_device.shell("input keyevent 66") # Press Enter automatically
            return f"I have typed '{text}' on the screen, sir."
        except Exception as e:
            return f"Failed to input text: {e}"

    def _tv_search(self, query: str) -> str:
        """YouTube on TV via TVAgent sniper (deep link + ENTER)."""
        print(f"[ACTION ENGINE] TV YouTube Search: {query}")
        query_lower = query.lower().replace("youtube:", "").strip()
        try:
            return self.tv_agent.tv_play_media(f"youtube:{query_lower}")
        except Exception as e:
            return f"Search failed: {e}"

    def _tv_play_media(self, target: str) -> str:
        """
        Route media play/search requests to TVAgent.tv_play_media.

        target formats:
          "netflix: Stranger Things"  → deep-link directly into Netflix
          "Stranger Things"           → no colon → agent queries installed apps
                                        and returns [ACTION REQUIRED] for the brain
                                        to ask the user which app to use.
        """
        try:
            return self.tv_agent.tv_play_media(target)
        except Exception as e:
            print(f"[ACTION ENGINE] tv_play_media failed: {e}")
            return f"TV media playback offline, Sir: {e}"

    # ==========================================
    # PHASE 8.2: OS MACRO ENGINE
    # ==========================================

    def _os_macro(self, target: str) -> str:
        """
        Dispatch an OS-level macro by name.

        target examples:
          "deep_work"                          → launch VS Code + dev URL, kill distractions
          "deep_work:http://localhost:5173"    → same but override dev URL
          "diagnostic"                         → open Task Manager + terminal
          "entertainment"                      → open YouTube + VLC

        All subprocess launches inside MacroAgent use Popen so they are
        non-blocking and never stall the J.A.R.V.I.S. event loop.
        """
        print(f"[ACTION ENGINE] OS Macro dispatch → target='{target}'", flush=True)
        if not target or not target.strip():
            return (
                "Please specify a macro target, Sir. "
                "Available: deep_work, shallow_work, diagnostic, entertainment."
            )
        try:
            return self.macro_agent.run(target)
        except Exception as e:
            print(f"[ACTION ENGINE] OS Macro failed: {e}", flush=True)
            return f"Macro execution encountered an error, Sir: {e}"

    def _movie_protocol(self) -> str:
        """A multi-step macro to set up the room."""
        print("[ACTION ENGINE] Executing Movie Protocol")
        if not self._connect_tv(): return "I am unable to reach the TV, sir."
        
        try:
            self.adb_device.shell("input keyevent 26") # Ensure TV is awake
            time.sleep(1.5) # Wait for OS to boot
            self.adb_device.shell("am start -n com.netflix.ninja/.MainActivity")
            
            # Blast the volume up a few notches
            for _ in range(3):
                self.adb_device.shell("input keyevent 24")
                time.sleep(0.2)
                
            return "Movie protocol engaged. TV awake, audio primed, and Netflix launched."
        except Exception as e:
            return f"Protocol interrupted: {e}"

    def _sleep_protocol(self) -> str:
        """Clears PC displays and pauses media for sleep."""
        print("[ACTION ENGINE] Executing Sleep Protocol")
        
        # Pause background media on the PC
        try:
            import ctypes
            # VK_MEDIA_PLAY_PAUSE = 0xB3
            ctypes.windll.user32.keybd_event(0xB3, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xB3, 0, 2, 0)
            print("[ACTION ENGINE] PC Media Paused.")
        except Exception as e:
            print(f"[ACTION ENGINE] Failed to pause PC media: {e}")
            
        # UI close_display intent is handled separately in main.py, so we just return the command
        return "UI_WIDGET_TOGGLE:close_display"

    # --- NEW: TV STATUS INTERROGATOR ---
    def get_tv_status(self) -> dict:
        """Polls the TV for its current power state and active app without freezing the server."""
        import socket
        import subprocess

        # 1. THE LIGHTNING CHECK: Only attempt a heavy connection if the port is physically open
        if not (self.adb_device and self.adb_device.available):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0) # Increased timeout for slow Android TV ADB ports
            try:
                # Try to knock on the exact ADB port
                s.connect((self.tv_ip, self.tv_port))
                s.close()
                
                # If the knock was answered, the TV woke up! Do the heavy connection.
                if not self._connect_tv():
                    return {"status": "online", "power": "standby", "app": "none"}
            except Exception:
                s.close()
                # Port is closed. Try a ping to see if the device is at least on the network.
                try:
                    ping_result = subprocess.run(
                        ["ping", "-n", "1", "-w", "1000", self.tv_ip],
                        capture_output=True, text=True, timeout=3
                    )
                    if ping_result.returncode == 0:
                        # Device is on the network but ADB port is closed (TV screen off but standby)
                        return {"status": "online", "power": "standby", "app": "none"}
                except Exception:
                    pass
                # Both ADB and ping failed. TV is truly offline.
                return {"status": "offline", "power": "off", "app": "none"}

        # 2. We are fully connected. Ask the TV what it is doing.
        try:
            power_output = self.adb_device.shell("dumpsys power | grep -E 'mWakefulness=|mInteractive=|Display Power'")
            is_on = (
                "mWakefulness=Awake" in power_output or 
                "mInteractive=true" in power_output or 
                "state=ON" in power_output
            )

            if not is_on:
                return {"status": "online", "power": "off", "app": "none"}

            app_output = self.adb_device.shell("dumpsys window windows | grep -E 'mCurrentFocus'")
            current_app = "Unknown"

            if "com.netflix.ninja" in app_output:
                current_app = "Netflix"
            elif "com.google.android.youtube.tv" in app_output:
                current_app = "YouTube"
            elif "com.spotify.tv.android" in app_output:
                current_app = "Spotify"
            elif "mCurrentFocus=null" in app_output or "com.google.android.tvlauncher" in app_output:
                current_app = "Home Screen"
            elif "u0 " in app_output:
                try:
                    current_app = app_output.split("u0 ")[1].split("/")[0]
                except IndexError:
                    current_app = "Unknown App"

            return {"status": "online", "power": "on", "app": current_app}

        except Exception as e:
            # If the connection drops mid-poll, clear the device so we use the Lightning Check next time
            self.adb_device = None
            return {"status": "offline", "power": "off", "app": "none"}

    # --- OS ACTIONS ---
    def _native_app_launcher(self, app_name: str) -> str:
        """
        Phase 8.6.10 Smart Launcher — with post-launch window tracking.
        Delegates resolution + execution to OSAgent.launch_application():
          1. Queries the startup AppIndexer (Start Menu .lnk + Registry App Paths).
          2. Fuzzy-matches the spoken name via difflib (typo-tolerant).
          3. Launches via os.startfile() — ShellExecute — handles UWP, exe, lnk.

        After launch, captures the new window's hwnd/pid via post_launch_focus
        so that subsequent ghost_type / ghost_save_file calls can target the
        correct window, preventing the 'open X and write Y into random window' bug.
        """
        import psutil as _psutil

        print(f"[ACTION ENGINE] Native App Launcher for: {app_name}")

        # Snapshot PIDs before launch to detect the new process
        pids_before = {p.pid for p in _psutil.process_iter()}

        result_str = self.os_agent.launch_application(app_name)

        # If the launch itself reported failure, don't bother tracking
        if self._is_failure(result_str, "native_app_launcher"):
            return result_str

        # Record the app name for session tracking (even before window resolves)
        self._last_launched_app = app_name
        self._last_launched_started_at = time.time()

        # Try to identify the new PID
        time.sleep(0.6)
        pids_after = {p.pid for p in _psutil.process_iter()}
        new_pids = pids_after - pids_before

        launched_pid = None
        if new_pids:
            # Prefer the new PID whose process name resembles the app
            app_lower = app_name.lower().replace(" ", "")
            for pid in new_pids:
                try:
                    proc = _psutil.Process(pid)
                    pname = proc.name().lower().replace(" ", "")
                    if app_lower in pname or pname.replace(".exe", "") in app_lower:
                        launched_pid = pid
                        break
                except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                    continue
            if not launched_pid:
                # Take the first new PID as best guess
                launched_pid = next(iter(new_pids))

        # Use post_launch_focus to resolve hwnd and assert focus
        if launched_pid:
            self._last_launched_pid = launched_pid
        focus_result = self.human_gui_agent.post_launch_focus(app_name, pid=launched_pid)
        self._last_launched_hwnd = focus_result.get("hwnd")
        if focus_result.get("pid"):
            self._last_launched_pid = focus_result["pid"]

        print(
            f"[ACTION ENGINE] Post-launch tracking: app='{app_name}', "
            f"pid={self._last_launched_pid}, hwnd={self._last_launched_hwnd}"
        )

        return result_str



    def _launch_app(self, app_name: str) -> str:
        """Translates app names to Windows executables and launches them safely via GUI Search Fallback."""
        print(f"[ACTION ENGINE] Launching app: {app_name}")
        app_name_lower = app_name.lower().strip()

        # --- Known desktop apps — short-circuit past web gate ---
        # Prevents "open google chrome" from matching the "google" web entry.
        _KNOWN_DESKTOP_APPS = frozenset({
            "google chrome", "chrome",
            "microsoft edge", "edge",
            "firefox", "mozilla firefox",
            "spotify", "discord", "slack",
            "telegram", "telegram desktop",
        })
        if app_name_lower not in _KNOWN_DESKTOP_APPS:
            # --- FIX: Web-based apps that don't have .exe files ---
            # EXACT match only — no substring matching.
            web_apps = {
                "youtube": "https://www.youtube.com",
                "spotify": "https://open.spotify.com",
                "gmail": "https://mail.google.com",
                "google": "https://www.google.com",
            }
            if app_name_lower in web_apps:
                url = web_apps[app_name_lower]
                webbrowser.open(url)
                return f"Opening {app_name_lower.title()} in your browser, sir."

        # Route to Human GUI Agent for physical OS automation
        return self.human_gui_agent.execute_gui_action("smart_open_app", app_name)

    def _normalize_hud_widget_id(self, target: str, *, default: str = "vitals") -> str:
        """Map brain/target tokens to HUD widget ids: vitals, mail, calendar, calculator, notepad, browser, camera."""
        t = (target or "").lower().strip()
        if not t:
            t = default
        if t in ("camera", "feed", "optical", "optical feed", "vision", "cam", "webcam", "eyes", "video"):
            return "camera"
        if t in ("map", "maps", "location", "gps", "navigation", "navigate", "directions", "where i stay", "tactical map"):
            return "map"
        if t in ("mail", "email", "gmail", "inbox", "messages"):
            return "mail"
        if t in ("calendar", "schedule", "agenda", "events", "today"):
            return "calendar"
        if t in ("calculator", "calc"):
            return "calculator"
        if t in ("notepad", "sticky", "note", "notes", "scratch"):
            return "notepad"
        if t in ("browser", "web", "chrome", "internet"):
            return "browser"
        if t in ("vitals", "health", "fitness", "biometrics", "steps", "heart"):
            return "vitals"
        if any(k in t for k in ("mail", "email", "gmail", "inbox")):
            return "mail"
        if any(k in t for k in ("calendar", "schedule", "agenda", "event")):
            return "calendar"
        if "calculat" in t or t == "calc":
            return "calculator"
        if any(k in t for k in ("notepad", "sticky note", "scratch")):
            return "notepad"
        if any(k in t for k in ("browser", "chrome ", " web")):
            return "browser"
        if any(k in t for k in ("health", "vital", "biometric", "fitness", "step")):
            return "vitals"
        if any(k in t for k in ("camera", "optical", "webcam", "what you see", "what you're seeing")):
            return "camera"
        return default

    def _hud_open_widget(self, target: str) -> dict:
        w = self._normalize_hud_widget_id(target, default="vitals")
        return {"action_type": "hud_open_widget", "widget": w}

    def _hud_close_widget(self, target: str) -> dict:
        w = self._normalize_hud_widget_id(target, default="vitals")
        return {"action_type": "hud_close_widget", "widget": w}

    def _close_app(self, app_name: str) -> str:
        """
        Phase 8.6.11 — psutil-based process terminator with shell protection.

        Strategy:
          1. HUD media tokens → return HUD_MEDIA_CLOSE_REQUEST (no OS process).
          2. Web-only services → return graceful refusal (cannot close tabs).
          3. Resolve the spoken app name to an executable filename using the
             same AppIndexer dictionary that launched it (Phase 8.6.9).
             A hardcoded alias table covers common spoken→exe mappings.
          4. Shell protection gate — explorer.exe is NEVER hard-killed because
             it is the Windows Taskbar/Desktop shell. Killing it crashes the UI.
          5. Terminate every running process whose .exe name matches via psutil.
             Falls back to taskkill /F if psutil finds nothing.

        This completely removes reliance on HWND tracking or SetForegroundWindow
        — it works even when the app is minimised, UAC-elevated, or frozen.
        """
        import psutil
        from modules.os_agent import AppIndexer, _clean_name

        raw_name = (app_name or "").strip()
        app_lower = raw_name.lower()

        # ── 1. HUD-embedded media: no OS process to kill ─────────────────────
        if any(
            t in app_lower
            for t in ("music", "media", "playback", "hud", "uplink", "song", "video", "jarvis media")
        ):
            return "HUD_MEDIA_CLOSE_REQUEST"

        # ── Phase 8.6.11 Bug 3: Web-only services — cannot close browser tabs ─
        # These were launched via the web-first gate (webbrowser.open). There is
        # no dedicated process to kill; attempting to do so would risk terminating
        # the user's entire Chrome/Edge session.
        _WEB_ONLY_SERVICES: frozenset[str] = frozenset({
            "youtube", "spotify web", "spotify", "gmail", "google",
            "google drive", "google docs", "google sheets", "google slides",
            "netflix", "prime video", "hotstar", "github", "chatgpt", "claude",
        })
        # Match: spoken name IS a web service — EXACT match only.
        # "google chrome" must NOT match "google" via substring; it's a real
        # desktop app with a killable process.
        _is_web_launch = app_lower in _WEB_ONLY_SERVICES
        if _is_web_launch:
            # Extra check: if the alias table has a local .exe for this name
            # (e.g. "spotify" → "Spotify.exe" desktop app), allow the kill.
            # Only block if the alias resolves to explorer.exe or is absent.
            _web_alias_check = {
                "spotify":      "Spotify.exe",   # Desktop app installed?
            }
            _known_local_exe = _web_alias_check.get(app_lower)
            if not _known_local_exe:
                print(
                    f"[ACTION ENGINE] close_app: '{raw_name}' is a web-only service — "
                    f"refusing tab kill.",
                    flush=True,
                )
                return (
                    f"I cannot close specific browser tabs natively, Sir. "
                    f"I can only terminate local applications. "
                    f"To close {raw_name.title()}, please close the tab manually."
                )

        # ── 2. Resolve exe name ───────────────────────────────────────────────
        # Hardcoded alias table: spoken name → exact Windows process image name.
        # These take priority over the AppIndexer to handle common ambiguities.
        _EXE_ALIASES: dict[str, list[str]] = {
            "notepad":       ["notepad.exe"],
            "chrome":        ["chrome.exe"],
            "google chrome": ["chrome.exe"],
            "edge":          ["msedge.exe"],
            "microsoft edge":["msedge.exe"],
            "firefox":       ["firefox.exe"],
            "spotify":       ["Spotify.exe"],
            "vlc":           ["vlc.exe"],
            "calculator":    ["CalculatorApp.exe", "win32calc.exe", "calc.exe"],
            "calc":          ["CalculatorApp.exe", "win32calc.exe", "calc.exe"],
            "paint":         ["mspaint.exe"],
            "task manager":  ["Taskmgr.exe"],
            "file explorer": ["explorer.exe"],
            "explorer":      ["explorer.exe"],
            "word":          ["WINWORD.EXE"],
            "excel":         ["EXCEL.EXE"],
            "powerpoint":    ["POWERPNT.EXE"],
            "outlook":       ["OUTLOOK.EXE"],
            "teams":         ["Teams.exe"],
            "discord":       ["Discord.exe"],
            "zoom":          ["Zoom.exe"],
            "obs":           ["obs64.exe", "obs32.exe"],
            "vs code":       ["Code.exe"],
            "visual studio code": ["Code.exe"],
            "code":          ["Code.exe"],
        }

        exe_targets: list[str] = []

        if app_lower in _EXE_ALIASES:
            exe_targets = _EXE_ALIASES[app_lower]
        else:
            # Try AppIndexer: the resolved path is either an .lnk or an .exe.
            _, resolved_path = AppIndexer.get().resolve(raw_name)
            if resolved_path:
                exe_name = Path(resolved_path).name
                # .lnk files aren't processes; strip to just the base name guess
                if exe_name.lower().endswith(".lnk"):
                    exe_targets = [f"{_clean_name(Path(resolved_path).stem)}.exe"]
                else:
                    exe_targets = [exe_name]
            else:
                # Last resort: assume spoken name is the exe stem
                exe_targets = [f"{app_lower.replace(' ', '')}.exe"]

        print(
            f"[ACTION ENGINE] close_app: '{raw_name}' → targets={exe_targets}",
            flush=True,
        )

        # ── Phase 8.6.11 Bug 1: Windows Shell protection ──────────────────────
        # explorer.exe IS the Windows Desktop + Taskbar shell. Hard-killing it
        # causes the entire shell environment to crash (black screen, no taskbar).
        # This guard fires BEFORE psutil and the taskkill fallback so there is
        # no code path that can accidentally kill explorer.exe.
        if any(t.lower() == "explorer.exe" for t in exe_targets):
            print(
                "[ACTION ENGINE] close_app: explorer.exe is the Windows Shell — "
                "termination blocked to prevent UI crash.",
                flush=True,
            )
            return (
                "Error: Cannot hard-kill explorer.exe as it will crash the "
                "Windows Shell. Skipping termination, Sir."
            )

        # ── 5. Terminate via psutil ───────────────────────────────────────────
        killed: list[str] = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                proc_exe = (proc.info["name"] or "").lower()
                for target_exe in exe_targets:
                    if proc_exe == target_exe.lower():
                        proc.kill()
                        killed.append(f"{proc.info['name']}(pid={proc.info['pid']})")
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if killed:
            print(f"[ACTION ENGINE] Terminated: {', '.join(killed)}", flush=True)
            return f"Task terminated. {raw_name.title()} is closed, Sir."

        # ── 6. psutil found nothing — taskkill hard fallback ──────────────────
        print(
            f"[ACTION ENGINE] psutil found no matching processes for {exe_targets}; "
            f"trying taskkill fallback.",
            flush=True,
        )
        from modules import shell_safety
        any_killed = False
        for target_exe in exe_targets:
            # Secondary shell protection — should never reach here, but belt-and-suspenders.
            if target_exe.lower() == "explorer.exe":
                continue
            # exe_targets falls through to f"{app_lower.replace(' ', '')}.exe" when
            # the name is not a known alias — the model's raw close_app target with
            # only spaces removed. Quotes and & survived that, and this line used to
            # interpolate them straight into a command line.
            if not shell_safety.is_shell_safe(target_exe):
                print(f"[ACTION ENGINE] taskkill refused: "
                      f"{shell_safety.reject_reason(target_exe)}", flush=True)
                continue
            # taskkill is a real executable, not a cmd builtin, so no shell is
            # needed at all — the argument list ends the injection outright.
            try:
                proc = subprocess.run(["taskkill", "/IM", target_exe, "/F"],
                                      shell=False, capture_output=True, timeout=15)
                if proc.returncode == 0:
                    any_killed = True
            except (subprocess.SubprocessError, OSError) as _tk_err:
                print(f"[ACTION ENGINE] taskkill failed for {target_exe}: {_tk_err}",
                      flush=True)

        if any_killed:
            return f"Task terminated. {raw_name.title()} is closed, Sir."

        return f"I couldn't find a running process for '{raw_name}', Sir. It may already be closed."

    def _protected_path_problem(self, target_path: str) -> str | None:
        """Instance view of `protected_path_problem` — see that function."""
        return protected_path_problem(target_path,
                                      self.protected_files, self.protected_folders)

    def _delete_file(self, target_path: str) -> str:
        try:
            path = Path(target_path).resolve()
            protected = self._protected_path_problem(target_path)
            if protected:
                print(f"[ACTION ENGINE] delete refused: {path.name} is protected.",
                      flush=True)
                return protected
            for restricted in self.restricted_folders:
                if restricted in path.parents or path == restricted:
                    return "Security override triggered."
            if path.is_file():
                path.unlink()
                return "File successfully deleted, sir."
            elif path.is_dir():
                shutil.rmtree(path)
                return "Directory removed."
            else:
                return "I couldn't find that specific target."
        except Exception as e:
            return f"Deletion protocol failed: {e}"

    def _open_link(self, url: str) -> str:
        print(f"[ACTION ENGINE] Navigating to: {url}")
        try:
            if not url.startswith("http"):
                url = f"https://{url}"
            webbrowser.open(url)
            return "Opening the requested page now, sir."
        except Exception as e:
            return f"Browser glitch: {e}"

    async def _web_browse(self, target: str) -> str:
        """Agentic web browsing via Playwright."""
        return await self.web_agent.browse(target)

    def _tavily_search(self, query: str) -> str:
        """
        Phase 3: Tavily AI search — fast, summarised info-gathering. Default for quick
        lookups (cheaper than driving Playwright). Requires TAVILY_API_KEY; returns a
        sentinel string if unconfigured so callers can fall back gracefully.
        """
        if not query or not str(query).strip():
            return "No search query provided, Sir."
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return "TAVILY_UNCONFIGURED"
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=api_key)
            resp = client.search(
                query=str(query).strip(),
                max_results=5,
                search_depth="basic",
                include_answer=True,
            )
            lines: list[str] = []
            answer = resp.get("answer")
            if answer:
                lines.append(f"Summary: {answer}")
            for r in resp.get("results", []):
                title = r.get("title", "")
                content = (r.get("content", "") or "").strip()
                if title or content:
                    lines.append(f"Title: {title} | Data: {content}")
            if not lines:
                return "I searched, Sir, but Tavily returned no relevant data."
            print(f"[ACTION ENGINE] Tavily returned {len(resp.get('results', []))} result(s).", flush=True)
            return "\n".join(lines)
        except Exception as e:
            print(f"[ACTION ENGINE] Tavily search failed: {e}", flush=True)
            return f"Error during Tavily search: {e}"

    async def _run_autopilot(self, target) -> str:
        """
        Run the Overnight Autopilot (Figma→code LangGraph pipeline) to completion.
        target: "file_key" | "file_key|out_dir" | "file_key|out_dir|token".
        Awaited here so the queued task's status reflects the real outcome.
        """
        parts = [p.strip() for p in str(target or "").split("|")]
        file_key = parts[0] if parts else ""
        if not file_key:
            return "No Figma file key provided for the autopilot, Sir."
        out_dir = parts[1] if len(parts) > 1 and parts[1] else "autopilot_output"
        token = parts[2] if len(parts) > 2 and parts[2] else None
        try:
            from modules import agent_worker
            final = await agent_worker.run_autopilot_task(file_key, out_dir=out_dir, token=token)
            status = final.get("status")
            files = list((final.get("code") or {}).keys())
            if status == "saved":
                return f"Autopilot complete. Generated {', '.join(files) or 'files'} in {out_dir}."
            return f"Autopilot finished with status '{status}', Sir."
        except Exception as e:
            print(f"[ACTION ENGINE] Autopilot run failed: {e}", flush=True)
            return f"Autopilot failed: {e}"

    def _web_search(self, query: str) -> str:
        print(f"[ACTION ENGINE] Initiating research for: {query}")
        # Phase 3: prefer Tavily for speed/quality when configured; fall back to DDGS.
        if os.getenv("TAVILY_API_KEY"):
            tav = self._tavily_search(query)
            if tav and tav != "TAVILY_UNCONFIGURED" and not tav.startswith("Error") \
               and "no relevant data" not in tav.lower():
                return tav
            print("[ACTION ENGINE] Tavily unavailable/empty — falling back to DDGS.", flush=True)
        try:
            results = []
            timelimit = None
            query_lower = query.lower()
            
            # Auto-apply a 1-week or 1-day time limit for news/sports/time-sensitive queries
            if any(w in query_lower for w in ["today", "yesterday", "latest", "now", "recent", "score", "match", "news", "result"]):
                timelimit = "w"  # Past week ensures we catch recent match results without being overly restrictive
                
            with DDGS() as ddgs:
                search_data = list(ddgs.text(query, max_results=5, timelimit=timelimit))
                
                # Fallback if strict timelimit yields nothing
                if not search_data and timelimit:
                    search_data = list(ddgs.text(query, max_results=5))
                    
                for r in search_data:
                    results.append(f"Title: {r.get('title', '')} | Data: {r.get('body', '')}")
            if not results:
                return "I searched the global archives, sir, but found no relevant data."
            return "\n".join(results)
        except Exception as e:
            return f"Error during research: {e}"

    def _web_search_image(self, query: str) -> dict:
        print(f"[ACTION ENGINE] Initiating image search for: {query}")
        try:
            with DDGS() as ddgs:
                results = list(ddgs.images(query, max_results=5))
                for r in results:
                    image_url = r.get('image') or r.get('url') 
                    if image_url:
                        return {"success": True, "url": image_url, "title": r.get('title', query)}
                return {"success": False, "error": "No valid image URLs found."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _play_music(self, target: str) -> str:
        print(f"[ACTION ENGINE] Playing music: {target}")
        chrome_path = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"

        # §6.8.2 wave 2: the service words used to be stripped as SUBSTRINGS, so
        # "on" was removed from the middle of ordinary titles — "Moonlight"
        # searched for "Molight". `clean_music_query` strips whole words only.
        from modules.media_query import SPOTIFY, clean_music_query
        service, search_query = clean_music_query(target)

        if service == SPOTIFY:
            search_query = search_query.replace(" ", "%20")
            url = f"https://open.spotify.com/search/{search_query}" if search_query else "https://open.spotify.com"
            return {"success": True, "action_type": "play_youtube", "url": url}
        else:
            # Default to YouTube
            if search_query:
                # Try to get the direct video link instead of search results
                try:
                    with DDGS() as ddgs:
                        results = list(ddgs.text(f"site:youtube.com watch {search_query}", max_results=1))
                        if results and "href" in results[0]:
                            url = results[0]["href"]
                        else:
                            url = f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}"
                except Exception:
                    url = f"https://www.youtube.com/results?search_query={search_query.replace(' ', '+')}"
            else:
                url = "https://www.youtube.com"
                
            return {"success": True, "action_type": "play_youtube", "url": url}

    def _remember_fact(self, target: str) -> str:
        try:
            if ":" in target:
                category, fact = target.split(":", 1)
                category = category.strip()
                fact = fact.strip()
            else:
                category = "Fact"
                fact = target.strip()
            
            # Map legacy action to the modern Tier 2 memory manager
            import memory_manager
            # Coerce category into valid enum if possible, otherwise default to Fact
            valid_cats = ["Preference", "Correction", "Fact"]
            cat_safe = category if category in valid_cats else "Fact"
            
            memory_manager.add_memory(content=fact, category=cat_safe, user="KAUSTAV")
            return "Committed to memory, Sir."
        except Exception as e:
            return f"Error: {e}"

    def _read_screen(self) -> str:
        print("[ACTION ENGINE] Executing Screen Reader OCR...")
        from modules.screen_reader import read_active_screen

        text = read_active_screen()
        # The result goes back to the LLM as research data
        return f"SCREEN CONTENTS:\n{text}"