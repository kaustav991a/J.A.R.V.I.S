import React, { useState, useEffect, useRef } from "react";
import Draggable from "react-draggable";
import { Battery, Wifi, Bluetooth, MapPin, Sun } from "lucide-react";
import Visualizer from "./components/Visualizer";
import "./App.scss";

// 1. The Robust Typewriter Hook
const useTypewriter = (text, speed = 30) => {
  const [displayedText, setDisplayedText] = useState("");

  useEffect(() => {
    if (!text) {
      setDisplayedText("");
      return;
    }

    let i = 0;
    setDisplayedText("");

    const timer = setInterval(() => {
      i++;
      setDisplayedText(text.slice(0, i));

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

  // --- NEW: Session State Tracker ---
  const [activeUser, setActiveUser] = useState("KAUSTAV");

  const [searchResult, setSearchResult] = useState("");
  const [searchImage, setSearchImage] = useState(null);
  const [isSearchPanelOpen, setIsSearchPanelOpen] = useState(false);

  const [tvData, setTvData] = useState({
    status: "standby",
    power: "unknown",
    app: "none",
  });
  const [isPollingTv, setIsPollingTv] = useState(false);

  const [logSpeaker, setLogSpeaker] = useState("SYSTEM");
  const [logTextRaw, setLogTextRaw] = useState(
    "SYSTEM OFFLINE // STANDBY FOR VOICE INPUT",
  );

  const typedLogText = useTypewriter(
    logTextRaw,
    logSpeaker === "SYSTEM" ? 15 : 35,
  );

  const socket = useRef(null);

  // Live Clock
  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // --- THE BOOT TRACKER ---
  useEffect(() => {
    const lastBoot = localStorage.getItem("jarvis_last_boot");
    const now = Date.now();
    const hoursSinceLastBoot = lastBoot
      ? (now - parseInt(lastBoot)) / (1000 * 60 * 60)
      : 99;

    setTimeout(() => {
      setIsInitialLoad(false);

      if (hoursSinceLastBoot > 4) {
        setLogSpeaker("SYSTEM");
        setLogTextRaw("EXECUTING FULL WAKE SEQUENCE AND SYSTEM BRIEFING...");
      } else {
        setLogSpeaker("SYSTEM");
        setLogTextRaw("SYSTEMS WARM. RESUMING SESSION.");
      }

      localStorage.setItem("jarvis_last_boot", now.toString());
    }, 1000);
  }, []);

  // --- IDLE CHATTER PROTOCOL ---
  useEffect(() => {
    if (status !== "online" || logSpeaker !== "SYSTEM") return;

    const idleMessages = [
      "Running background diagnostics on local subnet...",
      "Optimizing memory cache...",
      "Monitoring local atmospheric data in West Bengal...",
      "Awaiting verbal input...",
      "Checking local port configurations...",
    ];

    const chatterTimer = setInterval(() => {
      if (Math.random() > 0.7) {
        const randomMsg =
          idleMessages[Math.floor(Math.random() * idleMessages.length)];
        setLogTextRaw(`[IDLE] ${randomMsg}`);
      }
    }, 15000);

    return () => clearInterval(chatterTimer);
  }, [status, logSpeaker]);

  // --- MANUAL TV POLL FUNCTION ---
  const fetchTvStatus = async () => {
    setIsPollingTv(true);
    try {
      const response = await fetch("http://127.0.0.1:8000/api/tv/status");
      if (response.ok) {
        const data = await response.json();
        setTvData(data);
      }
    } catch (error) {
      setTvData({ status: "offline", power: "unknown", app: "error" });
    }
    setIsPollingTv(false);
  };

  // --- THE CONTEXT ENGINE (Dynamically switches based on User) ---
  const getSmartGreeting = () => {
    const hour = time.getHours();
    const day = time.getDay();
    const isWeekend = day === 0 || day === 6;

    // Dynamically assign the honorific
    let title = "Sir";
    if (activeUser === "MOUSUMI") title = "Madam";
    else if (activeUser === "KINSHUK") title = "Kinshuk";

    if (hour >= 1 && hour < 4) {
      return `It is quite late, ${title}. Even synthetic systems require downtime. I advise you get some rest.`;
    } else if (hour >= 4 && hour < 12) {
      if (!isWeekend)
        return `Good morning, ${title}. Traffic protocols and office schedules are standing by.`;
      return `Good morning, ${title}. The weekend is yours. What shall we build today?`;
    } else if (hour >= 12 && hour < 17) {
      return `Good afternoon, ${title}.`;
    } else if (hour >= 17 && hour < 21) {
      if (!isWeekend)
        return `Good evening, ${title}. I hope the office was tolerable today.`;
      return `Good evening, ${title}.`;
    } else {
      if (!isWeekend)
        return `Welcome back from the office, ${title}. Systems are primed for your evening projects.`;
      return `Good evening, ${title}. Ready for tonight's session.`;
    }
  };

  // --- WEBSOCKET LOGIC ---
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

      // --- NEW: Intercept the active user from the backend ---
      if (data.user) {
        setActiveUser(data.user);
      }

      if (data.status) {
        setStatus(data.status);

        // --- STICKY SECURITY BARRIER ---
        if (data.status === "offline" || data.status.startsWith("security_")) {
          setHasWokenUp(false);
        }

        if (
          ["booting", "waking", "online", "listening", "calibrating"].includes(
            data.status,
          )
        ) {
          setHasWokenUp(true);
        }

        if (data.status === "complete") {
          setCommandCount((prev) => prev + 1);
        }

        if (data.status === "search_result") {
          setSearchResult(data.result);
          setSearchImage(null);
          setIsSearchPanelOpen(true);
        }

        if (data.status === "search_result_image") {
          setSearchResult(`Displaying visual data for: ${data.title}`);
          setSearchImage(data.url);
          setIsSearchPanelOpen(true);
        }

        if (data.status === "close_search") {
          setIsSearchPanelOpen(false);
          setTimeout(() => setSearchImage(null), 600);
        }
      }

      let textContent = data.message || data.text;
      if (data.status === "executing" && data.intent) {
        textContent = `EXEC_PROTOCOL: ${data.intent.action_type.toUpperCase()}`;
      }

      if (textContent) {
        let currentSpeaker = "J.A.R.V.I.S";

        if (
          ["booting", "uplinking", "uplink_established", "offline"].includes(
            data.status,
          )
        ) {
          currentSpeaker = "SYSTEM";
        } else if (
          ["calibrating", "listening", "security_listening"].includes(
            data.status,
          )
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

  const isTvOnline = tvData.status === "online";
  const isTvOn = tvData.power === "on";
  const tvStatusColor =
    tvData.status === "standby"
      ? "#888"
      : isTvOnline
        ? isTvOn
          ? "#10B981"
          : "#F59E0B"
        : "#EF4444";

  const showSystemLog = hasWokenUp || status.startsWith("security_");

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
        defaultPos={{ x: 40, y: 250 }}
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
        title="TV UPLINK 📺"
        defaultPos={{ x: 40, y: 430 }}
        delayIndex={5}
        hasWokenUp={hasWokenUp}
      >
        <div
          style={{
            fontFamily: "monospace",
            lineHeight: "1.8",
            fontSize: "14px",
          }}
        >
          <div
            style={{
              display: "flex",
              justify: "space-between",
              marginBottom: "8px",
            }}
          >
            <span>NETWORK:</span>
            <span style={{ color: tvStatusColor, fontWeight: "bold" }}>
              {tvData.status.toUpperCase()}
            </span>
          </div>
          {isTvOnline && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span>POWER:</span>
                <span style={{ color: isTvOn ? "#00ffcc" : "#F59E0B" }}>
                  {tvData.power.toUpperCase()}
                </span>
              </div>
              {isTvOn && (
                <div
                  style={{
                    display: "flex",
                    justify: "space-between",
                    marginTop: "4px",
                  }}
                >
                  <span>ACTIVE:</span>
                  <span style={{ color: "#3B82F6", fontWeight: "bold" }}>
                    {tvData.app}
                  </span>
                </div>
              )}
            </>
          )}

          <button
            onClick={fetchTvStatus}
            disabled={isPollingTv}
            style={{
              width: "100%",
              padding: "8px",
              marginTop: "12px",
              backgroundColor: isPollingTv ? "#333" : "#1E3A8A",
              color: "#FFF",
              border: "1px solid #3B82F6",
              borderRadius: "4px",
              cursor: isPollingTv ? "wait" : "pointer",
              fontFamily: "monospace",
              fontWeight: "bold",
              transition: "background-color 0.2s",
            }}
          >
            {isPollingTv ? "SCANNING NETWORK..." : "SCAN TV STATUS"}
          </button>
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
        <h2>{getSmartGreeting()}</h2>
        <p>Standing by for instructions.</p>
        <span className="signature">- J.A.R.V.I.S. -</span>
      </div>

      <div
        className={`satellite-panel ${isSearchPanelOpen ? "panel-open" : "panel-closed"}`}
      >
        <div className="panel-header">SATELLITE DATA LINK</div>
        <div className="panel-body">
          {searchImage && (
            <div className="image-container">
              <img src={searchImage} alt="Search Result" />
            </div>
          )}
          <p className="search-text">{useTypewriter(searchResult, 40)}</p>
        </div>
      </div>

      <div
        className={`system-log-horizontal ${showSystemLog ? "slide-up" : "hidden-start"}`}
      >
        <div className="log-header">
          <span
            className={
              status.includes("processing") || status.includes("locked")
                ? "spinner-dot"
                : "pulse-dot"
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
