"""Ask the running desk one command through the text door, and print what it said.

The gate's rows are sentences a person says. This sends one, waits as long as a
real turn takes, and prints the answer verbatim — no interpretation, because the
whole point of a gate row is reading what he actually said rather than what the
code suggests he would say.

    venv\\Scripts\\python.exe tools\\gate_ask.py "what's on my calendar today?"
    venv\\Scripts\\python.exe tools\\gate_ask.py --file rows.txt

`--file` takes one command per line, blank lines and `#` comments skipped, and
prints a numbered transcript. Requires `JARVIS_ALLOW_BACKDOOR=1` on the desk.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:8000/api/backdoor"
# A real turn on this desk can take minutes when the first provider is timing
# out: every Gemini key is tried and rotated before the cascade escalates. A
# client timeout shorter than that reports "no answer" for a desk that is working
# — which is exactly the wrong thing for a gate row to record.
TIMEOUT = 900


def ask(command: str) -> tuple[float, str]:
    body = json.dumps({"command": command}).encode("utf-8")
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return time.monotonic() - t0, f"[HTTP {e.code}] {e.read().decode()[:400]}"
    except Exception as e:  # noqa: BLE001
        return time.monotonic() - t0, f"[NO ANSWER] {e}"
    took = time.monotonic() - t0
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return took, raw[:2000]
    if isinstance(data, dict):
        for key in ("response", "reply", "text", "answer", "message"):
            if isinstance(data.get(key), str) and data[key].strip():
                return took, data[key]
    return took, json.dumps(data, ensure_ascii=False)[:2000]


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    if args[0] == "--file":
        lines = [ln.strip() for ln in Path(args[1]).read_text(encoding="utf-8").splitlines()]
        commands = [ln for ln in lines if ln and not ln.startswith("#")]
    else:
        commands = [" ".join(args)]

    for i, command in enumerate(commands, 1):
        print(f"\n{'=' * 78}\n[{i}/{len(commands)}] > {command}\n{'-' * 78}")
        took, said = ask(command)
        print(said)
        print(f"{'-' * 78}\n({took:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
