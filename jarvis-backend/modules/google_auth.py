"""
Shared Google OAuth2 Authentication — Phase 6 Hardened
=======================================================
Handles token storage, refresh, and first-time authorisation for all
Google API integrations (Gmail, Calendar, Fitness/Health).

Credential resolution order (first found wins):
  1. JARVIS_GOOGLE_CREDENTIALS env var (absolute path to credentials JSON)
  2. <backend_root>/credentials.json          (root — Google Cloud Console default)
  3. <backend_root>/credentials/client_secret.json  (legacy JARVIS convention)

Token storage:
  - Always saved alongside the credential file that was found, as token.json
    (or google_token.json for the legacy subdirectory path).

Scopes:
  - gmail.modify   : read + archive + send (replaces readonly+send pair)
  - calendar.readonly / calendar.events
  - fitness.*

Pre-flight flag:
  - is_google_configured() returns False without raising if nothing is wired up.
  - get_google_credentials() returns None (never raises) so callers can
    gracefully degrade to "Gmail not configured, Sir." strings.
"""

import os
import json
import threading
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# ── Scopes ────────────────────────────────────────────────────────────────────
# gmail.modify is a superset of gmail.readonly + gmail.send.
# Changing scopes requires deleting the existing token file to force re-auth.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.body.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
]

# ── Path resolution ───────────────────────────────────────────────────────────

_BACKEND_ROOT = Path(__file__).resolve().parent.parent   # jarvis-backend/

def _resolve_credential_paths() -> tuple[Path | None, Path]:
    """
    Locate the client credential JSON and derive the token path.

    Returns:
        (client_secret_path, token_path)
        client_secret_path is None if no credential file is found anywhere.
    """
    # 1. Explicit env override
    env_path = os.getenv("JARVIS_GOOGLE_CREDENTIALS", "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p, p.parent / "token.json"
        print(f"[GOOGLE AUTH] JARVIS_GOOGLE_CREDENTIALS set but file not found: {p}")

    # 2. Root-level credentials.json (Google Cloud Console default export name)
    root_creds = _BACKEND_ROOT / "credentials.json"
    if root_creds.is_file():
        return root_creds, _BACKEND_ROOT / "token.json"

    # 3. Legacy JARVIS credentials subdirectory
    legacy_secret = _BACKEND_ROOT / "credentials" / "client_secret.json"
    legacy_token  = _BACKEND_ROOT / "credentials" / "google_token.json"
    if legacy_secret.is_file():
        return legacy_secret, legacy_token

    return None, _BACKEND_ROOT / "token.json"   # token path doesn't matter if no creds

# ── Thread safety for token refresh ──────────────────────────────────────────
_refresh_lock = threading.Lock()

# ── Pre-flight state (set once at module load) ────────────────────────────────
_CLIENT_SECRET_FILE, _TOKEN_FILE = _resolve_credential_paths()
_CREDENTIALS_PRESENT: bool = _CLIENT_SECRET_FILE is not None

# Ensure token directory exists (silently — no crash if it can't be created)
if _CREDENTIALS_PRESENT and _TOKEN_FILE:
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# ── Public API ────────────────────────────────────────────────────────────────

def is_google_configured() -> bool:
    """
    Pre-flight check: returns True only if Google credentials are present
    AND we have a valid / refreshable token.
    Does NOT raise. Safe to call at module import time or in health checks.
    """
    if not _CREDENTIALS_PRESENT:
        return False
    if not _TOKEN_FILE or not _TOKEN_FILE.is_file():
        return False
    try:
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)
        return bool(creds and (creds.valid or creds.refresh_token))
    except Exception:
        return False


# Set the moment a token is found dead with no way to refresh it, so every
# surface can say the true thing — "Google needs re-authorising" — instead of
# reporting an empty calendar or no vitals, which is the same sentence a genuinely
# free day produces.
_needs_reauth = False


def needs_reauth() -> bool:
    """True when a lookup failed for want of authorisation, not for want of data."""
    return _needs_reauth


def unauthorised_reply(what: str) -> str:
    """What to say when Google is unauthorised, rather than answering emptily.

    The distinction this exists to keep is the whole of habit 1: **"your calendar
    is clear today" and "I could not read your calendar" are different
    sentences**, and only one of them is true when a token has expired. An empty
    read reported as an empty day is a claim about the world made from an absence
    of information - the same shape as K3's "you never told me that" after a
    locked key store, which the gate marks 🛑 STOP.

    Also names the fix, because he is the only one who can apply it and a
    sentence that leaves him guessing costs another day of empty answers.
    """
    return (f"I can't read {what}, Sir — my Google authorisation has expired. "
            f"That is a gap in what I can see, not an empty result. "
            f"Re-authorise with tools/google_reauth.py and I'll have it back.")


