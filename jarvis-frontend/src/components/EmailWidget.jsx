import React from "react";
import { Mail, MailOpen, RefreshCw } from "lucide-react";
import { useSource } from "../useSource";

const EmailWidget = () => {
  // One source, three states kept apart - see ../useSource.js. This widget
  // used to render OFFLINE while the request was still in flight, and the
  // vitals call takes about ten seconds because it reaches Google Fit.
  const { data, phase, loading, refresh: fetchEmails } =
    useSource("/api/email/summary", { initial: { configured: false, unread: 0, previews: [] } });

  if (phase !== "ready") {
    // "Offline" is a claim about the source. Only say it when the source
    // actually said so; while the request is in flight the honest word is
    // that it is being fetched, and a failure names itself.
    const label = phase === "loading" ? "GMAIL\u2026"
                : phase === "error" ? "GMAIL UNREACHABLE"
                : "GMAIL OFFLINE";
    return (
      <div className="email-widget-offline">
        <Mail size={18} color="#555" />
        <span>{label}</span>
      </div>
    );
  }

  return (
    <div className="email-widget">
      <div className="email-header">
        <div className="email-unread-badge">
          {data.unread > 0 ? (
            <><MailOpen size={14} color="#ff3366" /> <span className="unread-count">{data.unread}</span> UNREAD</>
          ) : (
            <><Mail size={14} color="#00ffcc" /> INBOX CLEAR</>
          )}
        </div>
        <button className="email-refresh-btn" onClick={fetchEmails} disabled={loading}>
          <RefreshCw size={12} className={loading ? "spin" : ""} />
        </button>
      </div>

      <div className="email-list">
        {data.previews.length === 0 && (
          <div className="email-empty">No messages to display.</div>
        )}
        {data.previews.slice(0, 4).map((email, i) => (
          <div key={i} className={`email-item ${email.unread ? "email-unread" : ""}`}>
            <div className="email-sender">{email.sender}</div>
            <div className="email-subject">{email.subject}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default EmailWidget;
