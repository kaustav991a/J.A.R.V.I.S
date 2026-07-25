import React, { useEffect, useState } from "react";
import { API_BASE } from "../api";
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
 * LIVE FEED (follow-up shipped 2026-07-25): the reticle now shows the actual
 * camera behind the scan graphics, streamed from `GET /api/camera/stream` — an
 * MJPEG re-broadcast of the SHARED frame bus, so the browser never becomes a
 * second consumer of the phone stream (that is what used to kill the gesture
 * daemon's camera). The endpoint 503s when no camera owner is publishing, and
 * `<img onError>` drops us back to the abstract animation — the feed is a bonus
 * layer, never a requirement. The `matching` stage additionally draws the real
 * face box the recogniser locked onto.
 *
 * Prop: `auth` = { stage, user, reason, box } | null   (null = not rendered)
 */

const SCAN_LABEL = {
  start: "ACTIVATING OPTICAL SENSORS…",
  scanning: "SCANNING BIOMETRICS…",
  matching: "MATCHING IDENTITY…",
};

// Cache-buster per mount: an <img> MJPEG stream that was aborted once will
// otherwise be re-served from cache as a dead connection on the next auth.
const streamUrl = (nonce) => `${API_BASE}/api/camera/stream?fps=12&n=${nonce}`;

export default function FaceAuthOverlay({ auth }) {
  const stage = auth?.stage;
  const [feedOk, setFeedOk] = useState(true);
  const [nonce, setNonce] = useState(0);

  // Fresh stream (and a fresh chance for the feed) each time an auth starts.
  useEffect(() => {
    if (stage === "start") {
      setFeedOk(true);
      setNonce((n) => n + 1);
    }
  }, [stage]);

  if (!auth) return null;
  const { user, reason, box } = auth;
  const success = stage === "success";
  const fail = stage === "fail";
  const scanning = stage === "start" || stage === "scanning" || stage === "matching";

  const cls =
    "face-auth-overlay" +
    (success ? " is-success" : "") +
    (fail ? " is-fail" : "");

  // The feed is displayed mirrored (see .fa-feed), so the box has to be
  // mirrored POSITIONALLY — left = 1 - x - w. A CSS scaleX(-1) on the box would
  // only flip the rectangle in place and leave it on the wrong side of the face.
  const boxStyle =
    Array.isArray(box) && box.length === 4
      ? {
          left: `${(1 - box[0] - box[2]) * 100}%`,
          top: `${box[1] * 100}%`,
          width: `${box[2] * 100}%`,
          height: `${box[3] * 100}%`,
        }
      : null;

  return (
    <div className={cls} role="status" aria-live="polite">
      <div className="fa-reticle">
        {feedOk && (
          <img
            className="fa-feed"
            src={streamUrl(nonce)}
            alt=""
            aria-hidden="true"
            onError={() => setFeedOk(false)}
          />
        )}
        <div className="fa-bracket tl" />
        <div className="fa-bracket tr" />
        <div className="fa-bracket bl" />
        <div className="fa-bracket br" />
        {scanning && <div className="fa-laser" />}
        {scanning && <div className="fa-ring" />}
        {boxStyle && <div className="fa-face-box" style={boxStyle} />}
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
