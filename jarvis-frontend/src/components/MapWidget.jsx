import React, { useMemo } from "react";
import "./MapWidget.scss";

// Keyless Google Maps embed: ?q=<query>&output=embed renders an interactive map
// without an API key. `query` can be a place name, address, or "lat,lng".
const DEFAULT_QUERY = "Kolkata, West Bengal";

export default function MapWidget({ query }) {
  const q = (query && String(query).trim()) || DEFAULT_QUERY;
  const src = useMemo(
    () => `https://www.google.com/maps?q=${encodeURIComponent(q)}&z=13&output=embed`,
    [q]
  );

  return (
    <div className="map-feed-ui holographic-ui">
      <div className="map-viewport">
        <iframe
          title="tactical map"
          className="map-frame"
          src={src}
          loading="lazy"
          referrerPolicy="no-referrer-when-downgrade"
        />
        {/* HUD chrome to match the optical feed */}
        <div className="map-corner tl" />
        <div className="map-corner tr" />
        <div className="map-corner bl" />
        <div className="map-corner br" />
        <div className="map-scanline" />
      </div>
      <div className="map-status-bar">
        <span>TARGET: <b>{q.toUpperCase()}</b></span>
        <span className="map-stat-on">● TRACKING</span>
      </div>
    </div>
  );
}
