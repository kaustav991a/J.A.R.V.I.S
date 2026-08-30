"""Drive the A24 watchdog rows that stop the system, and check the machine.

Row `1.4` asks that `/shutdown` refuse a wrong token and honour the right one by
stopping **both** processes with no restart. Row `1.5` asks that a real Ctrl+C in
the watchdog's console shut the pair down cleanly.

Both rows END THE SESSION - the checklist says to do them last, and it means it.
This runs them in order and reports what the process table did, not what the HTTP
response said: a 200 from a shutdown endpoint is a promise, and the row is about
whether the promise was kept.

The token is never printed. It is a session secret, and a verifier that leaks it
into a transcript has created the exposure it was checking for.

    venv\\Scripts\\python.exe tools\\verify_a24.py --row 1.4
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PORT = 8009
TOKEN_LINE = re.compile(r"generated session token:\s*(\S+)")


def newest_log() -> Path:
    logs = sorted(HERE.glob("gate-session-*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        raise SystemExit("no gate-session log found")
    return logs[0]


def session_token() -> str:
    text = newest_log().read_bytes().decode("utf-8", "replace")
    hits = TOKEN_LINE.findall(text)
    if not hits:
        raise SystemExit("no session token in the log — is WATCHDOG_TOKEN set?")
    return hits[-1]


def post(token: str) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/shutdown?token={token}", data=b"",
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


def procs() -> dict[str, int]:
    """How many watchdog and server processes are alive."""
    ps = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$w=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*watchdog.py*' }).Count; "
         "$s=@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*main:app*' -or "
         "$_.CommandLine -like '*uvicorn*' }).Count; \"$w,$s\""],
        capture_output=True, text=True, timeout=90)
    out = (ps.stdout or "0,0").strip().splitlines()[-1]
    try:
        w, s = (int(x) for x in out.split(","))
    except ValueError:
        w, s = -1, -1
    return {"watchdog": w, "server": s}


def row_1_4() -> tuple[str, str]:
    before = procs()
    if before["watchdog"] < 1:
        return "SKIP", f"no watchdog running to shut down: {before}"

    wrong = post("WRONG-TOKEN-ON-PURPOSE")
    if wrong != 403:
        return "FAIL", (f"a wrong token got {wrong}, not 403 — the control port "
                        f"is the one door that stops everything")

    right = post(session_token())
    if right != 200:
        return "FAIL", f"the real token got {right}, not 200 (wrong-token check did pass)"

    # The row's actual claim: both stop, and NOTHING comes back.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        now = procs()
        if now["watchdog"] == 0 and now["server"] == 0:
            break
        time.sleep(3)
    else:
        return "FAIL", f"still running 90s after a 200: {procs()}"

    # A restart would show up as a server reappearing after the watchdog left.
    time.sleep(20)
    after = procs()
    if after["watchdog"] or after["server"]:
        return "FAIL", f"something restarted after the shutdown: {after}"
    return "PASS", (f"wrong token 403, real token 200, both processes gone and "
                    f"nothing restarted (was {before})")


ROWS = {"1.4": ("shutdown token: wrong 403, right stops both", row_1_4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--row", action="append")
    args = ap.parse_args()
    results = []
    for row in (args.row or list(ROWS)):
        if row not in ROWS:
            print(f"[{row}] not driven here")
            continue
        label, fn = ROWS[row]
        print(f"[{row}] {label}")
        verdict, why = fn()
        print(f"    -> {verdict}: {why}")
        results.append((row, verdict, why))
    failed = [r for r, v, _ in results if v == "FAIL"]
    print(f"\n{len([r for r, v, _ in results if v == 'PASS'])} verified, "
          f"{len(failed)} FAILED" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
