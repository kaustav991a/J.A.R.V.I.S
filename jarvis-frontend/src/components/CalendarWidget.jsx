import React, { useState, useEffect } from "react";
import { Calendar, Clock, RefreshCw } from "lucide-react";

const CalendarWidget = () => {
  const [data, setData] = useState({ configured: false, events: [] });
  const [loading, setLoading] = useState(false);

  const fetchCalendar = async () => {
    setLoading(true);
    try {
      const res = await fetch("http://127.0.0.1:8000/api/calendar/today");
      if (res.ok) setData(await res.json());
    } catch (e) { /* silent */ }
    setLoading(false);
  };

  useEffect(() => {
    fetchCalendar();
    const timer = setInterval(fetchCalendar, 60000);
    return () => clearInterval(timer);
  }, []);

  if (!data.configured) {
    return (
      <div className="calendar-widget-offline">
        <Calendar size={18} color="#555" />
        <span>CALENDAR OFFLINE</span>
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
