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

# Load .env so token/ports can live alongside the rest of J.A.R.V.I.S.'s config.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ── Paths ────────────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "watchdog.log")

# ── Config ────────────────────────────────────────────────────────────────────
CONTROL_PORT = int(os.getenv("WATCHDOG_CONTROL_PORT", "8009"))
HOST = os.getenv("JARVIS_HOST", "127.0.0.1")
PORT = os.getenv("JARVIS_PORT", "8000")
MAX_RAPID_FAILS = int(os.getenv("WATCHDOG_MAX_RAPID_FAILS", "5"))
RAPID_WINDOW = int(os.getenv("WATCHDOG_RAPID_WINDOW", "60"))

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
    except Exception:
        pass

    # On Windows, a new process group lets us deliver CTRL_BREAK to the child only.
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    crash_times: list[float] = []
    restart_count = 0

    while not _shutdown_event.is_set():
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
                log(f"💥 Server process exited with code {ret}. Restarting…")
                crash_times.append(time.time())
                restart_count += 1
                # Rapid-crash circuit breaker: if it's flapping, back off hard so
                # we don't spin the CPU restarting a server that can't start.
                recent = [t for t in crash_times if time.time() - t <= RAPID_WINDOW]
                crash_times[:] = recent
                if len(recent) >= MAX_RAPID_FAILS:
                    log(
                        f"⚠️  {len(recent)} crashes within {RAPID_WINDOW}s — likely a "
                        f"startup fault, not a transient crash. Backing off 30s before retry."
                    )
                    if _shutdown_event.wait(30):
                        return
                    crash_times.clear()
                else:
                    time.sleep(2)  # brief breather before respawn
                break
            time.sleep(1)

    log("Watchdog stopped. J.A.R.V.I.S. is offline.")


if __name__ == "__main__":
    main()
