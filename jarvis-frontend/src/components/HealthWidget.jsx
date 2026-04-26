import React, { useState, useEffect } from "react";
import { Activity, Heart, RefreshCw } from "lucide-react";

const HealthWidget = () => {
  const [data, setData] = useState({ configured: false, steps: 0, heart_rate: 0 });
  const [loading, setLoading] = useState(false);

  const fetchHealth = async () => {
    setLoading(true);
    try {
      const res = await fetch("http://127.0.0.1:8000/api/health/summary");
      if (res.ok) setData(await res.json());
    } catch (e) { /* silent */ }
    setLoading(false);
  };

  useEffect(() => {
    fetchHealth();
    const timer = setInterval(fetchHealth, 60000);
    return () => clearInterval(timer);
  }, []);

  if (!data.configured) {
    return (
      <div className="health-widget-offline">
        <Activity size={18} color="#555" />
        <span>VITALS OFFLINE</span>
      </div>
    );
  }

  const stepGoal = 10000;
  const stepPercent = Math.min((data.steps / stepGoal) * 100, 100);

  return (
    <div className="health-widget">
      <div className="health-header">
        <div className="health-title">
          <Activity size={14} color="#00ffcc" />
          <span>BIOMETRICS</span>
        </div>
        <button className="health-refresh-btn" onClick={fetchHealth} disabled={loading}>
          <RefreshCw size={12} className={loading ? "spin" : ""} />
        </button>
      </div>

      <div className="health-stats">
        <div className="health-stat-box">
          <div className="stat-icon pulse-heart">
            <Heart size={20} color="#ff3366" />
          </div>
          <div className="stat-info">
            <div className="stat-value">{data.heart_rate} <span className="stat-unit">BPM</span></div>
            <div className="stat-label">HEART RATE</div>
          </div>
        </div>

        <div className="health-stat-box">
          <div className="stat-icon">
            <Activity size={20} color="#00ffcc" />
          </div>
          <div className="stat-info">
            <div className="stat-value">{data.steps.toLocaleString()}</div>
            <div className="stat-label">STEPS TODAY</div>
          </div>
        </div>
      </div>

      <div className="health-progress-container">
        <div className="progress-label">DAILY GOAL: {stepGoal.toLocaleString()}</div>
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${stepPercent}%` }}></div>
        </div>
      </div>
    </div>
  );
};

export default HealthWidget;
