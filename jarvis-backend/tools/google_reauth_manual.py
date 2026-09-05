"""Re-authorise Google in two steps, with no listener to lose.

    venv\\Scripts\\python.exe tools\\google_reauth_manual.py            # step 1: get a URL
    venv\\Scripts\\python.exe tools\\google_reauth_manual.py --code ... # step 2: redeem it

WHY THIS EXISTS
---------------
`run_local_server` holds the PKCE verifier **in the running process**, so the
authorisation only completes if that exact process is still alive and still
listening when the browser redirects back. On 2026-09-05 that failed four ways in
one afternoon:

  * the process exited between opening the browser and the redirect
    (`ERR_CONNECTION_REFUSED` after he had already approved);
  * it timed out mid-consent (`WSGITimeoutError`) - signing in, choosing an
    account and reading six scopes takes longer than the default allows;
  * a redirect from an EARLIER attempt reached it first and killed it
    (`MismatchingStateError`), because the port is fixed and every old tab still
    points at it;
  * and the library serves *"The authentication flow has completed"* BEFORE
    validating, so he saw success three times while nothing was written.

Each of those cost him a full sign-in. The authorisation itself was never the
problem — the code came back correctly every time and landed on a socket that had
stopped listening.

So: the verifier is written to disk, the browser is allowed to fail at the
redirect (nothing is listening, and that is fine), and the `code` is read out of
the address bar afterwards. There is no window to miss.

The saved verifier is a one-use secret for a code that expires in minutes; it is
deleted as soon as it is redeemed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(HERE / ".env", override=True)

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402
from modules import google_auth as ga  # noqa: E402

REDIRECT = "http://localhost:8765/"
SECRET = HERE / "credentials" / "client_secret.json"
TOKEN = HERE / "credentials" / "google_token.json"
PENDING = HERE / "credentials" / ".oauth_pending.json"


def _flow() -> InstalledAppFlow:
    flow = InstalledAppFlow.from_client_secrets_file(str(SECRET), ga.SCOPES)
    flow.redirect_uri = REDIRECT
    return flow


def start() -> int:
    flow = _flow()
    url, state = flow.authorization_url(prompt="consent", access_type="offline")
    PENDING.write_text(json.dumps({
        "state": state,
        "code_verifier": getattr(flow, "code_verifier", None),
    }), encoding="utf-8")
    print("\n" + "=" * 74)
    print("1. Open this in a NEW INCOGNITO window, sign in, and approve:")
    print("=" * 74)
    print(url)
    print("=" * 74)
    print("2. The page will then fail with ERR_CONNECTION_REFUSED. That is")
    print("   EXPECTED - nothing is listening, and nothing needs to be.")
    print("3. Copy the whole address from the bar and pass its `code=` value:")
    print("      google_reauth_manual.py --code \"4/0A...\"")
    print("   (or pass the entire URL to --code; it is parsed out.)\n")
    return 0


def exchange(raw: str) -> int:
    if not PENDING.exists():
        print("No pending authorisation. Run this without --code first.")
        return 2
    saved = json.loads(PENDING.read_text(encoding="utf-8"))

    code = raw.strip()
    if "code=" in code:  # a whole URL was pasted, which is the easy mistake
        from urllib.parse import parse_qs, unquote, urlparse
        q = parse_qs(urlparse(code).query)
        code = (q.get("code") or [""])[0] or code
        code = unquote(code)

    flow = _flow()
    if saved.get("code_verifier"):
        flow.code_verifier = saved["code_verifier"]
    try:
        flow.fetch_token(code=code)
    except Exception as e:  # noqa: BLE001
        print(f"Exchange failed: {type(e).__name__}: {str(e)[:200]}")
        print("An authorisation code is single-use and expires in minutes. "
              "Start again if this one has been used or has aged out.")
        return 3

    creds = flow.credentials
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    PENDING.unlink(missing_ok=True)   # one-use secret, gone the moment it is spent
    print(f"\nToken written to {TOKEN}")
    print(f"refresh_token present: {bool(creds.refresh_token)}")
    print(f"scopes granted: {len(creds.scopes or [])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="the code= value from the redirect URL")
    args = ap.parse_args()
    return exchange(args.code) if args.code else start()


if __name__ == "__main__":
    raise SystemExit(main())
