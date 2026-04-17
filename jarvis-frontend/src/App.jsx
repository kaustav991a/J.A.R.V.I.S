import React, { useState, useEffect, useRef } from "react";
import Draggable from "react-draggable";
import { Battery, Wifi, Bluetooth, MapPin, Sun } from "lucide-react";
import Visualizer from "./components/Visualizer";
import "./App.scss";

// Reusable Typewriter Effect Component
const Typewriter = ({ text, delay = 50, start = false }) => {
  const [currentText, setCurrentText] = useState("");
  const [currentIndex, setCurrentIndex] = useState(0);

  useEffect(() => {
    if (start && currentIndex < text.length) {
      const timeout = setTimeout(() => {
        setCurrentText((prevText) => prevText + text[currentIndex]);
        setCurrentIndex((prevIndex) => prevIndex + 1);
      }, delay);
      return () => clearTimeout(timeout);
    }
  }, [currentIndex, delay, text, start]);

  return <span>{currentText}</span>;
};

// Upgraded Widget Wrapper (Now using Absolute Positioning)
const Widget = ({ title, children, defaultPos, delayIndex, hasWokenUp }) => {
  const [isMoveMode, setIsMoveMode] = useState(false);
  const nodeRef = useRef(null);

  const handleContextMenu = (e) => {
    e.preventDefault();
    setIsMoveMode(!isMoveMode);
  };

  return (
    <Draggable
      nodeRef={nodeRef}
      disabled={!isMoveMode}
      defaultPosition={defaultPos}
      // Forces standard left/top absolute positioning
      useCSSTransforms={false}
    >
      <div
        ref={nodeRef}
        className={`panel widget ${isMoveMode ? "move-mode-active" : ""} ${
          hasWokenUp ? "widget-awake" : "widget-sleep"
        }`}
        style={{ animationDelay: `${delayIndex * 0.15}s` }}
        onContextMenu={handleContextMenu}
      >
        {isMoveMode && <div className="move-badge">▤ MOVE MODE</div>}
        <div className="panel-header">{title}</div>
        <div
          className="widget-content"
          style={{ animationDelay: `${delayIndex * 0.15 + 0.3}s` }}
        >
          {children}
        </div>
      </div>
    </Draggable>
  );
};

