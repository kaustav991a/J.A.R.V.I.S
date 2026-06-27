"""
Phase 8.6.9: OS Integration Agent — Dynamic App Resolution Engine
=================================================================
Replaces the blind shell fallback with a three-source smart indexer
that builds a startup cache of every installed app on the machine
and fuzzy-matches spoken names against it.

Resolution architecture (AppIndexer):
  Source A -- Start Menu shortcuts (.lnk) -- catches UWP apps, Store apps, Adobe CC
  Source B -- Registry App Paths          -- catches Chrome, Edge, Excel, etc.
  Source C -- Hardcoded essentials        -- File Explorer, Notepad, Calc (never miss)
  Fuzzy match -- difflib.get_close_matches -- typo-tolerant (cutoff=0.68)
  Execution   -- os.startfile()           -- ShellExecute wrapper; handles all app types
  Web fallback -- webbrowser.open()       -- for apps not installed as desktop apps

Media control (Phase 8.6.8):
  Playback commands use the native Windows SMTC API via winrt.
  Volume commands use ctypes keybd_event (SMTC has no volume API).
  Falls back to legacy keybd_event if winrt is unavailable.
"""
import asyncio
import ctypes
import difflib
import os
import psutil
import re
import threading
import winreg
from pathlib import Path

# ---------------------------------------------------------------------------
# SMTC via winrt — imported lazily so a missing package doesn't crash startup
# ---------------------------------------------------------------------------
_SMTC_AVAILABLE = False
try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _MediaManager,
    )
    _SMTC_AVAILABLE = True
    print("[OS_AGENT] SMTC (winrt) available — context-aware media control active.", flush=True)
except ImportError:
    print("[OS_AGENT] winrt not available — falling back to legacy keybd_event media control.", flush=True)


# ---------------------------------------------------------------------------
# Async SMTC helpers (Phase 8.6.8)
# ---------------------------------------------------------------------------

async def _smtc_control(action: str) -> str:
    """Async SMTC dispatcher. Returns a TTS-ready result string."""
    try:
        sessions = await _MediaManager.request_async()
    except Exception as e:
        return f"SMTC unavailable: {e}"

    current_session = sessions.get_current_session()
    if current_session is None:
        return "System Failure: No media applications are currently active or playing."

    try:
        if action == "play_pause":
            await current_session.try_toggle_play_pause_async()
            return "Media playback toggled."
        elif action in ("next_track", "next"):
            await current_session.try_skip_next_async()
            return "Skipped to the next track."
        elif action in ("prev_track", "previous_track", "prev"):
            await current_session.try_skip_previous_async()
            return "Returned to the previous track."
        else:
            return f"SMTC does not handle the '{action}' command."
    except Exception as e:
        return f"SMTC command failed: {e}"


