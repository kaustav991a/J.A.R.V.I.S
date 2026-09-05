"""Re-authorise Google on a FIXED port, printing the URL before it waits.

`tools/google_reauth.py` uses `run_local_server(port=0)`, which picks a random
port and opens the default browser itself. That is right at the desk and wrong
from anywhere else:

  * the URL is never printed, so it cannot be handed to anyone;
  * the port changes each run, so a page left open from an earlier attempt is
    pointing at a local server that no longer exists;
  * killing the process leaves that stale page looking live, and clicking it
    produces a bare "401. That's an error." from Google with no explanation -
    which is exactly what happened on 2026-09-05.

This variant pins the port, prints the URL first, and then blocks. The URL stays
valid as long as this process is alive, and it can be pasted into any browser ON
THIS MACHINE - the redirect goes to localhost, so it cannot be completed from a
phone.

    venv\\Scripts\\python.exe tools\\google_reauth_fixed.py

Nothing here signs in. It prints a consent link and waits for the redirect.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(HERE / ".env", override=True)

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402
from modules import google_auth as ga  # noqa: E402

PORT = 8765
SECRET = HERE / "credentials" / "client_secret.json"
TOKEN = HERE / "credentials" / "google_token.json"


def main() -> int:
    if not SECRET.exists():
        print(f"No client secret at {SECRET}")
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), ga.SCOPES)
    flow.redirect_uri = f"http://localhost:{PORT}/"
    url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    print("\n" + "=" * 74)
    print("OPEN THIS URL ON THIS MACHINE, then sign in and approve:")
    print("=" * 74)
    print(url)
    print("=" * 74)
    print(f"Waiting for the redirect on http://localhost:{PORT}/ ...")
    print("(the link dies when this process does - do not use an older one)\n",
          flush=True)

    # A stale tab must not kill the wait.
    #
    # 2026-09-05: he consented, and the redirect hit ERR_CONNECTION_REFUSED
    # because this process had already exited - on a redirect from an EARLIER
    # flow whose `state` did not match:
    #
    #     MismatchingStateError: CSRF Warning! State not equal in request and
    #     response.
    #
    # The port is fixed, so every browser tab ever opened by this tool points at
    # it. Whichever arrives first is consumed, and one stale tab was enough to
    # end the session before the real consent arrived. The rejection is correct -
    # that check is CSRF protection and must stay - but it is a reason to keep
    # waiting, not to stop.
    creds = None
    for attempt in range(1, 6):
        try:
            # `timeout_seconds` matters: without it this build of
            # google-auth-oauthlib gave up with
            #
            #     WSGITimeoutError: Timed out waiting for response from
            #     authorization server
            #
            # while he was still on the consent screen - so he approved, and the
            # redirect arrived at a socket nobody was listening on any more.
            # Signing in, picking an account and reading six scopes takes longer
            # than the default allows, and it takes longer still when the person
            # doing it is not sitting at the machine.
            creds = flow.run_local_server(port=PORT, open_browser=(attempt == 1),
                                          authorization_prompt_message="",
                                          timeout_seconds=900)
            break
        except Exception as e:  # noqa: BLE001
            text = str(e).lower()
            if "state" in text:
                print(f"  (ignored a stale redirect from an earlier attempt "
                      f"[{attempt}/5] - still waiting for THIS one)", flush=True)
                continue
            if "timed out" in text or "timeout" in text:
                print(f"  (no redirect within the window [{attempt}/5] - "
                      f"listening again; the URL above is still valid)",
                      flush=True)
                continue
            raise
    if creds is None:
        print("Gave up after five stale redirects. Close every old Google tab "
              "and run this again.")
        return 3
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nToken written to {TOKEN}")
    print(f"refresh_token present: {bool(creds.refresh_token)}")
    print(f"scopes: {len(creds.scopes or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