function App() {
  const [time, setTime] = useState(new Date());
  const [status, setStatus] = useState("offline");

  const [hasWokenUp, setHasWokenUp] = useState(false);
  const [commandCount, setCommandCount] = useState(0);
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  const [log, setLog] = useState({
    speaker: "J.A.R.V.I.S",
    text: "SYSTEM ONLINE // STANDING BY",
  });
  const socket = useRef(null);

  // Live Clock
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    setTimeout(() => setIsInitialLoad(false), 500);
  }, []);

  const getGreeting = () => {
    const hour = time.getHours();
    if (hour < 12) return "Good Morning, Sir.";
    if (hour < 18) return "Good Afternoon, Sir.";
    return "Good Evening, Sir.";
  };

  // WebSocket Logic
  useEffect(() => {
    socket.current = new WebSocket("ws://127.0.0.1:8000/ws");

    socket.current.onopen = () => {
      setStatus("online");
      setLog({
        speaker: "J.A.R.V.I.S",
        text: "UPLINK ESTABLISHED // WAKE WORD ACTIVE",
      });
    };

    socket.current.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.status) {
        setStatus(data.status);

        if (
          data.status === "waking" ||
          data.status === "calibrating" ||
          data.status === "listening"
        ) {
          setHasWokenUp(true);
        }

        if (data.status === "complete") {
          setCommandCount((prev) => prev + 1);
        }
      }

      let textContent = data.message || data.text;

      if (data.status === "executing" && data.intent) {
        textContent = `EXEC_PROTOCOL: ${data.intent.action_type.toUpperCase()}`;
      }

      if (textContent) {
        // THE FIX: Simplified Speaker Tags
        let currentSpeaker = "J.A.R.V.I.S";
        if (data.status === "calibrating" || data.status === "listening") {
          currentSpeaker = "USER";
        }

        setLog({ speaker: currentSpeaker, text: textContent });
      }
    };

    socket.current.onclose = () => {
      setStatus("offline");
      setLog({ speaker: "J.A.R.V.I.S", text: "CONNECTION LOST" });
      setHasWokenUp(false);
    };

    return () => socket.current.close();
  }, []);

  const startVoiceCommand = () => {
    if (socket.current.readyState === WebSocket.OPEN) {
      if (!hasWokenUp) setHasWokenUp(true);
      setLog({ speaker: "J.A.R.V.I.S", text: "INITIALIZING MIC FORCED..." });
      socket.current.send("START_LISTENING");
    }
  };

  return (
    <div className="dashboard-container">
      <Widget
        title="📍 LOCATION"
        defaultPos={{ x: 40, y: 40 }}
        delayIndex={1}
        hasWokenUp={hasWokenUp}
      >
        <div className="loc-data">
          <h3>Ichhapur</h3>
          <p>West Bengal, India</p>
          <div className="coords">LAT: 22.81° LNG: 88.37°</div>
        </div>
      </Widget>
      <Widget
        title="HARDWARE TELEMETRY"
        defaultPos={{ x: 40, y: 300 }}
        delayIndex={2}
        hasWokenUp={hasWokenUp}
      >
        <div className="status-grid">
          <div className="status-item">
            <Battery size={16} color="#00ffcc" /> <span>88%</span>
          </div>
          <div className="status-item">
            <Wifi size={16} color="#00ffcc" /> <span>SECURE</span>
          </div>
          <div className="status-item">
            <MapPin size={16} color="#00ffcc" /> <span>SYNCED</span>
          </div>
          <div className="status-item">
            <Bluetooth size={16} color="#00ffcc" /> <span>AUDIO_ON</span>
          </div>
        </div>
      </Widget>
      <Widget
        title="SYSTEM PROTOCOLS"
        defaultPos={{ x: window.innerWidth - 340, y: 40 }}
        delayIndex={3}
        hasWokenUp={hasWokenUp}
      >
        <ul className="checklist">
          <li>
            SYSTEM ONLINE <span className="dot on"></span>
          </li>
          <li>
            J.A.R.V.I.S. ACTIVE{" "}
            <span className={`dot ${hasWokenUp ? "on" : "off"}`}></span>
          </li>
          <li>
            MICROPHONE{" "}
            <span
              className={`dot ${status !== "offline" ? "on" : "off"}`}
            ></span>
          </li>
          <li>
            TTS SPEAKING{" "}
            <span
              className={`dot ${status === "speaking" || status === "executing" ? "on" : "off"}`}
            ></span>
          </li>
          <li>
            WAKE DETECTED{" "}
            <span className={`dot ${hasWokenUp ? "on" : "off"}`}></span>
          </li>
          <li>
            API CONNECTION <span className="dot on"></span>
          </li>
        </ul>
      </Widget>
      <Widget
        title="SYSTEM_INFO 🟢"
        defaultPos={{ x: window.innerWidth - 340, y: 415 }}
        delayIndex={4}
        hasWokenUp={hasWokenUp}
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
              <br />
              {commandCount}
            </div>
          </div>
        </div>
      </Widget>
      <div
        className={`center-visualizer ${isInitialLoad ? "blob-loading" : "blob-ready"}`}
        onClick={startVoiceCommand}
      >
        <Visualizer status={status} />
      </div>
      <div
        className={`greeting-box ${hasWokenUp ? "fade-in" : "hidden-start"}`}
      >
        <h2>
          <Typewriter text={getGreeting()} delay={80} start={hasWokenUp} />
        </h2>
        <p>Standing by for instructions.</p>
        <span className="signature">- J.A.R.V.I.S. -</span>
      </div>
      {/* ${hasWokenUp ? "slide-up" : "hidden-start"}` */}
      <div className={`system-log-horizontal `}>
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
