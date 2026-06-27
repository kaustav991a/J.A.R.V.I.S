"""
Phase 7: Google Calendar Integration Agent
==========================================
Provides schedule reading, event creation, and proactive reminder support.

Changes from Phase 6:
  - datetime.datetime.utcnow() replaced with timezone-aware UTC throughout
    (utcnow() is deprecated in Python 3.12+).
  - Day-boundary queries now computed in IST (UTC+5:30) so midnight–5:30am
    events are not silently dropped.
  - Singleton service client — built once, reused across calls.
  - get_summary_string() added for TTS-direct action_engine call.
  - get_week_preview() added for Morning Briefing (Phase 7).
  - Event strings now include event IDs in structured output so the brain can
    reference specific events by name for cancellation.
"""
import datetime
import re
from typing import Optional
from googleapiclient.discovery import build
from modules.google_auth import get_google_credentials, is_google_configured

# IST offset (UTC+5:30)
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Module-level singleton
_service_singleton = None


def _get_service():
    """Return a cached Google Calendar service, building it on first call."""
    global _service_singleton
    if _service_singleton is not None:
        return _service_singleton

    creds = get_google_credentials()
    if not creds:
        print("[CALENDAR] Google credentials unavailable — OAuth not configured.", flush=True)
        return None

    try:
        _service_singleton = build("calendar", "v3", credentials=creds)
        print("[CALENDAR_WIDGET] Google Calendar service initialised (first call — singleton cached).", flush=True)
        return _service_singleton
    except Exception as e:
        print(f"[CALENDAR] Failed to build service: {e}", flush=True)
        return None


def _ist_day_bounds() -> tuple[str, str]:
    """
    Return (timeMin, timeMax) RFC3339 strings for today in IST.
    Fixes the UTC-boundary bug that dropped midnight–5:30am IST events.
    """
    now_ist = datetime.datetime.now(_IST)
    start   = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    end     = start + datetime.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _format_event_time(start_str: str) -> tuple[str, bool]:
    """
    Parse a Google Calendar start string into (human_time, is_all_day).
    Converts UTC/offset-aware times to IST for display.
    """
    if "T" not in start_str:
        return "All Day", True
    try:
        dt_utc = datetime.datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        dt_ist = dt_utc.astimezone(_IST)
        return dt_ist.strftime("%I:%M %p").lstrip("0"), False
    except Exception:
        return "TBD", False


