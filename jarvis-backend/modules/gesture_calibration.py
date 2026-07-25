r"""
gesture_calibration.py — persist live-tuned gesture knobs across restarts (G4)
==============================================================================

The gesture tuning that matters most is discovered LIVE: sensitivity (+/- keys
in gesture_spike.py), the mirror flip (m key — IP-Webcam streams arrive pre-
mirrored on some phones), palm_sign, smoothing. Before G4 those printed "persist
with JARVIS_… env=X" and were lost on the next run. This module persists them to
a small JSON so a calibrated setup survives restarts without editing .env.

Resolution order everywhere: GestureConfig defaults  <  this JSON  <  JARVIS_*
env vars. The JSON is the live-tuning layer; an env var stays a hard per-session
override (so CI / scripted runs can still pin a value).

Path: models/gesture_calibration.json (override with JARVIS_GESTURE_CALIBRATION).
Pure json+os — no cv2/numpy — so the daemon, the spike tool, and the harness all
share one loader and it unit-tests with no hardware.
"""
from __future__ import annotations

import json
import os
import tempfile

DEFAULT_PATH = os.path.join("models", "gesture_calibration.json")


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _mapping_mode(v) -> str:
    """Coerce to a valid GestureConfig.mapping_mode ("absolute" | "relative")."""
    return "relative" if str(v).strip().lower() == "relative" else "absolute"


# Persistable knobs and their coercers. Every GestureConfig field here is safe to
# round-trip; "mirror" is not a GestureConfig field (the frame flip lives in the
# camera loop) but is the other main live knob, so it rides along in the JSON.
SCHEMA = {
    "sensitivity": float,
    "min_cutoff": float,
    "beta": float,
    "deadzone": float,
    "pinch_down": float,
    "pinch_up": float,
    # G6.2 click/grab tuning
    "dwell_right_click_s": float,
    "grab_after_pinch_s": float,
    "scroll_gain": float,
    "start_hold_s": float,
    "stop_hold_s": float,
    "palm_sign": int,
    "require_palm_facing": _to_bool,
    "mirror": _to_bool,
    # G5.1 relative-trackpad knobs
    "mapping_mode": _mapping_mode,
    "base_gain": float,
    # G5.5 precision (fine-target damping) — env-only until 2026-07-25
    "precision": _to_bool,
    "precision_gain": float,
    "precision_v_lo": float,
    "precision_v_hi": float,
}


def path() -> str:
    """Calibration file path (env-overridable, e.g. for tests)."""
    return os.getenv("JARVIS_GESTURE_CALIBRATION") or DEFAULT_PATH


def _coerce(key, val):
    try:
        return SCHEMA[key](val)
    except (TypeError, ValueError):
        return None


def load(p: str | None = None) -> dict:
    """Read the calibration JSON. Returns {} on missing/corrupt (never raises).

    Unknown keys are dropped and values are coerced to the schema type; a value
    that won't coerce is skipped rather than poisoning the whole load."""
    p = p or path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k in SCHEMA:
            c = _coerce(k, v)
            if c is not None:
                out[k] = c
    return out


def save(fields: dict, p: str | None = None) -> bool:
    """Atomically write the whitelisted, coerced subset of `fields`. Never raises."""
    p = p or path()
    clean = {}
    for k, v in fields.items():
        if k in SCHEMA:
            c = _coerce(k, v)
            if c is not None:
                clean[k] = c
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2, sort_keys=True)
        os.replace(tmp, p)  # atomic on the same filesystem
        return True
    except OSError as e:  # noqa: BLE001
        print(f"[GESTURE-CAL] save failed: {e}", flush=True)
        return False


def apply_to(cfg, cal: dict) -> None:
    """Overlay persisted GestureConfig fields from a calibration dict.

    Skips "mirror" (not a GestureConfig field) and any key the config doesn't
    have, so an older/newer JSON never crashes a mismatched config."""
    for k, v in cal.items():
        if k == "mirror":
            continue
        if k in SCHEMA and hasattr(cfg, k):
            setattr(cfg, k, v)


def from_config(cfg, mirror: bool | None = None) -> dict:
    """Snapshot the persistable knobs off a GestureConfig (+ optional mirror flag)."""
    out = {k: getattr(cfg, k) for k in SCHEMA
           if k != "mirror" and hasattr(cfg, k)}
    if mirror is not None:
        out["mirror"] = bool(mirror)
    return out
