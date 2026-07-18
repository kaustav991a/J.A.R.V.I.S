import React, { useState, useEffect, useRef, useMemo } from "react";
import Draggable from "react-draggable";
import { motion, AnimatePresence } from "framer-motion";
import Visualizer from "./components/Visualizer";
import HudReticle from "./components/HudReticle";
import CalculatorWidget from "./components/CalculatorWidget";
import NotepadWidget from "./components/NotepadWidget";
import BrowserWidget from "./components/BrowserWidget";
import HealthWidget from "./components/HealthWidget";
import EmailWidget from "./components/EmailWidget";
import CalendarWidget from "./components/CalendarWidget";
import CameraFeedWidget from "./components/CameraFeedWidget";
import MapWidget from "./components/MapWidget";
import ScanlineTransition from "./components/ScanlineTransition";
import TypewriterText from "./components/TypewriterText";
import { MinimalHudClock } from "./components/ClockWidget";
import IntroductionCeremony from "./components/IntroductionCeremony";
import FirstBootSequence from "./components/FirstBootSequence";
import FaceScanOverlay from "./components/FaceScanOverlay";
import UplinkOverlay from "./components/UplinkOverlay";
import LockdownOverlay from "./components/LockdownOverlay";
import ScreenScanOverlay from "./components/ScreenScanOverlay";
import DataOverlay from "./components/DataOverlay";
import ChatPanel from "./components/ChatPanel";
import GestureGuide from "./components/GestureGuide";
import GestureChip from "./components/GestureChip";
import MicIndicator from "./components/MicIndicator";
import TaskHud from "./components/TaskHud";
import { API_BASE, WS_BASE, API_HOST } from "./api";
import "./App.scss";

// Background-worker lifecycle events broadcast by the Overnight Worker / Autopilot.
const TASK_EVENTS = [
  "task_started", "task_done", "task_failed", "task_needs_confirmation", "task_report",
  "autopilot_started", "autopilot_done", "autopilot_failed",
];

const HUD_EASE = [0.16, 1, 0.3, 1];
const HUD_T_DURATION = 0.55;

const MAX_CHAT_MESSAGES = 100;

/**
 * Append a transcript line, collapsing streaming partials.
 * The backend streams synthesis cumulatively (each "complete" carries the full
 * assembled text so far), so when the newest line extends the previous same-speaker
 * line we REPLACE it instead of appending a duplicate. Distinct lines are appended.
 */
function mergeChat(prev, speaker, rawText) {
  const text = (rawText || "").trim();
  if (!text) return prev;
  const last = prev[prev.length - 1];
  if (last && last.speaker === speaker) {
    if (text === last.text) return prev;
    if (text.startsWith(last.text) || last.text.startsWith(text)) {
      const copy = prev.slice();
      copy[copy.length - 1] = { ...last, text };
      return copy;
    }
  }
  const next = [...prev, { id: (last?.id ?? 0) + 1, speaker, text }];
  return next.length > MAX_CHAT_MESSAGES ? next.slice(-MAX_CHAT_MESSAGES) : next;
}

