"""
Phase 6: Google Calendar Integration Agent
Provides schedule reading, event creation, and proactive reminder support.
"""
import datetime
import re
from googleapiclient.discovery import build
from modules.google_auth import get_google_credentials, is_google_configured


class CalendarAgent:
    def __init__(self):
        self._service = None
    
    def _get_service(self):
        if self._service:
            return self._service
        creds = get_google_credentials()
        if not creds:
            return None
        try:
            self._service = build("calendar", "v3", credentials=creds)
            return self._service
        except Exception as e:
            print(f"[CALENDAR] Failed to build service: {e}")
            return None
    
    def get_today_schedule(self) -> str:
        service = self._get_service()
        if not service:
            return "Calendar integration is not configured yet, sir."
        try:
            now = datetime.datetime.utcnow()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + datetime.timedelta(days=1)
            events_result = service.events().list(
                calendarId="primary",
                timeMin=start_of_day.isoformat() + "Z",
                timeMax=end_of_day.isoformat() + "Z",
                maxResults=10, singleEvents=True, orderBy="startTime"
            ).execute()
            events = events_result.get("items", [])
            if not events:
                return "Your calendar is clear today, sir. No scheduled events."
            event_strings = []
            for event in events:
                summary = event.get("summary", "Untitled Event")
                start = event["start"].get("dateTime", event["start"].get("date"))
                try:
                    if "T" in start:
                        dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
                        event_strings.append(f"{summary} at {dt.strftime('%I:%M %p').lstrip('0')}")
                    else:
                        event_strings.append(f"{summary} (all day)")
                except Exception:
                    event_strings.append(summary)
            return f"You have {len(events)} event{'s' if len(events) != 1 else ''} today: {', '.join(event_strings)}"
        except Exception as e:
            print(f"[CALENDAR] Error fetching schedule: {e}")
            return f"I encountered an error accessing your calendar: {str(e)[:80]}"
    
    def get_upcoming(self, minutes: int = 30) -> list:
        """Returns events starting within the next N minutes for ProactiveAgent reminders."""
        service = self._get_service()
        if not service:
            return []
        try:
            now = datetime.datetime.utcnow()
            window = now + datetime.timedelta(minutes=minutes)
            events_result = service.events().list(
                calendarId="primary",
                timeMin=now.isoformat() + "Z",
                timeMax=window.isoformat() + "Z",
                maxResults=5, singleEvents=True, orderBy="startTime"
            ).execute()
            upcoming = []
            for event in events_result.get("items", []):
                summary = event.get("summary", "Untitled Event")
                start_str = event["start"].get("dateTime", event["start"].get("date"))
                try:
                    if "T" in start_str:
                        start_dt = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        now_aware = datetime.datetime.now(datetime.timezone.utc)
                        delta = (start_dt - now_aware).total_seconds() / 60
                        if delta > 0:
                            upcoming.append({"summary": summary, "start": start_str, "minutes_until": round(delta)})
                except Exception:
                    continue
            return upcoming
        except Exception as e:
            print(f"[CALENDAR] Error checking upcoming: {e}")
            return []
    
    def create_event(self, target: str) -> str:
        service = self._get_service()
        if not service:
            return "Calendar integration is not configured yet, sir."
        try:
            parsed = self._parse_event_string(target)
            event = {
                "summary": parsed["title"],
                "start": {"dateTime": parsed["start"].isoformat(), "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": parsed["end"].isoformat(), "timeZone": "Asia/Kolkata"}
            }
            if parsed.get("reminder_minutes") is not None:
                event["reminders"] = {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": parsed["reminder_minutes"]}
                    ]
                }
            service.events().insert(calendarId="primary", body=event).execute()
            time_str = parsed["start"].strftime("%I:%M %p").lstrip("0")
            return f"Event '{parsed['title']}' scheduled for {time_str}, sir."
        except Exception as e:
            print(f"[CALENDAR] Error creating event: {e}")
            return f"I had trouble creating that event: {str(e)[:80]}"

    def clear_today_schedule(self) -> str:
        service = self._get_service()
        if not service:
            return "Calendar integration is not configured yet, sir."
        try:
            # Query all events from start of today to end of today
            now_utc = datetime.datetime.utcnow()
            start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + datetime.timedelta(days=1)
            
            events_result = service.events().list(
                calendarId="primary", 
                timeMin=start_of_day.isoformat() + "Z",
                timeMax=end_of_day.isoformat() + "Z", 
                maxResults=50, 
                singleEvents=True
            ).execute()
            
            events = events_result.get("items", [])
            if not events:
                return "Your schedule for today is already clear, sir."
                
            deleted_count = 0
            for event in events:
                service.events().delete(calendarId="primary", eventId=event["id"]).execute()
                deleted_count += 1
                
            return f"I have cleared your schedule for today, sir. {deleted_count} event{'s' if deleted_count != 1 else ''} deleted."
        except Exception as e:
            print(f"[CALENDAR] Error clearing schedule: {e}")
            return f"I encountered an error while trying to clear your schedule: {str(e)[:80]}"
    
    def get_tomorrow_preview(self) -> str:
        service = self._get_service()
        if not service:
            return ""
        try:
            tomorrow_start = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            tomorrow_end = tomorrow_start + datetime.timedelta(days=1)
            events_result = service.events().list(
                calendarId="primary", timeMin=tomorrow_start.isoformat() + "Z",
                timeMax=tomorrow_end.isoformat() + "Z", maxResults=5, singleEvents=True, orderBy="startTime"
            ).execute()
            events = events_result.get("items", [])
            if not events:
                return "No events scheduled for tomorrow."
            names = [e.get("summary", "Untitled") for e in events]
            return f"Tomorrow you have {len(events)} event{'s' if len(events) != 1 else ''}: {', '.join(names)}"
        except Exception as e:
            return ""
    
    def get_today_events_structured(self) -> list:
        """Returns structured event data for the frontend widget."""
        service = self._get_service()
        if not service:
            return []
        try:
            now = datetime.datetime.utcnow()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = start_of_day + datetime.timedelta(days=1)
            events_result = service.events().list(
                calendarId="primary", timeMin=start_of_day.isoformat() + "Z",
                timeMax=end_of_day.isoformat() + "Z", maxResults=10, singleEvents=True, orderBy="startTime"
            ).execute()
            structured = []
            for event in events_result.get("items", []):
                summary = event.get("summary", "Untitled Event")
                start = event["start"].get("dateTime", event["start"].get("date"))
                time_str, is_all_day = "TBD", False
                try:
                    if "T" in start:
                        dt = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
                        time_str = dt.strftime("%I:%M %p").lstrip("0")
                    else:
                        is_all_day, time_str = True, "All Day"
                except Exception:
                    pass
                structured.append({"summary": summary, "time": time_str, "all_day": is_all_day})
            return structured
        except Exception:
            return []
    
    def _parse_event_string(self, target: str) -> dict:
        now = datetime.datetime.now()
        title = target
        start_time = now + datetime.timedelta(hours=1)
        duration = datetime.timedelta(hours=1)
        reminder_minutes = None
        
        # Check for reminder "remind me 5 mins earlier"
        rem_match = re.search(r'(?:remind|reminder).*?(\d+)\s*(?:min|minute)', target, re.IGNORECASE)
        if rem_match:
            reminder_minutes = int(rem_match.group(1))
            title = re.sub(r'(?:and\s*)?(?:with a\s*)?(?:remind|reminder).*?\d+\s*(?:min|minute)s?(?:\s*earlier|before)?', '', title, flags=re.IGNORECASE).strip()
            
        # Check for relative time "in 10 minutes"
        rel_match = re.search(r'in\s+(\d+)\s*(hour|minute|min|hr)s?', target, re.IGNORECASE)
        if rel_match:
            amount = int(rel_match.group(1))
            unit = rel_match.group(2).lower()
            if "min" in unit:
                start_time = now + datetime.timedelta(minutes=amount)
            else:
                start_time = now + datetime.timedelta(hours=amount)
            title = re.sub(r'\s*in\s+\d+\s*(?:hour|minute|min|hr)s?\s*', '', title, flags=re.IGNORECASE).strip()
        else:
            time_match = re.search(r'at\s+(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)', target, re.IGNORECASE)
            if time_match:
                time_str = time_match.group(1).strip()
                title = target[:time_match.start()].strip()
                if title.lower().endswith(" at"):
                    title = title[:-3].strip()
                try:
                    if re.search(r'[AaPp][Mm]', time_str):
                        parsed = datetime.datetime.strptime(time_str, "%I:%M %p") if ":" in time_str else datetime.datetime.strptime(time_str, "%I %p")
                    else:
                        parsed = datetime.datetime.strptime(time_str, "%H:%M") if ":" in time_str else datetime.datetime.strptime(time_str, "%H")
                    start_time = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                    if start_time < now:
                        start_time += datetime.timedelta(days=1)
                except ValueError:
                    pass
        dur_match = re.search(r'for\s+(\d+)\s*(hour|minute|min|hr)', target, re.IGNORECASE)
        if dur_match:
            amount = int(dur_match.group(1))
            unit = dur_match.group(2).lower()
            duration = datetime.timedelta(minutes=amount) if "min" in unit else datetime.timedelta(hours=amount)
            title = re.sub(r'\s*for\s+\d+\s*(?:hour|minute|min|hr)s?\s*', '', title, flags=re.IGNORECASE).strip()
        if not title:
            title = "Untitled Event"
        return {"title": title, "start": start_time, "end": start_time + duration, "reminder_minutes": reminder_minutes}


def is_calendar_available() -> bool:
    return is_google_configured()
