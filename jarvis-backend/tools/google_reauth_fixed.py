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

    creds = flow.run_local_server(port=PORT, open_browser=True,
                                  authorization_prompt_message="")
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nToken written to {TOKEN}")
    print(f"refresh_token present: {bool(creds.refresh_token)}")
    print(f"scopes: {len(creds.scopes or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
