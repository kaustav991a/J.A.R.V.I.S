"""Re-authorise Google, deliberately, at a keyboard you are typing at.

WHY THIS IS A SEPARATE SCRIPT
-----------------------------
Because the alternative was measured, on the desk, on 2026-08-29. The stored
token came back `invalid_grant: Token has been expired or revoked`, and one
routine `GET /api/health/summary` reached `get_google_credentials()`, which
called `flow.run_local_server()` — a blocking wait for a browser redirect, **on
the event loop, inside the request handler**. The whole desk API stopped
answering: not the health route, everything. `/docs` timed out at ninety seconds
with `Application startup complete` in the log and the process idle at 0% CPU.

So the browser flow is no longer reachable from any request, any background loop,
or any answer JARVIS gives. It lives here, where a human is present by
construction, and `get_google_credentials()` returns None everywhere else with a
line in the log naming this file.

WHAT IT DOES
------------
Opens the consent screen for exactly the scopes in `modules/google_auth.SCOPES`,
writes the token where the rest of the backend reads it, and prints what was
granted. It times out rather than waiting forever.

    venv\\Scripts\\python.exe tools\\google_reauth.py

Run it in a **real terminal**. A tool-driven shell has the null device on stdin
and no browser session to hand — the same reason `manage_keys.py` says so.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from modules import google_auth  # noqa: E402


def main() -> int:
    print("[REAUTH] scopes requested:")
    for scope in google_auth.SCOPES:
        print(f"          {scope}")
    if not google_auth._CREDENTIALS_PRESENT:
        print("[REAUTH] ⛔ no client secret file. Place credentials.json in the "
              "backend root, or set JARVIS_GOOGLE_CREDENTIALS in .env.")
        return 2

    print("[REAUTH] a browser window will open. Approve there, and come back.")
    creds = google_auth.get_google_credentials(interactive=True)
    if creds is None or not creds.valid:
        print("[REAUTH] ⛔ still unauthorised. Nothing was written.")
        return 1

    granted = list(getattr(creds, "scopes", None) or [])
    missing = [s for s in google_auth.SCOPES if s not in granted]
    print(f"[REAUTH] ✅ token saved to {google_auth._TOKEN_FILE}")
    print(f"[REAUTH] granted {len(granted)} scope(s).")
    if missing:
        # Named rather than assumed: a token that is valid but short a scope
        # makes ONE feature answer emptily while everything else works, which is
        # the hardest shape of this to diagnose from a chat window.
        print("[REAUTH] ⚠ granted, but these were NOT included — the features "
              "behind them will report that they cannot reach Google:")
        for scope in missing:
            print(f"          {scope}")
    print("[REAUTH] restart the desk so the cached services pick it up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
