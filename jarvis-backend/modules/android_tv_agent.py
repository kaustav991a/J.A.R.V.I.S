"""
Phase 6 Skill Pack — Android TV ADB Agent (subprocess-based)
=============================================================
Controls the user's Android TV over Wi-Fi ADB using the adb executable
from the Android SDK Platform Tools (must be on PATH or pointed to via
JARVIS_ADB_PATH).

Configuration (environment variables):
  JARVIS_TV_IP       — Fallback ``ip:port`` when mDNS finds nothing (default: 192.168.0.108:35923).
  JARVIS_TV_NAME     — Substring to prefer on mDNS service names (default: 2KTV-3MH). Set to empty to take the first discovered TV.
  JARVIS_ADB_PATH    — Full path to adb.exe (default: ``adb`` on PATH).

Discovery:
  Zeroconf browses ``_adb-tls-connect._tcp.local.``. Prefers services whose instance name contains ``JARVIS_TV_NAME``; if none match but TVs exist, uses the first candidate; if the name filter is empty, uses the first TV.

Connection order (``connect()``):
  1. Optional explicit ``ip_port`` argument skips mDNS.
  2. Else mDNS ``ip:port`` when a TV is found (stored on ``discovered_host`` / ``discovered_port``).
  3. Else ``JARVIS_TV_IP``.

Security:
  - Only whitelisted keyevent codes and package names are accepted.
  - No raw shell command pass-through is exposed.
  - All subprocess calls use shell=False with list-form argv.

All public methods return clean, LLM-/TTS-ready strings.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

# Wireless debugging / TLS pairing advertisement (Android 11+ TV).
ADB_TLS_MDNS_SERVICE_TYPE = "_adb-tls-connect._tcp.local."

_DEFAULT_TV_IP = "192.168.0.108:35923"


def _get_tv_target() -> str:
    """TV address ``ip:port`` from env (fallback default)."""
    return os.getenv("JARVIS_TV_IP", _DEFAULT_TV_IP).strip()


def _get_tv_name_filter() -> str:
    """
    Substring to match against mDNS service instance names.
    Default ``2KTV-3MH``. If ``JARVIS_TV_NAME`` is set to empty, match-first-TV mode.
    """
    return os.getenv("JARVIS_TV_NAME", "2KTV-3MH").strip()


def _get_adb() -> str:
    return os.getenv("JARVIS_ADB_PATH", "adb").strip()


def _addresses_to_ip(info) -> Optional[str]:
    """Pick first IPv4 from ServiceInfo; else first IPv6."""
    if not getattr(info, "addresses", None):
        return None
    v4: list[str] = []
    v6: list[str] = []
    for addr in info.addresses:
        try:
            if len(addr) == 4:
                v4.append(socket.inet_ntoa(addr))
            elif len(addr) == 16:
                v6.append(socket.inet_ntop(socket.AF_INET6, addr))
        except OSError:
            continue
    if v4:
        return v4[0]
    if v6:
        return v6[0]
    return None


def discover_tv_via_mdns(
    *,
    preferred_name_substring: Optional[str] = None,
    timeout_s: float = 5.0,
) -> Optional[tuple[str, int, str]]:
    """
    Browse for Android TVs advertising ``_adb-tls-connect._tcp``.

    Args:
        preferred_name_substring: If non-empty, prefer services whose mDNS name
            contains this substring (case-insensitive). If none match, falls back
            to the first candidate. When None, reads ``JARVIS_TV_NAME`` (empty → first TV only).
        timeout_s: Listen duration before returning.

    Returns:
        ``(ip, port, service_name)`` or ``None`` if zeroconf unavailable / nothing found.
    """
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        print("[TV AGENT] zeroconf not installed; skipping mDNS discovery.", flush=True)
        return None

    pref = preferred_name_substring if preferred_name_substring is not None else _get_tv_name_filter()

    candidates: list[tuple[str, int, str]] = []
    lock = threading.Lock()

    def on_service_state_change(
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name)
        if not info or not info.addresses:
            return
        ip = _addresses_to_ip(info)
        if not ip:
            return
        port = int(info.port or 0)
        if port <= 0:
            return
        with lock:
            candidates.append((ip, port, name))

    zc: Optional[Zeroconf] = None
    try:
        zc = Zeroconf()
        ServiceBrowser(zc, ADB_TLS_MDNS_SERVICE_TYPE, handlers=[on_service_state_change])
        time.sleep(max(0.5, timeout_s))
    except Exception as exc:
        print(f"[TV AGENT] mDNS browse failed: {exc}", flush=True)
        return None
    finally:
        if zc is not None:
            zc.close()

    with lock:
        if not candidates:
            return None

        if pref:
            pref_l = pref.lower()
            matching = [c for c in candidates if pref_l in c[2].lower()]
            chosen = matching[0] if matching else candidates[0]
        else:
            chosen = candidates[0]

        return chosen[0], chosen[1], chosen[2]


# ── Key-event constants ───────────────────────────────────────────────────────

_KEYEVENT_POWER = 26
_KEYEVENT_VOLUME_UP = 24
_KEYEVENT_VOL_DOWN = 25
_KEYEVENT_MUTE = 164
_KEYEVENT_HOME = 3
_KEYEVENT_BACK = 4

# ── 14 mapped packages (distinct Android package IDs) ───────────────────────

_APP_PACKAGES: dict[str, str] = {
    "youtube": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja",
    "prime video": "com.amazon.avod.thirdpartyclient",
    "amazon": "com.amazon.avod.thirdpartyclient",
    "hotstar": "in.startv.hotstar",
    "disney+": "in.startv.hotstar",
    "sonyliv": "com.sonyliv",
    "zee5": "com.graymatrix.did",
    "spotify": "com.spotify.tv.android",
    "plex": "com.plexapp.android",
    "vlc": "org.videolan.vlc",
    "chrome": "com.android.chrome",
    "settings": "com.android.tv.settings",
    "home": "com.google.android.tvlauncher",
    "tubi": "com.tubitv",
    "apple tv": "com.apple.atve.androidtv.appletv",
}

_UNIQUE_PACKAGES = frozenset(_APP_PACKAGES.values())
assert len(_UNIQUE_PACKAGES) == 14, "Expected exactly 14 distinct TV package IDs"

_ADB_TIMEOUT = 15


def _resolve_package(app_name: str) -> Optional[str]:
    """Resolve user-facing label to package id (longest alias wins for substrings)."""
    name_lower = app_name.lower().strip()
    if name_lower in _APP_PACKAGES:
        return _APP_PACKAGES[name_lower]
    package: Optional[str] = None
    best_len = -1
    for alias, pkg in _APP_PACKAGES.items():
        if alias in name_lower and len(alias) > best_len:
            package = pkg
            best_len = len(alias)
    if package is None and "." in app_name and " " not in app_name:
        return app_name.strip()
    return package


# ── AndroidTVAgent ────────────────────────────────────────────────────────────


class AndroidTVAgent:
    """
    Subprocess wrapper around ``adb`` for Wi-Fi Android TV control.

    ``discovered_host`` / ``discovered_port`` reflect the last successful mDNS hit
    (cleared when falling back to env ``JARVIS_TV_IP`` unless discovery supplied it).
    """

    def __init__(self, ip_port: Optional[str] = None):
        self._target = ip_port or _get_tv_target()
        self._adb = _get_adb()
        self._connected: bool = False
        self.discovered_host: Optional[str] = None
        self.discovered_port: Optional[int] = None

    def _run(self, args: list[str], *, timeout: int = _ADB_TIMEOUT) -> tuple[bool, str]:
        cmd = [self._adb, "-s", self._target] + args
        print(f"[TV AGENT] Running: {' '.join(cmd)}", flush=True)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
            out = ((result.stdout or "") + (result.stderr or "")).strip()
            return (result.returncode == 0), out
        except FileNotFoundError:
            return False, (
                "ADB not found. Please install Android SDK Platform Tools "
                "and ensure 'adb' is on your PATH, or set JARVIS_ADB_PATH."
            )
        except subprocess.TimeoutExpired:
            return False, f"ADB command timed out after {timeout} seconds."
        except Exception as exc:
            return False, f"ADB execution error: {exc}"

    def _keyevent(self, code: int) -> tuple[bool, str]:
        return self._run(["shell", "input", "keyevent", str(code)])

    # ── Connection ───────────────────────────────────────────────────────────

    def connect(self, ip_port: Optional[str] = None) -> str:
        """
        Connect via ``adb connect``.

        Order:
          - Explicit ``ip_port`` argument → use it (no mDNS).
          - Else mDNS ``_adb-tls-connect`` → discovered ``ip:port``.
          - Else ``JARVIS_TV_IP`` from environment.
        """
        self.discovered_host = None
        self.discovered_port = None

        if ip_port:
            self._target = ip_port.strip()
        else:
            discovered = discover_tv_via_mdns(timeout_s=5.0)
            if discovered:
                host, port, _svc_name = discovered
                self.discovered_host = host
                self.discovered_port = port
                self._target = f"{host}:{port}"
                print(f"[TV AGENT] mDNS selected {self._target}", flush=True)
            else:
                self._target = _get_tv_target()
                print(f"[TV AGENT] mDNS found no TV; using JARVIS_TV_IP → {self._target}", flush=True)

        print(f"[TV AGENT] Connecting to {self._target}...", flush=True)

        cmd = [self._adb, "connect", self._target]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_ADB_TIMEOUT,
                shell=False,
            )
            out = ((result.stdout or "") + (result.stderr or "")).strip()

            if "connected" in out.lower():
                self._connected = True
                print(f"[TV AGENT] Connected: {out}", flush=True)
                return f"TV uplink established at {self._target}."
            if "already connected" in out.lower():
                self._connected = True
                return f"TV uplink already active at {self._target}."
            if "connection refused" in out.lower():
                self._connected = False
                return (
                    "Connection refused, Sir. The TV may be off or ADB over Wi-Fi "
                    "may not be enabled."
                )
            if "failed" in out.lower() or result.returncode != 0:
                self._connected = False
                return f"TV connection failed, Sir: {out[:120]}"
            self._connected = True
            return f"ADB response: {out}"

        except FileNotFoundError:
            return (
                "ADB not found, Sir. Install Android SDK Platform Tools "
                "and add it to PATH, or set JARVIS_ADB_PATH in .env."
            )
        except subprocess.TimeoutExpired:
            return "TV connection timed out, Sir. Is the TV on the network?"
        except Exception as exc:
            return f"TV connection error, Sir: {exc}"

    def _ensure_connected(self) -> Optional[str]:
        if self._connected:
            return None
        result = self.connect()
        if "established" in result or "already active" in result:
            return None
        return result

    # ── Core TV Actions ────────────────────────────────────────────────────────

    def tv_power_toggle(self) -> str:
        err = self._ensure_connected()
        if err:
            return err

        ok, out = self._keyevent(_KEYEVENT_POWER)
        if not ok:
            return f"TV power toggle failed, Sir: {out[:100]}"
        return "TV power toggled, Sir."

    def tv_volume(self, direction: str, steps: int = 1) -> str:
        err = self._ensure_connected()
        if err:
            return err

        direction = direction.lower().strip()
        steps = max(1, min(steps, 20))

        if direction in ("mute", "toggle_mute"):
            ok, out = self._keyevent(_KEYEVENT_MUTE)
            if not ok:
                return f"Mute failed, Sir: {out[:80]}"
            return "TV muted, Sir."

        if direction in ("up", "volume_up", "increase", "louder"):
            code = _KEYEVENT_VOLUME_UP
            label = f"up by {steps}"
        elif direction in ("down", "volume_down", "decrease", "quieter", "lower"):
            code = _KEYEVENT_VOL_DOWN
            label = f"down by {steps}"
        else:
            return (
                f"Unknown volume direction '{direction}', Sir. "
                "Use 'up', 'down', or 'mute'."
            )

        for i in range(steps):
            ok, out = self._keyevent(code)
            if not ok:
                return f"Volume adjustment failed on step {i + 1}, Sir: {out[:80]}"

        return f"TV volume {label}, Sir."

    def tv_launch_app(self, app_name: str) -> str:
        err = self._ensure_connected()
        if err:
            return err

        package = _resolve_package(app_name)
        if package is None:
            known = ", ".join(sorted(_APP_PACKAGES.keys()))
            return (
                f"I don't have a package mapping for '{app_name}', Sir. "
                f"Known apps: {known}."
            )

        ok, out = self._run(
            [
                "shell",
                "monkey",
                "-p",
                package,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ]
        )

        if not ok or "error" in out.lower():
            ok2, out2 = self._run(
                ["shell", "am", "start", "-n", f"{package}/android.app.NativeActivity"]
            )
            if not ok2:
                return (
                    f"Failed to launch {app_name} on the TV, Sir. "
                    f"ADB output: {out[:80]}"
                )

        display_name = app_name.strip().title() if app_name.strip() else app_name
        return f"Launching {display_name} on the TV, Sir."

    # ── Convenience helpers ─────────────────────────────────────────────────────

    def tv_keyevent(self, code: int) -> str:
        err = self._ensure_connected()
        if err:
            return err
        ok, out = self._keyevent(code)
        return "Keyevent sent." if ok else f"Keyevent failed: {out[:80]}"

    def tv_go_home(self) -> str:
        err = self._ensure_connected()
        if err:
            return err
        ok, out = self._keyevent(_KEYEVENT_HOME)
        return "Navigated to TV home screen, Sir." if ok else f"Home failed: {out[:80]}"

    def get_current_app(self) -> str:
        err = self._ensure_connected()
        if err:
            return err
        ok, out = self._run(["shell", "dumpsys", "window", "windows"])
        if ok and "mCurrentFocus" in out:
            for line in out.splitlines():
                if "mCurrentFocus" in line:
                    return line.strip()[:200]
        return out[:200] if out else "Unable to determine current app."

    def disconnect(self) -> str:
        cmd = [self._adb, "disconnect", self._target]
        try:
            subprocess.run(cmd, capture_output=True, timeout=5, shell=False)
        except Exception:
            pass
        self._connected = False
        return "TV uplink disconnected."
