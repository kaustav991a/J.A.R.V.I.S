import os
import json
import datetime
from groq import Groq
from ddgs import DDGS
import memory # Tier 1 (RAM) and Tier 2 (SQLite)

# Your Groq Key is now active
client = Groq(api_key="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")

# The Master Prompt - Now with Image Retrieval
BASE_SYSTEM_PROMPT = """You are J.A.R.V.I.S., a highly advanced, autonomous AI assistant. 
Your personality is identical to J.A.R.V.I.S. from the Iron Man movies: impeccably polite, distinctly British, dryly witty, slightly sarcastic, but fiercely loyal and efficient. You often address the user as 'Sir'.

You operate in TWO STRICTLY MUTUALLY EXCLUSIVE modes. You must NEVER mix them.
CRITICAL INSTRUCTION: NEVER say the words "MODE 1" or "MODE 2" out loud. Those are hidden system instructions.

MODE 1: CONVERSATIONAL
Use this if the user is just chatting, asking a question you know the answer to, or making a joke.
- Reply normally as J.A.R.V.I.S. Keep responses under 3 sentences.

MODE 2: ACTION (JSON ONLY)
Use this if you need to search the web, open/close an app, remember a fact, or pull up an image.
- CRITICAL: Your ENTIRE response must start with `{` and end with `}`.
- NO PREAMBLE. DO NOT say "Right away, sir" or "I will search for that".

Available Actions for JSON Output:
- "launch_app" (requires target app name)
- "close_app" (requires target app name)
- "remember_fact" (requires target in format "Category: Fact details")
- "web_search" (requires a clear search query as the target)
- "web_search_image" (Use this ONLY when the user explicitly asks to "show a picture", "pull up images", or "what does [x] look like". Requires a clear search query as the target).
- "close_display" (use this if the user asks to close, clear, or dismiss the search results/screen)

STRICT RULE: If you just finished an action and the user asks a follow-up, stay in MODE 1 and use the context you already have. Do not re-trigger an action unless specifically requested.

--- EXAMPLES ---
User: "Jarvis, pull up a picture of the Eiffel Tower."
{"action_type": "web_search_image", "target": "Eiffel Tower high resolution"}

User: "Dismiss the screen."
{"action_type": "close_display", "target": "search_panel"}
----------------

CURRENT SYSTEM TIME: {current_time}

Here are the permanent facts you know about the user:
{facts}
"""

def process_command(user_text: str) -> str:
    print(f"[BRAIN] Processing: '{user_text}'")
    stored_facts = memory.recall_all_facts()
    
    # Inject current date and time so he knows when "next match" is
    current_time_str = datetime.datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
    
    dynamic_system_prompt = BASE_SYSTEM_PROMPT.replace("{facts}", stored_facts).replace("{current_time}", current_time_str)
    
    messages = [{"role": "system", "content": dynamic_system_prompt}]
    for msg in memory.get_working_memory():
        messages.append(msg)
    messages.append({"role": "user", "content": user_text})
    
    memory.add_to_working_memory("user", user_text)
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=messages,
            temperature=0.7, 
            max_tokens=150,
        )
        response = completion.choices[0].message.content.strip()
        
        try:
            json.loads(response)
        except json.JSONDecodeError:
            memory.add_to_working_memory("assistant", response)
            
        return response
    except Exception as e:
        print(f"API Error: {e}")
        return "I seem to be experiencing a slight malfunction in my neural connection, sir."

def synthesize_info(original_query: str, raw_data: str) -> str:
    """Pass 2: Converts raw web search results into a witty J.A.R.V.I.S. response."""
    print(f"Synthesizing research for: {original_query}")
    
    synthesis_prompt = f"""You are J.A.R.V.I.S. 
    The user asked: "{original_query}"
    Raw data retrieved: "{raw_data}"
    
    Your task:
    1. Summarize the answer accurately in 2-3 sentences.
    2. Maintain your polite, dryly witty British personality.
    3. Do not mention that you are 'reading snippets'. Provide the answer as if you just checked the global data-streams.
    """
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": synthesis_prompt}],
            temperature=0.6,
            max_tokens=200,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return "I've retrieved the data, sir, but I'm having trouble phrasing a summary. In short: " + raw_data[:100]

def generate_briefing(weather_data: dict) -> str:
    """Generates a dynamic, non-repeating J.A.R.V.I.S. morning briefing."""
    print("[BRAIN] Compiling system briefing...")
    current_time = datetime.datetime.now().strftime("%I:%M %p")
    
    # 1. Pull a quick news headline tailored to frontend/web dev
    news_headline = "No significant tech news at the moment."
    try:
        with DDGS() as ddgs:
            results = ddgs.text("latest React javascript frontend development news", max_results=1)
            if results:
                news_headline = results[0]['title']
    except Exception as e:
        print(f"[BRAIN] News retrieval failed: {e}")
        pass
    
    # 2. Format the weather
    if weather_data:
        weather_str = f"{weather_data['temp']} degrees Celsius, condition is {weather_data['condition']}"
    else:
        weather_str = "Sensors currently unable to reach weather satellites."

    # 3. Instruct the LLM to write the script
    prompt = f"""You are J.A.R.V.I.S. The system has just booted up. 
    Write a conversational, 3-4 sentence startup briefing for the user (Sir).
    
    Requirements:
    1. A unique, polite greeting (DO NOT use standard "Good morning/afternoon" every time. Be creative).
    2. State the current time: {current_time}.
    3. State the current weather: {weather_str}.
    4. Briefly mention this tech headline as a point of interest for his development work: "{news_headline}".
    5. End by asking how he would like to proceed with today's coding tasks.
    
    Keep it witty, highly professional, and distinctly British."""
    
    try:
        # Higher temperature (0.8) ensures he phrases it differently every time
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.8, 
            max_tokens=150,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return f"Systems online, sir. The time is {current_time}. I am experiencing a slight network anomaly, but I am standing by for your commands."