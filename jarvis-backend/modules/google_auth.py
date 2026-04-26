"""
Shared Google OAuth2 authentication for Gmail and Calendar APIs.
Handles token storage, refresh, and first-time authorization flow.
"""
import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# If you modify these scopes, delete the token file to force re-authentication
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.body.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
]

CREDENTIALS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "credentials")
TOKEN_FILE = os.path.join(CREDENTIALS_DIR, "google_token.json")
CLIENT_SECRET_FILE = os.path.join(CREDENTIALS_DIR, "client_secret.json")

# Ensure credentials directory exists
os.makedirs(CREDENTIALS_DIR, exist_ok=True)


def get_google_credentials():
    """
    Returns valid Google OAuth2 credentials.
    - If a token file exists and is valid, uses it.
    - If the token is expired, refreshes it.
    - If no token exists, returns None (user must run setup first).
    """
    creds = None
    
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"[GOOGLE AUTH] Failed to load token: {e}")
            creds = None
    
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save the refreshed token
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
            print("[GOOGLE AUTH] Token refreshed successfully.")
        except Exception as e:
            print(f"[GOOGLE AUTH] Token refresh failed: {e}")
            creds = None
    
    if not creds or not creds.valid:
        if os.path.exists(CLIENT_SECRET_FILE):
            print("[GOOGLE AUTH] No valid token found. Starting OAuth flow...")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
                with open(TOKEN_FILE, "w") as token:
                    token.write(creds.to_json())
                print("[GOOGLE AUTH] Authorization successful. Token saved.")
            except Exception as e:
                print(f"[GOOGLE AUTH] OAuth flow failed: {e}")
                return None
        else:
            print(f"[GOOGLE AUTH] No credentials found. Place your client_secret.json in: {CREDENTIALS_DIR}")
            return None
    
    return creds


def is_google_configured() -> bool:
    """Quick check if Google APIs are ready to use."""
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            if creds and (creds.valid or creds.refresh_token):
                return True
        except Exception:
            pass
    return False
