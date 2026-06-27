"""
Phase 6 — TVAgent (pure Python ADB via adb-shell + zeroconf)
===========================================================
Wi-Fi Android TV control without the platform ``adb`` binary.

- RSA keys live under ``credentials/adbkey`` (directory created if missing).
- mDNS discovers ``_adb-tls-connect._tcp`` (wireless debugging) and legacy
  ``_adb._tcp``. Endpoints are cached — discovery runs only when there is no
  cache or after a failed command triggers invalidation + re-discovery.
- Failed shell commands invalidate the session, re-discover, reconnect, and
  retry once.

Environment:
  JARVIS_TV_IP   — Fallback ``host:port`` when mDNS finds nothing.
  JARVIS_TV_NAME — Prefer service names containing this substring (default 2KTV-3MH).
                   Set empty for first-discovered device only.
"""

from __future__ import annotations

import os
import re
import shlex
import socket
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from adb_shell.adb_device import AdbDeviceTcp
from adb_shell.auth.keygen import keygen
from adb_shell.auth.sign_pythonrsa import PythonRSASigner

try:
    from youtubesearchpython import VideosSearch
except ImportError:
    VideosSearch = None  # type: ignore[misc, assignment]

# ── Paths & key material ──────────────────────────────────────────────────────

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_CREDENTIALS_DIR = _BACKEND_ROOT / "credentials"
ADB_KEY_PATH = _CREDENTIALS_DIR / "adbkey"

_SERVICE_TYPES = (
    "_adb-tls-connect._tcp.local.",
    "_adb._tcp.local.",
)

_DEFAULT_TV_IP = "192.168.0.108:5555"

_KEYEVENT_POWER = 26
_KEYEVENT_VOLUME_UP = 24
_KEYEVENT_VOLUME_DOWN = 25
_KEYEVENT_MUTE = 164

# Same registry as legacy AndroidTVAgent (14 distinct package IDs).
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

SUPPORTED_MEDIA_APPS = {
    'com.google.android.youtube.tv': 'YouTube',
    'in.startv.hotstar': 'Hotstar',
    'com.netflix.ninja': 'Netflix',
    'com.amazon.amazonvideo.livingroom': 'Prime Video',
    'com.spotify.tv.android': 'Spotify'
}


def _youtube_first_watch_url_fallback(query: str) -> str:
    """
    Resolve the first organic result's watch URL from YouTube search HTML.
    Used when ``youtubesearchpython`` is missing or incompatible (e.g. httpx>=0.28).
    """
    q = urllib.parse.quote_plus(query.strip())
    url = f"https://www.youtube.com/results?search_query={q}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    idx = 0
    while True:
        pos = html.find('"videoRenderer"', idx)
        if pos < 0:
            break
        window = html[pos : pos + 12000]
        m = re.search(r'"videoId"\s*:\s*"([\w-]{11})"', window)
        if m:
            return f"https://www.youtube.com/watch?v={m.group(1)}"
        idx = pos + 1
    raise RuntimeError("Could not parse a video id from YouTube search results.")


def _ensure_credentials_dir_and_key() -> str:
    _CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    key_str = str(ADB_KEY_PATH)
    if not ADB_KEY_PATH.is_file():
        keygen(key_str)
        print(f"[TVAgent] Generated new ADB RSA key at {key_str}", flush=True)
    return key_str


def _parse_env_tv_addr() -> tuple[str, int]:
    raw = os.getenv("JARVIS_TV_IP", _DEFAULT_TV_IP).strip()
    if ":" in raw:
        host, _, port_s = raw.rpartition(":")
        return host.strip(), max(1, min(int(port_s.strip()), 65535))
    return raw, 5555


def _tv_name_pref() -> str:
    return os.getenv("JARVIS_TV_NAME", "2KTV-3MH").strip()


def _addresses_to_ip(info) -> Optional[str]:
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


def _resolve_package(app_name: str) -> Optional[str]:
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


