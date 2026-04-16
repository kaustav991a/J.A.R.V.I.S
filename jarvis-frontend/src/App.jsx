import React, { useState, useEffect, useRef } from "react";
import Draggable from "react-draggable";
import { Battery, Wifi, Bluetooth, MapPin, Sun } from "lucide-react";
import Visualizer from "./components/Visualizer";
import "./App.scss";

// A reusable wrapper for the draggable widgets
const Widget = ({ title, children, defaultPos }) => {
  const [isMoveMode, setIsMoveMode] = useState(false);
  const nodeRef = useRef(null); // 1. Create a reference

  // Toggle move mode on right click
  const handleContextMenu = (e) => {
    e.preventDefault();
    setIsMoveMode(!isMoveMode);
  };

  return (
    // 2. Tell Draggable to use our reference instead of findDOMNode
    <Draggable
      nodeRef={nodeRef}
      disabled={!isMoveMode}
      defaultPosition={defaultPos}
    >
      {/* 3. Attach the reference to the actual div */}
      <div
        ref={nodeRef}
        className={`panel widget ${isMoveMode ? "move-mode-active" : ""}`}
        onContextMenu={handleContextMenu}
      >
        {isMoveMode && <div className="move-badge">▤ MOVE MODE</div>}
        <div className="panel-header">{title}</div>
        <div className="widget-content">{children}</div>
      </div>
    </Draggable>
  );
};

function App() {
  const [time, setTime] = useState(new Date());
  const [status, setStatus] = useState("offline");
  const [log, setLog] = useState({
    speaker: "J.A.R.V.I.S.",
    text: "SYSTEM ONLINE // STANDING BY",
  });
  const socket = useRef(null);

  // Live Clock
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // WebSocket Logic
  useEffect(() => {
    socket.current = new WebSocket("ws://127.0.0.1:8000/ws");

    socket.current.onopen = () => {
      setStatus("online");
      setLog({ speaker: "J.A.R.V.I.S.", text: "UPLINK ESTABLISHED" });
    };

    socket.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.status === "processing_llm")
        setLog({ speaker: "J.A.R.V.I.S.", text: "ANALYZING INTENT..." });
      if (data.status === "executing")
        setLog({
          speaker: "J.A.R.V.I.S.",
          text: `EXEC: ${data.intent.action_type}`,
        });
      if (data.status === "complete")
        setLog({ speaker: "J.A.R.V.I.S.", text: "TASK FINALIZED." });
    };

    socket.current.onclose = () => setStatus("offline");
    return () => socket.current.close();
  }, []);

  const startVoiceCommand = () => {
    if (socket.current.readyState === WebSocket.OPEN) {
      // Clear the log and notify the backend
      setLog({ speaker: "SYS", text: "INITIALIZING MIC..." });
      socket.current.send("START_LISTENING");
    }
  };

  return (
    <div className="dashboard-container">
      {/* Top Navigation */}
      {/* <nav className="top-nav">
        <div className="brand">J.A.R.V.I.S.</div>
        <div className="nav-links">
          <span>HOME</span>
          <span className="active">DASHBOARD</span>
          <span>SETTINGS</span>
          <span>ABOUT</span>
        </div>
      </nav> */}

      {/* DRAGGABLE WIDGETS */}

      {/* Left Area Widgets */}
      <Widget title="📍 LOCATION" defaultPos={{ x: 40, y: 40 }}>
        <div className="loc-data">
          <h3>Bengaluru</h3>
          <p>Karnataka, India</p>
          <div className="coords">LAT: 12.9057° LNG: 77.6107°</div>
        </div>
      </Widget>

      <Widget title="SYSTEM STATUS" defaultPos={{ x: 40, y: 300 }}>
        <div className="status-grid">
          <div className="status-item">
            <Battery size={16} color="#00ffcc" /> <span>62%</span>
          </div>
          <div className="status-item">
            <Wifi size={16} color="#00ffcc" /> <span>ONLINE</span>
          </div>
          <div className="status-item">
            <Wifi size={16} color="#00ffcc" /> <span>4G</span>
          </div>
          <div className="status-item">
            <Bluetooth size={16} color="#00ffcc" /> <span>READY</span>
          </div>
        </div>
      </Widget>

      {/* Right Area Widgets */}
      <Widget
        title="SYSTEM STATUS"
        defaultPos={{ x: window.innerWidth - 340, y: 40 }}
      >
        <ul className="checklist">
          <li>
            SYSTEM ONLINE <span className="dot on"></span>
          </li>
          <li>
            J.A.R.V.I.S. ACTIVE <span className="dot on"></span>
          </li>
          <li>
            MICROPHONE{" "}
            <span
              className={`dot ${status === "online" ? "on" : "off"}`}
            ></span>
          </li>
          <li>
            MIC PERMISSION <span className="dot on"></span>
          </li>
          <li>
            TTS SPEAKING <span className="dot off"></span>
          </li>
          <li>
            WAKE DETECTED <span className="dot off"></span>
          </li>
          <li>
            API CONNECTION <span className="dot on"></span>
          </li>
        </ul>
      </Widget>

      <Widget
        title="SYSTEM_INFO 🟢"
        defaultPos={{ x: window.innerWidth - 340, y: 415 }}
      >
        <div className="info-panel-content">
          <div className="time-display">
            <h1>{time.toLocaleTimeString([], { hour12: false })}</h1>
            <p>
              {time
                .toLocaleDateString("en-US", {
                  weekday: "short",
                  month: "short",
                  day: "numeric",
                })
                .toUpperCase()}
            </p>
          </div>
          <div className="weather-display">
            <Sun size={24} color="#ffcc00" />
            <h2>30°C</h2>
          </div>
          <div className="stats-row">
            <div>
              <span>UPTIME</span>
              <br />
              10h
            </div>
            <div>
              <span>COMMANDS</span>
              <br />1
            </div>
          </div>
        </div>
      </Widget>

      {/* CENTER IMMOBILE ELEMENTS */}

      {/* The Blob Visualizer Placeholder */}
      <div className="center-visualizer" onClick={startVoiceCommand}>
        <Visualizer status={status} />
      </div>

      {/* The Greeting Box */}
      <div className="greeting-box">
        <h2>Good Afternoon, Sir</h2>
        <p>Standing by for instructions.</p>
        <span className="signature">- J.A.R.V.I.S. -</span>
      </div>

      {/* The Horizontal Subtitle Log */}
      <div className="system-log-horizontal">
        <div className="log-header">
          <span className="pulse-dot"></span> SYSTEM_LOG // J.A.R.V.I.S.
        </div>
        <div className="log-text">
          <span className="speaker-tag">&gt; {log.speaker} &gt;</span>{" "}
          {log.text}
          <div className="cursor-block"></div>
        </div>
      </div>
    </div>
  );
}

export default App;
