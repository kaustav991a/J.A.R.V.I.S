import React, { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Brain, Wrench, Check, AlertTriangle, Ban, ShieldQuestion, Flag, Wand2,
  Scissors, Smartphone,
} from "lucide-react";
import "./AgentTrace.scss";
import { API_BASE } from "../api";

const HUD_EASE = [0.16, 1, 0.3, 1];

// One row per agent_step frame. The backend emits a ready-made `message` for
// each event (agent_runner.make_narrator), so this component decides how a step
// LOOKS, never what it says — the trace can't drift from what actually ran.
const STYLES = {
  model_turn: { Icon: Brain, tone: "think", label: "THINKING" },
  tool_start: { Icon: Wrench, tone: "act", label: "TOOL" },
  tool_ok: { Icon: Check, tone: "ok", label: "RESULT" },
  tool_error: { Icon: AlertTriangle, tone: "warn", label: "FAILED" },
  denied: { Icon: Ban, tone: "danger", label: "REFUSED" },
  repair: { Icon: Wand2, tone: "warn", label: "CORRECTING" },
  cap: { Icon: Flag, tone: "danger", label: "STOPPED" },
  provider_failed: { Icon: AlertTriangle, tone: "danger", label: "OFFLINE" },
  answer: { Icon: Check, tone: "done", label: "ANSWER" },
  // Phase 5.
  compacted: { Icon: Scissors, tone: "think", label: "TRIMMED" },
  parked: { Icon: Smartphone, tone: "warn", label: "PARKED" },
};

// A sub-agent's events arrive as `sub:tool_start` etc. They get the same icon and
// label as the parent's, plus an indent — a delegation is work happening inside
// one parent step, not another step of the main run.
function styleFor(event) {
  const sub = typeof event === "string" && event.startsWith("sub:");
  const style = STYLES[sub ? event.slice(4) : event] || STYLES.model_turn;
  return { ...style, sub };
}

/**
 * Live ReAct trace + the approve/deny prompt.
 *
 * `steps`   — agent_step frames in arrival order (App.jsx appends them).
 * `confirm` — the open agent_confirm frame, or null.
 *
 * Answering POSTs to /api/agent/confirm because the backend reads no
 * client->server WebSocket frames (the voice loop owns that handler), which is
 * the same reason click-to-talk is a POST.
 */
export default function AgentTrace({ steps = [], confirm = null, onAnswered }) {
  const listRef = useRef(null);
  const busy = useRef(false);

  // Follow the trace as it grows — this is meant to be watched, not scrolled.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [steps.length, confirm]);

  const answer = async (approved) => {
    if (!confirm || busy.current) return;
    busy.current = true;
    try {
      await fetch(`${API_BASE}/api/agent/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirmation_id: confirm.confirmation_id,
          approved,
        }),
      });
    } catch {
      /* the prompt times out on its own as a refusal — never as an approval */
    } finally {
      busy.current = false;
      onAnswered?.(approved);
    }
  };

  // Keyboard: Y/N while a prompt is open, so an approval is one keystroke.
  useEffect(() => {
    if (!confirm) return undefined;
    const onKey = (e) => {
      const k = e.key.toLowerCase();
      if (k === "y") answer(true);
      if (k === "n" || k === "escape") answer(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirm]);

  if (!steps.length && !confirm) return null;

  const goal = steps[0]?.goal;

  return (
    <motion.div
      className="agent-trace holographic-ui"
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 12 }}
      transition={{ duration: 0.35, ease: HUD_EASE }}
    >
      <div className="agent-trace__head">
        <span className="agent-trace__dot" />
        <span className="agent-trace__title">AGENT</span>
        {goal && <span className="agent-trace__goal">{goal}</span>}
      </div>

      <div className="agent-trace__list" ref={listRef}>
        <AnimatePresence initial={false}>
          {steps.map((s, i) => {
            const style = styleFor(s.event);
            const { Icon } = style;
            return (
              <motion.div
                key={`${i}-${s.event}`}
                className={`agent-step is-${style.tone}${style.sub ? " is-sub" : ""}`}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.25, ease: HUD_EASE }}
              >
                <span className="agent-step__icon">
                  <Icon size={13} strokeWidth={1.9} />
                </span>
                <span className="agent-step__label">{style.label}</span>
                <span className="agent-step__msg">{s.message}</span>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>

      <AnimatePresence>
        {confirm && (
          <motion.div
            className="agent-confirm"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.28, ease: HUD_EASE }}
          >
            <div className="agent-confirm__q">
              <ShieldQuestion size={15} strokeWidth={1.9} />
              <span>{confirm.question}</span>
            </div>
            <div className="agent-confirm__actions">
              <button
                type="button"
                className="agent-confirm__btn is-approve"
                onClick={() => answer(true)}
                autoFocus
              >
                APPROVE <kbd>Y</kbd>
              </button>
              <button
                type="button"
                className="agent-confirm__btn is-deny"
                onClick={() => answer(false)}
              >
                DENY <kbd>N</kbd>
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
