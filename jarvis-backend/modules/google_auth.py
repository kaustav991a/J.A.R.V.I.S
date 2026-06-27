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


def get_google_credentials() -> Credentials | None:
    """
    Return valid Google OAuth2 credentials, or None on any failure.
    Never raises — all exceptions produce a console warning and None return.

    Flow:
      1. Load existing token.json  → validate → return if valid.
      2. If expired + has refresh_token → refresh → save → return.
      3. If no valid token but client_secret.json present → run browser OAuth flow.
      4. If no client_secret.json at all → return None (pre-flight check failed).
    """
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
                    print(f"[GOOGLE AUTH] Token refresh failed: {exc}")
                    creds = None

    # ── Step 3: full OAuth flow if still no valid creds ───────────────────────
    if not creds or not creds.valid:
        print("[GOOGLE AUTH] No valid token — launching browser OAuth flow...")
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CLIENT_SECRET_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0, open_browser=True)
            _save_token(creds)
            print("[GOOGLE AUTH] Authorisation successful. Token saved.")
        except Exception as exc:
            print(f"[GOOGLE AUTH] OAuth flow failed: {exc}")
            return None

    return creds if (creds and creds.valid) else None


def _save_token(creds: Credentials) -> None:
    """Persist token to disk. Silently ignores write errors."""
    try:
        _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    except Exception as exc:
        print(f"[GOOGLE AUTH] Could not save token: {exc}")
