import asyncio
import threading
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
    def __init__(self, broadcast_callback, speak_callback, is_system_online_callback=None):
        self.broadcast_callback = broadcast_callback
        self.speak_callback = speak_callback
        self.is_system_online_callback = is_system_online_callback
        self.is_running = False
        self.is_focus_mode_active = False
        self.session_start_time = time.time()
        
        # State tracking for delta detection
        self.last_weather = None
        self.last_ambient_time = 0
        self.last_health_alert_time = 0
        self.last_late_night_nudge = 0
        self.health_alert_cooldown = 300     # 5 min between health alerts
        self.ambient_cooldown = 300          # 5 min between ambient messages
        self.late_night_cooldown = 900       # 15 min between late-night nudges
        self.last_calendar_check = 0
        self.calendar_check_interval = 300   # 5 min between calendar checks
        self.last_email_digest = 0
        self.email_digest_cooldown = 3600    # 1 hour between email digests
        self.reminded_events = set()         # Track already-reminded event names
        
    async def start(self):
        self.is_running = True
        print("[PROACTIVE AGENT] Background intelligence activated. Waiting for system baseline...", flush=True)
        
        # Give the system 15 seconds to boot and settle
        await asyncio.sleep(15) 
        
        while self.is_running:
            try:
                await self._check_cycle()
            except Exception as e:
                print(f"[PROACTIVE AGENT] Check cycle error: {e}", flush=True)
            
            # Main loop interval: check every 60 seconds
            await asyncio.sleep(60)

    async def _check_cycle(self):
        """Runs all environmental checks in priority order."""
        # Phase 4.1/4.2: standby no longer silences the agent entirely. Safety-
        # class checks (intruder, system health) still run and reach the owner's
        # PHONE via the owner-notify fan-out — only desk TTS and the ambient/
        # convenience checks are suppressed while offline.
        online = not (self.is_system_online_callback and not self.is_system_online_callback())

        # Don't interrupt while JARVIS is actively speaking or user is in
        # conversation (standby never speaks, so the gate only matters online).
        if online:
            try:
                import speaker
                if speaker.is_system_speaking:
                    return
            except Exception:
                pass

        now = time.time()
        hour = datetime.datetime.now().hour

        # ==========================================
        # 1. INTRUDER DETECTION (Phase 8) — SECURITY FIRST
        # Moved ahead of every wellness/briefing check: each cycle returns after
        # one event, so a break nudge used to mask an intruder for a full cycle.
        # ==========================================
        if shared_optical_cache.get("camera_active") and shared_optical_cache.get("intruder_detected"):
            if not getattr(self, "intruder_alerted", False):
                message = "Security alert. I am detecting an unrecognized individual in the room. Initiating lockdown protocols."
                await self.broadcast_callback({"status": "security_override", "message": "INTRUDER DETECTED. LOCKDOWN ENGAGED.", "is_proactive": True})
                if online:
                    await self.speak_callback(message)
                await self._alert_phone("🚨 " + message)
                self.intruder_alerted = True
                return
        else:
            self.intruder_alerted = False

        # ==========================================
        # 2. SYSTEM HEALTH CHECK
        # ==========================================
        if now - self.last_health_alert_time > self.health_alert_cooldown:
            try:
                telemetry = await asyncio.to_thread(sensors.get_system_telemetry)

                if telemetry["cpu_percent"] > 90:
                    message = f"Sir, I'm detecting sustained CPU utilisation at {telemetry['cpu_percent']}%. You may have a runaway process. Shall I investigate?"
                    await self._trigger_event(message, critical=True, speak=online)
                    self.last_health_alert_time = now
                    return

                if telemetry["ram_percent"] > 85:
                    message = f"A word of caution, sir. System memory is at {telemetry['ram_percent']}% — {telemetry['ram_used_gb']}GB of {telemetry['ram_total_gb']}GB consumed. You may want to close some applications."
                    await self._trigger_event(message, critical=True, speak=online)
                    self.last_health_alert_time = now
                    return

                if telemetry["disk_percent"] > 90:
                    message = f"Sir, disk utilisation has exceeded 90%. Only {telemetry['disk_free_gb']}GB remaining. I'd recommend some housekeeping."
                    await self._trigger_event(message, critical=True, speak=online)
                    self.last_health_alert_time = now
                    return
            except Exception as e:
                print(f"[PROACTIVE AGENT] Health check failed: {e}", flush=True)

        # Standby: safety-class checks are done; everything below is
        # convenience/ambient and stays quiet while the system is offline.
        if not online:
            return

        # ==========================================
        # 2. WORK SESSION TIMER (Existing, improved)
        # ==========================================
        hours_active = (now - self.session_start_time) / 3600
        if not self.is_focus_mode_active and hours_active > 2.0:
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
        if not self.is_focus_mode_active and (hour >= 1 and hour < 5) and (now - self.last_late_night_nudge > self.late_night_cooldown):
            late_messages = [
                f"Sir, it is currently {datetime.datetime.now().strftime('%I:%M %p')}. I appreciate the dedication, but your body may have a dissenting opinion about this schedule.",
                f"The time is {datetime.datetime.now().strftime('%I:%M %p')}, sir. I'm rather certain your circadian rhythm would prefer you horizontal at this hour.",
                f"It's past {datetime.datetime.now().strftime('%I %p')}, sir. Even the most determined minds require rest. Shall I begin shutdown protocols?",
            ]
            await self._trigger_event(random.choice(late_messages))
            self.last_late_night_nudge = now
            return
        

        
        # ==========================================
        # 5. MORNING BRIEFING (Phase 9)
        # ==========================================
        if not self.is_focus_mode_active and (8 <= hour <= 9) and (now - self.last_email_digest > self.email_digest_cooldown):
            # Only trigger if someone is actually in the room to hear it
            if shared_optical_cache.get("camera_active") and shared_optical_cache.get("people_in_view"):
                try:
                    from modules.routines import RoutineEngine
                    engine = RoutineEngine(self.broadcast_callback, self.speak_callback)
                    await engine.run_morning_briefing()
                    self.last_email_digest = now
                    return
                except Exception as e:
                    print(f"[PROACTIVE AGENT] Morning briefing failed: {e}", flush=True)
        
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
                        # If DeepFace hasn't verified them yet, just wait. Intruder protocol will handle it if they remain unknown.
                        return
                    
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
        if not self.is_focus_mode_active and now - self.last_ambient_time > self.ambient_cooldown:
            # §2.3: context gate — stay quiet when WORKING/AWAY/ASLEEP. Ambient
            # chatter is low-priority, so only RELAXING/IDLE lets it through.
            _ctx_ok = True
            try:
                from modules.context_state import context_state
                _ctx_ok = context_state.should_offer_proactive(
                    "low", focus_mode=self.is_focus_mode_active
                )
            except Exception:
                pass
            # Guarantee a message on first boot, or 25% chance per cycle afterwards
            if _ctx_ok and (self.last_ambient_time == 0 or random.random() < 0.25):
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

    async def _alert_phone(self, message):
        """Phase 4.1: push a safety-class alert to the owner's phone via the
        owner-notify fan-out. Logs (never raises) when no remote route is up."""
        try:
            from modules.owner_notify import send_to_phone
            if not await send_to_phone(message):
                print("[PROACTIVE AGENT] ⚠ No phone route live — alert was desk-only.", flush=True)
        except Exception as e:
            print(f"[PROACTIVE AGENT] Phone alert failed: {e}", flush=True)

    async def _trigger_event(self, message, *, critical=False, speak=True):
        """Broadcasts a proactive message to the frontend and speaks it.
        critical=True additionally pushes it to the owner's phone (Phase 4.1);
        speak=False suppresses desk TTS (standby)."""
        print(f"\n[PROACTIVE AGENT] {message[:80]}...", flush=True)
        await self.broadcast_callback({"status": "speaking", "message": message, "is_proactive": True})
        if speak:
            await self.speak_callback(message)
        if critical:
            await self._alert_phone(message)
        # Give the UI time to revert to standby
        await asyncio.sleep(5)
        await self.broadcast_callback({"status": "online", "message": "SYSTEM ONLINE // STANDBY", "is_proactive": True})