def _discover_tv_candidates(timeout_s: float = 5.0) -> list[tuple[str, int, str]]:
    """Return deduplicated (ip, port, service_name) from all browsed service types."""
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        print("[TVAgent] zeroconf not installed; cannot discover TVs via mDNS.", flush=True)
        return []

    candidates: list[tuple[str, int, str]] = []
    lock = threading.Lock()

    def on_change(zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
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
        for st in _SERVICE_TYPES:
            ServiceBrowser(zc, st, handlers=[on_change])
        time.sleep(max(0.5, timeout_s))
    except Exception as exc:
        print(f"[TVAgent] mDNS browse failed: {exc}", flush=True)
        return []
    finally:
        if zc is not None:
            zc.close()

    # Dedupe by (ip, port), preserve first-seen service name
    seen: set[tuple[str, int]] = set()
    unique: list[tuple[str, int, str]] = []
    with lock:
        for ip, port, name in candidates:
            k = (ip, port)
            if k not in seen:
                seen.add(k)
                unique.append((ip, port, name))
    return unique


def _pick_endpoint(candidates: list[tuple[str, int, str]]) -> tuple[str, int]:
    if not candidates:
        return _parse_env_tv_addr()
    pref = _tv_name_pref()
    if pref:
        pref_l = pref.lower()
        matching = [c for c in candidates if pref_l in c[2].lower()]
        chosen = matching[0] if matching else candidates[0]
    else:
        chosen = candidates[0]
    return chosen[0], chosen[1]


class TVAgent:
    """
    Stateful adb-shell TV client with cached discovery and auto-reconnect.

    Primary API for Action Engine compatibility:
      ``tv_power_toggle()``, ``tv_volume(direction, steps)``, ``tv_launch_app(name)``
    Low-level:
      ``execute_action("tv_power" | "tv_volume_up" | "tv_volume_down" | "tv_mute")``
    """

    _ACTION_MAP: dict[str, int] = {
        "tv_power": _KEYEVENT_POWER,
        "tv_volume_up": _KEYEVENT_VOLUME_UP,
        "tv_volume_down": _KEYEVENT_VOLUME_DOWN,
        "tv_mute": _KEYEVENT_MUTE,
    }

    _ACTION_MSG: dict[str, str] = {
        "tv_power": "TV power toggled, Sir.",
        "tv_volume_up": "TV volume increased, Sir.",
        "tv_volume_down": "TV volume decreased, Sir.",
        "tv_mute": "TV muted, Sir.",
    }

    def __init__(self) -> None:
        key_path = _ensure_credentials_dir_and_key()
        self._signer = PythonRSASigner.FromRSAKeyPath(key_path)
        self._lock = threading.Lock()
        self._device: Optional[AdbDeviceTcp] = None
        self._cached_host: Optional[str] = None
        self._cached_port: Optional[int] = None

    # ── Discovery / connection ────────────────────────────────────────────────

    def _invalidate_cache_locked(self) -> None:
        self._device = None
        self._cached_host = None
        self._cached_port = None

    def invalidate_cache(self) -> None:
        """Public hook to force next operation to re-discover."""
        with self._lock:
            self._invalidate_cache_locked()

    def _connect_to(self, host: str, port: int) -> AdbDeviceTcp:
        dev = AdbDeviceTcp(host, port, default_transport_timeout_s=9.0)
        dev.connect(rsa_keys=[self._signer])
        return dev

    def _establish_session_locked(self, *, allow_discovery: bool) -> tuple[bool, str]:
        """
        Ping existing transport, reconnect to cached host:port, or resolve a new
        endpoint. mDNS runs only when ``allow_discovery`` is True (typically cache
        is empty, or a retry after invalidate).
        """
        if self._device is not None:
            try:
                self._device.shell("echo jarvis_tv_ping")
                return True, ""
            except Exception:
                self._device = None

        if self._cached_host is not None and self._cached_port is not None:
            try:
                self._device = self._connect_to(self._cached_host, self._cached_port)
                return True, ""
            except Exception:
                self._invalidate_cache_locked()

        if allow_discovery:
            cands = _discover_tv_candidates(timeout_s=5.0)
            host, port = _pick_endpoint(cands)
            print(f"[TVAgent] Resolved TV endpoint {host}:{port}", flush=True)
        else:
            host, port = _parse_env_tv_addr()

        try:
            self._device = self._connect_to(host, port)
            self._cached_host = host
            self._cached_port = port
            return True, ""
        except Exception as exc:
            self._invalidate_cache_locked()
            return False, f"Could not connect to the TV, Sir: {exc!s}"

    def _ensure_session(self, *, allow_discovery: bool) -> tuple[bool, str]:
        with self._lock:
            return self._establish_session_locked(allow_discovery=allow_discovery)

    # ── Shell with reconnect + single retry ────────────────────────────────────

    def _shell(self, cmd: str, *, retry_on_failure: bool = True) -> tuple[bool, str]:
        """
        Run shell command on TV. On transport failure: invalidate cache, then retry
        once with mDNS discovery enabled.
        """
        last_err = ""

        with self._lock:
            cache_miss = self._cached_host is None

        def attempt(allow_discovery: bool) -> tuple[bool, str]:
            nonlocal last_err
            ok_sess, err_sess = self._ensure_session(allow_discovery=allow_discovery)
            if not ok_sess:
                return False, err_sess or "TV session unavailable, Sir."
            try:
                assert self._device is not None
                out = self._device.shell(cmd)
                return True, (out or "").strip()
            except Exception as exc:
                last_err = str(exc)
                print(f"[TVAgent] shell failed: {exc!r} cmd={cmd[:80]!r}", flush=True)
                with self._lock:
                    self._invalidate_cache_locked()
                return False, last_err

        ok, msg = attempt(allow_discovery=cache_miss)
        if ok:
            return True, msg

        if not retry_on_failure:
            return False, f"TV command failed, Sir: {last_err[:120]}"

        ok2, msg2 = attempt(allow_discovery=True)
        if ok2:
            return True, msg2
        return False, f"TV command failed after reconnect, Sir: {last_err[:120]}"

    def _keyevent(self, code: int) -> tuple[bool, str]:
        return self._shell(f"input keyevent {code}")

    # ── execute_action (intent bridge) ─────────────────────────────────────────

    def execute_action(self, action_type: str) -> str:
        """Map high-level intents to keyevents; returns user-facing status string."""
        key = action_type.lower().strip()
        if key not in self._ACTION_MAP:
            known = ", ".join(sorted(self._ACTION_MAP))
            return f"Unknown TV action '{action_type}', Sir. Known actions: {known}."

        code = self._ACTION_MAP[key]
        ok, _out = self._keyevent(code)
        if not ok:
            return (
                "I couldn't reach the TV, Sir. It may be off, on a new wireless-debug "
                f"port, or pairing was rejected. Detail: {_out[:100]}"
            )
        return self._ACTION_MSG[key]

    # ── Action Engine compatibility ─────────────────────────────────────────────

    def tv_power_toggle(self) -> str:
        return self.execute_action("tv_power")

    def tv_volume(self, direction: str, steps: int = 1) -> str:
        direction = direction.lower().strip()
        steps = max(1, min(steps, 20))

        if direction in ("mute", "toggle_mute"):
            return self.execute_action("tv_mute")

        if direction in ("up", "volume_up", "increase", "louder"):
            action_key = "tv_volume_up"
        elif direction in ("down", "volume_down", "decrease", "quieter", "lower"):
            action_key = "tv_volume_down"
        else:
            return (
                f"Unknown volume direction '{direction}', Sir. "
                "Use 'up', 'down', or 'mute'."
            )

        code = self._ACTION_MAP[action_key]
        last_fail = ""
        for i in range(steps):
            ok, err = self._keyevent(code)
            if not ok:
                last_fail = err
                break
        else:
            label = f"up by {steps}" if action_key == "tv_volume_up" else f"down by {steps}"
            return f"TV volume {label}, Sir."

        return f"Volume adjustment failed on step {i + 1}, Sir: {last_fail[:80]}"

    def tv_launch_app(self, app_name: str) -> str:
        pkg = _resolve_package(app_name)
        if pkg is None:
            known = ", ".join(sorted(_APP_PACKAGES.keys()))
            return (
                f"I don't have a package mapping for '{app_name}', Sir. "
                f"Known apps: {known}."
            )

        monkey = (
            f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1"
        )
        ok, out = self._shell(monkey)
        if ok and "error" not in out.lower():
            display = app_name.strip().title() if app_name.strip() else app_name
            return f"Launching {display} on the TV, Sir."

        ok2, out2 = self._shell(f"am start -n {pkg}/android.app.NativeActivity")
        if ok2:
            display = app_name.strip().title() if app_name.strip() else app_name
            return f"Launching {display} on the TV, Sir."

        return (
            f"Failed to launch {app_name} on the TV, Sir. "
            f"{(out or out2)[:100]}"
        )

    def get_installed_media_apps(self) -> list[str]:
        ok, out = self._shell("pm list packages")
        if not ok:
            return []
        
        installed = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("package:"):
                pkg = line.split("package:")[1].strip()
                if pkg in SUPPORTED_MEDIA_APPS:
                    installed.append(SUPPORTED_MEDIA_APPS[pkg])
        return installed

    def tv_play_media(self, target: str) -> str:
        if ":" not in target:
            installed = self.get_installed_media_apps()
            if not installed:
                return (
                    f"I'd love to put on {target}, Sir, but I couldn't find any "
                    "supported media apps installed on the TV."
                )
            if len(installed) == 1:
                app_list_str = installed[0]
            else:
                app_list_str = ", ".join(installed[:-1]) + " or " + installed[-1]
            return (
                f"I can play {target} for you, Sir, though you didn't specify a platform. "
                f"I can see {app_list_str} installed on the TV. Which one would you prefer?"
            )
        
        app, query = target.split(":", 1)
        app = app.lower().strip()
        query = query.strip()
        formatted_query = query.replace(" ", "%20")
        
        self._keyevent(224) # KEYCODE_WAKEUP
        time.sleep(1.0)
        
        package_hotstar = "in.startv.hotstar"
        if "hotstar" in app or "disney" in app:
            # Hotstar TV ignores SEARCH/query payloads on many builds — open search UI then type.
            self._shell(f'am start -a android.intent.action.SEARCH -p {package_hotstar}')
            time.sleep(4.0)
            safe_query = query.replace(" ", "%s")
            self._shell(f"input text {safe_query}")
            time.sleep(1.5)
            self._shell("input keyevent 20")  # DPAD_DOWN
            time.sleep(0.5)
            self._shell("input keyevent 20")  # DPAD_DOWN
            time.sleep(0.5)
            self._shell("input keyevent 66")  # ENTER — select/play focus
            return f"Opening Hotstar and searching for {query}, Sir."
        elif "netflix" in app:
            self._shell(f'am start -a android.intent.action.VIEW -d "netflix://search?q={formatted_query}" -f 0x10800000')
            return f"Launching Netflix and searching for {query}, sir."
        elif "prime" in app or "amazon" in app:
            self._shell('am start -a android.intent.action.VIEW -d "amzn://apps/android?p=com.amazon.avod.thirdpartyclient" -n com.amazon.avod.thirdpartyclient/.LauncherActivity')
            time.sleep(3.0)
            formatted_text = query.replace(" ", "%s")
            self._shell(f"input text {formatted_text}")
            self._shell("input keyevent 66") # Enter
            return f"Opening Prime Video and searching for {query}, sir."
        elif "spotify" in app:
            self._shell(f'am start -a android.intent.action.VIEW -d "spotify://search/{formatted_query}"')
            return f"Opening Spotify to search for {query}, sir."
        elif "youtube" in app:
            package = "com.google.android.youtube.tv"
            try:
                video_url: Optional[str] = None
                if VideosSearch is not None:
                    try:
                        videos_search = VideosSearch(query, limit=1)
                        items = videos_search.result().get("result") or []
                        if items:
                            link = items[0].get("link")
                            if link:
                                video_url = link
                    except Exception as inner_exc:
                        print(
                            f"[TVAgent] VideosSearch failed ({inner_exc!r}); "
                            "using HTML fallback.",
                            flush=True,
                        )
                if not video_url:
                    video_url = _youtube_first_watch_url_fallback(query)
                uri_q = shlex.quote(video_url)
                self._shell(
                    f"am start -a android.intent.action.VIEW -d {uri_q} -p {package}"
                )
                time.sleep(4)
                self._shell("input keyevent 66")  # DPAD_CENTER / ENTER
                return "Playing on YouTube, Sir."
            except Exception as exc:
                print(f"[TVAgent] YouTube sniper failed: {exc!r}", flush=True)
                return (
                    "I encountered an error fetching the exact YouTube link, Sir."
                )
        else:
            return "That TV app isn't wired up yet, Sir."