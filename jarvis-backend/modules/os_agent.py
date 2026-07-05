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

    # ── Private: Core Audio mute-state reader (no external deps) ──────────────

    @staticmethod
    def _get_mute_state() -> "bool | None":
        """
        Read the current system mute state via the Windows Core Audio
        IAudioEndpointVolume COM interface, using only ctypes (no pycaw /
        comtypes). Returns True if muted, False if unmuted, or None if the
        API is unavailable (e.g. headless / Wine).
        """
        try:
            import ctypes
            import ctypes.wintypes

            # ── GUID structure ────────────────────────────────────────────
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            def _guid(d1, d2, d3, *d4):
                return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))

            CLSID_MMDeviceEnumerator = _guid(
                0xBCDE0395, 0xE52F, 0x467C, 0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E)
            IID_IMMDeviceEnumerator = _guid(
                0xA95664D2, 0x9614, 0x4F35, 0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6)
            IID_IAudioEndpointVolume = _guid(
                0x5CDF2C82, 0x841E, 0x4546, 0x97, 0x22, 0x0C, 0xF7, 0x40, 0x78, 0x22, 0x9A)

            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)

            enumerator = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_MMDeviceEnumerator),
                None, 1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(IID_IMMDeviceEnumerator),
                ctypes.byref(enumerator),
            )
            if hr != 0:
                return None

            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eConsole=0, ...)
            vtbl = ctypes.cast(
                ctypes.cast(enumerator, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p * 20),
            ).contents
            GetDefaultAudioEndpoint = ctypes.CFUNCTYPE(
                ctypes.HRESULT, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                ctypes.POINTER(ctypes.c_void_p),
            )(vtbl[4])
            device = ctypes.c_void_p()
            hr = GetDefaultAudioEndpoint(enumerator, 0, 0, ctypes.byref(device))
            if hr != 0:
                return None

            # IMMDevice::Activate(IID_IAudioEndpointVolume, CLSCTX_ALL=23, ...)
            vtbl_dev = ctypes.cast(
                ctypes.cast(device, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p * 20),
            ).contents
            Activate = ctypes.CFUNCTYPE(
                ctypes.HRESULT, ctypes.c_void_p,
                ctypes.POINTER(GUID),  # REFIID
                ctypes.c_uint,         # CLSCTX
                ctypes.c_void_p,       # activation params
                ctypes.POINTER(ctypes.c_void_p),
            )(vtbl_dev[3])
            endpoint_vol = ctypes.c_void_p()
            hr = Activate(
                device,
                ctypes.byref(IID_IAudioEndpointVolume),
                23, None, ctypes.byref(endpoint_vol),
            )
            if hr != 0:
                return None

            # IAudioEndpointVolume::GetMute(pbMute)
            # IAudioEndpointVolume vtable layout (0-based):
            #   0-2: IUnknown (QueryInterface, AddRef, Release)
            #   3: RegisterControlChangeNotify
            #   4: UnregisterControlChangeNotify
            #   5: GetChannelCount
            #   6: SetMasterVolumeLevel
            #   7: SetMasterVolumeLevelScalar
            #   8: GetMasterVolumeLevel
            #   9: GetMasterVolumeLevelScalar
            #  10: SetChannelVolumeLevel
            #  11: SetChannelVolumeLevelScalar
            #  12: GetChannelVolumeLevel
            #  13: GetChannelVolumeLevelScalar
            #  14: SetMute
            #  15: GetMute
            vtbl_vol = ctypes.cast(
                ctypes.cast(endpoint_vol, ctypes.POINTER(ctypes.c_void_p))[0],
                ctypes.POINTER(ctypes.c_void_p * 20),
            ).contents
            GetMute = ctypes.CFUNCTYPE(
                ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
            )(vtbl_vol[15])
            muted = ctypes.c_int()
            hr = GetMute(endpoint_vol, ctypes.byref(muted))
            if hr != 0:
                return None

            return bool(muted.value)

        except Exception as e:
            print(f"[OS_AGENT] Core Audio mute-state read failed (non-fatal): {e}", flush=True)
            return None

    # ── Media control ────────────────────────────────────────────────────────

    def control_media(self, command: str) -> str:
        """
        Controls system volume and media playback.
        Volume → ctypes keybd_event (SMTC has no volume API).
        Mute/Unmute → state-aware via Core Audio API; falls back to blind toggle.
        Playback → SMTC API (winrt) with legacy keybd_event fallback.
        """
        command = command.lower().strip()

        try:
            if command == "mute":
                # Read actual state — only toggle if currently unmuted
                state = self._get_mute_state()
                if state is True:
                    return "System audio is already muted."
                self._keypress(self.VK_VOLUME_MUTE)
                return "System audio muted."
            elif command == "unmute":
                # Read actual state — only toggle if currently muted
                state = self._get_mute_state()
                if state is False:
                    return "System audio is already unmuted."
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

        # ── 2. Web service URL map ───────────────────────────────────────────
        # Used in two places: (a) the web-first gate below for pure web services,
        # and (b) the desktop-not-found fallback at the end for dual-listed apps.
        _WEB_URLS: dict[str, str] = {
            # Pure web services (no desktop app) — caught by the web-first gate.
            "youtube":       "https://www.youtube.com",
            "gmail":         "https://mail.google.com",
            "google":        "https://www.google.com",
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
            # Dual-listed apps (desktop-preferred). These are ALSO in
            # _KNOWN_DESKTOP_APPS so the web-first gate is skipped and the desktop
            # app is tried first; if it isn't installed we fall back to the web
            # version at the end instead of failing with "couldn't locate".
            "spotify":         "https://open.spotify.com",
            "telegram":        "https://web.telegram.org",
            "telegram desktop":"https://web.telegram.org",
            "whatsapp":        "https://web.whatsapp.com",
            "whatsapp desktop":"https://web.whatsapp.com",
            "discord":         "https://discord.com/app",
            "slack":           "https://app.slack.com",
        }

        # ── 3. Web-first gate (Bug 3 fix, substring-hijack hardened) ────────
        # Pure web services go straight to the OS default browser so the correct
        # user profile is used, even if a pinned .lnk exists in Start Menu.
        #
        # IMPORTANT: Known desktop apps whose names CONTAIN a web-service
        # substring (e.g. "google chrome" contains "google") — and dual-listed
        # apps we prefer to launch natively (Spotify, Telegram, …) — must be
        # checked FIRST so they short-circuit past the web gate into AppIndexer.
        _KNOWN_DESKTOP_APPS: frozenset[str] = frozenset({
            "google chrome", "chrome",
            "microsoft edge", "edge",
            "firefox", "mozilla firefox",
            "spotify",              # Spotify desktop app (if installed)
            "discord",
            "slack",
            "telegram", "telegram desktop",
            "whatsapp", "whatsapp desktop",
        })
        # EXACT match on the full cleaned name — NOT substring.
        # "google chrome" must NOT match the "google" key.
        if q_clean not in _KNOWN_DESKTOP_APPS and q_clean in _WEB_URLS:
            url = _WEB_URLS[q_clean]
            print(
                f"[OS_AGENT] Web-first gate: '{app_name}' -> {url} "
                f"(bypassing AppIndexer to preserve browser profile)",
                flush=True,
            )
            webbrowser.open(url)
            return f"Opening {app_name.title()} in your browser, Sir."

        # ── 4. AppIndexer — installed desktop apps ───────────────────────────
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

        # ── 5. Web fallback for dual-listed apps not installed as desktop ─────
        # e.g. "open spotify" with no Spotify desktop app → open.spotify.com,
        # rather than failing outright. Honest about the fallback.
        if q_clean in _WEB_URLS:
            url = _WEB_URLS[q_clean]
            print(
                f"[OS_AGENT] '{app_name}' not installed as desktop app; "
                f"falling back to web -> {url}",
                flush=True,
            )
            webbrowser.open(url)
            return (
                f"{app_name.title()} isn't installed as a desktop app, "
                f"so I opened it in your browser, Sir."
            )

        print(f"[OS_AGENT] '{app_name}' not found in index (query='{q_clean}').", flush=True)
        return f"I couldn't locate '{app_name}' on your system, Sir."
