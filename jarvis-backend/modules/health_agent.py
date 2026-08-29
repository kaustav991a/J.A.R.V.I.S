"""
Phase 7: Google Fit Health Agent
=================================
Queries step count, heart rate, calories, and active minutes from the
Google Fitness REST API.

Changes from Phase 6:
  - Singleton service client — built once, reused across calls.
  - Credential pre-flight guard with clear diagnostics on failure.
  - Correct HR extraction — tracks the most-recent data point by start time
    instead of the last-iterated value.
  - Richer payload: calories burned + active minutes added.
  - TTS-ready get_summary_string() so action_engine formats nothing itself.
  - is_health_available() now verifies the fitness scope is present.
"""
import datetime
from typing import Optional
from googleapiclient.discovery import build
from modules.google_auth import (get_google_credentials, is_google_configured,
                                 needs_reauth, unauthorised_reply)

# Required OAuth scope for Google Fitness data
_FITNESS_SCOPE = "https://www.googleapis.com/auth/fitness.activity.read"

# Module-level singleton — one service object for the lifetime of the process
_service_singleton = None


def _get_service():
    """Return a cached Google Fitness service, building it on first call."""
    global _service_singleton
    if _service_singleton is not None:
        return _service_singleton

    creds = get_google_credentials()
    if not creds:
        print("[HEALTH] Google credentials unavailable — OAuth not configured.", flush=True)
        return None

    try:
        _service_singleton = build("fitness", "v1", credentials=creds)
        print("[HEALTH] Google Fitness service initialised.", flush=True)
        return _service_singleton
    except Exception as e:
        print(f"[HEALTH] Failed to build Fitness service: {e}", flush=True)
        return None


def _aggregate_body(data_type: str, start_ms: int, end_ms: int) -> dict:
    """Convenience builder for the Fitness aggregate API request body."""
    return {
        "aggregateBy": [{"dataTypeName": data_type}],
        "bucketByTime": {"durationMillis": 86_400_000},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }


class HealthAgent:
    """
    Stateless façade over the module-level singleton service.
    Instantiated per-call by action_engine; the underlying HTTP client is shared.
    """

    def get_today_health_data(self) -> dict:
        """
        Returns today's health metrics as a structured dict.

        Keys:
          configured (bool)   — False if auth/API is unavailable
          steps       (int)   — step count since midnight UTC
          heart_rate  (int)   — most-recent average HR reading in BPM (0 = none)
          calories    (float) — kcal burned today (0 = none)
          active_mins (int)   — active minutes today (0 = none)
          error       (str)   — present only on exception
        """
        service = _get_service()
        if not service:
            return {"configured": False, "steps": 0, "heart_rate": 0,
                    "calories": 0.0, "active_mins": 0}

        try:
            # F-76: this used to be UTC midnight, which begins at 05:30 local
            # and threw away everything before it. Measured on 2026-08-29 at
            # 15:21 local: the UTC window held 0 steps, the real local day held
            # 64, and all 64 were in the discarded hours - so the desk reported
            # "no health data recorded yet today" while Fit held a day's worth of
            # his late-night walking. The calendar fixed this same bug for itself
            # long ago; the helper exists so there is no third copy to fix.
            from modules.local_day import elapsed_today_ms
            start_ms, now_ms = elapsed_today_ms()

            def _aggregate(data_type: str) -> list:
                resp = (
                    service.users()
                    .dataset()
                    .aggregate(userId="me", body=_aggregate_body(data_type, start_ms, now_ms))
                    .execute()
                )
                points = []
                for bucket in resp.get("bucket", []):
                    for dataset in bucket.get("dataset", []):
                        for point in dataset.get("point", []):
                            points.append(point)
                return points

            # ── Steps ──────────────────────────────────────────────────────────
            steps = 0
            for point in _aggregate("com.google.step_count.delta"):
                for v in point.get("value", []):
                    steps += v.get("intVal", 0)

            # ── Heart Rate — most recent point by startTimeNanos ───────────────
            hr_value = 0.0
            latest_hr_ts = -1
            for point in _aggregate("com.google.heart_rate.bpm"):
                ts = int(point.get("startTimeNanos", 0))
                if ts > latest_hr_ts:
                    for v in point.get("value", []):
                        fp = v.get("fpVal")
                        if fp is not None:
                            hr_value = fp
                            latest_hr_ts = ts

            # ── Calories ───────────────────────────────────────────────────────
            calories = 0.0
            for point in _aggregate("com.google.calories.expended"):
                for v in point.get("value", []):
                    fp = v.get("fpVal")
                    if fp is not None:
                        calories += fp

            # ── Active Minutes ─────────────────────────────────────────────────
            active_mins = 0
            for point in _aggregate("com.google.active_minutes"):
                for v in point.get("value", []):
                    active_mins += v.get("intVal", 0)

            return {
                "configured":  True,
                "steps":       steps,
                "heart_rate":  round(hr_value),
                "calories":    round(calories, 1),
                "active_mins": active_mins,
            }

        except Exception as e:
            print(f"[HEALTH] Error fetching health data: {e}", flush=True)
            return {"configured": False, "steps": 0, "heart_rate": 0,
                    "calories": 0.0, "active_mins": 0, "error": str(e)}

    def get_summary_string(self) -> str:
        """
        TTS-ready one-liner summary of today's vitals.
        The action_engine should call this instead of formatting the dict itself.
        """
        data = self.get_today_health_data()
        if not data.get("configured"):
            err = data.get("error", "")
            if err:
                print(f"[HEALTH] summary_string error detail: {err}", flush=True)
            if needs_reauth():
                return unauthorised_reply("your vitals")
            return "The health module is offline or not configured, Sir."

        steps = data["steps"]
        hr    = data["heart_rate"]
        cals  = data["calories"]
        mins  = data["active_mins"]

        parts = []
        if hr:
            parts.append(f"your heart rate is {hr} BPM")
        if steps:
            parts.append(f"you've logged {steps:,} steps")
        if cals:
            parts.append(f"{cals} kcal burned")
        if mins:
            parts.append(f"{mins} active minutes")

        if not parts:
            # This one IS an honest empty: the service answered, and it had
            # nothing. Left as it is deliberately - the sentence above is for
            # when nobody answered at all, and collapsing the two would undo the
            # distinction this change exists to make.
            return "No health data has been recorded yet today, Sir."

        sentence = ", ".join(parts[:-1])
        if len(parts) > 1:
            sentence += f", and {parts[-1]}"
        else:
            sentence = parts[0]

        return f"Vitals check, Sir — {sentence}."


def is_health_available() -> bool:
    """
    Returns True only when Google credentials are present AND the Fitness
    read scope was granted during OAuth consent.
    """
    if not is_google_configured():
        return False
    creds = get_google_credentials()
    if creds is None:
        return False
    granted: list = getattr(creds, "scopes", None) or []
    # If scope list is empty the token predates scope tracking — assume OK.
    return (not granted) or any(_FITNESS_SCOPE in s for s in granted)
