import React from "react";
import "./LockdownOverlay.scss";

export default function LockdownOverlay({ isActive }) {
  if (!isActive) return null;

  return (
    <div className="lockdown-overlay">
      <div className="lockdown-borders"></div>
      <div className="lockdown-content">
        <div className="hazard-icon">⚠️</div>
        <h1 className="lockdown-title">SECURITY OVERRIDE ACCEPTED</h1>
        <h2 className="lockdown-subtitle">INITIATING PROTOCOL</h2>
        <div className="lockdown-grid">
          <div className="grid-cell">EXTERNAL PORTS: SECURED</div>
          <div className="grid-cell">NETWORK TRAFFIC: BLOCKED</div>
          <div className="grid-cell">BIOMETRIC SCANNERS: MAXIMUM</div>
          <div className="grid-cell">DEFENSIVE MEASURES: ARMED</div>
        </div>
      </div>
    </div>
  );
}
