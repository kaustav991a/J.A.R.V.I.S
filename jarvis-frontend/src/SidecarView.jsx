import React, { useState, useEffect, useRef, useCallback } from 'react';
import TaskHud from './components/TaskHud';
import HealthWidget from './components/HealthWidget';
import CalendarWidget from './components/CalendarWidget';
import { MinimalHudClock } from './components/ClockWidget';
import './SidecarView.scss';

// Task event types that trigger a refresh of the TaskHud.
const TASK_EVENTS = [
  'task_started', 'task_done', 'task_failed', 'task_needs_confirmation', 'task_report',
  'autopilot_started', 'autopilot_done', 'autopilot_failed',
];

/**
 * SIDECAR VIEW — rendered inside the tall slim right-edge Electron window.
 * Stacks: Clock → Task Queue → System Vitals → Calendar.
 */
export default function SidecarView() {
  const [status, setStatus] = useState('offline');
  const [taskRefresh, setTaskRefresh] = useState(0);
  const socket = useRef(null);

  useEffect(() => {
    socket.current = new WebSocket('ws://127.0.0.1:8000/ws');

    socket.current.onopen = () => setStatus('online');

    socket.current.onmessage = (event) => {
      const data = JSON.parse(event.data);

      // Track status for visual cues.
      if (data.status) {
        setStatus(data.status);

        // Refresh Task HUD on worker lifecycle events.
        if (TASK_EVENTS.includes(data.status)) {
          setTaskRefresh((n) => n + 1);
        }
      }
    };

    socket.current.onerror = () => setStatus('offline');
    socket.current.onclose = () => setStatus('offline');

    return () => socket.current?.close();
  }, []);

  const statusLabel = status === 'offline' ? 'OFFLINE' : 'LINKED';

  return (
    <div className="sidecar-root">
      {/* ── Drag handle ─────────────────────────────────────────────────── */}
      <div className="sidecar-drag" />

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="sidecar-header">
        <span className="sidecar-header__title">J.A.R.V.I.S</span>
        <span className={`sidecar-header__status ${status === 'offline' ? 'is-offline' : ''}`}>
          <span className="sidecar-header__dot" aria-hidden />
          {statusLabel}
        </span>
      </div>

      {/* ── Clock ───────────────────────────────────────────────────────── */}
      <div className="sidecar-section sidecar-clock">
        <MinimalHudClock />
      </div>

      {/* ── Autonomy Queue (always open) ────────────────────────────────── */}
      <div className="sidecar-section sidecar-tasks">
        <TaskHud open refreshSignal={taskRefresh} onClose={() => {}} />
      </div>

      {/* ── System Vitals ───────────────────────────────────────────────── */}
      <div className="sidecar-section">
        <div className="sidecar-section__label">VITAL SIGNS</div>
        <HealthWidget />
      </div>

      {/* ── Calendar ────────────────────────────────────────────────────── */}
      <div className="sidecar-section">
        <div className="sidecar-section__label">TEMPORAL GRID</div>
        <CalendarWidget />
      </div>
    </div>
  );
}
