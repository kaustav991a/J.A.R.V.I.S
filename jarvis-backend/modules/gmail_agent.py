"""
Phase 6: Gmail Integration Agent
Provides email reading, summarization, and sending capabilities.
"""
import base64
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from modules.google_auth import get_google_credentials, is_google_configured


class GmailAgent:
    def __init__(self):
        self._service = None
    
    def _get_service(self):
        """Lazy-load the Gmail API service."""
        if self._service:
            return self._service
        
        creds = get_google_credentials()
        if not creds:
            return None
        
        try:
            self._service = build("gmail", "v1", credentials=creds)
            return self._service
        except Exception as e:
            print(f"[GMAIL] Failed to build service: {e}")
            return None
    
    def get_unread_summary(self, max_results: int = 5) -> str:
        """
        Returns a human-readable summary of unread emails.
        Format: "You have 5 unread emails. Top senders: Boss (Project Update), 
                 Mom (Dinner plans), GitHub (Security alert)"
        """
        service = self._get_service()
        if not service:
            return "Gmail integration is not configured yet, sir. You'll need to set up the OAuth credentials first."
        
        try:
            # Get unread messages from inbox
            results = service.users().messages().list(
                userId="me",
                labelIds=["INBOX", "UNREAD"],
                maxResults=max_results
            ).execute()
            
            messages = results.get("messages", [])
            total_unread = results.get("resultSizeEstimate", 0)
            
            if not messages:
                return "Your inbox is clear, sir. No unread messages."
            
            # Get details for each message
            email_previews = []
            for msg in messages:
                try:
                    detail = service.users().messages().get(
                        userId="me", id=msg["id"], format="metadata",
                        metadataHeaders=["From", "Subject"]
                    ).execute()
                    
                    headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                    sender = headers.get("From", "Unknown")
                    subject = headers.get("Subject", "(No subject)")
                    
                    # Clean up sender name (extract just the name, not the email)
                    if "<" in sender:
                        sender = sender.split("<")[0].strip().strip('"')
                    
                    email_previews.append({"sender": sender, "subject": subject, "id": msg["id"]})
                except Exception:
                    continue
            
            if not email_previews:
                return f"You have {total_unread} unread messages, but I couldn't retrieve the details."
            
            # Build the summary string with numbered emails for clear TTS pauses
            summary = f"You have {total_unread} unread email{'s' if total_unread != 1 else ''}. "
            for idx, e in enumerate(email_previews, 1):
                summary += f"Email {idx}, from {e['sender']}, subject: {e['subject']}. "
            
            return summary.strip()
            
        except Exception as e:
            print(f"[GMAIL] Error fetching inbox: {e}")
            return f"I encountered an error accessing your inbox: {str(e)[:80]}"
    
    def read_email(self, target: str = "latest") -> str:
        """
        Reads the full content of an email. 
        target can be "latest", "1", "2", etc. (1-indexed from most recent)
        """
        service = self._get_service()
        if not service:
            return "Gmail integration is not configured yet, sir."
        
        try:
            # Determine which email to read
            index = 0
            if target.isdigit():
                index = int(target) - 1
            
            results = service.users().messages().list(
                userId="me",
                labelIds=["INBOX"],
                maxResults=index + 1
            ).execute()
            
            messages = results.get("messages", [])
            if not messages or index >= len(messages):
                return "I couldn't find that email, sir."
            
            msg_id = messages[index]["id"]
            detail = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
            
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            sender = headers.get("From", "Unknown")
            subject = headers.get("Subject", "(No subject)")
            date = headers.get("Date", "Unknown date")
            
            # Clean sender
            if "<" in sender:
                sender = sender.split("<")[0].strip().strip('"')
            
            # Extract body
            body = self._extract_body(detail.get("payload", {}))
            
            # Truncate for LLM context
            if len(body) > 1500:
                body = body[:1500] + "... [truncated]"
            
            return f"EMAIL FROM: {sender}\nDATE: {date}\nSUBJECT: {subject}\nBODY:\n{body}"
            
        except Exception as e:
            print(f"[GMAIL] Error reading email: {e}")
            return f"I encountered an error reading that email: {str(e)[:80]}"
    
    def send_email(self, to: str, subject: str, body: str) -> str:
        """Sends an email from the authenticated account."""
        service = self._get_service()
        if not service:
            return "Gmail integration is not configured yet, sir."
        
        try:
            message = MIMEText(body)
            message["to"] = to
            message["subject"] = subject
            
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            send_body = {"raw": raw}
            
            service.users().messages().send(
                userId="me", body=send_body
            ).execute()
            
            return f"Email sent successfully to {to} with subject '{subject}'."
            
        except Exception as e:
            print(f"[GMAIL] Error sending email: {e}")
            return f"I failed to send the email: {str(e)[:80]}"
    
    def get_unread_count(self) -> int:
        """Returns just the unread count (used by frontend widget polling)."""
        service = self._get_service()
        if not service:
            return -1
        
        try:
            results = service.users().messages().list(
                userId="me",
                labelIds=["INBOX", "UNREAD"],
                maxResults=1
            ).execute()
            return results.get("resultSizeEstimate", 0)
        except Exception:
            return -1
    
    def get_inbox_preview(self, max_results: int = 5) -> list:
        """Returns structured data for the frontend widget."""
        service = self._get_service()
        if not service:
            return []
        
        try:
            results = service.users().messages().list(
                userId="me",
                labelIds=["INBOX"],
                maxResults=max_results
            ).execute()
            
            messages = results.get("messages", [])
            previews = []
            
            for msg in messages:
                try:
                    detail = service.users().messages().get(
                        userId="me", id=msg["id"], format="metadata",
                        metadataHeaders=["From", "Subject", "Date"]
                    ).execute()
                    
                    headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
                    sender = headers.get("From", "Unknown")
                    subject = headers.get("Subject", "(No subject)")
                    date = headers.get("Date", "")
                    
                    # Clean sender
                    if "<" in sender:
                        sender = sender.split("<")[0].strip().strip('"')
                    
                    # Check if unread
                    labels = detail.get("labelIds", [])
                    is_unread = "UNREAD" in labels
                    
                    previews.append({
                        "sender": sender,
                        "subject": subject[:60],
                        "date": date,
                        "unread": is_unread
                    })
                except Exception:
                    continue
            
            return previews
            
        except Exception as e:
            print(f"[GMAIL] Error getting previews: {e}")
            return []
    
    def _extract_body(self, payload: dict) -> str:
        """Recursively extracts plain text body from email payload."""
        body = ""
        
        if "body" in payload and payload["body"].get("data"):
            try:
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
            except Exception:
                pass
        
        if "parts" in payload:
            for part in payload["parts"]:
                mime_type = part.get("mimeType", "")
                if mime_type == "text/plain":
                    if part.get("body", {}).get("data"):
                        try:
                            body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                            break
                        except Exception:
                            pass
                elif mime_type.startswith("multipart/"):
                    body = self._extract_body(part)
                    if body:
                        break
        
        # Clean up
        body = body.strip()
        # Remove excessive whitespace
        import re
        body = re.sub(r'\n{3,}', '\n\n', body)
        
        return body


def is_gmail_available() -> bool:
    """Quick check for use by other modules."""
    return is_google_configured()
