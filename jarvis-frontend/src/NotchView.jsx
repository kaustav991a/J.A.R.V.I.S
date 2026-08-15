import React, { useState, useEffect, useRef } from 'react';
import Visualizer from './components/Visualizer';
import TypewriterText from './components/TypewriterText';
import './NotchView.scss';

/**
 * NOTCH VIEW — rendered inside the tiny top-center Electron window.
 * Shows the voice visualizer orb + a one-line status/listening indicator.
 */
export default function NotchView() {
  const [status, setStatus] = useState('offline');
  const [statusLabel, setStatusLabel] = useState('STANDBY');
  const socket = useRef(null);

  useEffect(() => {
    socket.current = new WebSocket('ws://127.0.0.1:8000/ws');

    socket.current.onopen = () => {
      setStatus('online');
      setStatusLabel('ONLINE');
    };

    socket.current.onmessage = (event) => {
      // A frame that is not JSON must cost that frame, not the handler. An
      // uncaught throw here aborts onmessage and the view silently stops
      // updating while the socket stays open — indistinguishable from an idle
      // assistant.
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        console.warn('[NotchView] dropped a frame that was not JSON');
        return;
      }

      // Mirror the status lifecycle from the main App so the orb animates.
      if (data.status) {
        setStatus(data.status);

        const labels = {
          booting:       'BOOTING',
          waking:        'WAKING',
          online:        'ONLINE',
          listening:     'LISTENING…',
          calibrating:   'CALIBRATING',
          processing_llm:'THINKING…',
          searching:     'SEARCHING…',
          executing:     'EXECUTING',
          speaking:      'SPEAKING',
          offline:       'STANDBY',
        };
        setStatusLabel(labels[data.status] || data.status.toUpperCase());
      }
    };

    socket.current.onerror = () => {
      setStatus('offline');
      setStatusLabel('OFFLINE');
    };

    socket.current.onclose = () => {
      setStatus('offline');
      setStatusLabel('DISCONNECTED');
    };

    return () => socket.current?.close();
  }, []);

  // Derive a glow colour class from status
  const glowClass =
    status === 'listening' || status === 'waking' ? 'notch--listening' :
    status === 'speaking' || status === 'executing' ? 'notch--speaking' :
    status === 'processing_llm' || status === 'searching' ? 'notch--thinking' :
    status === 'offline' ? 'notch--offline' :
    'notch--idle';

  return (
    <div className={`notch-root ${glowClass}`}>
      {/* Drag handle — the entire pill is draggable via -webkit-app-region */}
      <div className="notch-drag" />

      <div className="notch-orb">
        <Visualizer status={status} />
      </div>

      <div className="notch-info">
        <span className="notch-status-dot" aria-hidden />
        <span className="notch-label">
          <TypewriterText text={statusLabel} speed={20} />
        </span>
      </div>
    </div>
  );
}
