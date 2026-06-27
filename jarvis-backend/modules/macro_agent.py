"""
modules/macro_agent.py
======================
Phase 8.2 — OS Macro Engine

Defines a scalable registry of named OS-level macros that execute
multi-step subprocess sequences without blocking the J.A.R.V.I.S.
event loop (all launches use subprocess.Popen).

Architecture
------------
  MacroAgent.run(target)
    └── dispatches to a registered handler via MACRO_REGISTRY dict
        └── each handler uses Popen() + optional kill_process_by_name()

Adding a new macro
------------------
1. Define a _macro_<name> method.
2. Register it in MACRO_REGISTRY inside __init__.
3. Add the intent string to governance.json (AUTO tier).
4. Add an example sentence + routing rule to brain.py BASE_CORE.

Non-blocking contract
---------------------
All subprocess launches MUST use subprocess.Popen (never subprocess.run
or os.system for launchers) so the main FastAPI event loop is never
stalled waiting for a GUI process to exit.

Killing a distraction is intentionally best-effort: if the target is not
running the kill is silently ignored — never raises.
"""

import os
import platform
import subprocess
import time
from typing import Callable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _popen(cmd: list[str], *, shell: bool = False) -> subprocess.Popen:
    """
    Launch a process without blocking.
    On Windows we use DETACHED_PROCESS so the child survives if the Python
    host exits, and CREATE_NO_WINDOW so no flashing console appears.
    """
    flags = 0
    if platform.system() == "Windows":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(
        cmd,
        shell=shell,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _open_url(url: str) -> None:
    """Open a URL in the default browser (non-blocking)."""
    if platform.system() == "Windows":
        _popen(["cmd", "/c", "start", "", url], shell=False)
    elif platform.system() == "Darwin":
        _popen(["open", url])
    else:
        _popen(["xdg-open", url])


def _kill_process_by_name(process_name: str) -> bool:
    """
    Gracefully terminate all processes whose image name matches *process_name*.
    Returns True if at least one process was killed, False if none found.
    This is best-effort — never raises.
    """
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["taskkill", "/F", "/IM", process_name],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        else:
            result = subprocess.run(
                ["pkill", "-f", process_name],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
    except Exception as exc:
        print(f"[MACRO AGENT] kill_process_by_name({process_name}) failed silently: {exc}")
        return False


# ---------------------------------------------------------------------------
# MacroAgent
# ---------------------------------------------------------------------------

class MacroAgent:
    """
    Registry-driven OS macro engine.

    Each registered macro is a callable that takes no arguments and
    returns a TTS-friendly status string.
    """

    # Known distracting processes to terminate in Deep Work mode.
    # Extend this list as needed — the kill is silent if not running.
    DISTRACTION_PROCESSES: list[str] = [
        "Discord.exe",
        "Spotify.exe",
        "steam.exe",
        "EpicGamesLauncher.exe",
        "slack.exe",
    ]

    # Default dev URL opened alongside the editor in Deep Work mode.
    # Override by passing a custom target, e.g. "deep_work:http://localhost:3000"
    DEFAULT_DEV_URL: str = "http://localhost:3000"

    def __init__(self) -> None:
        # --- Macro registry: intent_target → handler -------------------------
        # All handlers must match the signature: () -> str
        self.MACRO_REGISTRY: dict[str, Callable[[], str]] = {
            "deep_work":    self._macro_deep_work,
            "shallow_work": self._macro_shallow_work,
            "diagnostic":   self._macro_system_diagnostic,
            "entertainment": self._macro_entertainment,
            # Aliases so the brain can use natural targets
            "deep work":    self._macro_deep_work,
            "deep_work_mode": self._macro_deep_work,
            "end_deep_work": self._macro_shallow_work,
            "exit_deep_work": self._macro_shallow_work,
            "system_diagnostic": self._macro_system_diagnostic,
            "system diagnostic": self._macro_system_diagnostic,
            "entertainment_mode": self._macro_entertainment,
        }
        self._dev_url: str = self.DEFAULT_DEV_URL

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, target: str) -> str:
        """
        Dispatch *target* to the matching macro handler.

        target format:
          "deep_work"                    → run default deep work macro
          "shallow_work" / "end_deep_work" → end deep-work session (HUD + voice ack only)
          "deep_work:http://localhost:5173"  → override dev URL for this run
          "diagnostic"                   → system diagnostic macro
          "entertainment"                → entertainment mode macro
        """
        raw = (target or "").strip().lower()

        # Allow inline URL override: "deep_work:http://..."
        url_override: str | None = None
        if ":" in raw and not raw.startswith(("http:", "https:")):
            key_part, _, url_part = raw.partition(":")
            raw_key = key_part.strip()
            url_override = url_part.strip()
        else:
            raw_key = raw

        handler = self.MACRO_REGISTRY.get(raw_key)
        if handler is None:
            known = ", ".join(sorted({k for k in self.MACRO_REGISTRY if "_" not in k or k.count("_") < 2}))
            return (
                f"Unrecognised macro target '{target}', Sir. "
                f"Available macros: {known}."
            )

        # Temporarily override dev URL if provided
        if url_override:
            self._dev_url = url_override

        try:
            result = handler()
        finally:
            # Always restore the default dev URL after the call
            self._dev_url = self.DEFAULT_DEV_URL

        return result

    # ------------------------------------------------------------------
    # Macro Handlers
    # ------------------------------------------------------------------

    def _macro_deep_work(self) -> str:
        """
        Deep Work Mode
        --------------
        1. Kill known distraction apps (Discord, Spotify, Steam, etc.)
        2. Open VS Code in the current working directory (non-blocking).
        3. Open the configured dev URL in the default browser.

        All steps are attempted independently — a failure in one does
        not abort the rest.
        """
        print("[MACRO AGENT] ▶ Initiating Deep Work Mode...", flush=True)
        try:
            from socket_manager import schedule_ui_update

            schedule_ui_update(
                {
                    "type": "ui_state",
                    "widget": "system_log",
                    "state": "visible",
                    "message": "Deep Work Mode initialized.",
                }
            )
        except Exception as exc:
            print(f"[MACRO AGENT] UI bridge emit skipped: {exc}", flush=True)

        steps: list[str] = []
        errors: list[str] = []

        # ── Step 1: Kill distractions ──────────────────────────────────
        killed: list[str] = []
        for proc in self.DISTRACTION_PROCESSES:
            if _kill_process_by_name(proc):
                killed.append(proc.replace(".exe", ""))
        if killed:
            steps.append(f"terminated {', '.join(killed)}")
            print(f"[MACRO AGENT] Killed: {killed}", flush=True)
        else:
            print("[MACRO AGENT] No distracting processes were running.", flush=True)

        # ── Step 2: Open VS Code ───────────────────────────────────────
        workspace = os.getcwd()
        try:
            if platform.system() == "Windows":
                # On Windows, `code` in PATH is usually code.cmd — list form + shell=False
                # raises WinError 193 (%1 is not a valid Win32 application).
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
                subprocess.Popen(
                    "code .",
                    shell=True,
                    cwd=workspace,
                    creationflags=flags,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                _popen(["code", "."], shell=False)
            steps.append("VS Code opened")
            print(f"[MACRO AGENT] VS Code launched in: {workspace}", flush=True)
        except FileNotFoundError:
            # 'code' not on PATH — try common install paths
            try:
                vscode_paths = [
                    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
                    r"C:\Program Files\Microsoft VS Code\Code.exe",
                ]
                launched = False
                for vsp in vscode_paths:
                    if os.path.isfile(vsp):
                        _popen([vsp, "."])
                        steps.append("VS Code opened")
                        launched = True
                        break
                if not launched:
                    errors.append("VS Code not found on PATH or default install location")
            except Exception as e:
                errors.append(f"VS Code launch failed: {e}")
        except Exception as e:
            errors.append(f"VS Code launch failed: {e}")

        # Brief pause so Code gets a head-start before the browser steals focus
        time.sleep(0.5)

        # ── Step 3: Open dev URL ───────────────────────────────────────
        try:
            _open_url(self._dev_url)
            steps.append(f"browser opened to {self._dev_url}")
            print(f"[MACRO AGENT] Browser → {self._dev_url}", flush=True)
        except Exception as e:
            errors.append(f"Browser launch failed: {e}")

        # ── Build response ─────────────────────────────────────────────
        summary = "Deep Work Mode engaged"
        if steps:
            summary += ": " + "; ".join(steps)
        if errors:
            summary += f". Note: {'; '.join(errors)}"
        summary += "."
        print(f"[MACRO AGENT] Deep Work complete → {summary}", flush=True)
        return summary

    def _macro_shallow_work(self) -> str:
        """
        End "deep work" / work-mode session (companion to _macro_deep_work).

        Does not kill or relaunch apps — killed distractions stay closed until
        the user opens them again. Clears the Phase 8.4 HUD work-mode ping.
        """
        print("[MACRO AGENT] ▶ Ending deep work / work mode (shallow_work)...", flush=True)
        try:
            from socket_manager import schedule_ui_update

            schedule_ui_update(
                {
                    "type": "ui_state",
                    "widget": "system_log",
                    "state": "hidden",
                    "hud_phase": "standby",
                }
            )
        except Exception as exc:
            print(f"[MACRO AGENT] UI bridge emit skipped: {exc}", flush=True)

        return (
            "Work mode ended, Sir. The deep-work HUD notice is cleared. "
            "Apps closed during lock-in are still closed — reopen Discord, Spotify, or Steam when you like. "
            "If you had JARVIS focus-mode silencing nudges, say disable focus mode for that separately."
        )

    def _macro_system_diagnostic(self) -> str:
        """
        System Diagnostic Mode
        ----------------------
        1. Open the OS resource monitor (Task Manager on Windows, htop on Linux/macOS).
        2. Open a terminal / command-line window for live inspection.

        Both are launched non-blocking so J.A.R.V.I.S. can keep talking.
        """
        print("[MACRO AGENT] ▶ Initiating System Diagnostic Mode...", flush=True)
        steps: list[str] = []
        errors: list[str] = []
        system = platform.system()

        # ── Step 1: Resource monitor ───────────────────────────────────
        # On Windows, Task Manager requires elevation (WinError 740).
        # We use ShellExecuteW with the 'runas' verb so the OS handles the
        # UAC prompt dynamically — no need to pre-launch J.A.R.V.I.S. as admin.
        try:
            if system == "Windows":
                import ctypes
                # ShellExecuteW(hwnd, verb, file, params, cwd, show_cmd)
                # show_cmd=1 → SW_SHOWNORMAL
                ret = ctypes.windll.shell32.ShellExecuteW(
                    None,          # parent hwnd
                    "runas",       # verb — triggers UAC elevation request
                    "taskmgr.exe", # executable (on PATH / System32)
                    None,          # parameters
                    None,          # working directory
                    1,             # SW_SHOWNORMAL
                )
                if ret > 32:  # ShellExecute returns >32 on success
                    steps.append("Task Manager opened (elevated)")
                    print("[MACRO AGENT] Task Manager launched via ShellExecute runas", flush=True)
                else:
                    errors.append(f"Task Manager elevation denied or failed (code {ret})")
                    print(f"[MACRO AGENT] ShellExecute returned {ret} — UAC may have been declined", flush=True)
            elif system == "Darwin":
                _popen(["open", "-a", "Activity Monitor"])
                steps.append("Activity Monitor opened")
            else:
                # Linux — try gnome-system-monitor, then xterm + htop
                try:
                    _popen(["gnome-system-monitor"])
                    steps.append("System Monitor opened")
                except FileNotFoundError:
                    _popen(["xterm", "-e", "htop"])
                    steps.append("htop opened")
            print(f"[MACRO AGENT] Resource monitor launched on {system}", flush=True)
        except Exception as e:
            errors.append(f"Resource monitor failed: {e}")

        # Brief stagger so both windows don't fight for z-order
        time.sleep(0.3)

        # ── Step 2: Terminal window ────────────────────────────────────
        try:
            if system == "Windows":
                # Prefer Windows Terminal (wt), fall back to cmd
                try:
                    _popen(["wt.exe"])
                    steps.append("Windows Terminal opened")
                except FileNotFoundError:
                    _popen(["cmd.exe"])
                    steps.append("Command Prompt opened")
            elif system == "Darwin":
                _popen(["open", "-a", "Terminal"])
                steps.append("Terminal opened")
            else:
                for term in ["gnome-terminal", "xfce4-terminal", "xterm"]:
                    try:
                        _popen([term])
                        steps.append(f"{term} opened")
                        break
                    except FileNotFoundError:
                        continue
            print("[MACRO AGENT] Terminal launched", flush=True)
        except Exception as e:
            errors.append(f"Terminal launch failed: {e}")

        # ── Build response ─────────────────────────────────────────────
        summary = "System Diagnostic Mode engaged"
        if steps:
            summary += ": " + "; ".join(steps)
        if errors:
            summary += f". Warning: {'; '.join(errors)}"
        summary += "."
        print(f"[MACRO AGENT] Diagnostic complete → {summary}", flush=True)
        return summary

    def _macro_entertainment(self) -> str:
        """
        Entertainment Mode
        ------------------
        Opens YouTube in the default browser for quick streaming access.
        Also tries to launch a local media player (VLC) if installed.

        Both are non-blocking. If VLC is not found the step is silently
        skipped — a browser alone is always sufficient.
        """
        print("[MACRO AGENT] ▶ Initiating Entertainment Mode...", flush=True)
        steps: list[str] = []
        errors: list[str] = []

        # ── Step 1: Open YouTube in browser ───────────────────────────
        try:
            _open_url("https://www.youtube.com")
            steps.append("YouTube opened in browser")
            print("[MACRO AGENT] Browser → YouTube", flush=True)
        except Exception as e:
            errors.append(f"YouTube launch failed: {e}")

        # ── Step 2: Try to open VLC (best-effort) ─────────────────────
        try:
            vlc_paths = [
                r"C:\Program Files\VideoLAN\VLC\vlc.exe",
                r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            ]
            vlc_launched = False
            for vlc in vlc_paths:
                if os.path.isfile(vlc):
                    _popen([vlc])
                    steps.append("VLC opened")
                    vlc_launched = True
                    print(f"[MACRO AGENT] VLC launched from {vlc}", flush=True)
                    break
            if not vlc_launched:
                # Try PATH (Linux/macOS)
                import shutil
                if shutil.which("vlc"):
                    _popen(["vlc"])
                    steps.append("VLC opened")
                    print("[MACRO AGENT] VLC launched via PATH", flush=True)
        except Exception as e:
            # VLC is optional — record but do not surface as an error
            print(f"[MACRO AGENT] VLC launch skipped: {e}", flush=True)

        # ── Build response ─────────────────────────────────────────────
        summary = "Entertainment Mode engaged"
        if steps:
            summary += ": " + "; ".join(steps)
        if errors:
            summary += f". Note: {'; '.join(errors)}"
        summary += "."
        print(f"[MACRO AGENT] Entertainment complete → {summary}", flush=True)
        return summary