def get_google_credentials(interactive: bool = False) -> Credentials | None:
    """
    Return valid Google OAuth2 credentials, or None on any failure.
    Never raises — all exceptions produce a console warning and None return.

    Flow:
      1. Load existing token.json  → validate → return if valid.
      2. If expired + has refresh_token → refresh → save → return.
      3. If still invalid: **None**, unless `interactive=True`.
      4. If no client_secret.json at all → return None (pre-flight check failed).

    **`interactive` defaults to False, and that default is load-bearing.**
    Measured on 2026-08-29, on the desk, with an `invalid_grant: Token has been
    expired or revoked`: one `GET /api/health/summary` reached
    `is_health_available()`, which reached this function, which called
    `flow.run_local_server()` — a blocking `socketserver.handle_request()` waiting
    for a browser redirect **on the event loop, inside the request handler**. The
    whole desk API stopped answering. Not the health route: everything. `/docs`,
    the HUD, the phone, ninety seconds each and no reply, with `Application
    startup complete` in the log and the process idle at 0% CPU. A `py-spy dump`
    is what found it, and nothing in the log ever said what had happened.

    An expired token is an ordinary event. It must degrade to "I cannot reach
    your calendar", never to a server that has silently stopped being a server.
    Re-authorising is a deliberate act at a real keyboard: `tools/google_reauth.py`.
    """
    global _needs_reauth
    if not _CREDENTIALS_PRESENT:
        print(
            "[GOOGLE AUTH] No credential file found. Place credentials.json in the "
            f"backend root ({_BACKEND_ROOT}) or set JARVIS_GOOGLE_CREDENTIALS in .env"
        )
        return None

    creds: Credentials | None = None

    # ── Step 1: load existing token ───────────────────────────────────────────
    if _TOKEN_FILE and _TOKEN_FILE.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)
        except Exception as exc:
            print(f"[GOOGLE AUTH] Token load failed (will re-auth): {exc}")
            creds = None

    # ── Step 2: refresh if expired ────────────────────────────────────────────
    if creds and creds.expired and creds.refresh_token:
        with _refresh_lock:
            # Double-check inside lock — another thread may have refreshed already
            if creds.expired:
                try:
                    creds.refresh(Request())
                    _save_token(creds)
                    print("[GOOGLE AUTH] Token refreshed successfully.")
                except Exception as exc:
                    # `invalid_grant` is the ordinary end of a refresh token's
                    # life (revoked, or 6 months unused on a test-mode project).
                    # It is not a transient error and retrying will not fix it.
                    print(f"[GOOGLE AUTH] Token refresh failed: {exc}")
                    creds = None

    # ── Step 3: no valid creds ────────────────────────────────────────────────
    if not creds or not creds.valid:
        if not interactive:
            _needs_reauth = True
            # Loud, and it names the fix. A silent None here is how "no events
            # today" came to mean two different things.
            print("[GOOGLE AUTH] ⛔ No valid token and no refresh — Google is "
                  "UNAUTHORISED. Calendar, Gmail and Fitness will report that "
                  "they cannot reach Google rather than answering emptily. "
                  "Re-authorise at a real keyboard: "
                  "venv\\Scripts\\python.exe tools\\google_reauth.py", flush=True)
            return None
        print("[GOOGLE AUTH] No valid token — launching browser OAuth flow...")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CLIENT_SECRET_FILE), SCOPES
            )
            # A timeout even here. `run_local_server` blocks forever by default,
            # and "forever" on a machine nobody is sitting at is a hang rather
            # than a prompt. Passed defensively: older google-auth-oauthlib does
            # not take the argument, and losing the timeout is better than losing
            # the ability to re-authorise at all.
            try:
                creds = flow.run_local_server(port=0, open_browser=True,
                                              timeout_seconds=300)
            except TypeError:
                creds = flow.run_local_server(port=0, open_browser=True)
            _save_token(creds)
            _needs_reauth = False
            print("[GOOGLE AUTH] Authorisation successful. Token saved.")
        except Exception as exc:
            print(f"[GOOGLE AUTH] OAuth flow failed: {exc}")
            return None

    if creds and creds.valid:
        _needs_reauth = False
        return creds
    _needs_reauth = True
    return None


def _save_token(creds: Credentials) -> None:
    """Persist token to disk. Silently ignores write errors."""
    try:
        _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    except Exception as exc:
        print(f"[GOOGLE AUTH] Could not save token: {exc}")
