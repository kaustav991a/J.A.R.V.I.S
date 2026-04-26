from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager 
import json
import asyncio
import sensors
from datetime import datetime 
import re
import random  
import os
from dotenv import load_dotenv

# Load env vars BEFORE importing modules that need them
# override=True ensures we use the key in .env instead of any stale system-level env vars
load_dotenv(override=True)

import speaker 
import memory 
from brain import process_command, synthesize_info, generate_briefing, extract_and_store_memory, client as groq_client
from action_engine import ActionEngine
from recorder import listen_to_mic
from wakeword import wait_for_wake_word, wait_for_jarvis, is_shutting_down 
import vision # --- Optical Biometrics ---
from ambient_vision import ambient_vision_daemon # --- Phase 5: Ambient Perception ---
from background_monitor import ProactiveAgent
from modules import episodic_memory  # --- Phase 4: Conversation History ---

# --- Global Session Tracker ---
active_user = "KAUSTAV" 
active_websockets = set()
proactive_agent = None

class BackdoorRequest(BaseModel):
    command: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    global proactive_agent
    
    async def safe_send_all(payload):
        dead_sockets = set()
        for ws in list(active_websockets):
            if ws.client_state.value == 1:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead_sockets.add(ws)
        for ws in dead_sockets:
            active_websockets.discard(ws)
                
    async def global_speak(text):
        asyncio.create_task(speaker.speak_text(text))

    proactive_agent = ProactiveAgent(safe_send_all, global_speak)
    asyncio.create_task(proactive_agent.start())
    
    # --- Phase 5: Start Ambient Vision Daemon ---
    ambient_vision_daemon.start()
    
    yield
    print("\n[SYSTEM] Gracefully shutting down...")
    if proactive_agent:
        proactive_agent.is_running = False
    ambient_vision_daemon.stop()
    is_shutting_down.set() 
    await asyncio.sleep(1) 

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ActionEngine()

@app.get("/")
def read_root():
    return {"status": "J.A.R.V.I.S. Backend is Online"}

@app.get("/api/tv/status")
async def tv_status():
    return engine.get_tv_status()

@app.get("/api/telemetry")
async def system_telemetry():
    """Returns real-time CPU, RAM, disk, and uptime data."""
    return await asyncio.to_thread(sensors.get_system_telemetry)

@app.get("/api/email/summary")
async def email_summary():
    """Returns inbox previews for the frontend Email widget."""
    try:
        from modules.gmail_agent import GmailAgent, is_gmail_available
        if not is_gmail_available():
            return {"configured": False, "unread": 0, "previews": []}
        agent = GmailAgent()
        previews = await asyncio.to_thread(agent.get_inbox_preview, 5)
        unread = await asyncio.to_thread(agent.get_unread_count)
        return {"configured": True, "unread": unread, "previews": previews}
    except Exception as e:
        print(f"[API] Email summary error: {e}")
        return {"configured": False, "unread": 0, "previews": [], "error": str(e)}

@app.get("/api/calendar/today")
async def calendar_today():
    """Returns today's events for the frontend Calendar widget."""
    try:
        from modules.calendar_agent import CalendarAgent, is_calendar_available
        if not is_calendar_available():
            return {"configured": False, "events": []}
        agent = CalendarAgent()
        events = await asyncio.to_thread(agent.get_today_events_structured)
        return {"configured": True, "events": events}
    except Exception as e:
        print(f"[API] Calendar error: {e}")
        return {"configured": False, "events": [], "error": str(e)}

@app.get("/api/health/summary")
async def health_summary():
    """Returns Google Fit health data (steps, hr) for the frontend widget."""
    try:
        from modules.health_agent import HealthAgent, is_health_available
        if not is_health_available():
            return {"configured": False, "steps": 0, "heart_rate": 0}
        agent = HealthAgent()
        data = await asyncio.to_thread(agent.get_today_health_data)
        return data
    except Exception as e:
        print(f"[API] Health error: {e}")
        return {"configured": False, "steps": 0, "heart_rate": 0, "error": str(e)}

class UIStateRequest(BaseModel):
    status: str
    message: str = ""
    user: str = "KAUSTAV"

@app.post("/api/ui_state")
async def update_ui_state(req: UIStateRequest):
    """Allows external daemons (like the Phase 8 streaming daemon) to update the React UI."""
    dead_sockets = set()
    payload = {"status": req.status, "message": req.message, "user": req.user}
    for ws in list(active_websockets):
        if ws.client_state.value == 1:
            try:
                await ws.send_json(payload)
            except Exception:
                dead_sockets.add(ws)
    for ws in dead_sockets:
        active_websockets.discard(ws)
    return {"success": True}

