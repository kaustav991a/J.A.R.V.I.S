import React from "react";
import { Mic, MicOff } from "lucide-react";
import "./MicIndicator.scss";

// The visible voice affordance a voice-first product was missing. It mirrors the
// REAL backend voice state (statuses already broadcast on the WS) so you can see
// whether JARVIS is listening / thinking / speaking, and it's an obvious "talk
// or type here" target. Click focuses the command line (works today); true
// voice click-to-talk needs a backend bidirectional trigger (roadmap follow-up).
function derive(status) {
  if (!status || status === "offline")
    return { tone: "off", label: "MIC OFF", hint: "system offline — say the wake word" };
  if (["listening", "security_listening", "calibrating"].includes(status))
    return { tone: "listening", label: "LISTENING", hint: "speak now" };
  if (["processing_llm", "searching"].includes(status))
    return { tone: "busy", label: "THINKING", hint: "processing your request" };
  if (status === "speaking")
    return { tone: "speaking", label: "SPEAKING", hint: "JARVIS is responding" };
  return { tone: "ready", label: "READY", hint: "say the wake word, or click to type" };
}

export default function MicIndicator({ status, onClick }) {
  const s = derive(status);
  const Icon = s.tone === "off" ? MicOff : Mic;
  return (
    <button
      type="button"
      className={`mic-indicator is-${s.tone}`}
      onClick={onClick}
      title={`Voice: ${s.label}. ${s.hint}.`}
      aria-label={`Voice status: ${s.label}. ${s.hint}.`}
    >
      <span className="mic-indicator__icon">
        <Icon size={15} strokeWidth={1.8} />
      </span>
      <span className="mic-indicator__label">{s.label}</span>
      {s.tone === "listening" && (
        <span className="mic-indicator__wave" aria-hidden>
          <i /><i /><i /><i />
        </span>
      )}
    </button>
  );
}
