import React, { useState, useEffect } from "react";
import "./FaceScanOverlay.scss";

export default function FaceScanOverlay({ isActive }) {
  const [phase, setPhase] = useState(-1);
  const [scanText, setScanText] = useState("");

  useEffect(() => {
    if (!isActive) {
      setPhase(-1);
      setScanText("");
      return;
    }

    setPhase(0);
    setScanText("INITIALIZING OPTICAL SENSORS...");

    const timers = [
      setTimeout(() => {
        setPhase(1);
        setScanText("SCANNING BIOMETRICS...");
      }, 1500),
      setTimeout(() => {
        setPhase(2);
        setScanText("MATCHING KNOWN DATABASES...");
      }, 4000)
    ];

    return () => timers.forEach(clearTimeout);
  }, [isActive]);

  if (!isActive) return null;

  return (
    <div className="face-scan-overlay">
      <div className="scan-reticle">
        <div className="bracket top-left"></div>
        <div className="bracket top-right"></div>
        <div className="bracket bottom-left"></div>
        <div className="bracket bottom-right"></div>
        
        {phase >= 1 && (
          <>
            <div className="scan-laser"></div>
            <div className="scan-grid"></div>
            <div className="scan-points">
              {Array.from({ length: 12 }).map((_, i) => (
                <div key={i} className="scan-point" style={{
                  top: `${20 + Math.random() * 60}%`,
                  left: `${20 + Math.random() * 60}%`,
                  animationDelay: `${Math.random() * 2}s`
                }}></div>
              ))}
            </div>
          </>
        )}
      </div>
      
      <div className="scan-status-text">
        {scanText}
      </div>
    </div>
  );
}
