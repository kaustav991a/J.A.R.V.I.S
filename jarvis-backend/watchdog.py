"""
watchdog.py — The Unkillable Supervisor
========================================

A standalone, dependency-light process manager that lives OUTSIDE the FastAPI app.
Its whole job is to keep J.A.R.V.I.S.'s server breathing.

WHAT IT DOES
------------
1. Launches the FastAPI server (`uvicorn main:app`) as a child process.
2. Continuously monitors that child. If `main.py` crashes, throws a fatal
   exception, or is killed from Task Manager, the watchdog logs the death and
   immediately restarts it. The server effectively cannot stay down.
3. Exposes a TINY authenticated control endpoint (localhost only) so the operator
   can take the whole system OFFLINE on purpose — the one legitimate way to stop
   the restart loop. Without the token, nothing can talk it down.

WHY A SEPARATE PROCESS
----------------------
A supervisor that lived inside the app would die with the app. By running the
server as a child of this script, a crash in the server never touches the
watchdog — it just observes the exit and respawns.

USAGE
-----
    python watchdog.py

Graceful shutdown (the only clean way to stop without a restart):
    curl -X POST "http://127.0.0.1:8009/shutdown?token=YOUR_TOKEN"
    (or, from Telegram:  /offline YOUR_TOKEN)

Ctrl+C also stops the watchdog and its child cleanly.

CONFIG (.env or environment)
----------------------------
    WATCHDOG_TOKEN          required-ish — shared secret for /shutdown (auto-generated & printed if unset)
    WATCHDOG_CONTROL_PORT   default 8009 — localhost control port
    JARVIS_HOST             default 127.0.0.1 — uvicorn bind host
    JARVIS_PORT             default 8000 — uvicorn bind port
    WATCHDOG_MAX_RAPID_FAILS  default 5 — abort if it crashes this many times in WATCHDOG_RAPID_WINDOW s
    WATCHDOG_RAPID_WINDOW     default 60 — rapid-crash window (seconds)
"""

from __future__ import annotations

import os
import sys
import time
import signal
import secrets
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# Force UTF-8 on stdout/stderr so the emoji log lines below can't raise
# UnicodeEncodeError when stdout is redirected (service/pipe/Electron shell → cp1252).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load .env so token/ports can live alongside the rest of J.A.R.V.I.S.'s config.
#
# F-03: this used to be `except Exception: pass`, and on 2026-08-08 that cost
# the one alert that matters. Launched under an interpreter without
# python-dotenv, the watchdog ran with NO environment at all, then gave up on a
# crash loop and reported "No TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID — owner
# alert not sent" while .env contained both. The single signal that says the
# server is unrecoverable failed silently AND blamed the wrong thing.
#
# A watchdog has no business running blind about its own config, so the failure
# is now loud and it is remembered — _notify_owner_down uses it to say which of
# the two things went wrong.
DOTENV_LOADED = False
DOTENV_ERROR: str | None = None
try:
    from dotenv import load_dotenv
    load_dotenv()
    DOTENV_LOADED = True
except ImportError:
    DOTENV_ERROR = ("python-dotenv is not installed for this interpreter, so "
                    ".env was NOT read")
except Exception as _dotenv_exc:            # a malformed .env, a permissions fault
    DOTENV_ERROR = f".env could not be loaded: {type(_dotenv_exc).__name__}: {_dotenv_exc}"

# ── Paths ────────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "watchdog.log")

# ── Config ────────────────────────────────────────────────────────────────────
CONTROL_PORT = int(os.getenv("WATCHDOG_CONTROL_PORT", "8009"))
HOST = os.getenv("JARVIS_HOST", "127.0.0.1")
PORT = os.getenv("JARVIS_PORT", "8000")
MAX_RAPID_FAILS = int(os.getenv("WATCHDOG_MAX_RAPID_FAILS", "5"))
RAPID_WINDOW = int(os.getenv("WATCHDOG_RAPID_WINDOW", "60"))
# After this many consecutive rapid-crash BACKOFF cycles with no healthy run in
# between, stop respawning and alert the owner: the server is permanently broken
# (bad config, missing dep, corrupt state) and blind respawning just spins the CPU.
MAX_GIVEUP_CYCLES = int(os.getenv("WATCHDOG_MAX_GIVEUP_CYCLES", "3"))

# A token is mandatory for shutdown. If the operator didn't set one, generate a
# session token and print it loudly — that way the endpoint is never wide open.
TOKEN = os.getenv("WATCHDOG_TOKEN", "").strip()
if not TOKEN:
    TOKEN = secrets.token_urlsafe(16)
    _TOKEN_AUTOGEN = True
