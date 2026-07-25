import React from "react";
import { Mic, MicOff } from "lucide-react";
import "./MicIndicator.scss";

// The visible voice affordance a voice-first product was missing. It mirrors the
// REAL backend voice state (statuses already broadcast on the WS) so you can see
// whether JARVIS is listening / thinking / speaking, and it is the click-to-talk
// button: a click POSTs /api/listen, which the SERVER-side microphone loop picks
// up between listen windows (so it takes a moment, and offline it wakes him).
function derive(status) {
  if (!status || status === "offline")
    return { tone: "off", label: "MIC OFF", hint: "click to wake, or say the wake word" };
  if (["listening", "security_listening", "calibrating"].includes(status))
    return { tone: "listening", label: "LISTENING", hint: "speak now" };
  if (["processing_llm", "searching"].includes(status))
    return { tone: "busy", label: "THINKING", hint: "processing your request" };
  if (status === "speaking")
    return { tone: "speaking", label: "SPEAKING", hint: "JARVIS is responding" };
  return { tone: "ready", label: "READY", hint: "click to talk, or say the wake word" };
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
