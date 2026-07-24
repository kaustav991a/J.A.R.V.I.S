import React, { useEffect, useState } from "react";
import "./IdentityPrompt.scss";

/**
 * G6.1 — visual identity challenge.
 *
 * The backend's "state your name" step is voice-only (server-side mic); the HUD
 * gave no visual for it. This overlay fills that gap: three identity cards +
 * a live mic pulse that makes it obvious JARVIS is LISTENING for a spoken name
 * (it also serves as the missing mic affordance from §5).
 *
 * Identification is still answered by VOICE — the cards are a visual, not a
 * submit control (the /ws voice loop reads the server mic, not client input),
 * so the overlay is intentionally non-interactive (pointer-events: none). A
 * roving highlight sweeps the cards to signal "awaiting input".
 *
 * Props:
 *   active — true while the backend is on the identification challenge
 *   hint   — optional challenge message from the backend (shown as sub-text)
 */

const IDENTITIES = ["KAUSTAV", "KINSHUK", "MOUSUMI"];
const ROVE_MS = 1100;

export default function IdentityPrompt({ active, hint }) {
  const [rove, setRove] = useState(0);

  useEffect(() => {
    if (!active) return;
    setRove(0);
    const t = setInterval(() => setRove((r) => (r + 1) % IDENTITIES.length), ROVE_MS);
    return () => clearInterval(t);
  }, [active]);

  if (!active) return null;

  return (
    <div className="ident-prompt" role="status" aria-live="polite">
      <div className="ident-prompt__grid" aria-hidden />

      <div className="ident-prompt__mic" aria-hidden>
        <span className="ident-prompt__mic-core" />
        <span className="ident-prompt__mic-ring" />
        <span className="ident-prompt__mic-ring ident-prompt__mic-ring--2" />
      </div>

      <div className="ident-prompt__title">AWAITING IDENTIFICATION</div>
      <div className="ident-prompt__sub">{hint || "SPEAK YOUR NAME"}</div>

      <ul className="ident-prompt__cards">
        {IDENTITIES.map((name, i) => (
          <li
            key={name}
            className={`ident-card ${i === rove ? "is-lit" : ""}`}
          >
            <span className="ident-card__initial">{name[0]}</span>
            <span className="ident-card__name">{name}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