class CalendarAgent:
    """
    Stateless façade over the module-level singleton Calendar service.
    All public methods return TTS-ready strings or structured data.
    """

    # ── Public query methods ──────────────────────────────────────────────────

    def get_today_schedule(self) -> str:
        """TTS-ready summary of today's events (IST day boundaries)."""
        service = _get_service()
        if not service:
            return "Calendar integration is not configured yet, Sir."
        try:
            time_min, time_max = _ist_day_bounds()
            events_result = service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = events_result.get("items", [])
            if not events:
                return "Your calendar is clear today, Sir. No scheduled events."

            event_strings = []
            for event in events:
                summary  = event.get("summary", "Untitled Event")
                start    = event["start"].get("dateTime", event["start"].get("date", ""))
                time_str, is_all_day = _format_event_time(start)
                label = f"{summary} (all day)" if is_all_day else f"{summary} at {time_str}"
                event_strings.append(label)

            count = len(events)
            plural = "s" if count != 1 else ""
            return f"You have {count} event{plural} today: {', '.join(event_strings)}."

        except Exception as e:
            print(f"[CALENDAR] Error fetching schedule: {e}", flush=True)
            return f"I encountered an error accessing your calendar: {str(e)[:80]}"

    def get_summary_string(self) -> str:
        """Alias for action_engine compatibility — delegates to get_today_schedule."""
        return self.get_today_schedule()

    def get_upcoming(self, minutes: int = 30) -> list:
        """
        Returns events starting within the next N minutes for ProactiveAgent reminders.
        Each item: {"summary": str, "start": ISO str, "minutes_until": int}
        """
        service = _get_service()
        if not service:
            return []
        try:
            now     = datetime.datetime.now(datetime.timezone.utc)
            window  = now + datetime.timedelta(minutes=minutes)
            result  = service.events().list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=window.isoformat(),
                maxResults=5,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            upcoming = []
            for event in result.get("items", []):
                summary   = event.get("summary", "Untitled Event")
                start_str = event["start"].get("dateTime", event["start"].get("date", ""))
                try:
                    if "T" in start_str:
                        start_dt = datetime.datetime.fromisoformat(
                            start_str.replace("Z", "+00:00")
                        )
                        delta = (start_dt - now).total_seconds() / 60
                        if delta > 0:
                            upcoming.append({
                                "id":           event.get("id", ""),
                                "summary":      summary,
                                "start":        start_str,
                                "minutes_until": round(delta),
                            })
                except Exception:
                    continue
            return upcoming
        except Exception as e:
            print(f"[CALENDAR] Error checking upcoming: {e}", flush=True)
            return []

    def get_week_preview(self) -> str:
        """
        TTS-ready summary of events for the next 7 days.
        Used by the Phase 7 Morning Briefing.
        """
        service = _get_service()
        if not service:
            return ""
        try:
            now   = datetime.datetime.now(_IST)
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end   = start + datetime.timedelta(days=7)
            result = service.events().list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                maxResults=20,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = result.get("items", [])
            if not events:
                return "Your schedule is clear for the next seven days."

            # Group by day label
            day_groups: dict[str, list[str]] = {}
            for event in events:
                summary  = event.get("summary", "Untitled Event")
                start_s  = event["start"].get("dateTime", event["start"].get("date", ""))
                try:
                    if "T" in start_s:
                        dt  = datetime.datetime.fromisoformat(
                            start_s.replace("Z", "+00:00")
                        ).astimezone(_IST)
                        day = dt.strftime("%A")  # e.g. "Monday"
                    else:
                        dt  = datetime.date.fromisoformat(start_s)
                        day = dt.strftime("%A")
                except Exception:
                    day = "This week"
                day_groups.setdefault(day, []).append(summary)

            parts = [
                f"{day}: {', '.join(items)}"
                for day, items in day_groups.items()
            ]
            return "Here's your week ahead — " + "; ".join(parts) + "."
        except Exception as e:
            print(f"[CALENDAR] Error fetching week preview: {e}", flush=True)
            return ""

    def get_tomorrow_preview(self) -> str:
        """TTS-ready single sentence for tomorrow's events."""
        service = _get_service()
        if not service:
            return ""
        try:
            now       = datetime.datetime.now(_IST)
            t_start   = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            t_end     = t_start + datetime.timedelta(days=1)
            result    = service.events().list(
                calendarId="primary",
                timeMin=t_start.isoformat(),
                timeMax=t_end.isoformat(),
                maxResults=5,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = result.get("items", [])
            if not events:
                return "No events scheduled for tomorrow."
            names  = [e.get("summary", "Untitled") for e in events]
            count  = len(events)
            plural = "s" if count != 1 else ""
            return f"Tomorrow you have {count} event{plural}: {', '.join(names)}."
        except Exception as e:
            print(f"[CALENDAR] Error fetching tomorrow preview: {e}", flush=True)
            return ""

    def get_today_events_structured(self) -> list:
        """
        Returns structured event data for the frontend calendar widget.
        Each item: {"summary": str, "time": str, "all_day": bool, "event_id": str}
        event_id allows the brain to reference a specific event for deletion.
        """
        service = _get_service()
        if not service:
            return []
        try:
            time_min, time_max = _ist_day_bounds()
            result = service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            structured = []
            for event in result.get("items", []):
                summary  = event.get("summary", "Untitled Event")
                start    = event["start"].get("dateTime", event["start"].get("date", ""))
                time_str, is_all_day = _format_event_time(start)
                structured.append({
                    "summary":  summary,
                    "time":     time_str,
                    "all_day":  is_all_day,
                    "event_id": event.get("id", ""),
                })
            return structured
        except Exception:
            return []

    # ── Mutation methods ──────────────────────────────────────────────────────

    def create_event(self, target: str) -> str:
        service = _get_service()
        if not service:
            return "Calendar integration is not configured yet, Sir."
        try:
            parsed = self._parse_event_string(target)
            event  = {
                "summary": parsed["title"],
                "start":   {"dateTime": parsed["start"].isoformat(), "timeZone": "Asia/Kolkata"},
                "end":     {"dateTime": parsed["end"].isoformat(),   "timeZone": "Asia/Kolkata"},
            }
            if parsed.get("reminder_minutes") is not None:
                event["reminders"] = {
                    "useDefault": False,
                    "overrides":  [{"method": "popup", "minutes": parsed["reminder_minutes"]}],
                }
            service.events().insert(calendarId="primary", body=event).execute()
            time_str = parsed["start"].strftime("%I:%M %p").lstrip("0")
            return f"Event '{parsed['title']}' scheduled for {time_str}, Sir."
        except Exception as e:
            print(f"[CALENDAR] Error creating event: {e}", flush=True)
            return f"I had trouble creating that event: {str(e)[:80]}"

    def clear_today_schedule(self) -> str:
        service = _get_service()
        if not service:
            return "Calendar integration is not configured yet, Sir."
        try:
            time_min, time_max = _ist_day_bounds()
            result = service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=50,
                singleEvents=True,
            ).execute()

            events = result.get("items", [])
            if not events:
                return "Your schedule for today is already clear, Sir."

            deleted = 0
            for event in events:
                service.events().delete(
                    calendarId="primary", eventId=event["id"]
                ).execute()
                deleted += 1

            plural = "s" if deleted != 1 else ""
            return f"I've cleared your schedule for today, Sir. {deleted} event{plural} deleted."
        except Exception as e:
            print(f"[CALENDAR] Error clearing schedule: {e}", flush=True)
            return f"I encountered an error while clearing your schedule: {str(e)[:80]}"

    # ── Event string parser ───────────────────────────────────────────────────

    def _parse_event_string(self, target: str) -> dict:
        """
        Extracts title, start time, duration, and optional reminder from a
        natural-language event description.

        Recognised patterns:
          "Team standup at 9am"
          "Doctor appointment at 3:30 PM for 1 hour"
          "Call in 20 minutes"
          "Review in 2 hours with a reminder 5 minutes earlier"
        """
        # Use IST-aware now so create_event datetimes are correct for Asia/Kolkata
        now   = datetime.datetime.now(_IST)
        title = target
        start_time       = now + datetime.timedelta(hours=1)
        duration         = datetime.timedelta(hours=1)
        reminder_minutes: Optional[int] = None

        # ── Reminder ──────────────────────────────────────────────────────────
        rem_match = re.search(
            r'(?:remind|reminder).*?(\d+)\s*(?:min|minute)', target, re.IGNORECASE
        )
        if rem_match:
            reminder_minutes = int(rem_match.group(1))
            title = re.sub(
                r'(?:and\s*)?(?:with a\s*)?(?:remind|reminder).*?\d+\s*(?:min|minute)s?'
                r'(?:\s*earlier|before)?',
                '', title, flags=re.IGNORECASE,
            ).strip()

        # ── Relative time: "in 10 minutes" ────────────────────────────────────
        rel_match = re.search(
            r'in\s+(\d+)\s*(hour|minute|min|hr)s?', target, re.IGNORECASE
        )
        if rel_match:
            amount = int(rel_match.group(1))
            unit   = rel_match.group(2).lower()
            start_time = (
                now + datetime.timedelta(minutes=amount)
                if "min" in unit
                else now + datetime.timedelta(hours=amount)
            )
            title = re.sub(
                r'\s*in\s+\d+\s*(?:hour|minute|min|hr)s?\s*', '',
                title, flags=re.IGNORECASE,
            ).strip()
        else:
            # ── Absolute time: "at 3pm" / "at 14:30" ─────────────────────────
            time_match = re.search(
                r'at\s+(\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)', target, re.IGNORECASE
            )
            if time_match:
                time_str = time_match.group(1).strip()
                title    = target[:time_match.start()].strip().rstrip(" at")
                try:
                    fmt    = ("%I:%M %p" if ":" in time_str else "%I %p") \
                             if re.search(r'[AaPp][Mm]', time_str) \
                             else ("%H:%M" if ":" in time_str else "%H")
                    parsed = datetime.datetime.strptime(time_str, fmt)
                    start_time = now.replace(
                        hour=parsed.hour, minute=parsed.minute,
                        second=0, microsecond=0,
                    )
                    if start_time < now:
                        start_time += datetime.timedelta(days=1)
                except ValueError:
                    pass

        # ── Duration: "for 2 hours" / "for 30 minutes" ────────────────────────
        dur_match = re.search(
            r'for\s+(\d+)\s*(hour|minute|min|hr)', target, re.IGNORECASE
        )
        if dur_match:
            amount   = int(dur_match.group(1))
            unit     = dur_match.group(2).lower()
            duration = (
                datetime.timedelta(minutes=amount)
                if "min" in unit
                else datetime.timedelta(hours=amount)
            )
            title = re.sub(
                r'\s*for\s+\d+\s*(?:hour|minute|min|hr)s?\s*', '',
                title, flags=re.IGNORECASE,
            ).strip()

        if not title:
            title = "Untitled Event"

        return {
            "title":            title,
            "start":            start_time,
            "end":              start_time + duration,
            "reminder_minutes": reminder_minutes,
        }


def is_calendar_available() -> bool:
    return is_google_configured()