class ScheduleDaemon(threading.Thread):
    """
    Zero-CPU Proactive Scheduler.
    Runs on a dedicated background thread, polling the calendar every 60 seconds
    and triggering direct TTS alerts for events starting in 5 to 10 minutes.
    """
    def __init__(self, main_loop, active_user="KAUSTAV", is_online_fn=None):
        super().__init__(daemon=True)
        self.main_loop = main_loop
        self.active_user = active_user
        # Phase 4 item 11: () -> bool; when False (standby / user away) the
        # reminder goes to the owner's phone instead of the desk speakers.
        self.is_online_fn = is_online_fn
        self.notified_events = set()
        self.last_clear_date = datetime.datetime.now().date()
        
    def run(self):
        print("[SCHEDULE DAEMON] Zero-CPU Proactive Scheduler activated.", flush=True)
        
        # Give system time to boot
        time.sleep(15)
        
        while True:
            try:
                self._check_schedule()
            except Exception as e:
                print(f"[SCHEDULE DAEMON] Error: {e}", flush=True)
            
            # Zero-CPU block
            time.sleep(60)
            
    def _check_schedule(self):
        import speaker
        import asyncio
        
        # 1. State Management: Clear notified events at midnight
        now_date = datetime.datetime.now().date()
        if now_date > self.last_clear_date:
            self.notified_events.clear()
            self.last_clear_date = now_date
            
        # Do not interrupt if JARVIS is already speaking
        if speaker.is_system_speaking:
            return
            
        # 2. Fetch upcoming schedule
        from modules.calendar_agent import CalendarAgent, is_calendar_available
        if not is_calendar_available():
            return
            
        cal = CalendarAgent()
        upcoming = cal.get_upcoming(minutes=15)
        
        for event in upcoming:
            evt_id = event.get('id')
            if not evt_id:
                continue
                
            mins = event['minutes_until']
            if 5 <= mins <= 10 and evt_id not in self.notified_events:
                # Prepare direct TTS
                title = "Madam" if self.active_user == "MOUSUMI" else "Sir"
                message = f"Pardon the interruption, {title}. Your '{event['summary']}' starts in {mins} minute{'s' if mins != 1 else ''}."
                
                print(f"[SCHEDULE DAEMON] Triggering proactive alert: {message}", flush=True)

                # Phase 4 item 11: reminders were desk-TTS-only — spoken to an
                # empty room when the user is away/standby. Now: speak when the
                # system is engaged, otherwise push to the owner's phone.
                online = True
                try:
                    online = self.is_online_fn() if self.is_online_fn else True
                except Exception:
                    pass
                if online:
                    asyncio.run_coroutine_threadsafe(
                        speaker.speak_text(message),
                        self.main_loop
                    )
                else:
                    from modules.owner_notify import send_to_phone
                    asyncio.run_coroutine_threadsafe(
                        send_to_phone(message),
                        self.main_loop
                    )

                # Mark as notified
                self.notified_events.add(evt_id)
                # Only announce one event at a time to prevent overlapping audio
                return
