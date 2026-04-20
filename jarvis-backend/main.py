from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager 
import json
import asyncio
import sensors
from datetime import datetime 
import re
import random  

import speaker 
import memory 
from brain import process_command, synthesize_info, generate_briefing 
from action_engine import ActionEngine
from recorder import listen_to_mic
from wakeword import wait_for_wake_word, wait_for_jarvis, is_shutting_down 

# --- Global Session Tracker ---
active_user = "KAUSTAV" 

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("\n[SYSTEM] Gracefully shutting down...")
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global active_user # Allows us to modify the global state based on login
    await websocket.accept()
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
                briefing_text = await asyncio.to_thread(generate_briefing, weather, str(wake_phrase))
                await safe_send({"status": "waking", "message": briefing_text, "user": active_user})
                await speaker.speak_text(briefing_text)

            # ==========================================
            # STAGE 1B: GUEST BOOT (Sequential Interrogation)
            # ==========================================
            else:
                # 1. NAME CHALLENGE
                challenge_msg = "Unrecognized voice protocol. Please state your name."
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
                    await safe_send({"status": "waking", "message": "UI UNLOCKED.", "user": active_user})

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

                # --- BRANCH C: MOUSUMI ---
                elif any(alias in name_lower for alias in mousumi_aliases):
                    active_user = "MOUSUMI" 
                    mousumi_welcome = "Access granted. Good evening, Miss Mousumi. Master Kaustav informed me to expect your arrival. It is an absolute honor to welcome you to the house for the very first time. Please, treat this home as your own. I am at your complete disposal for absolutely anything you might require. Unlocking the interface for your session now, Madam."
                    
                    await safe_send({"status": "security_locked", "message": mousumi_welcome})
                    await speaker.speak_text(mousumi_welcome)
                    
                    await safe_send({"status": "booting", "message": "[SYSTEM] VIP ACCESS GRANTED. UNLOCKING UI...", "user": active_user})
                    await asyncio.sleep(1.0)
                    await safe_send({"status": "waking", "message": "UI UNLOCKED.", "user": active_user})

                # --- BRANCH D: UNKNOWN ---
                else:
                    final_denial = "Access Denied. Interaction terminated."
                    await safe_send({"status": "security_locked", "message": final_denial})
                    await speaker.speak_text(final_denial)
                    continue 

            # ==========================================
            # STAGE 2: THE CONTINUOUS J.A.R.V.I.S. LOOP
            # ==========================================
            session_active = True
            while session_active:
                await safe_send({"status": "online", "message": "SYSTEM ONLINE // STANDBY", "user": active_user})
                jarvis_called = await asyncio.to_thread(wait_for_jarvis)
                    
                if jarvis_called:
                    if active_user == "MOUSUMI":
                        await speaker.speak_text("Yes, Madam?")
                    else:
                        await speaker.speak_text("Yes, sir?")
                        
                    while True:
                        await safe_send({"status": "listening", "message": "AWAITING INPUT..."})
                        command_text = await asyncio.to_thread(listen_to_mic, sync_status_update)
                        
                        if command_text:
                            sleep_phrases = ["go to sleep", "shut down", "lock the system", "sleep now", "stand down", "power down"]
                            if any(x in command_text.lower() for x in sleep_phrases):
                                await safe_send({"status": "close_search", "message": "CLEARING DISPLAY."})
                                await safe_send({"status": "security_locked", "message": "LOCKING SYSTEM..."})
                                
                                if active_user == "MOUSUMI":
                                    await speaker.speak_text("Very well, Madam. Powering down the interface. [sigh]")
                                else:
                                    await speaker.speak_text("Very well, sir. Powering down the interface. [sigh]")
                                session_active = False 
                                break

                            await safe_send({"status": "processing_llm", "message": "[BRAIN] ANALYZING REQUEST..."})
                            
                            sir_fillers = [
                                "Just a moment, sir.",
                                "Let me check that for you, sir.",
                                "Accessing the global data-streams now.",
                                "One moment, sir.",
                                "Processing your request, sir."
                            ]
                            madam_fillers = [
                                "Right away, Madam.",
                                "Let me look into that for you, Miss Mousumi.",
                                "Just a moment, Madam.",
                                "Allow me to check, Madam."
                            ]
                            
                            chosen_filler = random.choice(madam_fillers if active_user == "MOUSUMI" else sir_fillers)
                            # AWAIT the filler so it finishes before deep processing
                            await speaker.speak_text(chosen_filler)
                            
                            try:
                                llm_response = await asyncio.to_thread(process_command, command_text, active_user)
                                clean_response = llm_response.replace("```json", "").replace("```", "").strip()
                                json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
                                
                                if json_match:
                                    try:
                                        intent_json = json.loads(json_match.group(0))
                                        preamble = clean_response[:json_match.start()].strip()
                                        if preamble:
                                            await safe_send({"status": "speaking", "message": preamble})
                                            # --- BARGE IN FIX: Fire and forget speech ---
                                            asyncio.create_task(speaker.speak_text(preamble))
                                        
                                        await safe_send({"status": "executing", "intent": intent_json})
                                        result = engine.execute(intent_json)
                                        
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
                                            asyncio.create_task(speaker.speak_text("Display cleared."))
                                        else:
                                            await safe_send({"status": "complete", "result": result})
                                            asyncio.create_task(speaker.speak_text(result))
                                    except json.JSONDecodeError:
                                        await safe_send({"status": "speaking", "message": clean_response})
                                        asyncio.create_task(speaker.speak_text(clean_response))
                                else:
                                    await safe_send({"status": "speaking", "message": clean_response})
                                    asyncio.create_task(speaker.speak_text(clean_response))
                            except Exception as e:
                                await safe_send({"status": "error", "message": f"EXECUTION FAULT: {e}"})
                                asyncio.create_task(speaker.speak_text("I encountered a slight error."))
                        else:
                            await safe_send({"status": "online", "message": "RESUMING STANDBY PROTOCOLS."})
                            asyncio.create_task(speaker.speak_text("Returning to standby."))
                            break 
                            
    except WebSocketDisconnect:
        print("UI Disconnected.")
    except Exception as e:
        print(f"Critical System Error: {e}")