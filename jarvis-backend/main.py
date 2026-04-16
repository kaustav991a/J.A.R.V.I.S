from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json
import asyncio

from brain import process_command
from action_engine import ActionEngine
from recorder import listen_to_mic

app = FastAPI()

# 1. CORS Setup to allow React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ActionEngine()

# 2. Simple Health Check (To test your browser)
@app.get("/")
def read_root():
    return {"status": "J.A.R.V.I.S. Backend is Online"}

# 3. The WebSocket Connection
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("UI Connected to WebSocket")
    
    try:
        while True:
            data = await websocket.receive_text()
            
            if data == "START_LISTENING":
                await websocket.send_json({"status": "online", "message": "Listening..."})
                
                command_text = await asyncio.to_thread(listen_to_mic)
                
                if command_text:
                    await websocket.send_json({"status": "processing_llm", "text": command_text})
                    llm_response = await asyncio.to_thread(process_command, command_text)
                    
                    try:
                        intent_json = json.loads(llm_response)
                        await websocket.send_json({"status": "executing", "intent": intent_json})
                        
                        result = engine.execute(intent_json)
                        await websocket.send_json({"status": "complete", "result": result})
                    except Exception as e:
                        await websocket.send_json({"status": "error", "message": f"Failed to parse brain output: {e}"})
                else:
                    await websocket.send_json({"status": "online", "message": "No speech detected."})
            
    except WebSocketDisconnect:
        print("UI Disconnected")