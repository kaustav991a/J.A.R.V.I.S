import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Pointer,
  Hand,
  MousePointerClick,
  Grab,
  ArrowUpDown,
  MousePointer2,
  Ban,
  ScanFace,
} from "lucide-react";
import "./GestureGuide.scss";

const HUD_EASE = [0.16, 1, 0.3, 1];

// pose (from the backend gesture engine) -> which card lights up live
const POSE_TO_CARD = {
  index_only: "start",
  palm: "move",
  fist: "grab",
  two_finger: "scroll",
  back_palm: "stop",
};

const CARDS = [
  {
    id: "start",
    Icon: Pointer,
    title: "START",
    line: "Hold your INDEX FINGER up for 1 second.",
    sub: "Control always starts OFF — this arms it.",
  },
  {
    id: "move",
    Icon: Hand,
    title: "MOVE CURSOR",
    line: "Open palm facing the camera — the whole hand steers.",
    sub: "Steady hand = steady cursor (smoothed + deadzoned).",
  },
  {
    id: "click",
    Icon: MousePointerClick,
    title: "CLICK",
    line: "Tap THUMB + INDEX together = left click.",
    sub: "Tap twice within 1s at the same spot = double-click.",
  },
  {
    id: "grab",
    Icon: Grab,
    title: "GRAB & DRAG",
    line: "Close your FIST to grab, move it, open to drop.",
    sub: "Click and grab are separate — a click can never drag-select.",
  },
  {
    id: "scroll",
    Icon: ArrowUpDown,
    title: "SCROLL",
    line: "INDEX + MIDDLE fingers up, move hand up / down.",
    sub: "Hand up = scroll up.",
  },
  {
    id: "rightclick",
    Icon: MousePointer2,
    title: "RIGHT CLICK",
    line: "Tap THUMB + MIDDLE finger together.",
    sub: "",
  },
  {
    id: "stop",
    Icon: Ban,
    title: "STOP",
    line: "Show the BACK of your open hand for 1.5 seconds.",
    sub: "Flip the hand like a stop sign facing you.",
  },
  {
    id: "security",
    Icon: ScanFace,
    title: "BIOMETRIC GATE",
    line: "Gestures obey the enrolled owner only — others are denied.",
    sub: "Leave the desk → soft-lock + screen off. Come back → your face unlocks.",
  },
];

export default function GestureGuide({ open, gesture, onClose }) {
  const pose = gesture?.pose;
  const liveCard = gesture ? POSE_TO_CARD[pose] : null;
  const engaged = !!gesture?.engaged;
  const denied = !!gesture?.denied || gesture?.state === "denied";
  const locked = !!gesture?.locked;
  const owner = !!gesture?.owner;
  const progress = Math.max(gesture?.start_progress || 0, gesture?.stop_progress || 0);
  const live = !!gesture; // any gesture_state frame ever received = daemon alive

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          key="jarvis-gesture-guide"
          className="gesture-guide"
          initial={{ opacity: 0, x: 40 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 40 }}
          transition={{ duration: 0.45, ease: HUD_EASE }}
        >
          <div className="gesture-guide__header">
            <span className="gesture-guide__title">
              <span className="gesture-guide__dot" aria-hidden />
              GESTURE CONTROL // FIELD MANUAL
            </span>
            <button
              type="button"
              className="gesture-guide__close"
              onClick={onClose}
              title="Close guide"
              aria-label="Close guide"
            >
              ✕
            </button>
          </div>

          <div
            className={`gesture-guide__status ${
              denied
                ? "is-denied"
                : locked
                ? "is-locked"
                : engaged
                ? "is-active"
                : ""
            }`}
          >
            {!live ? (
              <span>PRACTICE MODE OFFLINE — gesture daemon not reporting</span>
            ) : denied ? (
              <span>UNAUTHORIZED — control is locked to the enrolled owner</span>
            ) : locked ? (
              <span>DESK SOFT-LOCKED — face the camera to unlock</span>
            ) : (
              <span>
                LIVE · {engaged ? "CONTROL ACTIVE" : "CONTROL OFF"} · POSE:{" "}
                {(pose || "none").replace("_", " ").toUpperCase()} · OWNER{" "}
                {owner ? "✓" : "—"}
              </span>
            )}
            {live && progress > 0 && (
              <div className="gesture-guide__hold">
                <div
                  className="gesture-guide__hold-bar"
                  style={{ width: `${Math.round(progress * 100)}%` }}
                />
              </div>
            )}
          </div>

          <div className="gesture-guide__body">
            {CARDS.map(({ id, Icon, title, line, sub }) => (
              <div
                key={id}
                className={`gesture-card ${liveCard === id ? "is-live" : ""}`}
              >
                <div className="gesture-card__icon">
                  <Icon size={26} strokeWidth={1.6} />
                </div>
                <div className="gesture-card__text">
                  <span className="gesture-card__title">
                    {title}
                    {liveCard === id && (
                      <span className="gesture-card__seen">SEEN</span>
                    )}
                  </span>
                  <p className="gesture-card__line">{line}</p>
                  {sub && <p className="gesture-card__sub">{sub}</p>}
                </div>
              </div>
            ))}
            <p className="gesture-guide__foot">
              Voice: “hand control on / off” · “auto lock on / off”. Practice
              live — the matching card lights up as the camera sees your hand.
            </p>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
