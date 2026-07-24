import React, { useEffect, useRef, useState } from "react";
import "./BootSequence.scss";

/**
 * G6.1 — staged power-on, GATED to the real boot.
 *
 * Walks POWER CORE -> NEURAL LINK -> MEMORY BANKS -> CALIBRATING on a timer, then
 * HOLDS on CALIBRATING (the last pre-final step) until `ready` turns true — i.e.
 * until the backend actually reports `waking` / `online`. Only then does it show
 * NOMINAL and fade into the HUD. The animation can never outrun reality: a slow
 * boot holds on CALIBRATING; a fast boot still plays the full minimum sweep.
 *
 * Props:
 *   active     — true while a wake is in progress (backend sent `booting`)
 *   ready      — true once the backend confirms `waking` / `online`
 *   onComplete — called after NOMINAL is shown and the overlay fades out
 */

const STAGES = [
  { key: "power", label: "POWER CORE" },
  { key: "neural", label: "NEURAL LINK" },
  { key: "memory", label: "MEMORY BANKS" },
  { key: "calib", label: "CALIBRATING" },
  { key: "nominal", label: "NOMINAL" },
];
const HOLD_INDEX = STAGES.length - 2; // CALIBRATING — hold here until `ready`
const LAST_INDEX = STAGES.length - 1; // NOMINAL
const STEP_MS = 720; // per-stage dwell while powering up
const NOMINAL_HOLD_MS = 900; // linger on NOMINAL before the wipe
const WIPE_MS = 850; // scanline wipe-to-HUD; must match boot-wipe/boot-beam in the scss

export default function BootSequence({ active, ready, onComplete }) {
  const [index, setIndex] = useState(0);
  const [exiting, setExiting] = useState(false);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  // (re)start on activation
  useEffect(() => {
    if (!active) return;
    setIndex(0);
    setExiting(false);
  }, [active]);

  // step forward — freely up to the hold point, past it only once `ready`
  useEffect(() => {
    if (!active || exiting) return;
    const canAdvance = index < HOLD_INDEX || (index === HOLD_INDEX && ready);
    if (!canAdvance || index >= LAST_INDEX) return;
    const t = setTimeout(() => setIndex((i) => i + 1), STEP_MS);
    return () => clearTimeout(t);
  }, [active, ready, index, exiting]);

  // reached NOMINAL -> linger, scanline-wipe into the HUD, complete
  useEffect(() => {
    if (!active || index !== LAST_INDEX) return;
    const hold = setTimeout(() => setExiting(true), NOMINAL_HOLD_MS);
    const done = setTimeout(
      () => onCompleteRef.current && onCompleteRef.current(),
      NOMINAL_HOLD_MS + WIPE_MS,
    );
    return () => {
      clearTimeout(hold);
      clearTimeout(done);
    };
  }, [active, index]);

  if (!active) return null;

  const pct = Math.round((index / LAST_INDEX) * 100);
  const holding = index === HOLD_INDEX && !ready;

  return (
    <div
      className={`boot-seq ${exiting ? "boot-seq--exit" : "boot-seq--enter"}`}
      role="status"
      aria-live="polite"
    >
      {/* content wipes away top-to-bottom on exit, revealing the HUD beneath */}
      <div className="boot-seq__inner">
        <div className="boot-seq__grid" aria-hidden />

        <div className={`boot-seq__ring ${holding ? "is-holding" : ""}`} aria-hidden>
          <span className="boot-seq__pct">{pct}%</span>
        </div>

        <ul className="boot-seq__stages">
          {STAGES.map((s, i) => {
            const state = i < index ? "done" : i === index ? "active" : "pending";
            const stageHolding = i === HOLD_INDEX && holding;
            return (
              <li
                key={s.key}
                className={`boot-seq__stage is-${state} ${stageHolding ? "is-holding" : ""}`}
              >
                <span className="boot-seq__mark">
                  {i < index ? "✓" : i === index ? "▸" : "·"}
                </span>
                <span className="boot-seq__label">{s.label}</span>
                {stageHolding && <span className="boot-seq__wait">SYNCING…</span>}
              </li>
            );
          })}
        </ul>

        <div className="boot-seq__status">
          {index >= LAST_INDEX ? "ALL SYSTEMS NOMINAL" : "INITIALISING SUBSYSTEMS…"}
        </div>
      </div>

      {/* scanline beam rides the reveal edge during the wipe */}
      {exiting && <div className="boot-seq__beam" aria-hidden />}
    </div>
  );
}
