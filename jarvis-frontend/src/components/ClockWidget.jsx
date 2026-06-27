import React, { useState, useEffect } from "react";

/** Minimal time readout for standby HUD (beneath core visualizer). */
export function MinimalHudClock({ variant = "default" }) {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div
      className={`hud-minimal-clock${variant === "immersive" ? " hud-minimal-clock--immersive" : ""}`}
      aria-hidden
    >
      <span className="hud-minimal-clock__time">
        {time.toLocaleTimeString([], { hour12: false })}
      </span>
      <span className="hud-minimal-clock__sep">│</span>
      <span className="hud-minimal-clock__date">
        {time
          .toLocaleDateString("en-US", {
            weekday: "short",
            month: "2-digit",
            day: "2-digit",
          })
          .toUpperCase()}
      </span>
    </div>
  );
}

const ClockWidget = () => {
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  return (
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
  );
};

export default ClockWidget;
