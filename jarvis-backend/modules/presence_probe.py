"""
presence_probe.py — Track B: is the owner HOME even when he's not at the desk?
=============================================================================

The face gate answers "is he AT THE DESK" (seconds-accurate, camera-bound).
Nothing answered "is he in the HOUSE" — so an alert either went to an empty room
or buzzed his phone while he was sitting right here. This module fills that gap
with the only signal available without an app: **is his phone on the home LAN.**

    AT_DESK  (face gate)          -> desk HUD + TTS, no phone buzz
    HOME, not at the desk         -> desk HUD + TTS *and* phone
    AWAY                          -> phone only (no talking to an empty room)

DETECTION LADDER (cheapest/most reliable first)
-----------------------------------------------
1. **ARP table** after a priming ping — the phone answers ARP even when every
   app is closed, and a MAC match finds it *even if DHCP moved its IP*. This is
   the primary signal; `JARVIS_PHONE_MAC` is worth setting.
2. **TCP connect** to a known phone port (IP Webcam 8080 / DroidCam 4747) — only
   true while those apps run, but a positive is unambiguous.
3. **ICMP ping** — last resort; many phones ignore pings while dozing.

WHY THE DEBOUNCE IS ASYMMETRIC
------------------------------
A phone sleeps its WiFi radio: it drops off ARP for a minute at a time while
sitting on the desk. So **any hit means HOME immediately**, but AWAY needs a long
unbroken miss streak (`JARVIS_PRESENCE_AWAY_GRACE`, default 180s). Getting this
backwards would make JARVIS announce "you left" while the owner is reading.

Config: `JARVIS_PHONE_IP`, `JARVIS_PHONE_MAC` (pin a fixed MAC for the home SSID
in Android's WiFi settings — MAC randomisation defeats this otherwise),
`JARVIS_PRESENCE_PORTS`, `JARVIS_PRESENCE_INTERVAL`, `JARVIS_PRESENCE_AWAY_GRACE`.
The monitor only runs when an IP or MAC is configured — no config, no probing.

Everything decision-shaped (parsing, debounce, fusion, routing) is pure and
injectable, so test_presence_probe.py exercises the whole ladder with fake
subprocess output and a fake clock: no network, no phone.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field

# States. "unknown" is a real state, not a placeholder: before the first probe
# lands we must NOT claim AWAY (that would silence the desk on every boot).
UNKNOWN, HOME, AWAY = "unknown", "home", "away"

# Fused presence
AT_DESK = "at_desk"

DEFAULT_PORTS = (8080, 4747)      # IP Webcam, DroidCam — what his phone runs
DESK_PRESENCE_MAX_AGE_S = 10.0    # a face sighting older than this isn't "at the desk"

_MAC_RE = re.compile(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #

def normalise_mac(mac: str | None) -> str | None:
    """`AA-BB-cc:DD:ee:FF` -> `aabbccddeeff`; None for anything unparseable."""
    if not mac:
        return None
    hexes = re.sub(r"[^0-9a-fA-F]", "", mac).lower()
    return hexes if len(hexes) == 12 else None


def parse_arp_table(text: str) -> list[tuple[str, str]]:
    """Extract `(ip, normalised_mac)` pairs from ARP output.

    Handles Windows `arp -a` (`192.168.0.5   aa-bb-cc-dd-ee-ff   dynamic`) and
    Linux `arp -n` / `ip neigh` (`192.168.0.5 dev wlan0 lladdr aa:bb:… REACHABLE`)
    by looking for an IPv4 and a MAC on the same line rather than by column, so a
    locale-translated header or an extra field can't break it.
    """
    pairs: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        mac_m = _MAC_RE.search(line)
        ip_m = _IP_RE.search(line)
        if not (mac_m and ip_m):
            continue
        mac = normalise_mac(mac_m.group(0))
        if mac and mac != "ffffffffffff":       # skip the broadcast row
            pairs.append((ip_m.group(0), mac))
    return pairs


def arp_hit(text: str, ip: str | None = None, mac: str | None = None) -> str | None:
    """Which identifier matched in the ARP table: "mac", "ip", or None.

    MAC wins because it survives a DHCP move — the IP is only a fallback for a
    setup where the MAC was never pinned.
    """
    want_mac = normalise_mac(mac)
    pairs = parse_arp_table(text)
    if want_mac and any(m == want_mac for _, m in pairs):
        return "mac"
    if ip and any(i == ip for i, _ in pairs):
        return "ip"
    return None


class PresenceDebounce:
    """Asymmetric HOME/AWAY debounce — see the module docstring for the why.

    `update(hit, t)` returns the current state. Pure; fake-clock testable.
    """

    def __init__(self, away_grace_s: float = 180.0):
        self.away_grace_s = away_grace_s
        self.state = UNKNOWN
        self.last_hit_t: float | None = None
        self.last_probe_t: float | None = None
        self.changed = False          # True on the probe that flipped the state

    def update(self, hit: bool, t: float) -> str:
        prev = self.state
        self.last_probe_t = t
        if hit:
            self.last_hit_t = t
            self.state = HOME
        else:
            # Start the grace at the FIRST miss (or the first probe ever), so a
            # cold start doesn't need a full grace window before it can say AWAY,
            # and a phone that never appeared isn't reported HOME.
            if self.last_hit_t is None:
                self.last_hit_t = t
            elif (t - self.last_hit_t) >= self.away_grace_s:
                self.state = AWAY
        self.changed = self.state != prev
        return self.state

    def seconds_since_hit(self, t: float) -> float | None:
        return None if self.last_hit_t is None else t - self.last_hit_t


def fuse(at_desk: bool, lan_state: str) -> str:
    """Combine the face gate with the LAN probe into one presence verdict.

    The face gate outranks the LAN: if he's on camera he is at the desk whatever
    his phone is doing (phone charging in another room, radio asleep, left at
    work). LAN only decides HOME vs AWAY when the camera can't see him.
    """
    if at_desk:
        return AT_DESK
    return lan_state if lan_state in (HOME, AWAY) else UNKNOWN


def routing(presence: str) -> dict:
    """Where a proactive message should go for a given presence.

    UNKNOWN deliberately routes EVERYWHERE — an unconfigured or not-yet-probed
    system must behave exactly like the pre-Track-B build (desk + phone), never
    quieter. Silence is the one failure mode an alert path must not have.
    """
    if presence == AT_DESK:
        return {"speak": True, "phone": False}
    if presence == HOME:
        return {"speak": True, "phone": True}
    if presence == AWAY:
        return {"speak": False, "phone": True}
    return {"speak": True, "phone": True}


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

@dataclass
class PresenceConfig:
    phone_ip: str | None = None
    phone_mac: str | None = None
    ports: tuple = DEFAULT_PORTS
    interval_s: float = 60.0
    away_grace_s: float = 180.0
    ping_timeout_ms: int = 800
    tcp_timeout_s: float = 0.6

    @property
    def configured(self) -> bool:
        """No phone IP and no phone MAC = nothing to probe. Don't run."""
        return bool(self.phone_ip or normalise_mac(self.phone_mac))

    @classmethod
    def from_env(cls, env=None) -> "PresenceConfig":
        env = os.environ if env is None else env

        def f(name, default):
            try:
                return float(env.get(name, ""))
            except (TypeError, ValueError):
                return default

        ports = []
        for chunk in (env.get("JARVIS_PRESENCE_PORTS") or "").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                ports.append(int(chunk))
        return cls(
            phone_ip=(env.get("JARVIS_PHONE_IP") or "").strip() or None,
            phone_mac=(env.get("JARVIS_PHONE_MAC") or "").strip() or None,
            ports=tuple(ports) or DEFAULT_PORTS,
            interval_s=f("JARVIS_PRESENCE_INTERVAL", 60.0),
            away_grace_s=f("JARVIS_PRESENCE_AWAY_GRACE", 180.0),
        )


