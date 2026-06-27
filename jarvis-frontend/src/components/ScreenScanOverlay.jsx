import React from "react";
import "./ScreenScanOverlay.scss";

export default function ScreenScanOverlay({ isActive }) {
  if (!isActive) return null;

  return (
    <div className="screen-scan-overlay">
      <div className="scan-line"></div>
      <div className="scan-grid"></div>
      <div className="scan-status-text">SCANNING OPTICAL DATA STREAM...</div>
      <div className="scan-data-stream">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="data-line" style={{ animationDelay: `${i * 0.2}s` }} />
        ))}
      </div>
    </div>
  );
}
