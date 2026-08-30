"""Catch the desk's event loop in the act of being blocked, and dump WHY.

Row 18.2 measured a 14-second stall while a long action ran. A stall is a
symptom; this names the cause. It polls a trivial endpoint, and the moment a
probe takes longer than the threshold it runs `py-spy dump` against the server
process, so the stack that was holding the loop is captured while it is still
holding it.

This is the instrument that found F-71, where the desk was not slow but stopped -
0% CPU, silent log, and nothing in any answer that showed it. A stack sample is
the only thing that says which line.

    venv\\Scripts\\python.exe tools\\catch_block.py "read my screen"
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
HEALTH = "http://127.0.0.1:8000/api/health/summary"
DOOR = "http://127.0.0.1:8000/api/backdoor"
SLOW = 3.0          # a probe over this means the loop is not being serviced
OUT = HERE / "block-dumps.txt"


def server_pid() -> int:
    """The uvicorn child, not the watchdog."""
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*main:app*' -or "
         "$_.CommandLine -like '*uvicorn*' } | "
         "Select-Object -First 1 -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=60)
    out = (ps.stdout or "").strip()
    return int(out) if out.isdigit() else 0


def dump(pid: int, why: str, fh) -> None:
    r = subprocess.run([str(HERE / "venv" / "Scripts" / "py-spy.exe"),
                        "dump", "--pid", str(pid)],
                       capture_output=True, text=True, timeout=120)
    fh.write(f"\n{'=' * 70}\n{why}\n{'=' * 70}\n")
    fh.write(r.stdout or r.stderr or "<no output>")
    fh.flush()


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "read my screen"
    pid = server_pid()
    if not pid:
        print("could not find the server process")
        return 2
    print(f"server pid {pid}; dumps -> {OUT.name}")

    stop = threading.Event()
    worst = [0.0]
    caught = [0]

    def poll() -> None:
        with OUT.open("w", encoding="utf-8") as fh:
            while not stop.is_set():
                t0 = time.monotonic()
                try:
                    with urllib.request.urlopen(HEALTH, timeout=60) as r:
                        r.read()
                    dt = time.monotonic() - t0
                except Exception as e:  # noqa: BLE001
                    dt = time.monotonic() - t0
                    print(f"  probe FAILED after {dt:.1f}s: {type(e).__name__}")
                worst[0] = max(worst[0], dt)
                if dt >= SLOW and caught[0] < 6:
                    caught[0] += 1
                    print(f"  !! probe took {dt:.1f}s - dumping the stack")
                    dump(pid, f"probe took {dt:.1f}s", fh)
                time.sleep(0.5)

    t = threading.Thread(target=poll, daemon=True)
    t.start()

    body = json.dumps({"command": command}).encode("utf-8")
    req = urllib.request.Request(DOOR, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=900):
            pass
    except Exception as e:  # noqa: BLE001
        print(f"command failed: {type(e).__name__} {e}")
    print(f"command took {time.monotonic() - t0:.1f}s")

    time.sleep(20)  # the stall can land after the HTTP call returns
    stop.set()
    t.join(timeout=15)
    print(f"worst probe {worst[0]:.1f}s; {caught[0]} stack dump(s) in {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