else:
    _TOKEN_AUTOGEN = False


# ── Shared shutdown flag (set by the control endpoint or Ctrl+C) ──────────────
_shutdown_event = threading.Event()


def log(msg: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════════════
# Authenticated control endpoint (localhost only)
# ════════════════════════════════════════════════════════════════════════════
class _ControlHandler(BaseHTTPRequestHandler):
    def _respond(self, code: int, body: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _check_token(self) -> bool:
        qs = parse_qs(urlparse(self.path).query)
        supplied = (qs.get("token", [""])[0]).strip()
        return bool(supplied) and secrets.compare_digest(supplied, TOKEN)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/shutdown":
            if not self._check_token():
                log(f"⛔ Rejected shutdown attempt from {self.client_address[0]} (bad/missing token).")
                return self._respond(403, "Forbidden: invalid token.\n")
            log("✅ Authenticated shutdown requested — taking the system offline.")
            self._respond(200, "J.A.R.V.I.S. is going offline. Watchdog stopping.\n")
            _shutdown_event.set()
        else:
            self._respond(404, "Not found.\n")

    def do_GET(self) -> None:
        # Lightweight health check — no token needed, exposes no secrets.
        if urlparse(self.path).path == "/health":
            self._respond(200, "watchdog: alive\n")
        else:
            self._respond(404, "Not found.\n")

    def log_message(self, *args) -> None:  # silence default stderr spam
        return


def _start_control_server() -> HTTPServer | None:
    try:
        # 127.0.0.1 only — never reachable off-box.
        server = HTTPServer(("127.0.0.1", CONTROL_PORT), _ControlHandler)
    except Exception as e:
        log(f"⚠️  Could not bind control port {CONTROL_PORT}: {e}. Shutdown endpoint disabled.")
        return None
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log(f"Control endpoint listening on http://127.0.0.1:{CONTROL_PORT} (POST /shutdown?token=…).")
    return server


# ════════════════════════════════════════════════════════════════════════════
# Child process management
# ════════════════════════════════════════════════════════════════════════════
def _server_command() -> list[str]:
    # Reuse THIS interpreter (the venv's python) so the child inherits the same
    # environment/packages. No --reload: the watchdog is the supervisor now.
    return [
        sys.executable, "-m", "uvicorn", "main:app",
        "--host", HOST, "--port", str(PORT),
    ]


class RespawnPolicy:
    """Pure respawn / give-up bookkeeping (no processes) so it is unit-testable.

    Feed each child death via record_death(uptime, now); it returns whether to
    back off (rapid flapping) and whether to GIVE UP (too many backoff cycles
    with no healthy run in between). A run that survived >= rapid_window resets
    the give-up strike count — a server that ran fine for a while and then died
    is a transient crash, not a startup fault.
    """

    def __init__(self, max_rapid: int = 5, rapid_window: int = 60, max_giveup: int = 3):
        self.max_rapid = max_rapid
        self.rapid_window = rapid_window
        self.max_giveup = max_giveup
        self.crash_times: list[float] = []
        self.giveup_strikes = 0

    def record_death(self, uptime: float, now: float) -> dict:
        if uptime >= self.rapid_window:
            self.giveup_strikes = 0          # healthy run — reset the strike count
        self.crash_times.append(now)
        self.crash_times = [t for t in self.crash_times if now - t <= self.rapid_window]
        recent = len(self.crash_times)
        rapid = recent >= self.max_rapid
        give_up = False
        if rapid:
            self.giveup_strikes += 1
            give_up = self.giveup_strikes >= self.max_giveup
            if not give_up:
                self.crash_times.clear()     # fresh window after a backoff
        return {"rapid_backoff": rapid, "give_up": give_up,
                "recent": recent, "strikes": self.giveup_strikes}


def _notify_owner_down(reason: str) -> None:
    """Best-effort owner alert when the watchdog gives up — sent from THIS
    standalone process with the stdlib only (urllib), so it works even if the
    FastAPI app that owns owner_notify is the very thing that won't start."""
    import urllib.parse
    import urllib.request

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_USER_ID", "").strip()
    msg = ("🛑 J.A.R.V.I.S. WATCHDOG: the server keeps crashing on startup and I "
           f"have stopped restarting it. {reason} It needs manual attention, Sir.")
    log(msg)
    if not (token and chat_id):
        if DOTENV_ERROR:
            # Do NOT say the credentials are missing — we never got to look.
            log(f"   (Owner alert NOT sent: {DOTENV_ERROR}. The credentials may "
                f"be present in .env; this process could not read it. "
                f"Re-launch with the venv interpreter.)")
        else:
            log("   (No TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID in a .env that WAS "
                "read — owner alert not sent.)")
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        urllib.request.urlopen(req, timeout=10).read()
        log("   Owner alerted via Telegram.")
    except Exception as e:  # noqa: BLE001 — alerting must never crash the watchdog
        log(f"   Owner alert failed to send: {e}")


def _terminate_child(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> None:
    log("=" * 70)
    log("J.A.R.V.I.S. WATCHDOG ONLINE — the server is now unkillable.")
    if DOTENV_ERROR:
        # Loud, at boot, before anything depends on it (F-03).
        log(f"⚠️  CONFIG NOT LOADED — {DOTENV_ERROR}.")
        log("⚠️  Every setting in .env reads as absent, including the Telegram "
            "credentials this watchdog needs to tell you it gave up.")
        log("⚠️  Re-launch with: venv\\Scripts\\python.exe watchdog.py")
    if _TOKEN_AUTOGEN:
        log(f"🔑 No WATCHDOG_TOKEN set — generated session token: {TOKEN}")
        log("   (Set WATCHDOG_TOKEN in .env for a stable token across restarts.)")
    log(f"Server command: {' '.join(_server_command())}")
    log("=" * 70)

    _start_control_server()

    # Ctrl+C / SIGTERM → graceful stop of the whole supervisor.
    def _on_signal(signum, frame):
        log(f"Received signal {signum} — shutting down watchdog.")
        _shutdown_event.set()
    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
        # SIGBREAK is Ctrl+Break, and on Windows it is a normal way to stop a
        # console program. Without this the supervisor died on the default
        # handler and left the uvicorn child RUNNING and unsupervised - a
        # half-stopped system that looks stopped from the console it was stopped
        # from. Same handler, because there is only one right answer to "stop".
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _on_signal)
    except Exception:
        pass

    # On Windows, a new process group lets us deliver CTRL_BREAK to the child only.
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    policy = RespawnPolicy(MAX_RAPID_FAILS, RAPID_WINDOW, MAX_GIVEUP_CYCLES)
    restart_count = 0
    gave_up = False

    while not _shutdown_event.is_set() and not gave_up:
        log(f"Launching FastAPI server (start #{restart_count + 1})…")
        try:
            proc = subprocess.Popen(
                _server_command(),
                cwd=HERE,
                creationflags=creationflags,
            )
        except Exception as e:
            log(f"❌ Failed to launch server: {e}. Retrying in 5s.")
            if _shutdown_event.wait(5):
                break
            continue
        child_start = time.time()

        # Block until the child exits OR a shutdown is requested.
        while True:
            if _shutdown_event.is_set():
                log("Shutdown requested — terminating the server child.")
                _terminate_child(proc)
                break
            ret = proc.poll()
            if ret is not None:
                # Child died on its own.
                if _shutdown_event.is_set():
                    break
                uptime = time.time() - child_start
                log(f"💥 Server process exited with code {ret} after {uptime:.0f}s. Restarting…")
                restart_count += 1
                d = policy.record_death(uptime, time.time())
                if d["give_up"]:
                    # Permanently broken: too many rapid-crash cycles, no healthy
                    # run in between. Stop respawning and alert the owner.
                    log(f"🛑 Gave up after {d['strikes']} rapid-crash cycles — "
                        f"the server cannot start. No more restarts.")
                    _notify_owner_down(
                        f"{d['strikes']} rapid-crash cycles "
                        f"({MAX_RAPID_FAILS}+ crashes / {RAPID_WINDOW}s each), last exit code {ret}.")
                    gave_up = True
                    break
                if d["rapid_backoff"]:
                    # Flapping: back off hard so we don't spin the CPU restarting a
                    # server that can't start.
                    log(f"⚠️  {d['recent']} crashes within {RAPID_WINDOW}s "
                        f"(give-up strike {d['strikes']}/{MAX_GIVEUP_CYCLES}) — "
                        f"likely a startup fault. Backing off 30s before retry.")
                    if _shutdown_event.wait(30):
                        return
                else:
                    time.sleep(2)  # brief breather before respawn
                break
            time.sleep(1)

    log("Watchdog stopped. J.A.R.V.I.S. is offline.")


if __name__ == "__main__":
    main()
