"""
Phase 6: Gmail Integration Agent — Hardened v3
===============================================
Provides email reading, search, sending, and thread-reply capabilities
via the Gmail REST API and Google OAuth2.

Pre-flight design:
  - On import the module checks whether Google credentials exist.
  - A module-level _GMAIL_CONFIGURED flag is set once at load time.
  - Every public method checks this flag first and returns a graceful
    "credentials missing" string without ever touching the network.
  - Server startup is never blocked regardless of credential state.

API design:
  - Every public method returns a clean, LLM-/TTS-ready string.
  - Never raises — all exceptions are caught and converted to persona strings.
  - Each method builds its own API service (googleapiclient's httplib2
    transport is NOT thread-safe; do not share one service across threads).

New in v3:
  - MIME-type-aware _extract_body() — never leaks raw HTML into snippets
  - _clean_snippet() — strips URLs, tracking tokens, blank runs, emoji-only lines
  - _strip_html() — hardened with convert_charrefs=True
"""

import base64
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from googleapiclient.discovery import build
from modules.google_auth import (
    get_google_credentials,
    is_google_configured,
    _CLIENT_SECRET_FILE,
    _TOKEN_FILE,
    _CREDENTIALS_PRESENT,
)

# ── Pre-flight check (runs exactly once at import) ────────────────────────────

_GMAIL_CONFIGURED: bool = _CREDENTIALS_PRESENT

_cred_path_display  = str(_CLIENT_SECRET_FILE) if _CLIENT_SECRET_FILE else "NOT FOUND"
_token_path_display = str(_TOKEN_FILE) if _TOKEN_FILE else "N/A"

print(
    f"[GMAIL AGENT] Pre-flight check => configured={_GMAIL_CONFIGURED} | "
    f"credentials={_cred_path_display} | token={_token_path_display}",
    flush=True,
)

_NOT_CONFIGURED_MSG = (
    "Gmail credentials are missing, Sir. Place your credentials.json in the "
    "backend root directory and restart J.A.R.V.I.S. to authorise."
)

# Serialize service construction across threads (httplib2 safety)
_service_build_lock = threading.Lock()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_service():
    """
    Thread-safe factory: build and return a Gmail API service, or None.
    Returns None (never raises) if credentials are missing or invalid.
    """
    if not _GMAIL_CONFIGURED:
        return None
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        with _service_build_lock:
            return build("gmail", "v1", credentials=creds)
    except Exception as exc:
        print(f"[GMAIL AGENT] Failed to build service: {exc}")
        return None


