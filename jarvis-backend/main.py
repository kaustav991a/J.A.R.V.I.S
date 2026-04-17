from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio
import speaker 

from brain import process_command
from action_engine import ActionEngine
from recorder import listen_to_mic
from wakeword import wait_for_wake_word 

app = FastAPI()

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
    
    # Grab the active event loop to allow the mic thread to talk to the websocket
    loop = asyncio.get_running_loop()
    
    def sync_status_update(status_str, message_str):
        """Pushes real-time text updates to React from inside the audio thread"""
        asyncio.run_coroutine_threadsafe(
            websocket.send_json({"status": status_str, "message": message_str}), 
            loop
        )

    try:
        while True:
            # 1. Go into standby and update the UI
            await websocket.send_json({"status": "online", "message": "Standby. Say 'Jarvis' to wake me..."})
            
            # --- THE SAFETY CATCH ---
            # 2. Run the wake word loop in the background, but catch the ghost crash
            try:
                wake_word_detected = await asyncio.to_thread(wait_for_wake_word)
            except asyncio.CancelledError:
                print("Wake word thread cancelled safely by UI disconnect.")
                break # Exit the loop cleanly
            
            if wake_word_detected:
                # Verbally confirm he is awake
                speaker.speak_text("Yes, sir?")
                
                # 3. Trigger the active listening sequence and pass the UI updater
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
                            speaker.speak_text(result)
                        else:
                            speaker.speak_text("Task completed, sir.")
                            
                    except json.JSONDecodeError:
                        await websocket.send_json({"status": "speaking", "message": llm_response})
                        speaker.speak_text(llm_response)
                            
                    except Exception as e:
                        await websocket.send_json({"status": "error", "message": f"Execution failed: {e}"})
                        speaker.speak_text("I encountered an error processing that request, sir.")
                else:
                    await websocket.send_json({"status": "online", "message": "No speech detected."})
                    
    except WebSocketDisconnect:
        print("UI Disconnected")
    except Exception as e:
        print(f"Global Error: {e}")