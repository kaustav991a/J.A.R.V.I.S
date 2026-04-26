import React, { useState, useEffect } from "react";
import { Mail, MailOpen, RefreshCw } from "lucide-react";

const EmailWidget = () => {
  const [data, setData] = useState({ configured: false, unread: 0, previews: [] });
  const [loading, setLoading] = useState(false);

  const fetchEmails = async () => {
    setLoading(true);
    try {
      const res = await fetch("http://127.0.0.1:8000/api/email/summary");
      if (res.ok) setData(await res.json());
    } catch (e) { /* silent */ }
    setLoading(false);
  };

  useEffect(() => {
    fetchEmails();
    const timer = setInterval(fetchEmails, 60000);
    return () => clearInterval(timer);
  }, []);

  if (!data.configured) {
    return (
      <div className="email-widget-offline">
        <Mail size={18} color="#555" />
        <span>GMAIL OFFLINE</span>
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
