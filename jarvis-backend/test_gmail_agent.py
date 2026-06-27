"""Phase 6 — GmailAgent tests with mocked Gmail API (no real network)."""

import base64
import email
from email import policy
from unittest.mock import MagicMock, patch

import pytest

from modules.gmail_agent import GmailAgent


def _fake_credentials():
    c = MagicMock()
    return c


def _sample_message_detail(msg_id: str = "m1") -> dict:
    body_b64 = base64.urlsafe_b64encode(b"Hello from the message body.").decode()
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": 'Jane Doe <jane@example.com>'},
                {"name": "Subject", "value": "Quarterly update"},
                {"name": "Date", "value": "Mon, 1 Jan 2024 12:00:00 +0000"},
            ],
            "body": {"data": body_b64},
        },
    }


@pytest.fixture
def mock_gmail_service() -> MagicMock:
    svc = MagicMock()
    users = MagicMock()
    messages = MagicMock()
    threads = MagicMock()
    svc.users.return_value = users
    users.messages.return_value = messages
    users.threads.return_value = threads
    return svc


def test_read_emails_formats_sender_subject_snippet(mock_gmail_service: MagicMock) -> None:
    messages = mock_gmail_service.users.return_value.messages.return_value

    list_exec = MagicMock()
    list_exec.execute.return_value = {
        "messages": [{"id": "m1"}],
        "resultSizeEstimate": 1,
    }
    messages.list.return_value = list_exec

    get_exec = MagicMock()
    get_exec.execute.return_value = _sample_message_detail("m1")
    messages.get.return_value = get_exec

    with patch("modules.gmail_agent.get_google_credentials", return_value=_fake_credentials()):
        with patch("modules.gmail_agent.build", return_value=mock_gmail_service):
            agent = GmailAgent()
            out = agent.read_emails(query="is:unread", max_results=1)

    assert "Jane Doe" in out
    assert "Quarterly update" in out
    assert "Hello from the message body" in out
    messages.list.assert_called()
    messages.get.assert_called()


def test_send_email_builds_mime_multipart(mock_gmail_service: MagicMock) -> None:
    messages = mock_gmail_service.users.return_value.messages.return_value
    send_exec = MagicMock()
    send_exec.execute.return_value = {"id": "sent123"}
    messages.send.return_value = send_exec

    with patch("modules.gmail_agent.get_google_credentials", return_value=_fake_credentials()):
        with patch("modules.gmail_agent.build", return_value=mock_gmail_service):
            agent = GmailAgent()
            out = agent.send_email("bob@example.com", "Hello", "Plain body text.")

    assert "sent" in out.lower()
    assert "bob@example.com" in out

    call_kw = messages.send.call_args.kwargs["body"]
    raw_b64 = call_kw["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    mime_msg = email.message_from_bytes(raw_bytes, policy=policy.default)

    assert mime_msg["To"] == "bob@example.com"
    assert mime_msg["Subject"] == "Hello"
    assert mime_msg.is_multipart()
    payloads = [p.get_payload(decode=True) for p in mime_msg.walk() if p.get_content_type() == "text/plain"]
    assert payloads and b"Plain body text." in payloads


def test_reply_email_sets_thread_id_and_in_reply_to(mock_gmail_service: MagicMock) -> None:
    users = mock_gmail_service.users.return_value
    messages = users.messages.return_value
    threads = users.threads.return_value

    thread_exec = MagicMock()
    thread_exec.execute.return_value = {"messages": [{"id": "last-msg-id"}]}
    threads.get.return_value = thread_exec

    meta_exec = MagicMock()
    meta_exec.execute.return_value = {
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "Subject", "value": "Original"},
                {"name": "Message-ID", "value": "<abc123@mail.example.com>"},
                {"name": "References", "value": "<older@mail.example.com>"},
            ]
        }
    }
    messages.get.return_value = meta_exec

    send_exec = MagicMock()
    send_exec.execute.return_value = {"id": "reply456"}
    messages.send.return_value = send_exec

    with patch("modules.gmail_agent.get_google_credentials", return_value=_fake_credentials()):
        with patch("modules.gmail_agent.build", return_value=mock_gmail_service):
            agent = GmailAgent()
            out = agent.reply_email("thread-xyz-789", "Received and logged.")

    assert "Reply sent" in out
    send_kw = messages.send.call_args.kwargs["body"]
    assert send_kw["threadId"] == "thread-xyz-789"

    raw_bytes = base64.urlsafe_b64decode(send_kw["raw"].encode("ascii"))
    mime_msg = email.message_from_bytes(raw_bytes, policy=policy.default)
    assert mime_msg["In-Reply-To"] == "<abc123@mail.example.com>"
    assert "<abc123@mail.example.com>" in (mime_msg["References"] or "")
    assert mime_msg["To"] == "Alice <alice@example.com>"
