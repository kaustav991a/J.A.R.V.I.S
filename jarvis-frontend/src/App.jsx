import React, { useState, useEffect, useRef } from "react";
import Draggable from "react-draggable";
import { Cpu, HardDrive, MemoryStick, Wifi, MapPin, Sun, Activity, Calendar, Mail } from "lucide-react";
import Visualizer from "./components/Visualizer";
import HudReticle from "./components/HudReticle";
import CalculatorWidget from "./components/CalculatorWidget";
import NotepadWidget from "./components/NotepadWidget";
import BrowserWidget from "./components/BrowserWidget";
import TypewriterText from "./components/TypewriterText";
import ClockWidget from "./components/ClockWidget";
import EmailWidget from "./components/EmailWidget";
import CalendarWidget from "./components/CalendarWidget";
import HealthWidget from "./components/HealthWidget";
import IntroductionCeremony from "./components/IntroductionCeremony";
import FirstBootSequence from "./components/FirstBootSequence";
import FaceScanOverlay from "./components/FaceScanOverlay";
import UplinkOverlay from "./components/UplinkOverlay";
import LockdownOverlay from "./components/LockdownOverlay";
import "./App.scss";

// 2. Upgraded Widget Wrapper
const Widget = ({ title, children, defaultPos, delayIndex, hasWokenUp, isFlush }) => {
  const [isMoveMode, setIsMoveMode] = useState(false);
  const nodeRef = useRef(null);

  // Load saved position from localStorage (v3 to reset overlapping layouts)
  const savedPos = localStorage.getItem(`widget_pos_v3_${title}`);
  const initialPos = savedPos ? JSON.parse(savedPos) : defaultPos;

  const handleContextMenu = (e) => {
    e.preventDefault();
    setIsMoveMode(!isMoveMode);
  };

  const handleStop = (e, data) => {
    if (title) {
      localStorage.setItem(`widget_pos_v3_${title}`, JSON.stringify({ x: data.x, y: data.y }));
    }
  };

  return (
    <Draggable
      nodeRef={nodeRef}
      disabled={!isMoveMode}
      defaultPosition={initialPos}
      useCSSTransforms={false}
      onStop={handleStop}
    >
      <div
        ref={nodeRef}
        className={`panel widget ${isMoveMode ? "move-mode-active" : ""} ${hasWokenUp ? "widget-awake" : "widget-sleep"
          } ${isFlush ? "flush-widget" : ""}`}
        style={{ animationDelay: `${delayIndex * 0.15}s` }}
        onContextMenu={handleContextMenu}
      >
        {isMoveMode && <div className="move-badge">▤ MOVE MODE</div>}
        {title && <div className="panel-header">{title}</div>}
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
  const [status, setStatus] = useState("offline");
  const [weather, setWeather] = useState({ temp: "--", condition: "Unknown" });

  const [hasWokenUp, setHasWokenUp] = useState(false);
  const hasWokenUpRef = useRef(false); // Ref mirror to avoid stale closure in WebSocket handler
  const [commandCount, setCommandCount] = useState(0);
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  // --- Phase 5a: Web Widget State ---
  const [isCalculatorOpen, setIsCalculatorOpen] = useState(false);
  const [isNotepadOpen, setIsNotepadOpen] = useState(false);
  const [isBrowserOpen, setIsBrowserOpen] = useState(false);
  const [browserUrl, setBrowserUrl] = useState("");

  // --- NEW: Backdoor Command State ---
  const [backdoorCommand, setBackdoorCommand] = useState("");

  // --- NEW: Session State Tracker ---
  const [activeUser, setActiveUser] = useState("KAUSTAV");

  const [searchResult, setSearchResult] = useState("");
  const [searchImage, setSearchImage] = useState(null);
  const [isSearchPanelOpen, setIsSearchPanelOpen] = useState(false);

  // --- Introduction Ceremony State ---
  const [isCeremonyActive, setIsCeremonyActive] = useState(false);
  const [isFirstBoot, setIsFirstBoot] = useState(false);
  const [isFaceScanning, setIsFaceScanning] = useState(false);
  const [isLockdown, setIsLockdown] = useState(false);

  const [tvData, setTvData] = useState({
    status: "standby",
    power: "unknown",
    app: "none",
  });
  const [isPollingTv, setIsPollingTv] = useState(false);

  // --- Phase 4: Real System Telemetry ---
  const [telemetry, setTelemetry] = useState({
    cpu_percent: 0, ram_percent: 0, ram_used_gb: 0,
    ram_total_gb: 0, disk_percent: 0, disk_free_gb: 0,
    uptime_hours: 0, status: "LOADING"
  });

  const [logSpeaker, setLogSpeaker] = useState("SYSTEM");
  const [logTextRaw, setLogTextRaw] = useState(
    "SYSTEM OFFLINE // STANDBY FOR VOICE INPUT",
  );

  const socket = useRef(null);

  // --- Keep ref in sync with state ---
  useEffect(() => {
    hasWokenUpRef.current = hasWokenUp;
  }, [hasWokenUp]);

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

  // --- Phase 4: Telemetry Polling (every 30s) ---
  useEffect(() => {
    const fetchTelemetry = async () => {
      try {
        const res = await fetch("http://127.0.0.1:8000/api/telemetry");
        if (res.ok) setTelemetry(await res.json());
      } catch (e) { /* silent */ }
    };
    fetchTelemetry();
    const timer = setInterval(fetchTelemetry, 30000);
    return () => clearInterval(timer);
  }, []);

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

      // --- Phase 4: Intercept telemetry sync ---
      if (data.status === "sync" && data.type === "telemetry") {
        setTelemetry(data.data);
      }

      // --- NEW: Intercept the active user from the backend ---
      if (data.user) {
        setActiveUser(data.user);
      }

      // --- Phase 4: Ignore proactive messages if the UI is asleep ---
      if (data.is_proactive && !hasWokenUpRef.current) {
        // Do nothing. Allow JARVIS to speak in the background without unlocking the UI.
        return;
      }

      if (data.status) {
        setStatus(data.status);

        // --- STICKY SECURITY BARRIER ---
        if (data.status === "offline" || data.status.startsWith("security_")) {
          setHasWokenUp(false);
        }

        if (data.status === "security_override") {
          setIsLockdown(true);
        } else {
          setIsLockdown(false);
        }

        if (data.status === "security_locked" && data.message?.includes("OPTICAL SENSORS")) {
          setIsFaceScanning(true);
        } else if (data.status !== "security_locked" || !data.message?.includes("OPTICAL SENSORS")) {
          setIsFaceScanning(false);
        }

        if (
          ["booting", "waking", "online", "listening", "calibrating"].includes(
            data.status,
          )
        ) {
          setHasWokenUp(true);
        }

        // --- NEW: Introduce Yourself Protocol ---
        if (data.status === "introduce_yourself") {
          setIsFirstBoot(true);
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

        if (data.status === "play_youtube") {
          setBrowserUrl(data.url);
          setIsBrowserOpen(true);
        }

        if (data.status === "close_search") {
          setIsSearchPanelOpen(false);
          setIsBrowserOpen(false); // Also close browser widget on display clear
          setTimeout(() => setSearchImage(null), 600);
        }

        // --- Phase 8: HUD Widget Toggles ---
        if (data.status === "toggle_notepad") {
          setIsNotepadOpen(data.visible);
        }
        if (data.status === "toggle_browser") {
          setIsBrowserOpen(data.visible);
        }
        if (data.status === "toggle_calculator") {
          setIsCalculatorOpen(data.visible);
        }

        // --- Introduction Ceremony ---
        if (data.status === "introduction_ceremony") {
          setIsCeremonyActive(true);
          setHasWokenUp(true);
        }
        if (data.status === "introduction_complete") {
          setIsCeremonyActive(false);
        }
      }

      let textContent = data.message || data.text;

      if (textContent) {
        let currentSpeaker = "J.A.R.V.I.S";

        if (
          ["booting", "uplinking", "uplink_established", "offline"].includes(
            data.status,
          )
        ) {
          currentSpeaker = "SYSTEM";
        } else if (
          ["calibrating", "listening", "security_listening", "processing_llm"].includes(
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

  const sendBackdoorCommand = async () => {
    if (!backdoorCommand.trim()) return;
    try {
      await fetch("http://127.0.0.1:8000/api/backdoor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: backdoorCommand })
      });
      setBackdoorCommand("");
    } catch (e) {
      console.error("Backdoor error:", e);
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
    <>
      <div className={`dashboard-container ${status === "offline" ? "power-down" : ""}`}>
        <FirstBootSequence 
        isActive={isFirstBoot} 
        onComplete={() => {
          setIsFirstBoot(false);
          setHasWokenUp(false);
          setStatus("offline");
          setLogSpeaker("SYSTEM");
          setLogTextRaw("SYSTEM OFFLINE // AWAITING WAKE COMMAND");
        }} 
      />
      <FaceScanOverlay isActive={isFaceScanning} />
      <UplinkOverlay isActive={status === "processing_llm" || status === "searching"} />
      <LockdownOverlay isActive={isLockdown} />
      <IntroductionCeremony isActive={isCeremonyActive} onComplete={() => setIsCeremonyActive(false)} />
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
        defaultPos={{ x: 40, y: 220 }}
        delayIndex={2}
        hasWokenUp={hasWokenUp}
      >
        <div className="telemetry-panel">
          <div className="telemetry-row">
            <div className="telemetry-label"><Cpu size={14} color="#00ffcc" /> CPU</div>
            <div className="telemetry-bar-track">
              <div className="telemetry-bar-fill" style={{ width: `${telemetry.cpu_percent}%`, background: telemetry.cpu_percent > 80 ? '#ff3366' : telemetry.cpu_percent > 50 ? '#F59E0B' : '#00ffcc' }} />
            </div>
            <span className="telemetry-value">{telemetry.cpu_percent}%</span>
          </div>
          <div className="telemetry-row">
            <div className="telemetry-label"><MemoryStick size={14} color="#00ffcc" /> RAM</div>
            <div className="telemetry-bar-track">
              <div className="telemetry-bar-fill" style={{ width: `${telemetry.ram_percent}%`, background: telemetry.ram_percent > 85 ? '#ff3366' : telemetry.ram_percent > 65 ? '#F59E0B' : '#00ffcc' }} />
            </div>
            <span className="telemetry-value">{telemetry.ram_used_gb}/{telemetry.ram_total_gb}G</span>
          </div>
          <div className="telemetry-row">
            <div className="telemetry-label"><HardDrive size={14} color="#00ffcc" /> DISK</div>
            <div className="telemetry-bar-track">
              <div className="telemetry-bar-fill" style={{ width: `${telemetry.disk_percent}%`, background: telemetry.disk_percent > 90 ? '#ff3366' : '#00ffcc' }} />
            </div>
            <span className="telemetry-value">{telemetry.disk_free_gb}G free</span>
          </div>
          <div className="telemetry-row">
            <div className="telemetry-label"><Activity size={14} color={telemetry.status === 'NOMINAL' ? '#00ffcc' : telemetry.status === 'ELEVATED' ? '#F59E0B' : '#ff3366'} /> STATUS</div>
            <span className="telemetry-status" style={{ color: telemetry.status === 'NOMINAL' ? '#00ffcc' : telemetry.status === 'ELEVATED' ? '#F59E0B' : '#ff3366' }}>{telemetry.status}</span>
          </div>
        </div>
      </Widget>

      <Widget
        title="TV UPLINK 📺"
        defaultPos={{ x: 380, y: 40 }}
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

      {activeUser === "KAUSTAV" && (
        <Widget
          title="📧 COMM LINK"
          defaultPos={{ x: 380, y: 250 }}
          delayIndex={6}
          hasWokenUp={hasWokenUp}
        >
          <EmailWidget />
        </Widget>
      )}

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
        defaultPos={{ x: window.innerWidth - 340, y: 320 }}
        delayIndex={4}
        hasWokenUp={hasWokenUp}
      >
        <div className="info-panel-content">
          <ClockWidget />
          <div className="weather-display">
            <Sun size={24} color="#ffcc00" />
            <h2>{weather.temp}°C</h2>
          </div>
          <div className="stats-row">
            <div>
              <span>UPTIME</span>
              <br />
              {telemetry.uptime_hours}h
            </div>
            <div>
              <span>COMMANDS</span>
              <br />
              {commandCount}
            </div>
          </div>
        </div>
      </Widget>

      {activeUser === "KAUSTAV" && (
        <>
          <Widget
            title="📅 SCHEDULE"
            defaultPos={{ x: window.innerWidth - 680, y: 40 }}
            delayIndex={7}
            hasWokenUp={hasWokenUp}
          >
            <CalendarWidget />
          </Widget>

          <Widget
            title="🧬 VITALS"
            defaultPos={{ x: window.innerWidth - 680, y: 270 }}
            delayIndex={8}
            hasWokenUp={hasWokenUp}
          >
            <HealthWidget />
          </Widget>
        </>
      )}

      <div className={`center-visualizer ${isInitialLoad ? "blob-loading" : "blob-ready"}`}>
        <HudReticle />
        <div style={{ zIndex: 10, position: "relative" }}>
          <Visualizer status={status} />
        </div>
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
          <p className="search-text"><TypewriterText text={searchResult} speed={40} /></p>
        </div>
      </div>

      {isCalculatorOpen && (
        <Widget
          title="CALCULATOR"
          defaultPos={{ x: window.innerWidth / 2 - 130, y: 150 }}
          delayIndex={0}
          hasWokenUp={true}
          isFlush={true}
        >
          <CalculatorWidget />
        </Widget>
      )}

      {isNotepadOpen && (
        <Widget
          title="SECURE NOTEPAD"
          defaultPos={{ x: window.innerWidth / 2 + 150, y: 150 }}
          delayIndex={0}
          hasWokenUp={true}
          isFlush={true}
        >
          <NotepadWidget />
        </Widget>
      )}

      {isBrowserOpen && (
        <Widget
          title="BROWSER LINK"
          defaultPos={{ x: window.innerWidth - 680, y: window.innerHeight - 560 }}
          delayIndex={7}
          hasWokenUp={hasWokenUp}
          isFlush={true}
        >
          <BrowserWidget externalUrl={browserUrl} />
        </Widget>
      )}

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
          <TypewriterText text={logTextRaw} speed={logSpeaker === "SYSTEM" ? 15 : 35} />
          <div className="cursor-block"></div>
        </div>
      </div>
      </div>

      {/* --- BACKDOOR DEV UI --- */}
      <div className="backdoor-panel">
        <input 
          type="text" 
          placeholder="[DEV] Enter Command..." 
          value={backdoorCommand}
          onChange={(e) => setBackdoorCommand(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') sendBackdoorCommand(); }}
        />
        <button onClick={sendBackdoorCommand}>EXECUTE</button>
        <button onClick={() => setIsCalculatorOpen(!isCalculatorOpen)} style={{ background: isCalculatorOpen ? '#00ffcc' : 'transparent', color: isCalculatorOpen ? '#000' : '#00ffcc' }}>CALC</button>
        <button onClick={() => setIsNotepadOpen(!isNotepadOpen)} style={{ background: isNotepadOpen ? '#00ffcc' : 'transparent', color: isNotepadOpen ? '#000' : '#00ffcc' }}>NOTES</button>
        <button onClick={() => setIsBrowserOpen(!isBrowserOpen)} style={{ background: isBrowserOpen ? '#00ffcc' : 'transparent', color: isBrowserOpen ? '#000' : '#00ffcc' }}>BROWSER</button>
      </div>
    </>
  );
}

export default App;