// 2. Upgraded Widget Wrapper
const Widget = ({
  title,
  children,
  defaultPos,
  delayIndex,
  hasWokenUp,
  isFlush,
  glassModular = false,
  modularSpawn = false,
  wide = false,
}) => {
  const [isMoveMode, setIsMoveMode] = useState(false);
  const nodeRef = useRef(null);

  const savedPos = localStorage.getItem(`widget_pos_v3_${title}`);
  const initialPos = savedPos ? JSON.parse(savedPos) : defaultPos;

  // Keep the widget inside the viewport — a position saved on a big monitor must
  // not render off-screen (unrecoverable) on a smaller one. Leaves >=60px on-screen.
  const clampPos = (p) => {
    const margin = 60;
    const maxX = Math.max(0, window.innerWidth - margin);
    const maxY = Math.max(0, window.innerHeight - margin);
    return {
      x: Math.min(Math.max(p?.x ?? 0, 0), maxX),
      y: Math.min(Math.max(p?.y ?? 0, 0), maxY),
    };
  };

  const [pos, setPos] = useState(() => clampPos(initialPos));

  // Re-clamp on window resize so a shrink can't strand a widget off-screen.
  useEffect(() => {
    const onResize = () => setPos((p) => clampPos(p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const handleContextMenu = (e) => {
    e.preventDefault();
    setIsMoveMode(!isMoveMode);
  };

  const handleDrag = (e, data) => setPos({ x: data.x, y: data.y });

  const handleStop = (e, data) => {
    const next = { x: data.x, y: data.y };
    setPos(next);
    if (title) {
      localStorage.setItem(`widget_pos_v3_${title}`, JSON.stringify(next));
    }
  };

  const panelClass = `panel widget ${isMoveMode ? "move-mode-active" : ""} ${hasWokenUp ? "widget-awake" : "widget-sleep"
    } ${isFlush ? "flush-widget" : ""} ${glassModular ? "widget-glass-modular" : ""} ${modularSpawn ? "widget-modular-spawn" : ""
    } ${wide ? "widget--hologram-wide" : ""}`;

  return (
    <Draggable
      nodeRef={nodeRef}
      disabled={!isMoveMode}
      position={pos}
      useCSSTransforms={false}
      onDrag={handleDrag}
      onStop={handleStop}
    >
      <div
        ref={nodeRef}
        className={panelClass}
        style={{ animationDelay: `${delayIndex * 0.15}s` }}
        onContextMenu={handleContextMenu}
      >
        {isMoveMode && <div className="move-badge">▤ MOVE MODE</div>}
        {title && <div className="panel-header">{title}</div>}
        <div className="widget-content" style={{ animationDelay: `${delayIndex * 0.15 + 0.3}s` }}>
          {children}
        </div>
      </div>
    </Draggable>
  );
};

function App() {
  const [status, setStatus] = useState("offline");

  const [hasWokenUp, setHasWokenUp] = useState(false);
  const hasWokenUpRef = useRef(false); // Ref mirror to avoid stale closure in WebSocket handler
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  // --- Phase 5a: Web Widget State ---
  const [isCalculatorOpen, setIsCalculatorOpen] = useState(false);
  const [isNotepadOpen, setIsNotepadOpen] = useState(false);
  const [isBrowserOpen, setIsBrowserOpen] = useState(false);
  const [browserUrl, setBrowserUrl] = useState("");
  const [isHealthWidgetOpen, setIsHealthWidgetOpen] = useState(false);
  const [isMailWidgetOpen, setIsMailWidgetOpen] = useState(false);
  const [isCalendarWidgetOpen, setIsCalendarWidgetOpen] = useState(false);
  const [isCameraOpen, setIsCameraOpen] = useState(false);
  const [isMapOpen, setIsMapOpen] = useState(false);
  const [mapQuery, setMapQuery] = useState("");

  // --- TAB SLIDER: Tab 0 = JARVIS circle, Tab 1 = full-screen activity stage ---
  // Auto-slides to the stage when a full-screen surface (camera / video / map)
  // opens; manual dots let the user slide back to the circle without closing it.
  const [activeTab, setActiveTab] = useState(0);
  const [lastStage, setLastStage] = useState(null); // which stage was opened most recently

  // --- NEW: Backdoor Command State ---
  const [backdoorCommand, setBackdoorCommand] = useState("");

  const [searchResult, setSearchResult] = useState("");
  const [searchImage, setSearchImage] = useState(null);
  const [isSearchPanelOpen, setIsSearchPanelOpen] = useState(false);

  // --- Introduction Ceremony State ---
  const [isCeremonyActive, setIsCeremonyActive] = useState(false);
  const [isFirstBoot, setIsFirstBoot] = useState(false);
  const [isFaceScanning, setIsFaceScanning] = useState(false);
  const [isScreenScanning, setIsScreenScanning] = useState(false);
  const [isLockdown, setIsLockdown] = useState(false);

  const [logSpeaker, setLogSpeaker] = useState("SYSTEM");
  const [logTextRaw, setLogTextRaw] = useState(
    "SYSTEM OFFLINE // STANDBY FOR VOICE INPUT",
  );
  /** Phase 8.4: backend `ui_state` can surface a transient system banner (e.g. deep work). */
  const [uiBridgeLogPinned, setUiBridgeLogPinned] = useState(false);
  /** True when WebSocket cannot reach the API (usually uvicorn not running on :8000). */
  const [backendUnreachable, setBackendUnreachable] = useState(false);

  /** Structured HUD payloads: { ui_action, data } from backend (not chat / system log). */
  const [overlayData, setOverlayData] = useState(null);

  // --- COMM TRANSCRIPT (chat panel) — default hidden, toggled by command/button ---
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState([]);

  // --- AUTONOMY QUEUE (Task HUD) — polls /api/tasks; auto-opens on worker activity ---
  const [isTaskHudOpen, setIsTaskHudOpen] = useState(false);
  const [isGestureGuideOpen, setIsGestureGuideOpen] = useState(false);
  const [gestureState, setGestureState] = useState(null);
  const [taskRefresh, setTaskRefresh] = useState(0);

  const socket = useRef(null);
  const commandInputRef = useRef(null);
  const reconnectTimer = useRef(null);
  const reconnectDelay = useRef(1000);   // backoff, 1s -> 30s
  const wsWantOpen = useRef(true);        // false once the effect unmounts

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

  // --- WEBSOCKET LOGIC (auto-reconnecting) ---
  useEffect(() => {
    wsWantOpen.current = true;

    const scheduleReconnect = () => {
      if (!wsWantOpen.current || reconnectTimer.current) return;
      const delay = reconnectDelay.current;
      reconnectTimer.current = setTimeout(() => {
        reconnectTimer.current = null;
        connect();
      }, delay);
      reconnectDelay.current = Math.min(delay * 2, 30000); // exponential backoff, cap 30s
    };

    const connect = () => {
      if (!wsWantOpen.current) return;
      try {
        socket.current = new WebSocket(`${WS_BASE}/ws`);
      } catch (e) {
        setBackendUnreachable(true);
        scheduleReconnect();
        return;
      }

    socket.current.onopen = () => {
      reconnectDelay.current = 1000;   // reset backoff on a healthy connection
      setBackendUnreachable(false);
      setStatus("online");
    };

    socket.current.onerror = () => {
      setBackendUnreachable(true);
    };

    socket.current.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        console.warn("[WS] dropped non-JSON frame", err);
        return;
      }

      // --- Structured data overlay (files / processes) — bypass chat + system log ---
      if (data.ui_action) {
        setOverlayData({
          ui_action: data.ui_action,
          data: Array.isArray(data.data) ? data.data : [],
        });
        return;
      }

      // --- G3: live gesture/presence state (HUD chip + Gesture Guide practice mode) ---
      // Stamp receive-time so a staleness watcher can hide the chip if the daemon
      // goes silent (daemon sends a ~2s heartbeat, so real silence == daemon dead).
      if (data.type === "gesture_state") {
        setGestureState({ ...data, _rxAt: Date.now() });
        return;
      }

      // --- Phase 8.4: WebSocket UI bridge (macros, daemons) — runs before is_proactive gate ---
      if (data.type === "ui_state") {
        if (data.hud_phase === "standby") {
          setIsCalculatorOpen(false);
          setIsNotepadOpen(false);
          setIsBrowserOpen(false);
          setIsSearchPanelOpen(false);
          setIsHealthWidgetOpen(false);
          setIsMailWidgetOpen(false);
          setIsCalendarWidgetOpen(false);
          setIsCameraOpen(false);
          setIsMapOpen(false);
        }
        if (data.widget === "system_log") {
          if (data.state === "visible") {
            setUiBridgeLogPinned(true);
            if (data.message != null && String(data.message).trim() !== "") {
              setLogSpeaker("SYSTEM");
              setLogTextRaw(String(data.message));
            }
          } else if (data.state === "hidden") {
            setUiBridgeLogPinned(false);
          }
        }
        if (data.open_widget === "notepad") setIsNotepadOpen(true);
        if (data.open_widget === "calculator") setIsCalculatorOpen(true);
        if (data.open_widget === "vitals") setIsHealthWidgetOpen(true);
        if (data.open_widget === "mail") setIsMailWidgetOpen(true);
        if (data.open_widget === "calendar") setIsCalendarWidgetOpen(true);
        if (data.open_widget === "camera") setIsCameraOpen(true);
        if (data.open_widget === "map") {
          setIsMapOpen(true);
          if (data.map_query) setMapQuery(data.map_query);
        }
        if (data.open_widget === "browser") {
          setIsBrowserOpen(true);
          if (data.browser_url) setBrowserUrl(data.browser_url);
        }
        if (data.close_widget === "notepad") setIsNotepadOpen(false);
        if (data.close_widget === "calculator") setIsCalculatorOpen(false);
        if (data.close_widget === "vitals") setIsHealthWidgetOpen(false);
        if (data.close_widget === "mail") setIsMailWidgetOpen(false);
        if (data.close_widget === "calendar") setIsCalendarWidgetOpen(false);
        if (data.close_widget === "camera") setIsCameraOpen(false);
        if (data.close_widget === "map") setIsMapOpen(false);
        if (data.close_widget === "browser") setIsBrowserOpen(false);
        return;
      }

      // --- Autonomy Queue events: refresh the Task HUD, auto-open on new activity.
      // Handled here so worker lifecycle pings never leak into the chat / system log / status.
      if (data.status && TASK_EVENTS.includes(data.status)) {
        setTaskRefresh((n) => n + 1);
        if (data.status === "task_started" || data.status === "autopilot_started") {
          setIsTaskHudOpen(true);
        }
        return;
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

        if (data.status === "scanning_screen") {
          setIsScreenScanning(true);
        } else if (["executing", "complete", "speaking", "error"].includes(data.status)) {
          setIsScreenScanning(false);
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

        if (data.status === "satellite_uplink") {
          setSearchImage(null);
          setSearchResult(
            data.result != null && String(data.result).trim() !== ""
              ? String(data.result)
              : (data.message || "ESTABLISHING SATELLITE LINK…"),
          );
          setIsSearchPanelOpen(true);
        }

        if (data.status === "search_result") {
          setSearchResult(data.result);
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

        if (data.status === "show_map") {
          setMapQuery(data.query || data.location || "");
          setIsMapOpen(true);
        }

        if (data.status === "close_search") {
          setIsSearchPanelOpen(false);
          setSearchResult("");
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
        if (data.status === "toggle_chat") {
          setIsChatOpen(data.visible);
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

      const textContent =
        data.message ||
        data.text ||
        (data.result != null && String(data.result).trim() !== ""
          ? String(data.result)
          : "");

      if (textContent && data.status !== "satellite_uplink") {
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
          data.status === "search_result_image" ||
          data.status === "satellite_uplink"
        ) {
          currentSpeaker = "SYSTEM";
        }

        setLogSpeaker(currentSpeaker);
        setLogTextRaw(textContent);

        // --- COMM TRANSCRIPT: capture only the actual conversation ---
        // (USER utterances + J.A.R.V.I.S replies; system/idle chatter is excluded).
        if (currentSpeaker === "USER" || currentSpeaker === "J.A.R.V.I.S") {
          setChatMessages((prev) => mergeChat(prev, currentSpeaker, textContent));
        }
      }
    };

    socket.current.onclose = (ev) => {
      if (ev.code !== 1000) {
        setBackendUnreachable(true);
      }
      setStatus("offline");
      setLogSpeaker("SYSTEM");
      setLogTextRaw("CRITICAL FAULT: CONNECTION LOST");
      setHasWokenUp(false);
      setUiBridgeLogPinned(false);
      setIsCalculatorOpen(false);
      setIsNotepadOpen(false);
      setIsBrowserOpen(false);
      setIsSearchPanelOpen(false);
      setIsHealthWidgetOpen(false);
      setIsMailWidgetOpen(false);
      setIsCalendarWidgetOpen(false);
      setIsCameraOpen(false);
      setIsMapOpen(false);
      setOverlayData(null);
      setGestureState(null);   // clear the gesture chip so it can't latch after a drop
      scheduleReconnect();     // keep trying until the backend comes back
    };
    };  // end connect()

    connect();

    return () => {
      wsWantOpen.current = false;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      try { socket.current && socket.current.close(1000); } catch (e) { /* noop */ }
    };
  }, []);

  // --- Gesture chip staleness watcher: if the daemon stops heartbeating (~2s),
  //     hide the chip after 6s of silence so it never latches a dead state. ---
  useEffect(() => {
    const GESTURE_STALE_MS = 6000;
    const t = setInterval(() => {
      setGestureState((prev) =>
        prev && prev._rxAt && Date.now() - prev._rxAt > GESTURE_STALE_MS ? null : prev
      );
    }, 2000);
    return () => clearInterval(t);
  }, []);

  const startVoiceCommand = () => {
    if (socket.current?.readyState === WebSocket.OPEN) {
      if (!hasWokenUp) setHasWokenUp(true);
      setLogSpeaker("SYSTEM");
      setLogTextRaw("INITIALIZING MIC OVERRIDE...");
      socket.current.send("START_LISTENING");
    }
  };

  // Mic affordance click: focus the command line (works today) and fire the
  // (future) voice trigger — harmless no-op until the backend reads WS input.
  const handleMicClick = () => {
    if (status === "offline") return;
    startVoiceCommand();
    commandInputRef.current?.focus();
  };

  const sendBackdoorCommand = async () => {
    const cmd = backdoorCommand.trim();
    if (!cmd) return;
    try {
      const res = await fetch(`${API_BASE}/api/backdoor`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: cmd }),
      });
      if (!res.ok) {
        const t = await res.text();
        console.error("[Backdoor] HTTP", res.status, t);
        return;
      }
      setBackdoorCommand("");
    } catch (e) {
      console.error("Backdoor error (is the API running on :8000?):", e);
    }
  };

  /** Full-canvas search/satellite content: reticle moves to corner (stays on Tab 1). */
  const isImmersiveLayout = isSearchPanelOpen;
  /** Floating glass tools (Tab 1). Camera/video/map now live on Tab 2's stage. */
  const hasModularSurface =
    isCalculatorOpen || isNotepadOpen || isHealthWidgetOpen || isMailWidgetOpen || isCalendarWidgetOpen;

  // --- TAB 2 (activity stage) surfaces: camera feed, video/media, map ---
  const openStages = [];
  if (isCameraOpen) openStages.push("camera");
  if (isBrowserOpen) openStages.push("media");
  if (isMapOpen) openStages.push("map");
  const stageOpen = openStages.length > 0;
  /** The surface shown on Tab 2: the most-recently-opened still-open stage. */
  const activeStage = openStages.includes(lastStage) ? lastStage : (openStages[0] || null);

  // Remember which stage opened most recently (so re-opening one brings it forward).
  useEffect(() => { if (isCameraOpen) setLastStage("camera"); }, [isCameraOpen]);
  useEffect(() => { if (isBrowserOpen) setLastStage("media"); }, [isBrowserOpen]);
  useEffect(() => { if (isMapOpen) setLastStage("map"); }, [isMapOpen]);

  // Auto-slide: to Tab 1 when nothing is on the stage, to Tab 2 when something opens.
  useEffect(() => {
    setActiveTab(stageOpen ? 1 : 0);
  }, [stageOpen]);

  const hudPhase = useMemo(() => {
    if (isImmersiveLayout) return "immersive";
    if (hasModularSurface) return "modular";
    return "standby";
  }, [isImmersiveLayout, hasModularSurface]);

  /** Phase 8.3 strict standby: only terminal bottom-left + optional bridge / security / full-screen search. */
  const showSystemLog =
    uiBridgeLogPinned ||
    status.startsWith("security_") ||
    hudPhase === "immersive";

  return (
    <>
      <AnimatePresence>
        {overlayData ? (
          <DataOverlay
            key="jarvis-data-overlay"
            data={overlayData}
            onClose={() => setOverlayData(null)}
          />
        ) : null}
      </AnimatePresence>
      <div
        className={`dashboard-container hud-phase-${hudPhase} ${status === "offline" ? "power-down" : ""
          }`}
      >
        <div className="dashboard-container__grid" aria-hidden />
        <div className="dashboard-container__main">
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
          <ScreenScanOverlay isActive={isScreenScanning} />
          <UplinkOverlay isActive={status === "processing_llm" || status === "searching"} />
          <LockdownOverlay isActive={isLockdown} />
          <IntroductionCeremony isActive={isCeremonyActive} onComplete={() => setIsCeremonyActive(false)} />

          {/* ===== TWO-TAB SLIDER: Tab 0 = circle, Tab 1 = full-screen activity ===== */}
          <div
            className="hud-tab-track"
            style={{ transform: `translateX(${activeTab * -100}%)` }}
          >
          <div className="hud-tab hud-tab--core">

          <motion.div
            className={`hud-core-stack ${hudPhase === "standby" ? "hud-core-pulse-standby" : ""
              }`}
            initial={false}
            animate={
              hudPhase === "immersive"
                ? {
                  left: "calc(100% - 40px)",
                  top: "calc(100% - 232px)",
                  x: "-100%",
                  y: "-100%",
                  scale: 0.34,
                  opacity: isInitialLoad ? 0 : 1,
                }
                : {
                  left: "50%",
                  top: "50%",
                  x: "-50%",
                  y: "-50%",
                  scale: 1,
                  opacity: isInitialLoad ? 0 : 1,
                }
            }
            transition={{ duration: HUD_T_DURATION, ease: HUD_EASE }}
            style={{
              transformOrigin: hudPhase === "immersive" ? "100% 100%" : "50% 50%",
            }}
          >
            <div className="hud-core-visual">
              <HudReticle />
              <div style={{ zIndex: 10, position: "relative" }}>
                <Visualizer status={status} />
              </div>
            </div>
            {(hudPhase === "standby" || hudPhase === "modular") && <MinimalHudClock />}
            {hudPhase === "immersive" && <MinimalHudClock variant="immersive" />}
          </motion.div>

          <AnimatePresence>
            {hudPhase === "immersive" && isSearchPanelOpen && (
              <motion.div
                key="immersive-main"
                className="immersive-main-canvas"
                initial={{ opacity: 1, scale: 1, x: "-50%", y: "-48%" }}
                animate={{ opacity: 1, scale: 1, x: "-50%", y: "-48%" }}
                exit={{ opacity: 0, scale: 0.98, x: "-50%", y: "-45%" }}
                transition={{ duration: 0.25, ease: HUD_EASE }}
                style={{ position: "absolute", left: "50%", top: "48%" }}
              >
                <ScanlineTransition active>
                  <div className="immersive-main-canvas__header">SATELLITE DATA LINK</div>
                  <div className="immersive-main-canvas__body">
                    {searchImage && (
                      <div className="image-container">
                        <img src={searchImage} alt="Search Result" />
                      </div>
                    )}
                    <p className="search-text">{searchResult}</p>
                  </div>
                </ScanlineTransition>
              </motion.div>
            )}
          </AnimatePresence>

          {isMailWidgetOpen && (
            <Widget
              key="jarvis-mail"
              title="COMM LINK // INBOX"
              defaultPos={{ x: 48, y: 120 }}
              delayIndex={1}
              hasWokenUp
              isFlush
              glassModular
              modularSpawn
            >
              <EmailWidget />
            </Widget>
          )}

          {isCalendarWidgetOpen && (
            <Widget
              key="jarvis-calendar"
              title="TEMPORAL GRID"
              defaultPos={{ x: Math.max(48, window.innerWidth - 420), y: 280 }}
              delayIndex={2}
              hasWokenUp
              isFlush
              glassModular
              modularSpawn
            >
              <CalendarWidget />
            </Widget>
          )}

          {isHealthWidgetOpen && (
            <Widget
              key="jarvis-health"
              title="VITAL SIGNS"
              defaultPos={{ x: window.innerWidth - 400, y: 120 }}
              delayIndex={1}
              hasWokenUp
              isFlush
              glassModular
              modularSpawn
            >
              <HealthWidget />
            </Widget>
          )}

          {hudPhase === "modular" && isCalculatorOpen && (
            <Widget
              key="jarvis-calc"
              title="CALCULATOR"
              defaultPos={{ x: window.innerWidth / 2 - 130, y: 150 }}
              delayIndex={0}
              hasWokenUp
              isFlush={true}
              glassModular
              modularSpawn
            >
              <CalculatorWidget />
            </Widget>
          )}

          {hudPhase === "modular" && isNotepadOpen && (
            <Widget
              key="jarvis-notepad"
              title="SECURE NOTEPAD"
              defaultPos={{ x: window.innerWidth / 2 + 150, y: 150 }}
              delayIndex={0}
              hasWokenUp
              isFlush={true}
              glassModular
              modularSpawn
            >
              <NotepadWidget />
            </Widget>
          )}

          </div>{/* /hud-tab--core (Tab 0) */}

          {/* ===== Tab 1: full-screen activity stage (camera / video / map) ===== */}
          <div className="hud-tab hud-tab--stage">
            <AnimatePresence mode="wait">
              {activeStage === "camera" && (
                <motion.div
                  key="stage-camera"
                  className="activity-stage"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3 }}
                >
                  <div className="activity-stage__header">
                    <span className="activity-stage__title">OPTICAL FEED</span>
                    <button
                      type="button"
                      className="activity-stage__close"
                      onClick={() => setIsCameraOpen(false)}
                    >
                      ✕ CLOSE
                    </button>
                  </div>
                  <div className="activity-stage__body">
                    <CameraFeedWidget />
                  </div>
                </motion.div>
              )}

              {activeStage === "media" && (
                <motion.div
                  key="stage-media"
                  className="activity-stage"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3 }}
                >
                  <div className="activity-stage__header">
                    <span className="activity-stage__title activity-stage__title--cyan">MEDIA UPLINK</span>
                    <button
                      type="button"
                      className="activity-stage__close"
                      onClick={() => setIsBrowserOpen(false)}
                    >
                      ✕ CLOSE
                    </button>
                  </div>
                  <div className="activity-stage__body activity-stage__body--flush">
                    <BrowserWidget externalUrl={browserUrl} immersive />
                  </div>
                </motion.div>
              )}

              {activeStage === "map" && (
                <motion.div
                  key="stage-map"
                  className="activity-stage"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.3 }}
                >
                  <div className="activity-stage__header">
                    <span className="activity-stage__title activity-stage__title--cyan">TACTICAL MAP</span>
                    <button
                      type="button"
                      className="activity-stage__close"
                      onClick={() => setIsMapOpen(false)}
                    >
                      ✕ CLOSE
                    </button>
                  </div>
                  <div className="activity-stage__body activity-stage__body--flush">
                    <MapWidget query={mapQuery} />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>{/* /hud-tab--stage (Tab 1) */}

          </div>{/* /hud-tab-track */}

          {/* Tab navigation dots — always visible (home + activity stage) */}
          {(
            <div className="hud-tab-dots" role="tablist">
              <button
                type="button"
                className={`hud-tab-dot ${activeTab === 0 ? "is-active" : ""}`}
                onClick={() => setActiveTab(0)}
                title="J.A.R.V.I.S. core"
                aria-label="J.A.R.V.I.S. core"
              />
              <button
                type="button"
                className={`hud-tab-dot ${activeTab === 1 ? "is-active" : ""}`}
                onClick={() => setActiveTab(1)}
                title="Activity stage"
                aria-label="Activity stage"
              />
            </div>
          )}

        </div>
      </div>

      <ChatPanel
        open={isChatOpen}
        messages={chatMessages}
        onClose={() => setIsChatOpen(false)}
      />

      <TaskHud
        open={isTaskHudOpen}
        refreshSignal={taskRefresh}
        onClose={() => setIsTaskHudOpen(false)}
      />

      <GestureGuide
        open={isGestureGuideOpen}
        gesture={gestureState}
        onClose={() => setIsGestureGuideOpen(false)}
      />

      {/* G4: compact always-on gesture-state pill; click opens the guide */}
      <GestureChip
        gesture={gestureState}
        onClick={() => setIsGestureGuideOpen(true)}
      />

      {backendUnreachable && (
        <div className="backend-offline-banner" role="status">
          <strong>API unreachable</strong> ({API_HOST}). Start the backend from{' '}
          <code className="backend-offline-banner__code">jarvis-backend</code>:{' '}
          <code className="backend-offline-banner__code">
            venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
          </code>
          <span className="backend-offline-banner__note"> Then reload this page.</span>
        </div>
      )}
      <div className="hud-bottom-cluster">
        <div
          className={`system-log-horizontal ${showSystemLog ? "slide-up" : "hidden-start"} ${hudPhase === "immersive" ? "hud-immersive-log" : ""
            }`}
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
        <div
          className={`hud-command-terminal hud-command-terminal--phase-${hudPhase} backdoor-panel`}
          tabIndex={-1}
        >
          <MicIndicator status={status} onClick={handleMicClick} />
          <span className="hud-command-terminal__prompt" aria-hidden>
            &gt;
          </span>
          <input
            ref={commandInputRef}
            type="text"
            placeholder="COMMAND LINE // ENTER DIRECTIVE"
            value={backdoorCommand}
            onChange={(e) => setBackdoorCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                sendBackdoorCommand();
              }
            }}
            className="hud-command-terminal__input"
          />
          <button type="button" className="hud-command-terminal__exec" onClick={sendBackdoorCommand}>
            EXECUTE
          </button>
          <div className="hud-command-terminal__tools">
            <button
              type="button"
              onClick={() => setIsCalculatorOpen((v) => !v)}
              className={isCalculatorOpen ? "is-active" : ""}
              title="Calculator"
            >
              CALC
            </button>
            <button
              type="button"
              onClick={() => setIsNotepadOpen((v) => !v)}
              className={isNotepadOpen ? "is-active" : ""}
              title="Notepad"
            >
              NOTES
            </button>
            <button
              type="button"
              onClick={() => setIsBrowserOpen((v) => !v)}
              className={isBrowserOpen ? "is-active" : ""}
              title="Media / browser"
            >
              MEDIA
            </button>
            <button
              type="button"
              onClick={() => setIsChatOpen((v) => !v)}
              className={isChatOpen ? "is-active" : ""}
              title="Conversation transcript"
            >
              CHAT
            </button>
            <button
              type="button"
              onClick={() => setIsTaskHudOpen((v) => !v)}
              className={isTaskHudOpen ? "is-active" : ""}
              title="Background task queue"
            >
              TASKS
            </button>
            <button
              type="button"
              onClick={() => setIsGestureGuideOpen((v) => !v)}
              className={isGestureGuideOpen ? "is-active" : ""}
              title="How to control the PC by hand gestures"
            >
              GESTURES
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

export default App;
