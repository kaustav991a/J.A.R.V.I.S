import React, { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import "./ChatPanel.scss";

const HUD_EASE = [0.16, 1, 0.3, 1];

/**
 * COMM TRANSCRIPT — live conversation panel.
 * Shows what the user said (gold, right) and what J.A.R.V.I.S replied (cyan, left).
 * Default hidden; toggled by command (status: "toggle_chat") or the CHAT button.
 */
export default function ChatPanel({ open, messages = [], onClose }) {
  const bodyRef = useRef(null);

  // Auto-scroll to the newest message whenever the log grows or opens.
  useEffect(() => {
    if (open && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [messages, open]);

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          key="jarvis-chat-panel"
          className="chat-panel"
          initial={{ opacity: 0, x: 40 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 40 }}
          transition={{ duration: 0.45, ease: HUD_EASE }}
        >
          <div className="chat-panel__header">
            <span className="chat-panel__title">
              <span className="chat-panel__dot" aria-hidden />
              COMM TRANSCRIPT
            </span>
            <button
              type="button"
              className="chat-panel__close"
              onClick={onClose}
              title="Hide transcript"
              aria-label="Hide transcript"
            >
              ✕
            </button>
          </div>

          <div className="chat-panel__body" ref={bodyRef}>
            {messages.length === 0 ? (
              <div className="chat-panel__empty">Awaiting conversation…</div>
            ) : (
              messages.map((m) => {
                const isUser = m.speaker === "USER";
                return (
                  <div
                    key={m.id}
                    className={`chat-msg ${isUser ? "chat-msg--user" : "chat-msg--jarvis"}`}
                  >
                    <span className="chat-msg__tag">{isUser ? "YOU" : "J.A.R.V.I.S"}</span>
                    <p className="chat-msg__text">{m.text}</p>
                  </div>
                );
              })
            )}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
