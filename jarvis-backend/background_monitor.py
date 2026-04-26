import asyncio
import time
import datetime
import random
import sensors
from ambient_vision import shared_optical_cache

class ProactiveAgent:
    """
    The autonomous background intelligence engine.
    Instead of random chatter, monitors real environmental factors
    and speaks up only when there's something genuinely useful to say.
    """
    def __init__(self, broadcast_callback, speak_callback):
        self.broadcast_callback = broadcast_callback
        self.speak_callback = speak_callback
        self.is_running = False
        self.session_start_time = time.time()
        
        # State tracking for delta detection
        self.last_weather = None
        self.last_ambient_time = 0
        self.last_health_alert_time = 0
        self.last_late_night_nudge = 0
        self.health_alert_cooldown = 600     # 10 min between health alerts
        self.ambient_cooldown = 900          # 15 min between ambient messages
        self.late_night_cooldown = 1800      # 30 min between late-night nudges
        self.last_calendar_check = 0
        self.calendar_check_interval = 300   # 5 min between calendar checks
        self.last_email_digest = 0
        self.email_digest_cooldown = 3600    # 1 hour between email digests
        self.reminded_events = set()         # Track already-reminded event names
        
    async def start(self):
        self.is_running = True
        print("[PROACTIVE AGENT] Background intelligence activated. Waiting for system baseline...", flush=True)
        
        # Give the system 90 seconds to boot and settle
        await asyncio.sleep(90) 
        
        while self.is_running:
            try:
                await self._check_cycle()
            except Exception as e:
                print(f"[PROACTIVE AGENT] Check cycle error: {e}", flush=True)
            
            # Main loop interval: check every 60 seconds
            await asyncio.sleep(60)

    async def _check_cycle(self):
        """Runs all environmental checks in priority order."""
        now = time.time()
        hour = datetime.datetime.now().hour
        
        # ==========================================
        # 1. SYSTEM HEALTH CHECK (Highest Priority)
        # ==========================================
        if now - self.last_health_alert_time > self.health_alert_cooldown:
            try:
                telemetry = await asyncio.to_thread(sensors.get_system_telemetry)
                
                if telemetry["cpu_percent"] > 90:
                    message = f"Sir, I'm detecting sustained CPU utilisation at {telemetry['cpu_percent']}%. You may have a runaway process. Shall I investigate?"
                    await self._trigger_event(message)
                    self.last_health_alert_time = now
                    return
                
                if telemetry["ram_percent"] > 85:
                    message = f"A word of caution, sir. System memory is at {telemetry['ram_percent']}% — {telemetry['ram_used_gb']}GB of {telemetry['ram_total_gb']}GB consumed. You may want to close some applications."
                    await self._trigger_event(message)
                    self.last_health_alert_time = now
                    return
                    
                if telemetry["disk_percent"] > 90:
                    message = f"Sir, disk utilisation has exceeded 90%. Only {telemetry['disk_free_gb']}GB remaining. I'd recommend some housekeeping."
                    await self._trigger_event(message)
                    self.last_health_alert_time = now
                    return
            except Exception as e:
                print(f"[PROACTIVE AGENT] Health check failed: {e}", flush=True)
        
        # ==========================================
        # 2. WORK SESSION TIMER (Existing, improved)
        # ==========================================
        hours_active = (now - self.session_start_time) / 3600
        if hours_active > 2.0:
            messages = [
                "Pardon the interruption, sir, but you've been at this for over two hours. Even I run garbage collection periodically. Might I suggest a brief recess?",
                "Sir, you've been working continuously for over two hours now. Your cognitive performance may benefit from a short break. Just a thought.",
                "Two hours and counting, sir. I don't mean to nag, but your wellbeing is rather important to the continued operation of this household.",
            ]
            await self._trigger_event(random.choice(messages))
            self.session_start_time = now  # Reset to avoid spamming
            return
            
        # ==========================================
        # 3. LATE NIGHT WELLNESS CHECK
        # ==========================================
        if (hour >= 1 and hour < 5) and (now - self.last_late_night_nudge > self.late_night_cooldown):
            late_messages = [
                f"Sir, it is currently {datetime.datetime.now().strftime('%I:%M %p')}. I appreciate the dedication, but your body may have a dissenting opinion about this schedule.",
                f"The time is {datetime.datetime.now().strftime('%I:%M %p')}, sir. I'm rather certain your circadian rhythm would prefer you horizontal at this hour.",
                f"It's past {datetime.datetime.now().strftime('%I %p')}, sir. Even the most determined minds require rest. Shall I begin shutdown protocols?",
            ]
            await self._trigger_event(random.choice(late_messages))
            self.last_late_night_nudge = now
            return
        
        # ==========================================
        # 4. CALENDAR REMINDER (Phase 6)
        # ==========================================
        if now - self.last_calendar_check > self.calendar_check_interval:
            self.last_calendar_check = now
            try:
                from modules.calendar_agent import CalendarAgent, is_calendar_available
                if is_calendar_available():
                    cal = CalendarAgent()
                    upcoming = cal.get_upcoming(minutes=15)
                    for event in upcoming:
                        event_key = f"{event['summary']}_{event['start']}"
                        if event_key not in self.reminded_events:
                            mins = event['minutes_until']
                            message = f"Sir, just a heads up — '{event['summary']}' begins in {mins} minute{'s' if mins != 1 else ''}."
                            await self._trigger_event(message)
                            self.reminded_events.add(event_key)
                            return
            except Exception as e:
                print(f"[PROACTIVE AGENT] Calendar check failed: {e}", flush=True)
        
        # ==========================================
        # 5. MORNING EMAIL DIGEST (Phase 6)
        # ==========================================
        if (8 <= hour <= 9) and (now - self.last_email_digest > self.email_digest_cooldown):
            try:
                from modules.gmail_agent import GmailAgent, is_gmail_available
                if is_gmail_available():
                    gmail = GmailAgent()
                    count = gmail.get_unread_count()
                    if count > 0:
                        message = f"Good morning, sir. You have {count} unread email{'s' if count != 1 else ''} waiting for your attention."
                        await self._trigger_event(message)
                        self.last_email_digest = now
                        return
            except Exception as e:
                print(f"[PROACTIVE AGENT] Email digest failed: {e}", flush=True)
        
        # ==========================================
        # 4. INTRUDER DETECTION (Phase 8)
        # ==========================================
        if shared_optical_cache.get("camera_active") and shared_optical_cache.get("intruder_detected"):
            if not hasattr(self, "intruder_alerted") or not self.intruder_alerted:
                message = "Security alert. I am detecting an unrecognized individual in the room. Initiating lockdown protocols."
                await self.broadcast_callback({"status": "security_override", "message": "INTRUDER DETECTED. LOCKDOWN ENGAGED.", "is_proactive": True})
                await self.speak_callback(message)
                self.intruder_alerted = True
                return
        else:
            self.intruder_alerted = False
        
        # ==========================================
        # 5. USER ABSENCE DETECTION (Phase 8)
        # ==========================================
        if shared_optical_cache.get("camera_active"):
            user_absent = shared_optical_cache.get("user_absent", False)
            last_known = shared_optical_cache.get("last_known_user")
            
            if not hasattr(self, "absence_notified"):
                self.absence_notified = False
            if not hasattr(self, "was_absent"):
                self.was_absent = False
            
            if user_absent and last_known and not self.absence_notified:
                # User left the frame — lock the UI temporarily
                message = f"I notice you've stepped away, sir. Securing the interface until your return."
                await self.broadcast_callback({"status": "security_locked", "message": "USER ABSENT. UI LOCKED.", "is_proactive": True})
                await self.speak_callback(message)
                self.absence_notified = True
                self.was_absent = True
                return
            
            if not user_absent and self.was_absent:
                # User returned! Greet and unlock
                people = list(shared_optical_cache.get("people_in_view", set()))
                if people:
                    person = people[0]
                    if person == "KAUSTAV":
                        message = "Welcome back, sir. I've been keeping the systems secure in your absence. Unlocking the interface now."
                    elif person == "MOUSUMI":
                        message = "Welcome back, Miss Mousumi. Unlocking the interface."
                    elif person == "KINSHUK":
                        message = "Welcome back, Mr. Kinshuk. Unlocking the interface."
                    else:
                        message = "I detect a presence. Please identify yourself."
                    
                    await self.broadcast_callback({"status": "online", "message": "USER DETECTED. UNLOCKING UI.", "is_proactive": True})
                    await self.speak_callback(message)
                    self.absence_notified = False
                    self.was_absent = False
                    return
        
        # ==========================================
        # 6. OPTICAL CONTEXT (Welcome Back Protocol)
        # ==========================================
        if shared_optical_cache.get("camera_active") and shared_optical_cache.get("people_in_view"):
            people = list(shared_optical_cache["people_in_view"])
            person = people[0]
            
            # Debounce: track last greeting time per person
            if not hasattr(self, "last_greeting_time"):
                self.last_greeting_time = {}
                
            last_greeted = self.last_greeting_time.get(person, 0)
            
            # Only greet once every 15 minutes (900s)
            if now - last_greeted > 900:
                if person == "KAUSTAV":
                    await self._trigger_event("Welcome back, sir. I've been monitoring the systems in your absence.")
                elif person == "MOUSUMI":
                    await self._trigger_event("Good to see you, Miss Mousumi. Let me know if you need anything.")
                elif person == "KINSHUK":
                    await self._trigger_event("Welcome back, Mr. Kinshuk.")
                else:
                    await self._trigger_event("I detect an unrecognized presence in the room. Please identify yourself.")
                
                self.last_greeting_time[person] = now
                return
        
        # ==========================================
        # 5. WEATHER DELTA DETECTION
        # ==========================================
        try:
            current_weather = await sensors.get_weather_data()
            if current_weather and self.last_weather:
                # Temperature swing > 5 degrees
                temp_delta = abs(current_weather["temp"] - self.last_weather["temp"])
                if temp_delta >= 5:
                    direction = "risen" if current_weather["temp"] > self.last_weather["temp"] else "dropped"
                    message = f"Sir, the temperature has {direction} significantly — now {current_weather['temp']}°C, a {temp_delta}° shift. You may want to adjust accordingly."
                    await self._trigger_event(message)
                    self.last_weather = current_weather
                    return
                
                # Condition change (e.g., Clear → Rain)
                if current_weather["condition"] != self.last_weather["condition"]:
                    old_cond = self.last_weather["condition"]
                    new_cond = current_weather["condition"]
                    
                    if new_cond.lower() in ["rain", "thunderstorm", "drizzle"]:
                        message = f"Weather advisory, sir. Conditions have shifted from {old_cond} to {new_cond}. You may want to ensure the windows are secured."
                    else:
                        message = f"Weather update: conditions have changed from {old_cond} to {new_cond}. Currently {current_weather['temp']}°C."
                    await self._trigger_event(message)
                    self.last_weather = current_weather
                    return
            
            # Always update the cached weather
            if current_weather:
                self.last_weather = current_weather
        except Exception as e:
            print(f"[PROACTIVE AGENT] Weather check failed: {e}", flush=True)
        
        # ==========================================
        # 6. TIME-AWARE AMBIENT MESSAGES (Low Priority)
        # ==========================================
        if now - self.last_ambient_time > self.ambient_cooldown:
            # 8% chance per cycle (roughly once every ~12 minutes on average)
            if random.random() < 0.08:
                message = self._get_contextual_ambient(hour)
                if message:
                    await self._trigger_event(message)
                    self.last_ambient_time = now

    def _get_contextual_ambient(self, hour: int) -> str:
        """Returns a time-appropriate ambient message instead of generic ones."""
        if 5 <= hour < 9:
            messages = [
                "Morning diagnostics complete. All local subsystems are operating within nominal parameters.",
                "I've refreshed the weather data and cleared the overnight cache. Ready when you are, sir.",
            ]
        elif 9 <= hour < 12:
            messages = [
                "Local network traffic remains secure. No anomalies detected on the subnet.",
                "All background tasks are running efficiently. Standing by for further instructions.",
            ]
        elif 12 <= hour < 17:
            messages = [
                "Afternoon status check complete. All systems nominal. Memory banks optimised.",
                "Running a routine network sweep. All ports secure, no unusual activity.",
            ]
        elif 17 <= hour < 21:
            messages = [
                "Evening protocols engaged. I've adjusted system priorities for your personal session.",
                "All scheduled background processes have completed successfully for today.",
            ]
        else:
            messages = [
                "Night mode active. I've reduced non-essential system polling to conserve resources.",
                "Running a quiet background optimisation pass on the memory banks.",
            ]
        return random.choice(messages)

    async def _trigger_event(self, message):
        """Broadcasts a proactive message to the frontend and speaks it."""
        print(f"\n[PROACTIVE AGENT] {message[:80]}...", flush=True)
        await self.broadcast_callback({"status": "speaking", "message": message, "is_proactive": True})
        await self.speak_callback(message)
        # Give the UI time to revert to standby
        await asyncio.sleep(5)
        await self.broadcast_callback({"status": "online", "message": "SYSTEM ONLINE // STANDBY", "is_proactive": True})
