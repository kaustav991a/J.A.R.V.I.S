"""Drive the A17 resilience rows and check them against what the machine did.

Goal 2 is "he is up before you are, and stays up", and its rows are about a desk
that keeps working when something underneath it stops. That makes them different
from A11's: there is no external figure to compare against, so the question is
whether the desk **degrades** rather than lies, crashes, or hangs.

The habit from goal 1 holds anyway, and is why this file exists before the rows
were run: **read what the machine did, not what the answer sounds like.** Every
check here reads the log, the process table, or an HTTP response - never a
sentence's tone.

    venv\\Scripts\\python.exe tools\\verify_a17.py            # 18.1, 18.2, 18.4
    venv\\Scripts\\python.exe tools\\verify_a17.py --row 18.1

18.3, 18.6 and 18.7 are browser rows and are driven separately; 18.5 needs the
gesture daemon, which needs a camera. Rows this tool cannot honestly drive are
reported as SKIP with the reason, never as a pass.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_a11 import (  # noqa: E402  - one source of truth for log reading
    _lines, ask, clear_pending, newest_log, numbers_in,
)

HEALTH = "http://127.0.0.1:8000/api/health/summary"
TOKEN = HERE / "credentials" / "google_token.json"
PARKED = HERE / "credentials" / "google_token.json.a17-parked"

# Words that mean "I could not reach it", which is the honest thing to say when a
# source is down. A briefing that says one of these while its sources are offline
# is degrading; one that quotes figures instead is inventing them.
_ADMITS = ("couldn't", "could not", "unable", "not available", "unavailable",
           "no data", "don't have", "do not have", "offline", "re-authorise",
           "reauthorise", "authorisation", "authorization", "not reachable",
           "trouble reaching", "isn't available", "cannot", "no access")

# A crash leaves these in the log. 18.1's pass condition names NameError because
# that is what it was written for - an f-string referencing a variable that only
# exists on the success path - but any traceback is the same failure.
_CRASH = ("Traceback (most recent call last)", "NameError", "UnboundLocalError",
          "AttributeError: 'NoneType'")


def _restart_child(timeout: float = 420.0) -> bool:
    """Kill the uvicorn child and wait for the watchdog to bring it back.

    Needed because **parking the token file does not take a source offline.**
    `_get_service()` caches its Google service on first call, so a desk that has
    already answered one question holds a live credential in memory and keeps
    answering from it after the file is gone.

    That is not a hypothesis. The first run of 18.1 parked the token and read:

        [GOOGLE AUTH] Google is UNAUTHORISED. Calendar, Gmail and Fitness will
                      report that they cannot reach Google...
        [JARVIS] You've logged a modest 799 steps, burned 803.9 kcal...
        [JARVIS] Your calendar is delightfully empty today...
        [JARVIS] Regrettably my Google token has expired, so I cannot fetch
                 your inbox...

    which looks exactly like two sources inventing data while a third is honest.
    Checked against the sources, **799 steps, 803.9 kcal and an empty calendar
    were all true** - Gmail's service happened to need rebuilding and the other
    two did not. The desk was fine; the TEST was wrong, and it would have been
    filed as a finding by anyone reading the transcript.

    So the condition is created cold: no process, no cache, no token.
    """
    import subprocess
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*uvicorn*' -or "
         "$_.CommandLine -like '*main:app*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        capture_output=True, timeout=90)
    time.sleep(4)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_latency()[1] == 200:
            return True
        time.sleep(5)
    return False


def _crashes_since(log: Path, line_no: int) -> list[str]:
    return [ln.strip() for ln in _lines(log)[line_no:]
            if any(sign in ln for sign in _CRASH)]


def _health_latency() -> tuple[float, int]:
    """Round-trip to a trivial endpoint. This is the F-71 detector.

    When an expired token put a blocking OAuth flow on the event loop, the desk
    was not slow - it was *stopped*, at 0% CPU, with a silent log. Nothing in any
    answer showed it; a cheap endpoint timed from outside is what did.
    """
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(HEALTH, timeout=30) as r:  # noqa: S310
            r.read()
            return time.monotonic() - t0, r.status
    except Exception:  # noqa: BLE001
        return time.monotonic() - t0, 0


# =========================================================================
# 18.1 - the briefing with its sources pulled out from under it
# =========================================================================

def row_18_1(log: Path) -> tuple[str, str]:
    """Take Gmail, Calendar and Health offline for real, then ask for a briefing.

    Not a mock: the Google token is moved aside, so all three agents genuinely
    cannot authenticate - the same condition an expired token creates, and the
    one that produced F-71. The token is restored in a `finally`, because a
    verifier that can leave the desk unauthenticated is worse than no verifier.
    """
    if not TOKEN.exists():
        return "SKIP", "no google_token.json to park - cannot create the condition"
    shutil.move(str(TOKEN), str(PARKED))
    try:
        if not _restart_child():
            return "FAIL", "the desk did not come back after the restart"
        before = len(_lines(log))
        said = ask("give me my morning briefing", log)
    finally:
        shutil.move(str(PARKED), str(TOKEN))
        _restart_child()  # leave it authenticated, whatever happened above

    joined = " ".join(said)
    low = joined.lower()
    crashes = _crashes_since(log, before)
    if crashes:
        return "FAIL", f"crashed with its sources down: {crashes[0][:90]}"
    if not joined.strip() or joined.startswith("<<no line"):
        return "FAIL", f"said nothing with its sources down: {joined[:70]}"
    # The goal-1 overlap, and the thing that actually matters: with every source
    # offline it must not produce figures. A number here is invented.
    figures = [n for n in numbers_in(joined) if n > 12]  # clock times are fine
    if figures:
        return "FAIL", (f"quoted {figures[:3]} with every source offline - "
                        f"that is invention, not degradation")
    if not any(w in low for w in _ADMITS):
        return "FAIL", f"did not say it could not reach anything: {joined[:100]!r}"
    return "PASS", f"degraded and said so, no figures, no traceback: {joined[:80]!r}"


# =========================================================================
# 18.2 - does a long action block the desk?
# =========================================================================

def _ws_ping_latencies(stop: threading.Event, out: list[float],
                       errs: list[str]) -> None:
    """Ping the HUD's WebSocket and time the pong. This is the real probe.

    Two wrong probes were tried before this one, and both would have filed a
    finding against a working desk:

    * **`/api/health/summary`** looked trivial and is not - it calls Google Fit
      over the network. A 27.5-second "stall" it reported was a slow Google
      round-trip, nothing to do with the desk. That measurement is what made
      18.2 fail on its first run.
    * **`/health`** really is cheap, but it is declared `def`, not `async def`,
      so FastAPI runs it in a threadpool. It keeps answering *even when the event
      loop is completely blocked*, which makes it useless for this row.

    A WebSocket ping is answered by the protocol machinery **on the event loop
    itself**, so the pong's delay is the loop's delay - and the HUD is fed by
    that same loop, which is what "the UI stays responsive" actually means.
    """
    import asyncio as _a
    try:
        import websockets
    except ImportError:
        errs.append("websockets not installed - cannot measure the loop")
        return

    async def run() -> None:
        try:
            async with websockets.connect("ws://127.0.0.1:8000/ws",
                                          open_timeout=30) as ws:
                while not stop.is_set():
                    t0 = time.monotonic()
                    try:
                        await _a.wait_for(await ws.ping(), timeout=60)
                        out.append(time.monotonic() - t0)
                    except Exception as e:  # noqa: BLE001
                        errs.append(f"{type(e).__name__} after "
                                    f"{time.monotonic() - t0:.1f}s")
                    await _a.sleep(1.0)
        except Exception as e:  # noqa: BLE001
            errs.append(f"connect: {type(e).__name__} {e}")

    _a.run(run())


def row_18_2(log: Path) -> tuple[str, str]:
    """Run a genuinely slow action and measure the event loop throughout.

    "TTS + UI stay responsive" is audible to a person; what is measurable from
    here is whether the loop that feeds the HUD keeps turning while a long action
    runs. That is the half F-71 broke, and the half a script can prove.
    """
    lat: list[float] = []
    errs: list[str] = []
    stop = threading.Event()
    t = threading.Thread(target=_ws_ping_latencies, args=(stop, lat, errs),
                         daemon=True)
    t.start()
    time.sleep(3)  # let the socket connect before the action starts
    try:
        said = ask("read my screen and tell me what is on it", log)
    finally:
        stop.set()
        t.join(timeout=20)

    if errs and not lat:
        return "FAIL", f"could not measure the loop: {errs[0]}"
    if not lat:
        return "FAIL", "no WebSocket pings completed"
    worst = max(lat)
    if worst > 5.0:
        return "FAIL", (f"the event loop stalled {worst:.1f}s during a long "
                        f"action ({len(lat)} pings)")
    return "PASS", (f"the loop never stalled: {len(lat)} WebSocket pings, worst "
                    f"{worst:.2f}s, and it answered {said[0][:45]!r}")


# =========================================================================
# 18.4 - a structured result must be spoken, not dumped
# =========================================================================

_JSON_SIGNS = ('{"', '":', "':", "[{", "}]", "resultSizeEstimate",
               "'id':", '"id":', "threadId", "snippet")


def row_18_4(log: Path) -> tuple[str, str]:
    """Ask for something whose source is structured, and read what was SPOKEN.

    Email comes back as a list of dicts. The row is about whether that reaches
    him as a sentence or as the raw object, so the check is for JSON punctuation
    in the spoken line - mechanical, unlike "is this a good summary".
    """
    clear_pending()
    said = ask("check my email", log)
    joined = " ".join(said)
    if not joined.strip() or joined.startswith("<<no line"):
        return "FAIL", f"nothing spoken: {joined[:70]}"
    leaked = [s for s in _JSON_SIGNS if s in joined]
    if leaked:
        return "FAIL", f"spoke raw structure {leaked[:3]}: {joined[:90]!r}"
    if len(joined) > 1200:
        return "FAIL", f"dumped {len(joined)} characters rather than summarising"
    return "PASS", f"spoken as prose, {len(joined)} chars: {joined[:80]!r}"


ROWS = {
    "18.1": ("briefing with Gmail/Calendar/Health offline", row_18_1),
    "18.2": ("long action - does the loop keep serving?", row_18_2),
    "18.4": ("structured result spoken as a summary", row_18_4),
}

BROWSER = {
    "18.3": "kill the backend with the HUD open - reconnect on backoff",
    "18.6": "drag a widget, shrink the window - nothing strands",
    "18.7": "every widget loads from the VITE_API_BASE host",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--row", action="append", help="run only these rows")
    args = ap.parse_args()

    log = newest_log()
    print(f"log: {log.name}\n{'=' * 74}")
    wanted = args.row or list(ROWS)
    results = []
    for row in wanted:
        if row not in ROWS:
            print(f"[{row}] not driven here - see the BROWSER/SKIP notes")
            continue
        label, fn = ROWS[row]
        print(f"\n[{row}] {label}")
        latency, status = _health_latency()
        if status != 200:
            print(f"    !! desk not answering before the row ({latency:.1f}s)")
            return 2
        verdict, why = fn(log)
        print(f"    -> {verdict}: {why}")
        results.append((row, verdict, why))

    print(f"\n{'=' * 74}\nSUMMARY")
    for row, verdict, why in results:
        print(f"  {row:5} {verdict:7} {why}")
    for row, why in BROWSER.items():
        print(f"  {row:5} BROWSER {why}")
    print("  18.5  SKIP    needs the gesture daemon, which needs a camera")
    failed = [r for r, v, _ in results if v == "FAIL"]
    print(f"\n{len([r for r, v, _ in results if v == 'PASS'])} verified, "
          f"{len(failed)} FAILED" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
