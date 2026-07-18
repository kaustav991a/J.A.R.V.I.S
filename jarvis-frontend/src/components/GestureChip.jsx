import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Hand, Lock, Ban, Cpu, ScanFace, AlertTriangle } from "lucide-react";
import "./GestureChip.scss";

const HUD_EASE = [0.16, 1, 0.3, 1];

// Collapse the backend gesture_state frame into a single, glanceable status.
// Priority matters: a locked desk or a stranger-denial outranks "hand active".
function derive(g) {
  if (!g) return null; // no frame ever received -> daemon silent -> no chip
  const enabled = !!g.enabled;
  const engaged = !!g.engaged;
  const denied = !!g.denied || g.state === "denied";
  const locked = !!g.locked;
  const suspended = !!g.suspended;

  if (g.state === "camera_error")
    return { tone: "warn", Icon: AlertTriangle, label: "CAM ERROR",
             hint: "gesture camera unavailable — retrying" };
  if (locked)
    return { tone: "danger", Icon: Lock, label: "LOCKED",
             hint: "desk soft-locked — face the camera to unlock" };
  if (denied)
    return { tone: "danger", Icon: Ban, label: "DENIED",
             hint: "control is locked to the enrolled owner" };
  if (suspended)
    return { tone: "busy", Icon: Cpu, label: "JARVIS DRIVING",
             hint: `hand paused while JARVIS controls the cursor${
               g.suspend_reason ? ` (${g.suspend_reason})` : ""}` };
  if (!enabled)
    return { tone: "off", Icon: Hand, label: "HAND OFF",
             hint: 'say "hand control on" to arm gesture control' };
  if (engaged)
    return { tone: "live", Icon: Hand, label: "HAND ACTIVE",
             hint: "gesture cursor control is live" };
  return { tone: "ready", Icon: Hand, label: "HAND READY",
           hint: "armed — hold index finger up 1s to start" };
}

export default function GestureChip({ gesture, onClick }) {
  const s = derive(gesture);
  const progress = Math.max(
    gesture?.start_progress || 0,
    gesture?.stop_progress || 0
  );
  // biometric-gate tick is only meaningful while gestures can run
  const showOwner = s && !["off", "warn"].includes(s.tone) && !gesture?.locked;
  const owner = !!gesture?.owner;

  return (
    <AnimatePresence>
      {s && (
        <motion.button
          key="jarvis-gesture-chip"
          type="button"
          className={`gesture-chip is-${s.tone}`}
          title={`${s.hint}. Click for the gesture field manual.`}
          aria-label={`Gesture control: ${s.label}. ${s.hint}`}
          onClick={onClick}
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.35, ease: HUD_EASE }}
        >
          <span className="gesture-chip__icon">
            <s.Icon size={14} strokeWidth={1.8} />
          </span>
          <span className="gesture-chip__label">{s.label}</span>
          {showOwner && (
            <span
              className={`gesture-chip__owner ${owner ? "is-ok" : ""}`}
              title={owner ? "owner verified" : "owner not verified"}
            >
              <ScanFace size={12} strokeWidth={1.8} />
              {owner ? "✓" : "—"}
            </span>
          )}
          {progress > 0 && (
            <span className="gesture-chip__hold" aria-hidden>
              <span
                className="gesture-chip__hold-bar"
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            </span>
          )}
        </motion.button>
      )}
    </AnimatePresence>
  );
}
