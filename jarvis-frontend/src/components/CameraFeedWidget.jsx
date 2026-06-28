import React, { useEffect, useRef, useState } from "react";
import "./CameraFeedWidget.scss";

const API_BASE = "http://127.0.0.1:8000";
const POLL_MS = 800; // detection overlay refresh (the ambient daemon itself updates ~6s)

// Box colour by what JARVIS sees: intruder/unknown = red, known person = gold, object = cyan.
function colorFor(det) {
  if (det.label === "person") {
    if (!det.identity || det.identity === "Unknown Person") return "#ff3366";
    return "#ffb800";
  }
  return "#00ffcc";
}

function labelFor(det) {
  if (det.label === "person") {
    const who = det.identity || "PERSON";
    const emo = det.emotion ? ` · ${det.emotion}` : "";
    return `${who.toUpperCase()}${emo}`;
  }
  return `${det.label.toUpperCase()} ${Math.round((det.conf || 0) * 100)}%`;
}

export default function CameraFeedWidget() {
  const [state, setState] = useState(null);
  const [streamError, setStreamError] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/vision/state`);
        const data = await res.json();
        if (alive) setState(data);
      } catch {
        if (alive) setState((s) => (s ? { ...s, camera_active: false } : s));
      }
    };
    poll();
    timerRef.current = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timerRef.current);
    };
  }, []);

  const cameraUrl = state?.camera_url;
  const fw = state?.frame_w || 0;
  const fh = state?.frame_h || 0;
  const detections = state?.detections || [];
  const objects = state?.objects_in_view || [];
  const emotion = state?.dominant_emotion || "neutral";
  const intruder = state?.intruder_detected;
  const offline = !cameraUrl || state?.camera_active === false || streamError;

  return (
    <div className="camera-feed-ui holographic-ui">
      <div className={`cam-viewport ${intruder ? "is-intruder" : ""}`}>
        {cameraUrl && (
          <img
            className="cam-stream"
            src={cameraUrl}
            alt="optical feed"
            onError={() => setStreamError(true)}
            onLoad={() => setStreamError(false)}
          />
        )}

        {/* Detection overlay — viewBox matches the analysed frame so boxes scale
            with the video (both use contain / meet, so they letterbox identically). */}
        {fw > 0 && fh > 0 && !offline && (
          <svg
            className="cam-overlay"
            viewBox={`0 0 ${fw} ${fh}`}
            preserveAspectRatio="xMidYMid meet"
          >
            {detections.map((det, i) => {
              const [x1, y1, x2, y2] = det.box;
              const c = colorFor(det);
              const w = x2 - x1;
              const h = y2 - y1;
              const bracket = Math.min(w, h) * 0.22;
              return (
                <g key={i} className="cam-det">
                  <rect x={x1} y={y1} width={w} height={h} fill="none" stroke={c} strokeWidth="2" opacity="0.85" />
                  {/* corner brackets for the targeting look */}
                  <path d={`M${x1},${y1 + bracket} L${x1},${y1} L${x1 + bracket},${y1}`} stroke={c} strokeWidth="3" fill="none" />
                  <path d={`M${x2 - bracket},${y1} L${x2},${y1} L${x2},${y1 + bracket}`} stroke={c} strokeWidth="3" fill="none" />
                  <path d={`M${x1},${y2 - bracket} L${x1},${y2} L${x1 + bracket},${y2}`} stroke={c} strokeWidth="3" fill="none" />
                  <path d={`M${x2 - bracket},${y2} L${x2},${y2} L${x2},${y2 - bracket}`} stroke={c} strokeWidth="3" fill="none" />
                  <rect x={x1} y={Math.max(0, y1 - 22)} width={Math.max(labelFor(det).length * 9 + 12, 60)} height="20" fill={c} opacity="0.85" />
                  <text x={x1 + 6} y={Math.max(14, y1 - 8)} className="cam-det-label" fill="#05121a">
                    {labelFor(det)}
                  </text>
                </g>
              );
            })}
          </svg>
        )}

        {/* Live HUD chrome */}
        <div className="cam-corner tl" />
        <div className="cam-corner tr" />
        <div className="cam-corner bl" />
        <div className="cam-corner br" />
        <div className="cam-scanline" />

        {offline && (
          <div className="cam-signal-lost">
            <span className="cam-lost-glyph">⚠</span>
            <span>OPTICAL FEED OFFLINE</span>
            <span className="cam-lost-sub">camera unreachable — check IP Webcam</span>
          </div>
        )}

        {!offline && (
          <div className="cam-live-tag">
            <span className="cam-live-dot" /> LIVE
          </div>
        )}

        {intruder && !offline && (
          <div className="cam-intruder-banner">⚠ UNIDENTIFIED PRESENCE</div>
        )}
      </div>

      <div className="cam-status-bar">
        <span>OBJECTS: <b>{objects.length}</b></span>
        <span>TRACKING: <b>{detections.filter((d) => d.label === "person").length}</b></span>
        <span>EMOTION: <b>{emotion}</b></span>
        <span className={offline ? "cam-stat-off" : "cam-stat-on"}>
          {offline ? "● OFFLINE" : "● ONLINE"}
        </span>
      </div>
    </div>
  );
}
