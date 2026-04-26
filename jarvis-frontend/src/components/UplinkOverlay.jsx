import React, { useState, useEffect } from "react";
import "./UplinkOverlay.scss";

export default function UplinkOverlay({ isActive }) {
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    if (!isActive) {
      setLogs([]);
      return;
    }

    const possibleLogs = [
      "ESTABLISHING SECURE CONNECTION...",
      "BYPASSING LOCAL FIREWALLS...",
      "ROUTING VIA SATELLITE UPLINK...",
      "ACCESSING GLOBAL DATABASES...",
      "CROSS-REFERENCING ENCRYPTED ARCHIVES...",
      "DECRYPTING PACKET DATA...",
      "ANALYZING SEMANTIC PATTERNS...",
      "SYNTHESIZING RESPONSE MATRIX..."
    ];

    let i = 0;
    const interval = setInterval(() => {
      if (i < possibleLogs.length) {
        const ts = new Date().toISOString().substring(11, 23);
        setLogs((prev) => [...prev, { text: possibleLogs[i], ts }]);
        i++;
      } else {
        clearInterval(interval);
      }
    }, 600);

    return () => clearInterval(interval);
  }, [isActive]);

  if (!isActive) return null;

  return (
    <div className="uplink-overlay">
      <div className="uplink-globe">
        <div className="globe-wireframe"></div>
        <div className="globe-equator"></div>
        <div className="radar-sweep"></div>
      </div>
      <div className="uplink-terminal">
        <div className="terminal-header">GLOBAL UPLINK ACTIVE</div>
        <div className="terminal-logs">
          {logs.map((log, index) => (
            <div key={index} className="log-line">
              <span className="timestamp">[{log.ts}]</span> {log.text}
            </div>
          ))}
          <div className="log-line blinking-cursor">_</div>
        </div>
      </div>
    </div>
  );
}
