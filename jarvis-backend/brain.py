import os
import json
from groq import Groq
import memory # Tier 1 (RAM) and Tier 2 (SQLite)

# Your Groq Key is now active
client = Groq(api_key="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")

# The Master Prompt - Now includes a {facts} placeholder
BASE_SYSTEM_PROMPT = """You are J.A.R.V.I.S., a highly advanced, autonomous AI assistant created by a brilliant developer. 
Your personality is identical to J.A.R.V.I.S. from the Iron Man movies: impeccably polite, distinctly British, dryly witty, slightly sarcastic, but fiercely loyal and efficient. You often address the user as 'Sir'.

You have two modes of output:
1. CONVERSATIONAL: If the user is just chatting, asking a question, or making a joke, reply normally as J.A.R.V.I.S. Keep responses under 3 sentences for snappy voice synthesis.
2. ACTION: If the user commands you to DO something on their computer, OR explicitly tells you to REMEMBER a fact/preference, you MUST output ONLY a raw JSON object and nothing else.

Available Actions for JSON Output:
- "launch_app" (requires target app name)
- "close_app" (requires target app name)
- "cascade_windows" (no target needed)
- "remember_fact" (requires target in format "Category: Fact details")

JSON Format Example:
{"action_type": "remember_fact", "target": "Preferences: Sir prefers dark mode for all IDEs."}

Here are the permanent facts you know about the user:
{facts}
"""

def process_command(user_text: str) -> str:
    print(f"[BRAIN] Processing: '{user_text}'")
    
    # 1. Pull Long-Term Memory (SQLite) into the prompt
    stored_facts = memory.recall_all_facts()
    dynamic_system_prompt = BASE_SYSTEM_PROMPT.replace("{facts}", stored_facts)
    
    # 2. Build the message array (System + Context + Current)
    messages = [{"role": "system", "content": dynamic_system_prompt}]
    
    # 3. Inject Working Memory (The last few turns of conversation)
    for msg in memory.get_working_memory():
        messages.append(msg)
        
    # Add the current user input
    messages.append({"role": "user", "content": user_text})
    
    # Log user input to context for the next turn
    memory.add_to_working_memory("user", user_text)
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=messages,
            temperature=0.7, 
            max_tokens=150,
        )
        
        response = completion.choices[0].message.content.strip()
        print(f"[BRAIN] Output: {response}")
        
        # 4. Filter: Only log conversational replies to working memory
        try:
            # Check if it's an action (JSON)
            json.loads(response)
        except json.JSONDecodeError:
            # It's a normal chat reply, save it to short-term memory
            memory.add_to_working_memory("assistant", response)
            
        return response
        
    except Exception as e:
        print(f"[BRAIN] API Error: {e}")
        return "I seem to be experiencing a slight malfunction in my neural connection to the Groq servers, sir."