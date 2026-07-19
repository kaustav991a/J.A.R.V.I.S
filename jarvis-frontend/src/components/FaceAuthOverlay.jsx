import React from "react";
import "./FaceAuthOverlay.scss";

/**
 * G6.1 — synced face-auth overlay.
 *
 * Unlike the old self-timed FaceScanOverlay (setTimeout 1500/4000ms), this is
 * driven entirely by the backend `auth_face_*` contract (modules/auth_status.py):
 * it HOLDS on the scanning state — the laser sweeps and the ring pulses in a loop
 * with no auto-advance — until a real `auth_face_success` / `auth_face_fail`
 * frame arrives. Success locks on green with the matched user; failure rejects in
 * red. The animation can never outrun the real scan.
 *
 * Prop: `auth` = { stage, user, reason } | null   (null = not rendered)
 */

const SCAN_LABEL = {
  start: "ACTIVATING OPTICAL SENSORS…",
  scanning: "SCANNING BIOMETRICS…",
  matching: "MATCHING IDENTITY…",
};

export default function FaceAuthOverlay({ auth }) {
  if (!auth) return null;
  const { stage, user, reason } = auth;
  const success = stage === "success";
  const fail = stage === "fail";
  const scanning = stage === "start" || stage === "scanning" || stage === "matching";

  const cls =
    "face-auth-overlay" +
    (success ? " is-success" : "") +
    (fail ? " is-fail" : "");

  return (
    <div className={cls} role="status" aria-live="polite">
      <div className="fa-reticle">
        <div className="fa-bracket tl" />
        <div className="fa-bracket tr" />
        <div className="fa-bracket bl" />
        <div className="fa-bracket br" />
        {scanning && <div className="fa-laser" />}
        {scanning && <div className="fa-ring" />}
        {success && <div className="fa-mark">✓</div>}
        {fail && <div className="fa-mark">✕</div>}
      </div>

      <div className="fa-status">
        {scanning && (SCAN_LABEL[stage] || "SCANNING…")}
        {success && `IDENTITY CONFIRMED — ${user || "USER"}`}
        {fail && "NO MATCH — IDENTITY UNVERIFIED"}
      </div>
      {fail && reason && <div className="fa-sub">reason: {reason}</div>}
    </div>
  );
}
