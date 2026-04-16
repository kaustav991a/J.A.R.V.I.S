from openai import OpenAI
import json

# Connect to your local Ollama instance (No API key needed!)
client = OpenAI(base_url="http://localhost:11434/v1", api_key="jarvis-local")

# The System Prompt forces the LLM to act strictly as a logic engine
SYSTEM_PROMPT = """
You are J.A.R.V.I.S., a system control AI. 
You DO NOT output conversational text unless explicitly asked a general question.
If the user requests a PC action, you MUST output ONLY valid JSON matching this exact schema:

{"action_type": "launch_app", "target": "name"}
{"action_type": "close_app", "target": "name"}
{"action_type": "delete_file", "target": "path"}

Example: If the user says "Jarvis, open Spotify", you output EXACTLY:
{"action_type": "launch_app", "target": "spotify"}
"""

def process_command(transcribed_text):
    print(f"[BRAIN] Thinking about: '{transcribed_text}'...")
    try:
        response = client.chat.completions.create(
            model="llama3", # Make sure this matches the model you pulled in Ollama!
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcribed_text}
            ],
            # Temperature 0.1 keeps it deterministic so it doesn't hallucinate extra text
            temperature=0.1 
        )
        
        output = response.choices[0].message.content
        print(f"[BRAIN] Raw Output: {output}")
        return output
        
    except Exception as e:
        print(f"[BRAIN] Connection Error: Is Ollama running? Details: {e}")
        return json.dumps({"action_type": "error", "target": str(e)})