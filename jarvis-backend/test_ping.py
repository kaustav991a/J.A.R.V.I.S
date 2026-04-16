import asyncio
import websockets
import json

async def test_jarvis():
    uri = "ws://localhost:8000/ws"
    
    # Connect to the Hub
    async with websockets.connect(uri) as ws:
        print("Connected to Hub...")
        
        # The command we are testing
        command = "Jarvis, open the calculator."
        print(f"Sending: '{command}'")
        await ws.send(command)
        
        # Listen for the live updates
        while True:
            response = await ws.recv()
            data = json.loads(response)
            print(f"Update: {data['status'].upper()} -> {data}")
            
            if data.get("status") in ["complete", "error"]:
                break

if __name__ == "__main__":
    asyncio.run(test_jarvis())