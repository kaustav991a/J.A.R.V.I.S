from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager 
import json
import asyncio
import sensors
from datetime import datetime 
import re

import speaker 
from brain import process_command, synthesize_info, generate_briefing 
from action_engine import ActionEngine
from recorder import listen_to_mic
from wakeword import wait_for_wake_word, wait_for_jarvis, is_shutting_down 

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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("UI Connected to WebSocket")
    loop = asyncio.get_running_loop()
    
    # --- PHASE 2/3: The Safe-Send Wrapper ---
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
        # STAGE 0: INITIAL SYSTEM SYNC
        weather = await sensors.get_weather_data()
        if weather:
            await safe_send({"status": "sync", "type": "weather", "data": weather})
        
        # STAGE 1: THE CINEMATIC BOOT SEQUENCE
        await safe_send({"status": "offline", "message": "SYSTEM OFFLINE // STANDBY FOR VOICE INPUT"})
        
        boot_triggered = await asyncio.to_thread(wait_for_wake_word)
        
        if boot_triggered:
            # --- PHASE 2: Staggered UI Boot Animation (Paced for Readability) ---
            await safe_send({"status": "booting", "message": "[SYSTEM] INITIATING BOOT PROTOCOLS..."})
            await asyncio.sleep(2.5) 
            
            await safe_send({"status": "uplinking", "message": "[NETWORK] UPLINKING TO SATELLITE ARRAYS..."})
            await asyncio.sleep(2.5)
            
            await safe_send({"status": "uplink_established", "message": "[LINK] UPLINK ESTABLISHED. NEURAL BRIDGE ACTIVE."})
            await asyncio.sleep(2.0)

            # --- DYNAMIC MORNING BRIEFING ---
            await safe_send({"status": "processing_llm", "message": "[SYSTEM] COMPILING MORNING BRIEFING..."})
            
            briefing_text = await asyncio.to_thread(generate_briefing, weather)
                
            await safe_send({"status": "waking", "message": briefing_text})
            await speaker.speak_text(briefing_text)
            
            # STAGE 2: THE CONTINUOUS J.A.R.V.I.S. LOOP
            while True:
                await safe_send({"status": "online", "message": "SYSTEM ONLINE // STANDING BY"})
                jarvis_called = await asyncio.to_thread(wait_for_jarvis)
                    
                if jarvis_called:
                    await speaker.speak_text("Yes, sir?")
                    
                    # STAGE 3: ACTIVE LISTENING
                    while True:
                        await safe_send({"status": "listening", "message": "AWAITING INPUT..."})
                        command_text = await asyncio.to_thread(listen_to_mic, sync_status_update)
                        
                        if command_text:
                            await safe_send({"status": "processing_llm", "message": "[BRAIN] ANALYZING REQUEST..."})
                            
                            try:
                                llm_response = await asyncio.to_thread(process_command, command_text)
                                
                                clean_response = llm_response.replace("```json", "").replace("```", "").strip()
                                json_match = re.search(r'\{.*\}', clean_response, re.DOTALL)
                                
                                if json_match:
                                    try:
                                        intent_json = json.loads(json_match.group(0))
                                        
                                        preamble = clean_response[:json_match.start()].strip()
                                        if preamble:
                                            await safe_send({"status": "speaking", "message": preamble})
                                            await speaker.speak_text(preamble)
                                        
                                        await safe_send({"status": "executing", "intent": intent_json})
                                        result = engine.execute(intent_json)
                                        
                                        # --- PHASE 3: SECONDARY DISPLAY PROTOCOLS ---
                                        if intent_json.get("action_type") == "web_search":
                                            await safe_send({"status": "searching", "message": "[ACTION] PULLING DATA FROM GLOBAL STREAMS..."})
                                            final_answer = await asyncio.to_thread(synthesize_info, command_text, result)
                                            
                                            await safe_send({
                                                "status": "search_result", 
                                                "message": "ANALYSIS COMPLETE. ROUTING TO SECONDARY DISPLAY.",
                                                "result": final_answer
                                            })
                                            await speaker.speak_text(final_answer)
                                            
                                        # --- NEW: THE IMAGE DISPLAY PROTOCOL ---
                                        elif intent_json.get("action_type") == "web_search_image":
                                            await safe_send({"status": "searching", "message": "[ACTION] PULLING VISUAL DATA..."})
                                            
                                            if isinstance(result, dict) and result.get("success"):
                                                image_url = result["url"]
                                                image_title = result["title"]
                                                
                                                await safe_send({
                                                    "status": "search_result_image", 
                                                    "message": "VISUAL DATA SECURED. ROUTING TO DISPLAY.",
                                                    "url": image_url,
                                                    "title": image_title
                                                })
                                                await speaker.speak_text(f"I have pulled up the requested image, sir.")
                                            else:
                                                await speaker.speak_text("I apologize, sir, but I was unable to retrieve a clear image for that request.")
                                            
                                        # --- THE DISMISS PROTOCOL ---
                                        elif intent_json.get("action_type") == "close_display":
                                            await safe_send({"status": "close_search", "message": "[ACTION] CLEARING SECONDARY DISPLAY."})
                                            await speaker.speak_text("Display cleared, sir.")
                                            
                                        else:
                                            await safe_send({"status": "complete", "result": result})
                                            await speaker.speak_text(result)
                                            
                                    except json.JSONDecodeError:
                                        await safe_send({"status": "speaking", "message": clean_response})
                                        await speaker.speak_text(clean_response)
                                        
                                else:
                                    await safe_send({"status": "speaking", "message": clean_response})
                                    await speaker.speak_text(clean_response)
                                    
                            except Exception as e:
                                await safe_send({"status": "error", "message": f"[ERROR] EXECUTION FAULT: {e}"})
                                await speaker.speak_text("I encountered a slight error, sir.")
                        else:
                            await safe_send({"status": "online", "message": "RESUMING STANDBY PROTOCOLS."})
                            await speaker.speak_text("Returning to standby.")
                            break 
                            
    except WebSocketDisconnect:
        print("UI Disconnected. Awaiting reconnection...")
    except AttributeError as e:
        if "'NoneType' object has no attribute 'close'" in str(e):
            print("[ERROR] Microphone is locked by a ghost thread. Please restart the Python backend.")
        else:
            print(f"Global AttributeError: {e}")
    except Exception as e:
        print(f"Global Error: {e}")