def _decode_part_data(data: str) -> str:
    """Base64url-decode a Gmail payload data field to a UTF-8 string."""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    """
    Convert HTML email body to clean plain text.
    Uses Python's built-in html.parser — zero external dependencies.

    - Skips <script>, <style>, <head>, <noscript>, <meta>, <link> blocks entirely.
    - Inserts newlines at block-level tags.
    - Decodes HTML entities via convert_charrefs=True (handles &amp; &nbsp; etc.).
    - Collapses excessive whitespace and blank lines.
    """
    from html.parser import HTMLParser

    class _Extractor(HTMLParser):
        _SKIP  = {"script", "style", "head", "noscript", "meta", "link"}
        _BLOCK = {
            "p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
            "article", "section", "header", "footer", "blockquote", "pre", "table",
        }

        def __init__(self):
            # convert_charrefs=True automatically decodes &amp; &nbsp; etc.
            super().__init__(convert_charrefs=True)
            self._buf: list[str] = []
            self._skip_depth: int = 0

        def handle_starttag(self, tag, attrs):
            t = tag.lower()
            if t in self._SKIP:
                self._skip_depth += 1
            if not self._skip_depth and t in self._BLOCK:
                self._buf.append("\n")

        def handle_endtag(self, tag):
            t = tag.lower()
            if t in self._SKIP and self._skip_depth > 0:
                self._skip_depth -= 1

        def handle_data(self, data):
            if not self._skip_depth and data:
                self._buf.append(data)

        def result(self) -> str:
            return "".join(self._buf)

    try:
        ex = _Extractor()
        ex.feed(html)
        text = ex.result()
    except Exception:
        # Brute-force fallback
        text = re.sub(r"<[^>]+>", " ", html)

    # Collapse horizontal whitespace (spaces, tabs, non-breaking spaces)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    # Remove whitespace-only lines, then collapse 3+ blank lines -> one blank
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    text  = "\n".join(lines)
    text  = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body(payload: dict) -> str:
    """
    MIME-type-aware body extractor.  Never returns raw HTML tags.

    Strategy (highest priority first):
      1. text/plain  → return decoded text directly
      2. multipart/* → recurse; prefer text/plain child, fallback to text/html child
      3. text/html   → decode then strip HTML tags via _strip_html()
      4. Unknown top-level mimeType with body.data → heuristic: detect HTML vs plain
    """
    mime = payload.get("mimeType", "")

    # ── 1. Plain text leaf ────────────────────────────────────────────────────
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        return _decode_part_data(data) if data else ""

    # ── 2. HTML leaf — strip tags ─────────────────────────────────────────────
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        return _strip_html(_decode_part_data(data)) if data else ""

    # ── 3. Multipart — recurse ────────────────────────────────────────────────
    if mime.startswith("multipart/") or "parts" in payload:
        parts = payload.get("parts", [])

        # Pass 1: prefer text/plain (including nested multipart/alternative)
        for part in parts:
            child_mime = part.get("mimeType", "")
            if child_mime == "text/plain":
                result = _extract_body(part)
                if result.strip():
                    return result
            elif child_mime.startswith("multipart/"):
                result = _extract_body(part)
                if result.strip():
                    return result

        # Pass 2: fall back to text/html child
        for part in parts:
            if part.get("mimeType", "") == "text/html":
                result = _extract_body(part)
                if result.strip():
                    return result

        return ""

    # ── 4. Unknown mimeType — try top-level body.data ─────────────────────────
    data = payload.get("body", {}).get("data", "")
    if data:
        raw = _decode_part_data(data)
        # Heuristic: if it contains an HTML root tag, strip it
        if re.search(r"<\s*(html|body|p|div|span|table)\b", raw, re.I):
            return _strip_html(raw)
        return raw

    return ""


def _clean_snippet(text: str, max_chars: int = 220) -> str:
    """
    Post-process an extracted email body into a clean, TTS-ready snippet.

    Removes:
      - All URLs (https://... / http://...)
      - Lines that consist only of a URL or tracking link
      - Long base64/token strings (>40 chars, no spaces)
      - Lines that are only punctuation dashes / equals
      - Lines with no real word characters (emoji-only, punctuation-only)
      - HTML remnant lines starting with '<' and ending with '>'

    Trims to max_chars at the nearest word boundary and appends '...'
    """
    if not text or not text.strip():
        return ""

    # Remove all inline URLs first
    text = re.sub(r"https?://\S+", "", text)

    # Process line-by-line
    clean: list[str] = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        # Base64 / long token noise (no spaces, purely alnum+symbols, long)
        if len(ln) > 40 and " " not in ln and re.fullmatch(r"[A-Za-z0-9+/=_\-]{40,}", ln):
            continue
        # Punctuation-only lines (----, ====, ....)
        if re.fullmatch(r"[-=_*|.]{4,}", ln):
            continue
        # HTML remnant (<something>)
        if ln.startswith("<") and ln.endswith(">"):
            continue
        # Emoji-only / no real word characters
        if not re.search(r"[A-Za-z0-9\u0900-\u097F]", ln):
            continue
        clean.append(ln)

    text = " ".join(clean)
    # Collapse multiple spaces
    text = re.sub(r" {2,}", " ", text).strip()

    if not text:
        return ""

    # Trim to max_chars at word boundary
    if len(text) > max_chars:
        trimmed = text[:max_chars]
        last_space = trimmed.rfind(" ")
        if last_space > max_chars // 2:
            trimmed = trimmed[:last_space]
        text = trimmed.rstrip(".,;:") + "..."

    return text