def _run_smtc_sync(action: str) -> str:
    """
    Synchronous bridge: runs the async SMTC coroutine safely regardless of
    whether we are inside an existing event loop or not.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _smtc_control(action))
                return future.result(timeout=5.0)
        else:
            return loop.run_until_complete(_smtc_control(action))
    except Exception:
        return asyncio.run(_smtc_control(action))


# ---------------------------------------------------------------------------
# Phase 8.6.9 — App Resolution Engine
# ---------------------------------------------------------------------------

def _clean_name(raw: str) -> str:
    """
    Normalise a display name for index keys and query matching.
    Strips version numbers, edition tags, architecture suffixes, and
    punctuation so that 'Google Chrome (x64)' becomes 'google chrome'.
    """
    name = raw
    # Drop file extension
    name = re.sub(r'\.(lnk|exe)$', '', name, flags=re.IGNORECASE)
    # Drop bracketed/parenthesised qualifiers: (x64), (32bit), [Beta], etc.
    name = re.sub(r'[\[\(][^\]\)]*[\]\)]', '', name)
    # Drop trailing year/version: "After Effects 2020", "FL Studio 12"
    name = re.sub(r'\s+\d{4}$', '', name)
    name = re.sub(r'\s+\d+(\.\d+)*$', '', name)
    # Collapse whitespace, lower
    return re.sub(r'\s+', ' ', name).strip().lower()


def _build_app_index() -> dict[str, str]:
    """
    Scans three sources and returns {'clean name': 'launch target'}.

    Source A  — Start Menu .lnk shortcuts (All Users + Current User).
                os.startfile() handles .lnk files directly via ShellExecute,
                so we store the .lnk path as the launch target.
    Source B  -- HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths.
                Registry value is the absolute .exe path.
    Source C  — Hardcoded essentials that must never be missed.

    Later sources override earlier ones so registry exe paths take priority
    over a potentially stale Start Menu shortcut for the same app.
    """
    index: dict[str, str] = {}

    # ── Source A: Start Menu shortcuts ──────────────────────────────────────
    start_menu_dirs = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        / r"Microsoft\Windows\Start Menu\Programs",
        Path(os.environ.get("APPDATA", ""))
        / r"Microsoft\Windows\Start Menu\Programs",
    ]
    for base in start_menu_dirs:
        if not base.exists():
            continue
        for lnk in base.rglob("*.lnk"):
            stem = lnk.stem          # e.g. "Microsoft Edge"
            key  = _clean_name(stem)
            if key and len(key) > 1:
                index[key] = str(lnk)

    # ── Source B: Registry App Paths ────────────────────────────────────────
    REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, REG_KEY) as root:
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, i)   # e.g. "chrome.exe"
                        i += 1
                        with winreg.OpenKey(root, sub_name) as sub:
                            try:
                                exe_path, _ = winreg.QueryValueEx(sub, "")
                                if exe_path and Path(exe_path).exists():
                                    key = _clean_name(sub_name)   # "chrome"
                                    if key:
                                        index[key] = exe_path.strip('"')
                            except OSError:
                                pass
                    except OSError:
                        break  # no more sub-keys
        except OSError:
            pass  # hive key doesn't exist

    # ── Source C: Hardcoded essentials ───────────────────────────────────────
    essentials: dict[str, str] = {
        "file explorer":  "explorer.exe",
        "explorer":       "explorer.exe",
        "notepad":        "notepad.exe",
        "calculator":     "calc.exe",
        "calc":           "calc.exe",
        "paint":          "mspaint.exe",
        "task manager":   "taskmgr.exe",
        "control panel":  "control.exe",
        "command prompt": "cmd.exe",   # blocked at execution layer
        "cmd":            "cmd.exe",
        "powershell":     "powershell.exe",
        "wordpad":        "wordpad.exe",
        "magnifier":      "magnify.exe",
        "snipping tool":  "SnippingTool.exe",
    }
    # Essentials override earlier sources
    index.update(essentials)

    print(
        f"[OS_AGENT] App index built: {len(index)} entries "
        f"(Start Menu + Registry + Essentials).",
        flush=True,
    )
    return index


class AppIndexer:
    """
    Singleton launcher index.  Built once on first access (lazy init) so that
    startup latency is zero even if the backend boots before the OS is fully
    ready.  Thread-safe: protected by a lock during build.
    """
    _instance: "AppIndexer | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._index: dict[str, str] = {}
        self._built = False
        self._build_lock = threading.Lock()

    @classmethod
    def get(cls) -> "AppIndexer":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = AppIndexer()
        return cls._instance

    def _ensure_built(self) -> None:
        if self._built:
            return
        with self._build_lock:
            if not self._built:
                self._index = _build_app_index()
                self._built = True

    @property
    def index(self) -> dict[str, str]:
        self._ensure_built()
        return self._index

    def resolve(self, query: str) -> tuple[str | None, str | None]:
        """
        Fuzzy-match *query* against the index.
        Returns (matched_display_name, absolute_path_or_lnk) or (None, None).

        Match strategy:
          1. Exact key lookup (fastest).
          2. Substring lookup — any key that starts with query.
          3. difflib fuzzy match (cutoff=0.62, picks closest).
        """
        idx = self.index
        q   = _clean_name(query)

        # 1. Exact
        if q in idx:
            return q, idx[q]

        # 2. Prefix / substring
        candidates = [k for k in idx if k.startswith(q) or q in k]
        if candidates:
            best = min(candidates, key=len)   # shortest = most specific
            return best, idx[best]

        # 3. Fuzzy
        keys = list(idx.keys())
        matches = difflib.get_close_matches(q, keys, n=1, cutoff=0.68)
        if matches:
            key = matches[0]
            return key, idx[key]

        return None, None

    def rebuild(self) -> int:
        """Force a cache rebuild (e.g. after installing a new app)."""
        with self._build_lock:
            self._index = _build_app_index()
            self._built = True
        return len(self._index)


# Eagerly warm the cache in a background thread so the first launch request
# is instant (not the <50 ms build time).
def _warm_cache_async() -> None:
    try:
        AppIndexer.get()._ensure_built()
    except Exception as e:
        print(f"[OS_AGENT] Cache warm error (non-fatal): {e}", flush=True)

threading.Thread(target=_warm_cache_async, daemon=True, name="AppIndexerWarm").start()


# ---------------------------------------------------------------------------
# OSAgent class
# ---------------------------------------------------------------------------

# Apps that must NEVER be launched by voice command (security policy).
_BLOCKED_APPS = frozenset({"cmd", "command prompt", "powershell", "regedit", "registry editor"})


class OSAgent:
    def __init__(self):
        # Keyboard virtual key codes (legacy fallback path)
        self.VK_VOLUME_MUTE      = 0xAD
        self.VK_VOLUME_DOWN      = 0xAE
        self.VK_VOLUME_UP        = 0xAF
        self.VK_MEDIA_NEXT_TRACK = 0xB0
        self.VK_MEDIA_PREV_TRACK = 0xB1
        self.VK_MEDIA_PLAY_PAUSE = 0xB3

    # ── Private: legacy keybd_event ──────────────────────────────────────────

    def _keypress(self, vk: int) -> None:
        """Send a single virtual-key press + release via Win32."""
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)

    def _legacy_media(self, command: str) -> str:
        """Blind keybd_event fallback for media commands."""
        if command == "play_pause":
            self._keypress(self.VK_MEDIA_PLAY_PAUSE)
            return "Toggled media playback."
        elif command in ("next_track", "next"):
            self._keypress(self.VK_MEDIA_NEXT_TRACK)
            return "Skipping to the next track."
        elif command in ("prev_track", "previous_track", "prev"):
            self._keypress(self.VK_MEDIA_PREV_TRACK)
            return "Returning to the previous track."
        return f"Unknown media command: {command}"

    # ── Media control ────────────────────────────────────────────────────────

    def control_media(self, command: str) -> str:
        """
        Controls system volume and media playback.
        Volume → ctypes keybd_event (SMTC has no volume API).
        Playback → SMTC API (winrt) with legacy keybd_event fallback.
        """
        command = command.lower().strip()

        try:
            if command == "mute":
                self._keypress(self.VK_VOLUME_MUTE)
                return "System audio muted."
            elif command == "unmute":
                self._keypress(self.VK_VOLUME_MUTE)
                return "System audio unmuted."
            elif command == "volume_up":
                for _ in range(5):
                    self._keypress(self.VK_VOLUME_UP)
                return "System volume increased."
            elif command == "volume_down":
                for _ in range(5):
                    self._keypress(self.VK_VOLUME_DOWN)
                return "System volume decreased."
        except Exception as e:
            return f"Failed to control volume: {e}"

        if command in ("play_pause", "next_track", "next", "prev_track",
                       "previous_track", "prev"):
            if _SMTC_AVAILABLE:
                result = _run_smtc_sync(command)
                print(f"[OS_AGENT] SMTC result for '{command}': {result}", flush=True)
                return result
            print(f"[OS_AGENT] SMTC unavailable — using legacy keypress for '{command}'.", flush=True)
            return self._legacy_media(command)

        return f"Unknown media command: {command}"

    # ── Workstation lock ──────────────────────────────────────────────────────

    def lock_workstation(self) -> str:
        """Instantly locks the Windows session."""
        try:
            ctypes.windll.user32.LockWorkStation()
            return "Workstation locked securely, Sir."
        except Exception as e:
            return f"Failed to lock workstation: {e}"

    # ── System diagnostics ────────────────────────────────────────────────────

    def get_system_diagnostics(self) -> str:
        """Returns hardware diagnostics for the LLM."""
        try:
            cpu_usage = psutil.cpu_percent(interval=1.0)
            ram       = psutil.virtual_memory()
            disk      = psutil.disk_usage("C:\\")
            return (
                f"SYSTEM HARDWARE DIAGNOSTIC:\n"
                f"- CPU Load: {cpu_usage}%\n"
                f"- RAM Usage: {ram.percent}% "
                f"({round(ram.used/(1024**3),1)} GB / {round(ram.total/(1024**3),1)} GB)\n"
                f"- C: Drive Free Space: {round(disk.free/(1024**3),1)} GB"
            )
        except Exception as e:
            return f"Diagnostic scan failed: {e}"

    # ── Smart App Launcher (Phase 8.6.9) ─────────────────────────────────────

    def launch_application(self, app_name: str) -> str:
        """
        Phase 8.6.10 Smart Launcher — fire-and-forget via os.startfile().

        Resolution order:
          1. Security gate — block terminal/shell access immediately.
          2. Web-first gate — if the spoken name is a known web-only service
             (YouTube, Spotify, Gmail, etc.), open via webbrowser.open() NOW,
             before AppIndexer is consulted. This prevents Start Menu .lnk
             shortcuts from hijacking the request into a wrong Chrome profile.
          3. AppIndexer — fuzzy-match against Start Menu + Registry App Paths.
          4. os.startfile() — ShellExecute; handles .exe, UWP .lnk, Store apps.

        Returns a TTS-ready status string.
        """
        import webbrowser

        q_clean = _clean_name(app_name)

        # ── 1. Security gate ─────────────────────────────────────────────────
        if q_clean in _BLOCKED_APPS:
            return "Security Protocol: Access to command line interfaces is restricted, Sir."

        # ── 2. Web-first gate (Bug 3 fix) ────────────────────────────────────
        # These are pure web services. Even if a pinned .lnk exists in Start
        # Menu, we MUST use the OS default browser so the correct user profile
        # is used. webbrowser.open() always respects the OS default browser.
        _WEB_ONLY: dict[str, str] = {
            "youtube":       "https://www.youtube.com",
            "spotify":       "https://open.spotify.com",
            "gmail":         "https://mail.google.com",
            "google drive":  "https://drive.google.com",
            "google docs":   "https://docs.google.com",
            "google sheets": "https://sheets.google.com",
            "google slides": "https://slides.google.com",
            "netflix":       "https://www.netflix.com",
            "prime video":   "https://www.primevideo.com",
            "hotstar":       "https://www.hotstar.com",
            "github":        "https://github.com",
            "chatgpt":       "https://chat.openai.com",
            "claude":        "https://claude.ai",
        }
        for web_key, url in _WEB_ONLY.items():
            if web_key in q_clean or q_clean in web_key:
                print(
                    f"[OS_AGENT] Web-first gate: '{app_name}' -> {url} "
                    f"(bypassing AppIndexer to preserve browser profile)",
                    flush=True,
                )
                webbrowser.open(url)
                return f"Opening {app_name.title()} in your browser, Sir."

        # ── 3. AppIndexer — installed desktop apps ───────────────────────────
        indexer = AppIndexer.get()
        matched_key, resolved_path = indexer.resolve(app_name)

        if resolved_path:
            print(
                f"[OS_AGENT] Resolved '{app_name}' -> '{matched_key}' -> {resolved_path}",
                flush=True,
            )
            try:
                os.startfile(resolved_path)
                display = matched_key.title() if matched_key else Path(resolved_path).stem.title()
                return f"Launching {display}, Sir."
            except Exception as e:
                print(f"[OS_AGENT] os.startfile failed for '{resolved_path}': {e}", flush=True)
                return f"I found '{app_name}' but couldn't open it: {e}"

        print(f"[OS_AGENT] '{app_name}' not found in index (query='{q_clean}').", flush=True)
        return f"I couldn't locate '{app_name}' on your system, Sir."