# --------------------------------------------------------------------------- #
# probes (I/O, all injectable)
# --------------------------------------------------------------------------- #

def _run_capture(cmd: list[str], timeout: float = 4.0) -> str:
    """Run a command, return stdout ('' on any failure). Never raises."""
    import subprocess
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout,
                             creationflags=0x08000000 if os.name == "nt" else 0)
        return (out.stdout or b"").decode("utf-8", "replace")
    except Exception:      # noqa: BLE001 — a missing binary must not break presence
        return ""


def ping_cmd(ip: str, timeout_ms: int = 800) -> list[str]:
    """Platform ping args for ONE probe packet (pure, so the harness can assert)."""
    if os.name == "nt":
        return ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    return ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), ip]


def arp_cmd() -> list[str]:
    return ["arp", "-a"] if os.name == "nt" else ["arp", "-n"]


def tcp_open(ip: str, port: int, timeout: float = 0.6, connect=None) -> bool:
    """One TCP connect probe. `connect` injectable for the harness."""
    if connect is None:
        import socket

        def connect(addr, t):    # noqa: E306
            s = socket.create_connection(addr, t)
            s.close()
    try:
        connect((ip, port), timeout)
        return True
    except Exception:      # noqa: BLE001
        return False


def probe_once(cfg: PresenceConfig, *, run=None, connect=None) -> tuple[bool, str | None]:
    """Walk the detection ladder once. Returns `(hit, how)`.

    `how` is the rung that answered ("arp:mac", "arp:ip", "tcp:8080", "icmp") —
    logged on transitions so a live gate can tell WHICH signal is carrying, which
    is the difference between "presence works" and "presence works by luck".
    """
    run = _run_capture if run is None else run

    # 1. prime the ARP cache, then read it. The ping's own result is ignored
    #    here — a phone that refuses ICMP still populates ARP by replying to the
    #    ARP request the ping forces.
    if cfg.phone_ip:
        run(ping_cmd(cfg.phone_ip, cfg.ping_timeout_ms))
    where = arp_hit(run(arp_cmd()), ip=cfg.phone_ip, mac=cfg.phone_mac)
    if where:
        return True, f"arp:{where}"

    # 2. a known phone app port
    if cfg.phone_ip:
        for port in cfg.ports:
            if tcp_open(cfg.phone_ip, port, cfg.tcp_timeout_s, connect=connect):
                return True, f"tcp:{port}"

        # 3. ICMP, for whatever it's worth
        out = run(ping_cmd(cfg.phone_ip, cfg.ping_timeout_ms))
        if ping_succeeded(out):
            return True, "icmp"

    return False, None


