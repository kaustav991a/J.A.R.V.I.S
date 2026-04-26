"""
Phase 7: Google Fit Health Agent
Queries step count and heart rate data from the Google Fitness API.
"""
import datetime
from googleapiclient.discovery import build
from modules.google_auth import get_google_credentials, is_google_configured


class HealthAgent:
    def __init__(self):
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service
        creds = get_google_credentials()
        if not creds:
            return None
        try:
            self._service = build("fitness", "v1", credentials=creds)
            return self._service
        except Exception as e:
            print(f"[HEALTH] Failed to build service: {e}")
            return None

    def get_today_health_data(self) -> dict:
        """Returns today's step count and latest heart rate."""
        service = self._get_service()
        if not service:
            return {"configured": False, "steps": 0, "heart_rate": 0}

        try:
            now = datetime.datetime.utcnow()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            now_ms = int(now.timestamp() * 1000)
            start_of_day_ms = int(start_of_day.timestamp() * 1000)

            # Query Steps
            step_body = {
                "aggregateBy": [{
                    "dataTypeName": "com.google.step_count.delta"
                }],
                "bucketByTime": {"durationMillis": 86400000},
                "startTimeMillis": start_of_day_ms,
                "endTimeMillis": now_ms
            }

            steps_response = service.users().dataset().aggregate(userId="me", body=step_body).execute()
            steps = 0
            for bucket in steps_response.get("bucket", []):
                for dataset in bucket.get("dataset", []):
                    for point in dataset.get("point", []):
                        for value in point.get("value", []):
                            steps += value.get("intVal", 0)

            # Query Heart Rate (Last 24 hours, average)
            hr_body = {
                "aggregateBy": [{
                    "dataTypeName": "com.google.heart_rate.bpm"
                }],
                "bucketByTime": {"durationMillis": 86400000},
                "startTimeMillis": start_of_day_ms,
                "endTimeMillis": now_ms
            }

            hr_response = service.users().dataset().aggregate(userId="me", body=hr_body).execute()
            hr_value = 0
            # Get the most recent average HR if available
            for bucket in hr_response.get("bucket", []):
                for dataset in bucket.get("dataset", []):
                    for point in dataset.get("point", []):
                        for value in point.get("value", []):
                            hr_value = value.get("fpVal", 0)

            return {
                "configured": True,
                "steps": steps,
                "heart_rate": round(hr_value)
            }
        except Exception as e:
            print(f"[HEALTH] Error fetching health data: {e}")
            return {"configured": False, "steps": 0, "heart_rate": 0, "error": str(e)}

def is_health_available() -> bool:
    return is_google_configured()
