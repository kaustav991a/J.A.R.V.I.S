import React from "react";
import { Activity, Heart, RefreshCw } from "lucide-react";
import { useSource } from "../useSource";

const HealthWidget = () => {
  // One source, three states kept apart - see ../useSource.js. This widget
  // used to render OFFLINE while the request was still in flight, and the
  // vitals call takes about ten seconds because it reaches Google Fit.
  const { data, phase, loading, refresh: fetchHealth } =
    useSource("/api/health/summary", { initial: { configured: false, steps: 0, heart_rate: 0 } });

  if (phase !== "ready") {
    // "Offline" is a claim about the source. Only say it when the source
    // actually said so; while the request is in flight the honest word is
    // that it is being fetched, and a failure names itself.
    const label = phase === "loading" ? "VITALS\u2026"
                : phase === "error" ? "VITALS UNREACHABLE"
                : "VITALS OFFLINE";
    return (
      <div className="health-widget-offline">
        <Activity size={18} color="#555" />
        <span>{label}</span>
      </div>
    );
  }

  // Coerced, not trusted. `data` is replaced wholesale by whatever
  // /api/health/summary returns, so a payload with configured:true and no
  // `steps` reached `data.steps.toLocaleString()` and threw at RENDER time —
  // which in React unmounts the whole tree, not just this widget. F2's shape.
  const stepGoal = 10000;
  const steps = Number(data.steps) || 0;
  const heartRate = Number(data.heart_rate) || 0;
  const stepPercent = Math.min((steps / stepGoal) * 100, 100);

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
            <div className="stat-value">{heartRate} <span className="stat-unit">BPM</span></div>
            <div className="stat-label">HEART RATE</div>
          </div>
        </div>

        <div className="health-stat-box">
          <div className="stat-icon">
            <Activity size={20} color="#00ffcc" />
          </div>
          <div className="stat-info">
            <div className="stat-value">{steps.toLocaleString()}</div>
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
