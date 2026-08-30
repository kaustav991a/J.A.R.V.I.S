import React from "react";
import { Calendar, Clock, RefreshCw } from "lucide-react";
import { useSource } from "../useSource";

const CalendarWidget = () => {
  // One source, three states kept apart - see ../useSource.js. This widget
  // used to render OFFLINE while the request was still in flight, and the
  // vitals call takes about ten seconds because it reaches Google Fit.
  const { data, phase, loading, refresh: fetchCalendar } =
    useSource("/api/calendar/today", { initial: { configured: false, events: [] } });

  if (phase !== "ready") {
    // "Offline" is a claim about the source. Only say it when the source
    // actually said so; while the request is in flight the honest word is
    // that it is being fetched, and a failure names itself.
    const label = phase === "loading" ? "CALENDAR\u2026"
                : phase === "error" ? "CALENDAR UNREACHABLE"
                : "CALENDAR OFFLINE";
    return (
      <div className="calendar-widget-offline">
        <Calendar size={18} color="#555" />
        <span>{label}</span>
      </div>
    );
  }

  return (
    <div className="calendar-widget">
      <div className="calendar-header">
        <div className="calendar-date">
          <Calendar size={14} color="#00ffcc" />
          <span>{new Date().toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}</span>
        </div>
        <button className="calendar-refresh-btn" onClick={fetchCalendar} disabled={loading}>
          <RefreshCw size={12} className={loading ? "spin" : ""} />
        </button>
      </div>

      <div className="calendar-events">
        {data.events.length === 0 && (
          <div className="calendar-empty">
            <Clock size={16} color="#444" />
            <span>No events today</span>
          </div>
        )}
        {data.events.map((event, i) => (
          <div key={i} className={`calendar-event ${event.all_day ? "all-day" : ""}`}>
            <div className="event-time">{event.time}</div>
            <div className="event-summary">{event.summary}</div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default CalendarWidget;