def _clean_sender(raw: str) -> str:
    """
    Extract a display name from a raw From header.

    'John Doe <john@example.com>'  -> 'John Doe'
    '<noreply@example.com>'        -> 'noreply@example.com'
    'john@example.com'             -> 'john@example.com'
    '' / None                      -> 'Unknown Sender'
    """
    if not raw or not raw.strip():
        return "Unknown Sender"
    raw = raw.strip()
    if "<" in raw:
        name = raw.split("<")[0].strip().strip('"').strip("'")
        if not name:
            addr = raw.split("<")[1].rstrip(">").strip()
            return addr or "Unknown Sender"
        return name
    return raw


def _headers_dict(payload: dict) -> dict:
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


# ── GmailAgent ────────────────────────────────────────────────────────────────

class GmailAgent:
    """
    Gmail operations wrapper for J.A.R.V.I.S.

    All public methods:
      - Return str  (never raise).
      - Check _GMAIL_CONFIGURED first and return _NOT_CONFIGURED_MSG if unset.
      - Are safe to call from any thread (each builds its own service).
    """

    def __init__(self):
        self._service = None
        self._configured: bool = _GMAIL_CONFIGURED

    # ── Pre-flight guard ──────────────────────────────────────────────────────

    def _check_configured(self) -> str | None:
        """Return an error string if Gmail is not configured, else None."""
        if not self._configured:
            return _NOT_CONFIGURED_MSG
        svc = _build_service()
        if svc is None:
            return (
                "Gmail is configured but the OAuth token is invalid or expired, Sir. "
                "Please re-run the authorisation flow."
            )
        return None

    def _get_service(self):
        if self._service:
            return self._service
        self._service = _build_service()
        return self._service

    def _clean_text(self, text: str) -> str:
        """Helper to strip HTML tags and URLs, and normalize whitespace."""
        if not text:
            return ""
        # Strip HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Replace URLs with [Link]
        text = re.sub(r'https?://\S+', '[Link]', text)
        # Normalize whitespace
        return re.sub(r'\s+', ' ', text).strip()

    # ── Primary unread email fetcher ──────────────────────────────────────────

    def get_unread_emails(self, limit: int = 5) -> str:
        """
        Fetch the latest unread emails from the inbox and return a
        clean, numbered, TTS-ready summary.

        Args:
            limit: Max emails to fetch (1–20, default 5).
        """
        err = self._check_configured()
        if err:
            return err

        limit = max(1, min(limit, 20))
        service = _build_service()
        if not service:
            return "Gmail service is temporarily unavailable, Sir."

        try:
            results = service.users().messages().list(
                userId="me",
                labelIds=["INBOX", "UNREAD"],
                maxResults=limit,
            ).execute()

            messages     = results.get("messages", [])
            total_unread = results.get("resultSizeEstimate", 0)

            if not messages:
                return "Your inbox is spotless, Sir. No unread messages."

            # Parallel fetch — each worker gets a fresh service instance
            def _fetch(msg_id: str) -> dict | None:
                try:
                    svc = _build_service()
                    if not svc:
                        return None
                    return svc.users().messages().get(
                        userId="me", id=msg_id, format="full"
                    ).execute()
                except Exception:
                    return None

            with ThreadPoolExecutor(max_workers=min(len(messages), 5)) as pool:
                futures = {pool.submit(_fetch, m["id"]): m["id"] for m in messages}
                by_id   = {}
                for fut in as_completed(futures):
                    detail = fut.result()
                    if detail and detail.get("id"):
                        by_id[detail["id"]] = detail

            # Preserve Gmail list order (newest-first)
            fetched = [by_id[m["id"]] for m in messages if m["id"] in by_id]

            if not fetched:
                return (
                    f"You have {total_unread} unread messages, Sir, "
                    "but I couldn't retrieve the details right now."
                )

            count_word = "emails" if total_unread != 1 else "email"
            lines = [
                f"You have {total_unread} unread {count_word}. "
                f"Here {'are' if len(fetched) > 1 else 'is'} the top {len(fetched)}:"
            ]

            for idx, detail in enumerate(fetched, 1):
                payload = detail.get("payload", {})
                hdrs    = _headers_dict(payload)
                sender  = _clean_sender(hdrs.get("From", ""))
                subject = hdrs.get("Subject", "(No subject)")

                # Extract and clean the body snippet
                body_text = _extract_body(payload)
                snippet   = _clean_snippet(body_text, max_chars=220)

                # Final fallback: use the Gmail API's own snippet field
                # (pre-truncated by Google, but always clean plain text)
                if not snippet:
                    raw_api_snippet = detail.get("snippet", "")
                    snippet = _clean_snippet(raw_api_snippet, max_chars=200)

                snippet = self._clean_text(snippet)

                lines.append(
                    f"\n[{idx}] From: {sender} | Subject: {subject}"
                    + (f"\n    {snippet}" if snippet else "")
                )

            return "\n".join(lines)

        except Exception as exc:
            print(f"[GMAIL AGENT] get_unread_emails error: {exc}")
            return f"I encountered an error reading your inbox, Sir: {str(exc)[:100]}"

    # ── Send email ────────────────────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body: str) -> str:
        """Compose and send a plain-text email from the authenticated account."""
        err = self._check_configured()
        if err:
            return err

        to      = (to or "").strip()
        subject = (subject or "").strip()
        body    = (body or "").strip()

        if not to or "@" not in to:
            return f"'{to}' doesn't look like a valid email address, Sir."
        if not subject:
            return "A subject line is required to send an email, Sir."
        if not body:
            return "The email body is empty, Sir. Please provide a message."

        service = _build_service()
        if not service:
            return "Gmail service is temporarily unavailable, Sir."

        try:
            msg = MIMEMultipart("alternative")
            msg["To"]      = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            return f"Email sent to {to} with subject '{subject}', Sir."

        except Exception as exc:
            print(f"[GMAIL AGENT] send_email error: {exc}")
            es = str(exc).lower()
            if "invalid_grant" in es or "token" in es:
                return "Email send failed, Sir. OAuth token has expired. Please re-authorise."
            if "quota" in es or "rate" in es:
                return "Email send failed, Sir. Gmail API quota exceeded. Try again shortly."
            if "invalid" in es and "address" in es:
                return f"The email address '{to}' was rejected by Gmail, Sir."
            return f"Failed to send the email, Sir: {str(exc)[:100]}"

    # ── Reply to thread ───────────────────────────────────────────────────────

    def reply_email(self, thread_id: str, body: str) -> str:
        """Send a reply to an existing email thread with proper RFC 2822 headers."""
        err = self._check_configured()
        if err:
            return err

        thread_id = (thread_id or "").strip()
        body      = (body or "").strip()

        if not thread_id:
            return "No thread ID provided, Sir. I need to know which email to reply to."
        if not body:
            return "The reply body is empty, Sir. Please provide a message."

        service = _build_service()
        if not service:
            return "Gmail service is temporarily unavailable, Sir."

        try:
            thread = service.users().threads().get(
                userId="me", id=thread_id, format="metadata"
            ).execute()

            msgs = thread.get("messages", [])
            if not msgs:
                return "I couldn't find that email thread, Sir."

            last_hdrs        = _headers_dict(msgs[-1].get("payload", {}))
            original_from    = last_hdrs.get("From", "")
            original_subject = last_hdrs.get("Subject", "")
            message_id_hdr   = last_hdrs.get("Message-ID", "")
            references       = last_hdrs.get("References", "")

            reply_subject = (
                original_subject
                if original_subject.lower().startswith("re:")
                else f"Re: {original_subject}"
            )

            reply_msg = MIMEMultipart("alternative")
            reply_msg["To"]          = original_from
            reply_msg["Subject"]     = reply_subject
            reply_msg["In-Reply-To"] = message_id_hdr
            reply_msg["References"]  = (
                f"{references} {message_id_hdr}".strip() if references else message_id_hdr
            )
            reply_msg.attach(MIMEText(body, "plain", "utf-8"))

            raw = base64.urlsafe_b64encode(reply_msg.as_bytes()).decode()
            service.users().messages().send(
                userId="me",
                body={"raw": raw, "threadId": thread_id},
            ).execute()

            sender_name = _clean_sender(original_from)
            return f"Reply sent to {sender_name} on thread '{reply_subject}', Sir."

        except Exception as exc:
            print(f"[GMAIL AGENT] reply_email error: {exc}")
            es = str(exc).lower()
            if "not found" in es:
                return "I couldn't find that email thread, Sir. The thread ID may be incorrect."
            if "invalid_grant" in es or "token" in es:
                return "Reply failed, Sir. OAuth token has expired. Please re-authorise."
            return f"Failed to send the reply, Sir: {str(exc)[:100]}"

    # ── Read / search helpers ─────────────────────────────────────────────────

    def read_emails(self, query: str = "is:unread", max_results: int = 5) -> str:
        """Flexible query-based email fetch."""
        if query.strip() in ("is:unread", "unread", "inbox") and max_results <= 5:
            return self.get_unread_emails(limit=max_results)

        err = self._check_configured()
        if err:
            return err

        max_results = max(1, min(max_results, 20))
        service = _build_service()
        if not service:
            return "Gmail service is temporarily unavailable, Sir."

        try:
            results  = service.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute()
            messages = results.get("messages", [])
            total    = results.get("resultSizeEstimate", 0)

            if not messages:
                return f"No emails found matching '{query}', Sir."

            def _fetch(msg_id: str) -> dict | None:
                try:
                    svc = _build_service()
                    return svc.users().messages().get(
                        userId="me", id=msg_id, format="full"
                    ).execute() if svc else None
                except Exception:
                    return None

            fetched: list[dict] = []
            with ThreadPoolExecutor(max_workers=min(len(messages), 5)) as pool:
                futures = {pool.submit(_fetch, m["id"]): m["id"] for m in messages}
                for fut in as_completed(futures):
                    d = fut.result()
                    if d:
                        fetched.append(d)

            if not fetched:
                return f"Found ~{total} email(s) matching '{query}' but couldn't retrieve details, Sir."

            lines = [f"Found {total} email(s) matching '{query}'. Showing {len(fetched)}:"]
            for idx, detail in enumerate(fetched, 1):
                payload = detail.get("payload", {})
                hdrs    = _headers_dict(payload)
                sender  = _clean_sender(hdrs.get("From", ""))
                subject = hdrs.get("Subject", "(No subject)")
                body    = _extract_body(payload)
                snippet = _clean_snippet(body, max_chars=200) or _clean_snippet(detail.get("snippet", ""), max_chars=180)
                lines.append(
                    f"\n[{idx}] From: {sender} | Subject: {subject}"
                    + (f"\n    {snippet}" if snippet else "")
                )
            return "\n".join(lines)

        except Exception as exc:
            print(f"[GMAIL AGENT] read_emails error: {exc}")
            return f"I encountered an error retrieving emails, Sir: {str(exc)[:100]}"

    def get_unread_summary(self, max_results: int = 5) -> str:
        """Legacy alias — backward compat."""
        return self.get_unread_emails(limit=max_results)

    def read_email(self, target: str = "latest") -> str:
        """Read a single email by index ('latest', '1', '2', ...)."""
        err = self._check_configured()
        if err:
            return err

        service = _build_service()
        if not service:
            return "Gmail service is temporarily unavailable, Sir."

        try:
            index   = int(target) - 1 if (target or "").isdigit() else 0
            results = service.users().messages().list(
                userId="me", labelIds=["INBOX"], maxResults=index + 1
            ).execute()

            messages = results.get("messages", [])
            if not messages or index >= len(messages):
                return "I couldn't find that email, Sir."

            detail  = service.users().messages().get(
                userId="me", id=messages[index]["id"], format="full"
            ).execute()
            payload = detail.get("payload", {})
            hdrs    = _headers_dict(payload)
            sender  = _clean_sender(hdrs.get("From", ""))
            subject = hdrs.get("Subject", "(No subject)")
            date    = hdrs.get("Date", "Unknown date")
            body    = _extract_body(payload)

            if len(body) > 1500:
                body = body[:1500] + "... [truncated]"

            return f"EMAIL FROM: {sender}\nDATE: {date}\nSUBJECT: {subject}\nBODY:\n{body}"

        except Exception as exc:
            print(f"[GMAIL AGENT] read_email error: {exc}")
            return f"I encountered an error reading that email, Sir: {str(exc)[:80]}"

    def search_email(self, query: str) -> str:
        """Search by keyword and read the top result."""
        err = self._check_configured()
        if err:
            return err

        service = _build_service()
        if not service:
            return "Gmail service is temporarily unavailable, Sir."

        try:
            results  = service.users().messages().list(
                userId="me", q=query, maxResults=1
            ).execute()
            messages = results.get("messages", [])
            if not messages:
                return f"I couldn't find any emails matching '{query}', Sir."

            detail  = service.users().messages().get(
                userId="me", id=messages[0]["id"], format="full"
            ).execute()
            payload = detail.get("payload", {})
            hdrs    = _headers_dict(payload)
            sender  = _clean_sender(hdrs.get("From", ""))
            subject = hdrs.get("Subject", "(No subject)")
            date    = hdrs.get("Date", "Unknown date")
            body    = _extract_body(payload)

            if len(body) > 1500:
                body = body[:1500] + "... [truncated]"

            return f"EMAIL FROM: {sender}\nDATE: {date}\nSUBJECT: {subject}\nBODY:\n{body}"

        except Exception as exc:
            print(f"[GMAIL AGENT] search_email error: {exc}")
            return f"I encountered an error searching for that email, Sir: {str(exc)[:80]}"

    def get_unread_count(self) -> int:
        """Return unread inbox count (-1 if unavailable)."""
        if not self._configured:
            return -1
        service = _build_service()
        if not service:
            return -1
        try:
            results = service.users().messages().list(
                userId="me", labelIds=["INBOX", "UNREAD"], maxResults=1
            ).execute()
            return results.get("resultSizeEstimate", 0)
        except Exception:
            return -1

    def get_inbox_preview(self, max_results: int = 5) -> list:
        """Return structured preview list for the frontend widget."""
        if not self._configured:
            return []
        service = _build_service()
        if not service:
            return []

        try:
            results = service.users().messages().list(
                userId="me", labelIds=["INBOX"], maxResults=max_results
            ).execute()
            previews = []
            for msg in results.get("messages", []):
                try:
                    detail = service.users().messages().get(
                        userId="me", id=msg["id"], format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    ).execute()
                    hdrs   = _headers_dict(detail.get("payload", {}))
                    previews.append({
                        "sender":  _clean_sender(hdrs.get("From", "")),
                        "subject": hdrs.get("Subject", "(No subject)")[:60],
                        "date":    hdrs.get("Date", ""),
                        "unread":  "UNREAD" in detail.get("labelIds", []),
                    })
                except Exception:
                    continue
            return previews

        except Exception as exc:
            print(f"[GMAIL AGENT] get_inbox_preview error: {exc}")
            return []

    def _extract_body(self, payload: dict) -> str:
        """Backward-compat delegate."""
        return _extract_body(payload)


# ── Module-level convenience ──────────────────────────────────────────────────

def is_gmail_available() -> bool:
    """Quick check — safe to call at any time."""
    return is_google_configured()
