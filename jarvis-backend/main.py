from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager 
import json
import asyncio
from datetime import datetime 

import speaker 
from brain import process_command
from action_engine import ActionEngine
from recorder import listen_to_mic
from wakeword import wait_for_wake_word, wait_for_jarvis, is_shutting_down 

# --- THE SHUTDOWN PROTOCOL ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("\n[SYSTEM] Gracefully shutting down... Releasing microphone lock.")
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
    
    def sync_status_update(status_str, message_str):
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({"status": status_str, "message": message_str}), 
            loop
        )

    try:
        # ==========================================
        # STAGE 1: THE BOOT SEQUENCE
        # ==========================================
        await websocket.send_json({"status": "offline", "message": "System Offline. Say 'Wake Up'."})
        
        try:
            boot_triggered = await asyncio.to_thread(wait_for_wake_word)
        except asyncio.CancelledError:
            print("Boot thread cancelled.")
            return 
        
        if boot_triggered:
            current_hour = datetime.now().hour
            if current_hour < 12:
                greeting = "Good morning, sir."
            elif current_hour < 18:
                greeting = "Good afternoon, sir."
            else:
                greeting = "Good evening, sir."
                
            await websocket.send_json({"status": "waking", "message": greeting})
            # ---> THE FIX: Added await <---
            await speaker.speak_text(greeting)
            
            # ==========================================
            # STAGE 2: THE CONTINUOUS J.A.R.V.I.S. LOOP
            # ==========================================
            while True:
                await websocket.send_json({"status": "online", "message": "Standing by..."})
                
                try:
                    jarvis_called = await asyncio.to_thread(wait_for_jarvis)
                except asyncio.CancelledError:
                    break
                    
                if jarvis_called:
                    # ---> THE FIX: Added await <---
                    await speaker.speak_text("Yes, sir?")
                    
                    # ==========================================
                    # STAGE 3: ACTIVE LISTENING
                    # ==========================================
                    while True:
                        await websocket.send_json({"status": "listening", "message": "Listening..."})
                        command_text = await asyncio.to_thread(listen_to_mic, sync_status_update)
                        
                        if command_text:
                            await websocket.send_json({"status": "processing_llm", "message": f"Thinking about: '{command_text}'..."})
                            llm_response = await asyncio.to_thread(process_command, command_text)
                            
                            try:
                                intent_json = json.loads(llm_response)
                                await websocket.send_json({"status": "executing", "intent": intent_json})
                                
                                result = engine.execute(intent_json)
                                await websocket.send_json({"status": "complete", "result": result})
                                
                                if isinstance(result, str):
                                    await speaker.speak_text(result)
                                else:
                                    await speaker.speak_text("Task completed, sir.")
                                    
                            except json.JSONDecodeError:
                                await websocket.send_json({"status": "speaking", "message": llm_response})
                                await speaker.speak_text(llm_response)
                                
                            except Exception as e:
                                await websocket.send_json({"status": "error", "message": f"Execution failed: {e}"})
                                await speaker.speak_text("I encountered a slight error, sir.")
                                
                        else:
                            await websocket.send_json({"status": "online", "message": "Going back to standby."})
                            await speaker.speak_text("Returning to standby.")
                            break 
                            
    except WebSocketDisconnect:
        print("UI Disconnected")
    except Exception as e:
        print(f"Global Error: {e}")