def ping_succeeded(output: str) -> bool:
    """Did a single-packet ping actually get a reply?

    Exit codes lie on Windows ("Destination host unreachable" is a *reply* and
    exits 0), so this reads the text: a real reply carries a TTL.
    """
    low = (output or "").lower()
    if not low:
        return False
    if "unreachable" in low or "timed out" in low or "100% packet loss" in low:
        return False
    return "ttl=" in low or "ttl " in low


# --------------------------------------------------------------------------- #
# desk presence (fed by the gesture daemon's face gate)
# --------------------------------------------------------------------------- #

_desk_lock = threading.Lock()
_desk_present = False
_desk_stamp = 0.0


def note_desk_presence(present: bool, now: float | None = None) -> None:
    """Called by gesture_daemon on each face check.

    Push, not pull: this module must never import gesture_daemon (that would drag
    the camera stack into the notify path), and the daemon already knows the
    answer once per second.
    """
    global _desk_present, _desk_stamp
    with _desk_lock:
        _desk_present = bool(present)
        _desk_stamp = time.monotonic() if now is None else now


def at_desk(max_age_s: float = DESK_PRESENCE_MAX_AGE_S, now: float | None = None) -> bool:
    """True only for a FRESH owner sighting — a stale one means the daemon died,
    not that he's sitting there."""
    t = time.monotonic() if now is None else now
    with _desk_lock:
        return _desk_present and (t - _desk_stamp) <= max_age_s


# --------------------------------------------------------------------------- #
# monitor
# --------------------------------------------------------------------------- #

@dataclass
class _State:
    lan: str = UNKNOWN
    how: str | None = None
    last_hit_ago: float | None = None
    last_probe: float = 0.0
    running: bool = False


_state = _State()
_state_lock = threading.Lock()


def snapshot() -> dict:
    """Current presence for the HUD / API — fused, so callers get one answer."""
    with _state_lock:
        lan, how, ago, last, running = (_state.lan, _state.how, _state.last_hit_ago,
                                        _state.last_probe, _state.running)
    return {"presence": fuse(at_desk(), lan), "lan": lan, "at_desk": at_desk(),
            "how": how, "seconds_since_phone_seen": ago, "last_probe": last,
            "running": running}


def presence_routing() -> dict:
    """Routing decision for the current fused presence (owner_notify uses this)."""
    with _state_lock:
        lan = _state.lan
    return routing(fuse(at_desk(), lan))


class PresenceMonitor:
    """Thread daemon (ambient_vision Pattern B): probe, debounce, publish."""

    def __init__(self, cfg: PresenceConfig | None = None):
        self.cfg = cfg or PresenceConfig.from_env()
        self.debounce = PresenceDebounce(self.cfg.away_grace_s)
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.running:
            return
        if not self.cfg.configured:
            print("[PRESENCE] no JARVIS_PHONE_IP/JARVIS_PHONE_MAC — probe disabled",
                  flush=True)
            return
        self.running = True
        with _state_lock:
            _state.running = True
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="presence-probe")
        self.thread.start()
        print(f"[PRESENCE] Track B probe started (ip={self.cfg.phone_ip} "
              f"mac={'set' if normalise_mac(self.cfg.phone_mac) else 'unset'} "
              f"every {self.cfg.interval_s:.0f}s, away after "
              f"{self.cfg.away_grace_s:.0f}s)", flush=True)

    def stop(self) -> None:
        self.running = False
        with _state_lock:
            _state.running = False
        if self.thread:
            self.thread.join(timeout=2.0)

    def tick(self, now: float | None = None) -> str:
        """One probe + debounce + publish. Separated from the loop so it can be
        driven directly (harness / a manual API poke) without a thread."""
        t = time.monotonic() if now is None else now
        try:
            hit, how = probe_once(self.cfg)
        except Exception as e:   # noqa: BLE001 — presence must never crash a daemon
            print(f"[PRESENCE] probe fault: {e}", flush=True)
            hit, how = False, None
        state = self.debounce.update(hit, t)
        with _state_lock:
            _state.lan = state
            _state.how = how
            _state.last_hit_ago = self.debounce.seconds_since_hit(t)
            _state.last_probe = time.time()
        if self.debounce.changed:
            print(f"[PRESENCE] phone {state.upper()}"
                  f"{f' via {how}' if how else ''}", flush=True)
        return state

    def _run(self) -> None:
        while self.running:
            self.tick()
            # sleep in slices so stop() doesn't wait out a whole interval
            slept = 0.0
            while self.running and slept < self.cfg.interval_s:
                time.sleep(min(1.0, self.cfg.interval_s - slept))
                slept += 1.0