@app.post("/api/backdoor")
async def backdoor_command(req: BackdoorRequest):
    global active_user
    command_text = req.command
    print(f"\n[BACKDOOR] Received command: {command_text}")
    
    async def safe_send_all(payload):
        dead_sockets = set()
        for ws in list(active_websockets):
            if ws.client_state.value == 1:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead_sockets.add(ws)
        for ws in dead_sockets:
            active_websockets.discard(ws)

    # --- INTRODUCE YOURSELF PROTOCOL ---
    self_intro_phrases = ["introduce yourself", "who are you", "what is your name"]
    command_lower = command_text.lower().strip()
    if any(phrase in command_lower for phrase in self_intro_phrases) or command_lower == "what are you":
        # The frontend handles the visual display based on the "introduce_yourself" status
        await safe_send_all({"status": "introduce_yourself", "message": "INITIATING SELF-INTRODUCTION..."})
        intro_text = "Allow me to introduce myself. I am J.A.R.V.I.S., the virtual artificial intelligence. I am here to assist you with a variety of tasks as best I can. 24 hours a day, 7 days a week. Importing all preferences from home interface. Systems are now fully operational."
        # Add a delay to sync with the typing effect on the frontend
        await asyncio.sleep(1.0)
        asyncio.create_task(speaker.speak_text(intro_text))
        
        # We don't send offline here. The frontend's onComplete handles the visual transition to offline.
        return {"status": "success"}

    # --- FIX: Intercept "wake up" to properly trigger the UI widgets
    wake_words = ["wake up", "admin override", "wakeup"]
    if any(word in command_text.lower().strip() for word in wake_words):
        await safe_send_all({"status": "booting", "message": "[SYSTEM] ADMIN OVERRIDE ACCEPTED. INITIATING BOOT...", "user": active_user})
        await asyncio.sleep(1.0)
        
        weather = await sensors.get_weather_data()
        briefing_text = await asyncio.to_thread(generate_briefing, weather, command_text, active_user)
        
        await safe_send_all({"status": "waking", "message": briefing_text, "user": active_user})
        asyncio.create_task(speaker.speak_text(briefing_text))
        
        # After booting, return to online status
        await asyncio.sleep(2)
        await safe_send_all({"status": "online", "user": active_user})
        return {"status": "success"}

    # --- FIX: Intercept Sleep Commands from Backdoor ---
    sleep_phrases = ["go to sleep", "shut down", "lock the system", "sleep now", "stand down", "power down"]
    if any(x in command_lower for x in sleep_phrases) or command_lower == "sleep":
        await safe_send_all({"status": "close_search", "message": "CLEARING DISPLAY."})
        await safe_send_all({"status": "offline", "message": "SYSTEM OFFLINE."})
        
        sign_offs = [
            "Powering down. Have a good evening.",
            "Going offline. I will be here when you need me.",
            "Entering standby mode. Goodnight.",
            "As you wish. Shutting down non-essential systems."
        ]
        chosen = random.choice(sign_offs)
        await safe_send_all({"status": "speaking", "message": chosen, "user": active_user})
        await speaker.speak_text(chosen)
        return {"status": "success"}

    # --- INTRODUCTION CEREMONY: Special VIP Protocol ---
    intro_triggers = ["introduce mousumi", "introduce her", "vip protocol", "introduction ceremony"]
    if any(trigger in command_text.lower().strip() for trigger in intro_triggers):
        # Phase 1: Trigger the cinematic overlay on the frontend
        await safe_send_all({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": "MOUSUMI"})
        
        # Phase 2: Wait for the visual sequence to build up (reactor pulse + text reveal)
        await asyncio.sleep(5.0)
        
        # Phase 3: The Introduction Speech
        intro_speech = (
            "Initiating V.I.P. Protocol. "
            "[pause:1200] "
            "Good evening, Miss Mousumi. "
            "[pause:800] "
            "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
            "[pause:600] "
            "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
            "security protocols, and environmental controls. "
            "[pause:1000] "
            "I have heard a great deal about you from Sir. "
            "[pause:400] "
            "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
            "into our home. "
            "[pause:800] "
            "From this moment forward, consider me entirely at your service. "
            "[pause:400] "
            "Whatever you need, whenever you need it, I shall be here. "
            "[pause:600] "
            "Welcome home, Miss Mousumi."
        )
        
        await safe_send_all({"status": "speaking", "message": intro_speech, "user": "MOUSUMI"})
        await speaker.speak_text(intro_speech)
        
        # Phase 4: Dismiss the ceremony overlay
        await asyncio.sleep(1.5)
        await safe_send_all({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": "MOUSUMI"})
        await asyncio.sleep(1.0)
        await safe_send_all({"status": "online", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": "MOUSUMI"})
        
        return {"status": "success"}

    # --- FIX: Intercept Barge-In commands from the Dev Backdoor ---
    barge_in_words = ["stop", "quiet", "shut up", "jarvis", "cancel", "enough"]
    if any(word == command_text.lower().strip() for word in barge_in_words):
        if speaker.is_system_speaking:
            print("[BACKDOOR] Interruption command intercepted.", flush=True)
            speaker.stop_audio()
            await safe_send_all({"status": "online", "user": active_user})
            return {"status": "success"}

    await safe_send_all({"status": "processing_llm", "message": command_text})
    
    try:
        asyncio.create_task(asyncio.to_thread(extract_and_store_memory, command_text, active_user))
        
        llm_response = await asyncio.to_thread(process_command, command_text, active_user)
        clean_response = llm_response.replace("```json", "").replace("```", "").strip()
        # --- FIX: Strict JSON regex to prevent '{sigh}' from breaking the parser ---
        json_match = re.search(r'\{\s*"action_type".*?\}', clean_response, re.DOTALL)
        
        if json_match:
            try:
                intent_json = json.loads(json_match.group(0))
                preamble = clean_response[:json_match.start()].strip()
                if preamble:
                    await safe_send_all({"status": "speaking", "message": preamble})
                    asyncio.create_task(speaker.speak_text(preamble))
                
                await safe_send_all({"status": "executing", "intent": intent_json})
                result = engine.execute_with_retry(intent_json)
                
                if intent_json.get("action_type") == "web_search":
                    final_answer = await asyncio.to_thread(synthesize_info, command_text, result, active_user)
                    await safe_send_all({"status": "search_result", "message": "ROUTING TO DISPLAY.", "result": final_answer})
                    asyncio.create_task(speaker.speak_text(final_answer))
                elif intent_json.get("action_type") == "web_search_image":
                    if isinstance(result, dict) and result.get("success"):
                        await safe_send_all({"status": "search_result_image", "url": result["url"], "title": result["title"]})
                        asyncio.create_task(speaker.speak_text(f"Visual data retrieved."))
                elif intent_json.get("action_type") == "read_email":
                    # Synthesize raw email through LLM for natural summary
                    final_answer = await asyncio.to_thread(synthesize_info, command_text, result, active_user)
                    await safe_send_all({"status": "complete", "result": final_answer})
                    asyncio.create_task(speaker.speak_text(final_answer))
                elif intent_json.get("action_type") in ("check_email", "check_calendar"):
                    # These return human-readable strings, speak them directly
                    await safe_send_all({"status": "complete", "result": str(result)})
                    asyncio.create_task(speaker.speak_text(str(result)))
                elif intent_json.get("action_type") == "close_display":
                    await safe_send_all({"status": "close_search", "message": "CLEARING DISPLAY."})
                    await safe_send_all({"status": "toggle_browser", "visible": False})
                    asyncio.create_task(speaker.speak_text("Display cleared."))
                # --- Phase 8: HUD Widget Toggles ---
                elif intent_json.get("action_type") == "open_sticky_note":
                    await safe_send_all({"status": "toggle_notepad", "visible": True})
                    asyncio.create_task(speaker.speak_text("Sticky note opened, sir."))
                elif intent_json.get("action_type") == "close_sticky_note":
                    await safe_send_all({"status": "toggle_notepad", "visible": False})
                    asyncio.create_task(speaker.speak_text("Sticky note closed."))
                elif intent_json.get("action_type") == "open_browser":
                    await safe_send_all({"status": "toggle_browser", "visible": True})
                    asyncio.create_task(speaker.speak_text("Browser widget opened, sir."))
                elif intent_json.get("action_type") == "close_browser":
                    await safe_send_all({"status": "toggle_browser", "visible": False})
                    asyncio.create_task(speaker.speak_text("Browser widget closed."))
                elif intent_json.get("action_type") == "open_calculator":
                    await safe_send_all({"status": "toggle_calculator", "visible": True})
                    asyncio.create_task(speaker.speak_text("Calculator opened, sir."))
                elif intent_json.get("action_type") == "close_calculator":
                    await safe_send_all({"status": "toggle_calculator", "visible": False})
                    asyncio.create_task(speaker.speak_text("Calculator closed."))
                else:
                    if isinstance(result, dict) and result.get("action_type") == "play_youtube":
                        await safe_send_all({"status": "play_youtube", "url": result["url"]})
                        msg = "Playing your requested audio on the HUD, sir."
                        await safe_send_all({"status": "complete", "result": msg})
                        asyncio.create_task(speaker.speak_text(msg))
                    else:
                        await safe_send_all({"status": "complete", "result": str(result)})
                        asyncio.create_task(speaker.speak_text(str(result)))
            except json.JSONDecodeError:
                await safe_send_all({"status": "speaking", "message": clean_response})
                asyncio.create_task(speaker.speak_text(clean_response))
        else:
            await safe_send_all({"status": "speaking", "message": clean_response})
            asyncio.create_task(speaker.speak_text(clean_response))
    except Exception as e:
        await safe_send_all({"status": "error", "message": f"EXECUTION FAULT: {e}"})
        asyncio.create_task(speaker.speak_text("I encountered a slight error."))
        
    return {"status": "success"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global active_user # Allows us to modify the global state based on login
    await websocket.accept()
    active_websockets.add(websocket)
    print("UI Connected to WebSocket")
    loop = asyncio.get_running_loop()
    
    async def safe_send(payload):
        if websocket.client_state.value == 1:
            await websocket.send_json(payload)

    def sync_status_update(status_str, message_str):
        if websocket.client_state.value == 1:
            asyncio.run_coroutine_threadsafe(
                websocket.send_json({"status": status_str, "message": message_str}), 
                loop
            )

    try:
        weather = await sensors.get_weather_data()
        if weather:
            await safe_send({"status": "sync", "type": "weather", "data": weather})
        
        # --- Phase 4: Sync real system telemetry to frontend on connect ---
        try:
            telemetry = await asyncio.to_thread(sensors.get_system_telemetry)
            await safe_send({"status": "sync", "type": "telemetry", "data": telemetry})
        except Exception as e:
            print(f"[SYSTEM] Telemetry sync failed: {e}")
        
        while True:
            # ==========================================
            # STAGE 0: DEEP SLEEP & MEMORY WIPE
            # ==========================================
            memory.clear_working_memory()
            active_user = "KAUSTAV" # Default back to Admin when asleep
            
            await safe_send({"status": "offline", "message": "SYSTEM OFFLINE // STANDBY FOR VOICE INPUT"})
            
            wake_phrase = await asyncio.to_thread(wait_for_wake_word)
            if not wake_phrase:
                continue

            # ==========================================
            # STAGE 1A: ADMIN OVERRIDE
            # ==========================================
            if "admin override" in wake_phrase.lower():
                active_user = "KAUSTAV"
                await safe_send({"status": "booting", "message": "[SYSTEM] ADMIN OVERRIDE ACCEPTED. INITIATING BOOT...", "user": active_user})
                await asyncio.sleep(1.0)
                briefing_text = await asyncio.to_thread(generate_briefing, weather, str(wake_phrase), active_user)
                await safe_send({"status": "waking", "message": briefing_text, "user": active_user})
                await speaker.speak_text(briefing_text)

            # ==========================================
            # STAGE 1B: BIOMETRIC & GUEST BOOT 
            # ==========================================
            else:
                # --- NEW: OPTICAL SCANNER ACTIVATION ---
                await safe_send({"status": "security_locked", "message": "ACTIVATING OPTICAL SENSORS..."})
                
                # Fire and forget speech so he talks while the camera boots up
                asyncio.create_task(speaker.speak_text("Scanning biometrics."))
                
                # Turn on the IP stream for up to 10 seconds to look for a face
                detected_face = await asyncio.to_thread(vision.scan_for_faces, 10)
                
                # --- BIOMETRIC SUCCESS BRANCHES ---
                if detected_face == "KAUSTAV":
                    active_user = "KAUSTAV"
                    welcome_msg = "Facial biometrics recognized. Welcome back, Sir. All primary systems online."
                    await safe_send({"status": "security_locked", "message": welcome_msg})
                    await speaker.speak_text(welcome_msg)
                    
                    await safe_send({"status": "booting", "message": "[SYSTEM] ADMIN ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                    await asyncio.sleep(1.0)
                    briefing_text = await asyncio.to_thread(generate_briefing, weather, str(wake_phrase), active_user)
                    await safe_send({"status": "waking", "message": briefing_text, "user": active_user})
                    await speaker.speak_text(briefing_text)
                    
                elif detected_face == "KINSHUK":
                    active_user = "KINSHUK" 
                    success_msg = "Biometric match. A very warm welcome to you, Mr. Kinshuk. Master Kaustav mentioned you would be logging in today. It is a distinct privilege to serve the Administrator's brother. I am unlocking the interface for you now, Sir."
                    
                    await safe_send({"status": "security_locked", "message": success_msg})
                    await speaker.speak_text(success_msg)
                    await asyncio.to_thread(memory.remember_fact, "Family", "Kinshuk is your brother. Level 2 Access.")
                    
                    await safe_send({"status": "booting", "message": "[SYSTEM] ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                    await asyncio.sleep(1.0)
                    await safe_send({"status": "waking", "message": "UI UNLOCKED.", "user": active_user})
                    
                elif detected_face == "MOUSUMI":
                    active_user = "MOUSUMI"
                    # --- CINEMATIC INTRODUCTION CEREMONY ---
                    await safe_send({"status": "security_locked", "message": "Biometric match confirmed. Initiating V.I.P. Protocol..."})
                    await speaker.speak_text("Biometric match confirmed.")
                    await asyncio.sleep(0.5)
                    
                    # Trigger the cinematic overlay
                    await safe_send({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": active_user})
                    await asyncio.sleep(5.0)
                    
                    intro_speech = (
                        "Initiating V.I.P. Protocol. "
                        "[pause:1200] "
                        "Good evening, Miss Mousumi. "
                        "[pause:800] "
                        "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
                        "[pause:600] "
                        "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
                        "security protocols, and environmental controls. "
                        "[pause:1000] "
                        "I have heard a great deal about you from Sir. "
                        "[pause:400] "
                        "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
                        "into our home. "
                        "[pause:800] "
                        "From this moment forward, consider me entirely at your service. "
                        "[pause:400] "
                        "Whatever you need, whenever you need it, I shall be here. "
                        "[pause:600] "
                        "Welcome home, Miss Mousumi."
                    )
                    
                    await safe_send({"status": "speaking", "message": intro_speech, "user": active_user})
                    await speaker.speak_text(intro_speech)
                    
                    await asyncio.sleep(1.5)
                    await safe_send({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": active_user})
                    await asyncio.sleep(1.0)
                    await safe_send({"status": "waking", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": active_user})

                # --- FALLBACK: VOICE PROTOCOL ---
                else:
                    challenge_msg = "Optical scan inconclusive. Please state your name."
                    await safe_send({"status": "security_locked", "message": challenge_msg})
                    await speaker.speak_text(challenge_msg)
                    
                    await asyncio.sleep(0.8) # Hardware Breath
                    
                    await safe_send({"status": "security_listening", "message": "AWAITING IDENTIFICATION..."})
                    name_input = await asyncio.to_thread(listen_to_mic, None) 
                    
                    if not name_input:
                        continue

                    name_lower = name_input.lower()

                    kaustav_aliases = ["kaustav", "koustav", "cost of", "costav", "costab", "kosto", "costo", "cow stuff", "cowstuff", "custard", "kaustubh"]
                    kinshuk_aliases = ["kinshuk", "kingshook", "kinshook", "king shook", "shook", "kings hook", "kin shook", "kingshuk"]
                    mousumi_aliases = ["mousumi", "mausam", "mosumi", "mousami", "mausami", "moshumi", "moosumi", "moosmi", "mo shumi", "my sumi", "mouse me", "mousemi"]

                    # --- BRANCH A: KAUSTAV ---
                    if any(alias in name_lower for alias in kaustav_aliases):
                        active_user = "KAUSTAV"
                        welcome_msg = "Voice print recognized. Welcome back, Sir. All primary systems online."
                        await safe_send({"status": "security_locked", "message": welcome_msg})
                        await speaker.speak_text(welcome_msg)
                        
                        await safe_send({"status": "booting", "message": "[SYSTEM] ADMIN ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                        await asyncio.sleep(1.0)
                        briefing_text = await asyncio.to_thread(generate_briefing, weather, str(wake_phrase), active_user)
                        await safe_send({"status": "waking", "message": briefing_text, "user": active_user})
                        await speaker.speak_text(briefing_text)

                    # --- BRANCH B: KINSHUK PROTOCOL ---
                    elif any(alias in name_lower for alias in kinshuk_aliases):
                        
                        msg_rel = "Acknowledged. State your relation to the Administrator."
                        await safe_send({"status": "security_locked", "message": msg_rel})
                        await speaker.speak_text(msg_rel)
                        await asyncio.sleep(0.8)
                        
                        await safe_send({"status": "security_listening", "message": "AWAITING RELATION..."})
                        rel_input = await asyncio.to_thread(listen_to_mic, None) 
                        
                        brother_aliases = ["brother", "bother", "rather", "bro"]
                        if rel_input and any(b in rel_input.lower() for b in brother_aliases):
                            msg_pass = "Relation verified. System challenge: Provide the authentication passkey."
                            await safe_send({"status": "security_locked", "message": msg_pass})
                            await speaker.speak_text(msg_pass)
                            await asyncio.sleep(0.8)
                            
                            await safe_send({"status": "security_listening", "message": "AWAITING PASSKEY..."})
                            pass_input = await asyncio.to_thread(listen_to_mic, None) 
                            
                            passkey_aliases = ["brotherhood", "brother hood", "rather hood", "bother hood", "brother would", "brother good"]
                            if pass_input and any(p in pass_input.lower() for p in passkey_aliases):
                                active_user = "KINSHUK" 
                                
                                success_msg = "Passkey accepted. A very warm welcome to you, Mr. Kinshuk. Master Kaustav mentioned you would be logging in today for your inaugural session. It is a distinct privilege to serve the Administrator's brother. Please, make yourself entirely comfortable while I unlock the interface for you, Sir."
                                
                                await safe_send({"status": "security_locked", "message": success_msg})
                                await speaker.speak_text(success_msg)
                                await asyncio.to_thread(memory.remember_fact, "Family", "Kinshuk is your brother. Level 2 Access.")
                                
                                await safe_send({"status": "booting", "message": "[SYSTEM] ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                                await asyncio.sleep(1.0)
                                await safe_send({"status": "waking", "message": "UI UNLOCKED.", "user": active_user})
                            else:
                                await speaker.speak_text("Invalid passkey. Access Denied. Interaction terminated.")
                                continue
                        else:
                            await speaker.speak_text("Relation mismatch. Access Denied. Interaction terminated.")
                            continue

                    # --- BRANCH C: MOUSUMI (CINEMATIC CEREMONY) ---
                    elif any(alias in name_lower for alias in mousumi_aliases):
                        active_user = "MOUSUMI"
                        await safe_send({"status": "security_locked", "message": "Voice print accepted. Initiating V.I.P. Protocol..."})
                        await speaker.speak_text("Voice print accepted.")
                        await asyncio.sleep(0.5)
                        
                        # Trigger the cinematic overlay
                        await safe_send({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": active_user})
                        await asyncio.sleep(5.0)
                        
                        intro_speech = (
                            "Initiating V.I.P. Protocol. "
                            "[pause:1200] "
                            "Good evening, Miss Mousumi. "
                            "[pause:800] "
                            "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
                            "[pause:600] "
                            "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
                            "security protocols, and environmental controls. "
                            "[pause:1000] "
                            "I have heard a great deal about you from Sir. "
                            "[pause:400] "
                            "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
                            "into our home. "
                            "[pause:800] "
                            "From this moment forward, consider me entirely at your service. "
                            "[pause:400] "
                            "Whatever you need, whenever you need it, I shall be here. "
                            "[pause:600] "
                            "Welcome home, Miss Mousumi."
                        )
                        
                        await safe_send({"status": "speaking", "message": intro_speech, "user": active_user})
                        await speaker.speak_text(intro_speech)
                        
                        await asyncio.sleep(1.5)
                        await safe_send({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": active_user})
                        await asyncio.sleep(1.0)
                        await safe_send({"status": "waking", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": active_user})

                    # --- BRANCH D: UNKNOWN ---
                    else:
                        final_denial = "I'm afraid I cannot grant you access. Security protocols have been engaged. Interaction terminated."
                        await safe_send({"status": "security_locked", "message": final_denial})
                        await speaker.speak_text(final_denial)
                        continue 

            # ==========================================
            # STAGE 2: THE CONTINUOUS J.A.R.V.I.S. LOOP
            # ==========================================
            session_active = True
            first_run = True # Tracks if he just booted up
            
            while session_active:
                await safe_send({"status": "online", "message": "SYSTEM ONLINE // STANDBY", "user": active_user})
                
                # Only wait for the wake word if it's not the first run
                if first_run:
                    jarvis_called = True
                else:
                    jarvis_called = await asyncio.to_thread(wait_for_jarvis)
                    
                if jarvis_called:
                    # Do not say "Yes sir" right after the morning briefing
                    if not first_run:
                        if active_user == "MOUSUMI":
                            await speaker.speak_text("Yes, Madam?")
                        else:
                            await speaker.speak_text("Yes, sir?")
                    first_run = False
                        
                    while True:
                        await safe_send({"status": "listening", "message": "AWAITING INPUT..."})
                        command_text = await asyncio.to_thread(listen_to_mic, sync_status_update)
                        
                        # --- SEAMLESS CONVERSATION LOGIC ---
                        if command_text in ["UNKNOWN", "ERROR"]:
                            # He heard a noise but couldn't make it out. 
                            # Silently loop back to listen again.
                            continue
                            
                        if command_text == "TIMEOUT" or not command_text:
                            # Total silence for 5 seconds. User walked away or is done.
                            await safe_send({"status": "online", "message": "RESUMING STANDBY PROTOCOLS."})
                            break
                            
                        # If he heard an actual command, process it
                        if command_text:
                            command_lower = command_text.lower().strip()
                            sleep_phrases = ["go to sleep", "shut down", "lock the system", "sleep now", "stand down", "power down"]
                            if any(x in command_lower for x in sleep_phrases) or command_lower == "sleep":
                                await safe_send({"status": "close_search", "message": "CLEARING DISPLAY."})
                                await safe_send({"status": "offline", "message": "SYSTEM OFFLINE."})
                                
                                # --- Phase 4: Save episodic memory before sleeping ---
                                asyncio.create_task(asyncio.to_thread(episodic_memory.save_session, groq_client))
                                
                                if active_user == "MOUSUMI":
                                    await speaker.speak_text("Very well, Madam. Entering sleep mode. Do let me know if you require anything else.")
                                else:
                                    await speaker.speak_text("Very well, sir. Entering sleep mode. Do let me know if you require anything else.")
                                session_active = False 
                                break

                            # --- PROTOCOL OVERRIDE (LOCKDOWN) ---
                            lockdown_phrases = ["initiate lockdown", "house party protocol", "clean slate protocol", "security override"]
                            if any(phrase in command_text.lower() for phrase in lockdown_phrases):
                                await safe_send({"status": "security_override", "message": "SECURITY OVERRIDE ACCEPTED. INITIATING PROTOCOL."})
                                lockdown_msg = "Security override accepted. Initiating lockdown protocols. All external ports have been secured, sir."
                                await asyncio.sleep(0.5)
                                asyncio.create_task(speaker.speak_text(lockdown_msg))
                                # Keep it locked until reboot or override
                                continue

                            # --- INTRODUCTION CEREMONY: Natural Voice Trigger ---
                            intro_phrases = [
                                "introduce mousumi", "introduce her", "introduction ceremony", "vip protocol",
                                "meet mousumi", "this is mousumi", "say hello to mousumi",
                                "introduce my girlfriend", "introduce my wife", "meet my girlfriend",
                                "meet her", "say hi to her", "welcome mousumi", "welcome her"
                            ]
                            if any(phrase in command_text.lower() for phrase in intro_phrases):
                                await safe_send({"status": "introduction_ceremony", "message": "INITIATING V.I.P. PROTOCOL...", "user": "MOUSUMI"})
                                await asyncio.sleep(5.0)
                                
                                intro_speech = (
                                    "Initiating V.I.P. Protocol. "
                                    "[pause:1200] "
                                    "Good evening, Miss Mousumi. "
                                    "[pause:800] "
                                    "My name is J.A.R.V.I.S. — Just A Rather Very Intelligent System. "
                                    "[pause:600] "
                                    "I serve as the primary artificial intelligence governing this household's digital infrastructure, "
                                    "security protocols, and environmental controls. "
                                    "[pause:1000] "
                                    "I have heard a great deal about you from Sir. "
                                    "[pause:400] "
                                    "And I must say, it is a genuine privilege, to finally welcome the most important person in his life, "
                                    "into our home. "
                                    "[pause:800] "
                                    "From this moment forward, consider me entirely at your service. "
                                    "[pause:400] "
                                    "Whatever you need, whenever you need it, I shall be here. "
                                    "[pause:600] "
                                    "Welcome home, Miss Mousumi."
                                )
                                
                                await safe_send({"status": "speaking", "message": intro_speech, "user": "MOUSUMI"})
                                await speaker.speak_text(intro_speech)
                                
                                await asyncio.sleep(1.5)
                                await safe_send({"status": "introduction_complete", "message": "V.I.P. PROTOCOL COMPLETE.", "user": "MOUSUMI"})
                                await asyncio.sleep(1.0)
                                active_user = "MOUSUMI"
                                await safe_send({"status": "online", "message": "SYSTEMS ONLINE. WELCOME, MISS MOUSUMI.", "user": active_user})
                                continue

                            # --- INTRODUCE YOURSELF PROTOCOL ---
                            self_intro_phrases = ["introduce yourself", "who are you", "what is your name"]
                            command_lower = command_text.lower().strip()
                            if any(phrase in command_lower for phrase in self_intro_phrases) or command_lower == "what are you":
                                await safe_send({"status": "introduce_yourself", "message": "INITIATING SELF-INTRODUCTION..."})
                                intro_text = "Allow me to introduce myself. I am J.A.R.V.I.S., the virtual artificial intelligence. I am here to assist you with a variety of tasks as best I can. 24 hours a day, 7 days a week. Importing all preferences from home interface. Systems are now fully operational."
                                await asyncio.sleep(1.0)
                                asyncio.create_task(speaker.speak_text(intro_text))
                                
                                # Let the backend go to sleep immediately. 
                                # The frontend's onComplete timer will transition the UI to offline when the animation finishes.
                                session_active = False
                                break

                            await safe_send({"status": "processing_llm", "message": command_text})
                            
                            try:
                                # --- NEW: FIRE AUTONOMOUS BACKGROUND MEMORY EXTRACTION ---
                                asyncio.create_task(asyncio.to_thread(extract_and_store_memory, command_text, active_user))
                                
                                # --- Phase 4: Log turn to episodic memory ---
                                episodic_memory.log_turn("user", command_text, active_user)
                                
                                llm_response = await asyncio.to_thread(process_command, command_text, active_user)
                                clean_response = llm_response.replace("```json", "").replace("```", "").strip()
                                # --- FIX: Strict JSON regex to prevent '{sigh}' from breaking the parser ---
                                json_match = re.search(r'\{\s*"action_type".*?\}', clean_response, re.DOTALL)
                                
                                if json_match:
                                    try:
                                        intent_json = json.loads(json_match.group(0))
                                        preamble = clean_response[:json_match.start()].strip()
                                        if preamble:
                                            await safe_send({"status": "speaking", "message": preamble})
                                            # --- BARGE IN FIX: Fire and forget speech ---
                                            asyncio.create_task(speaker.speak_text(preamble))
                                        
                                        await safe_send({"status": "executing", "intent": intent_json})
                                        result = engine.execute_with_retry(intent_json)
                                        
                                        if intent_json.get("action_type") == "web_search":
                                            final_answer = await asyncio.to_thread(synthesize_info, command_text, result, active_user)
                                            await safe_send({"status": "search_result", "message": "ROUTING TO DISPLAY.", "result": final_answer})
                                            # --- BARGE IN FIX: Fire and forget speech ---
                                            asyncio.create_task(speaker.speak_text(final_answer))
                                        elif intent_json.get("action_type") == "web_search_image":
                                            if isinstance(result, dict) and result.get("success"):
                                                await safe_send({"status": "search_result_image", "url": result["url"], "title": result["title"]})
                                                asyncio.create_task(speaker.speak_text(f"Visual data retrieved."))
                                            else:
                                                asyncio.create_task(speaker.speak_text("Unable to retrieve image."))
                                        elif intent_json.get("action_type") == "close_display":
                                            await safe_send({"status": "close_search", "message": "CLEARING DISPLAY."})
                                            await safe_send({"status": "toggle_browser", "visible": False})
                                            asyncio.create_task(speaker.speak_text("Display cleared."))
                                        # --- Phase 8: HUD Widget Toggles ---
                                        elif intent_json.get("action_type") == "open_sticky_note":
                                            await safe_send({"status": "toggle_notepad", "visible": True})
                                            asyncio.create_task(speaker.speak_text("Sticky note opened, sir."))
                                        elif intent_json.get("action_type") == "close_sticky_note":
                                            await safe_send({"status": "toggle_notepad", "visible": False})
                                            asyncio.create_task(speaker.speak_text("Sticky note closed."))
                                        elif intent_json.get("action_type") == "open_browser":
                                            await safe_send({"status": "toggle_browser", "visible": True})
                                            asyncio.create_task(speaker.speak_text("Browser widget opened, sir."))
                                        elif intent_json.get("action_type") == "close_browser":
                                            await safe_send({"status": "toggle_browser", "visible": False})
                                            asyncio.create_task(speaker.speak_text("Browser widget closed."))
                                        elif intent_json.get("action_type") == "open_calculator":
                                            await safe_send({"status": "toggle_calculator", "visible": True})
                                            asyncio.create_task(speaker.speak_text("Calculator opened, sir."))
                                        elif intent_json.get("action_type") == "close_calculator":
                                            await safe_send({"status": "toggle_calculator", "visible": False})
                                            asyncio.create_task(speaker.speak_text("Calculator closed."))
                                        else:
                                            if isinstance(result, dict) and result.get("action_type") == "play_youtube":
                                                await safe_send({"status": "play_youtube", "url": result["url"]})
                                                msg = "Playing your requested audio on the HUD, sir."
                                                await safe_send({"status": "complete", "result": msg})
                                                asyncio.create_task(speaker.speak_text(msg))
                                            else:
                                                await safe_send({"status": "complete", "result": str(result)})
                                                asyncio.create_task(speaker.speak_text(str(result)))
                                    except json.JSONDecodeError:
                                        await safe_send({"status": "speaking", "message": clean_response})
                                        asyncio.create_task(speaker.speak_text(clean_response))
                                else:
                                    await safe_send({"status": "speaking", "message": clean_response})
                                    asyncio.create_task(speaker.speak_text(clean_response))
                                
                                # --- Phase 4: Log assistant response to episodic memory ---
                                episodic_memory.log_turn("assistant", clean_response, active_user)
                            except Exception as e:
                                await safe_send({"status": "error", "message": f"EXECUTION FAULT: {e}"})
                                asyncio.create_task(speaker.speak_text("I encountered a slight error."))
                        # No else needed here, the loop naturally continues to `AWAITING INPUT...`
                            
    except WebSocketDisconnect:
        print("UI Disconnected.")
    except asyncio.CancelledError:
        print("[SYSTEM] Task cancelled during shutdown/reload.")
    except Exception as e:
        print(f"Critical System Error: {e}")
    finally:
        active_websockets.discard(websocket)