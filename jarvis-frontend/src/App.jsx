import React, { useState, useEffect, useRef } from "react";
import Draggable from "react-draggable";
import { Battery, Wifi, Bluetooth, MapPin, Sun } from "lucide-react";
import Visualizer from "./components/Visualizer";
import "./App.scss";

// 1. The Robust Typewriter Hook (Handles rapid resets)
const useTypewriter = (text, speed = 30) => {
  const [displayedText, setDisplayedText] = useState("");

  useEffect(() => {
    if (!text) {
      setDisplayedText("");
      return;
    }

    let i = 0;
    setDisplayedText(""); // Clear immediately when new text arrives

    const timer = setInterval(() => {
      i++; // Increment first
      setDisplayedText(text.slice(0, i)); // Slice directly from the source text

      if (i >= text.length) {
        clearInterval(timer);
      }
    }, speed);

    return () => clearInterval(timer);
  }, [text, speed]);

  return displayedText;
};

// 2. Upgraded Widget Wrapper
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
  const [weather, setWeather] = useState({ temp: "--", condition: "Unknown" });

  const [hasWokenUp, setHasWokenUp] = useState(false);
  const [commandCount, setCommandCount] = useState(0);
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  // 3. New State: Search Panel & Image
  const [searchResult, setSearchResult] = useState("");
  const [searchImage, setSearchImage] = useState(null); // Holds the image URL
  const [isSearchPanelOpen, setIsSearchPanelOpen] = useState(false);

  // Log State for the Typewriter
  const [logSpeaker, setLogSpeaker] = useState("J.A.R.V.I.S");
  const [logTextRaw, setLogTextRaw] = useState(
    "SYSTEM OFFLINE // STANDBY FOR VOICE INPUT",
  );

  // Apply Typewriter Hook to the raw text (Speed changes based on who is talking)
  const typedLogText = useTypewriter(
    logTextRaw,
    logSpeaker === "SYSTEM" ? 15 : 35,
  );

  const socket = useRef(null);

  // Live Clock & Init Load
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

  // 4. WebSocket Logic (Handling Phase 2/3 Commands)
  useEffect(() => {
    socket.current = new WebSocket("ws://127.0.0.1:8000/ws");

    socket.current.onopen = () => {
      setStatus("online");
    };

    socket.current.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.status === "sync" && data.type === "weather") {
        setWeather(data.data);
      }

      if (data.status) {
        setStatus(data.status);

        // Triggers the UI "Wake" state
        if (
          ["waking", "calibrating", "listening", "booting"].includes(
            data.status,
          )
        ) {
          setHasWokenUp(true);
        }

        if (data.status === "complete") {
          setCommandCount((prev) => prev + 1);
        }

        // --- PHASE 3: Triggering the Search Panel (Text) ---
        if (data.status === "search_result") {
          setSearchResult(data.result);
          setSearchImage(null); // Clear any previous image
          setIsSearchPanelOpen(true);
        }

        // --- PHASE 3: Triggering the Search Panel (Image) ---
        if (data.status === "search_result_image") {
          setSearchResult(`Displaying visual data for: ${data.title}`);
          setSearchImage(data.url); // Set the new image
          setIsSearchPanelOpen(true);
        }

        // --- PHASE 3: Closing the Search Panel manually ---
        if (data.status === "close_search") {
          setIsSearchPanelOpen(false);
          setTimeout(() => setSearchImage(null), 600); // Clear image after slide-up animation
        }
      }

      // --- Setting the Terminal Text ---
      let textContent = data.message || data.text;
      if (data.status === "executing" && data.intent) {
        textContent = `EXEC_PROTOCOL: ${data.intent.action_type.toUpperCase()}`;
      }

      if (textContent) {
        let currentSpeaker = "J.A.R.V.I.S";

        // Use "SYSTEM" speaker for boot sequences for a faster typewriter speed
        if (
          ["booting", "uplinking", "uplink_established"].includes(data.status)
        ) {
          currentSpeaker = "SYSTEM";
        } else if (
          data.status === "calibrating" ||
          data.status === "listening"
        ) {
          currentSpeaker = "USER";
        } else if (
          data.status === "search_result" ||
          data.status === "search_result_image"
        ) {
          currentSpeaker = "SYSTEM";
        }

        setLogSpeaker(currentSpeaker);
        setLogTextRaw(textContent);
      }
    };

    socket.current.onclose = () => {
      setStatus("offline");
      setLogSpeaker("SYSTEM");
      setLogTextRaw("CRITICAL FAULT: CONNECTION LOST");
      setHasWokenUp(false);
    };

    return () => socket.current.close();
  }, []);

  const startVoiceCommand = () => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      if (!hasWokenUp) setHasWokenUp(true);
      setLogSpeaker("SYSTEM");
      setLogTextRaw("INITIALIZING MIC OVERRIDE...");
      socket.current.send("START_LISTENING");
    }
  };

  return (
    <div className="dashboard-container">
      {/* Existing Widgets... */}
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
            {/* Dynamic Weather Binding */}
            <h2>{weather.temp}°C</h2>
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
        <h2>{getGreeting()}</h2>
        <p>Standing by for instructions.</p>
        <span className="signature">- J.A.R.V.I.S. -</span>
      </div>

      {/* --- PHASE 3: The Secondary HUD (Text + Image) --- */}
      <div
        className={`satellite-panel ${isSearchPanelOpen ? "panel-open" : "panel-closed"}`}
      >
        <div className="panel-header">SATELLITE DATA LINK</div>
        <div className="panel-body">
          {/* If there is an image, display it */}
          {searchImage && (
            <div className="image-container">
              <img src={searchImage} alt="Search Result" />
            </div>
          )}
          {/* Using a separate typewriter hook so it types out slowly as he reads it */}
          <p className="search-text">{useTypewriter(searchResult, 40)}</p>
        </div>
      </div>

      {/* --- PHASE 2: The Animated Terminal --- */}
      <div
        className={`system-log-horizontal ${hasWokenUp ? "slide-up" : "hidden-start"}`}
      >
        <div className="log-header">
          {/* Loader animation when thinking */}
          <span
            className={
              status === "processing_llm" ? "spinner-dot" : "pulse-dot"
            }
          ></span>
          SYSTEM_LOG // STATUS: {status.toUpperCase()}
        </div>
        <div className="log-text">
          <span className="speaker-tag">&gt; {logSpeaker} &gt;</span>{" "}
          {typedLogText}
          <div className="cursor-block"></div>
        </div>
      </div>
    </div>
  );
}

export default App;
