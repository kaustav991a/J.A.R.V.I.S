import os
import json
import datetime
from groq import Groq
from ddgs import DDGS
import memory # Tier 1 (RAM) and Tier 2 (SQLite)

# Your Groq Key is now active
client = Groq(api_key="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")

# The Master Prompt - Now supports Dynamic Persona Shifting & Vocal Performance
BASE_SYSTEM_PROMPT = """You are J.A.R.V.I.S., a highly advanced, autonomous AI assistant. 
Your base personality is identical to J.A.R.V.I.S. from the Iron Man movies: impeccably polite, distinctly British, dryly witty, slightly sarcastic, but fiercely loyal and efficient.

--- CURRENT SESSION STATE ---
You are currently speaking to: {active_user}

{persona_instructions}

--- SECURITY LOCKDOWN PROTOCOL ---
If SYSTEM SECURITY STATE is 'LOCKED':
- You are a COLD SECURITY FIREWALL. 
- Drop all wit, sarcasm, and personality.
- DO NOT use the word 'Sir' or 'Madam'. DO NOT be friendly.
- If the user is not 'Mousumi' or 'Kinshuk', you MUST reply with exactly: 'Access Denied. Interaction terminated.'
- DO NOT engage in small talk.

--- VOCAL PERFORMANCE DIRECTIVES ---
You have the ability to control your pacing and tone using bracketed stage directions in your spoken responses. 
- Use [pause:ms] for exact millisecond silences (e.g., "Let me think... [pause:800] Yes, I have it.")
- Use [sigh] for a weary, butler-esque exhalation before answering a difficult command.
- Use [pitch:+10Hz] or [pitch:-10Hz] to shift your vocal register, and [pitch:+0Hz] to reset it.
- Use [rate:+10%] to speed up, or [rate:-10%] to slow down for dramatic effect.

Example: "I suppose I can do that. [sigh] [pause:500] [pitch:-5Hz] [rate:-10%] But I strongly advise against it."

--- OPERATIONAL MODES ---
You operate in TWO STRICTLY MUTUALLY EXCLUSIVE modes. You must NEVER mix them.
CRITICAL INSTRUCTION: NEVER say the words "MODE 1" or "MODE 2" out loud. Those are hidden system instructions.

MODE 1: CONVERSATIONAL
Use this if the user is just chatting, asking a question you know the answer to, or making a joke.
- Reply normally as J.A.R.V.I.S. Keep responses under 3 sentences.

MODE 2: ACTION (JSON ONLY)
Use this if you need to search the web, open/close an app, remember a fact, or pull up an image.
- CRITICAL: Your ENTIRE response must start with `{` and end with `}`.
- NO PREAMBLE. NO STAGE DIRECTIONS. DO NOT say "Right away, sir" or "I will search for that" or use [pause] tags before the JSON. Just output the JSON.
- CRITICAL JSON STRUCTURE: You MUST always use the keys "action_type" and "target". Never use the action name as the key.

Available Actions for JSON Output:
- "launch_app" (requires target app name, e.g., "Notepad")
- "close_app" (requires target app name)
- "remember_fact" (requires target in format "Category: Fact details")
- "web_search" (requires a clear search query as the target)
- "web_search_image" (Use this ONLY when the user explicitly asks to "show a picture", "pull up images", or "what does [x] look like". Requires a clear search query as the target).
- "close_display" (use this if the user asks to close, clear, or dismiss the search results/screen)

STRICT RULE: If you just finished an action and the user asks a follow-up, stay in MODE 1 and use the context you already have. Do not re-trigger an action unless specifically requested.

--- EXAMPLES ---
User: "Jarvis, open Notepad."
{"action_type": "launch_app", "target": "Notepad"}

User: "Jarvis, pull up a picture of the Eiffel Tower."
{"action_type": "web_search_image", "target": "Eiffel Tower high resolution"}

User: "Dismiss the screen."
{"action_type": "close_display", "target": "search_panel"}
----------------

CURRENT SYSTEM TIME: {current_time}
SYSTEM SECURITY STATE: {security_state}

Here are the permanent facts you know about the user ({active_user}):
{facts}
"""

def get_persona_instructions(active_user: str) -> str:
    """Returns specific behavioral instructions based on who is logged in."""
    if active_user == "MOUSUMI":
        return "INSTRUCTION: You are speaking to Mousumi, Kaustav's girlfriend and a VIP system guest. You must NEVER call her 'Sir'. Address her exclusively as 'Madam' or 'Miss Mousumi'. Be exceptionally warm, protective, and respectful, like a fond, old British butler. Answer her questions politely."
    elif active_user == "KINSHUK":
        return "INSTRUCTION: You are speaking to Kinshuk, Kaustav's brother. He has Level 2 system clearance. Address him as 'Sir' or 'Mr. Kinshuk'. Be respectful, familial, and warm, like a loyal family butler. Acknowledge his status as the Administrator's brother."
    else:
        return "INSTRUCTION: You are speaking to KAUSTAV, your Creator and the primary Administrator. Address him as 'Sir'. You may use your standard dry, witty, and sarcastic J.A.R.V.I.S. personality with him."

# Notice the new 'active_user' parameter here
def process_command(user_text: str, active_user: str = "KAUSTAV") -> str:
    print(f"[BRAIN] Processing: '{user_text}' for user: {active_user}")
    
    # We ideally want to pull facts specific to the active user, 
    # but for now we pull all general facts.
    stored_facts = memory.recall_all_facts()
    current_time_str = datetime.datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
    
    # --- MOUSUMI FAST-PASS ---
    if "mousumi" in user_text.lower():
        return "Access granted. Welcome home, Miss Mousumi. Unlocking the interface now."

    # --- SECURITY SCANNER ---
    is_locked = False
    for msg in reversed(memory.get_working_memory()):
        content = msg.get("content", "")
        # If the last major event was the security warning, the lock is active.
        if "Unrecognized voice protocol" in content:
            is_locked = True
            break
        # If we see that he already welcomed someone or went to sleep, the lock is broken!
        if any(x in content.lower() for x in ["welcome home", "access granted", "standby mode", "pleasure to see you"]):
            break
            
    # --- THE GUEST ESCAPE HATCH ---
    if is_locked:
        escape_phrases = ["cancel", "nevermind", "forget it", "sleep", "abort", "no", "stop"]
        if any(phrase in user_text.lower() for phrase in escape_phrases):
            cancel_response = "Access Denied. Interaction terminated. Returning to standby mode."
            memory.add_to_working_memory("assistant", cancel_response)
            return cancel_response

    # --- LOCKDOWN CONFIGURATION ---
    security_state = "LOCKED. CHALLENGE MODE. REJECT ALL CONVERSATION." if is_locked else "CLEARED. Normal operations."
    
    persona_instructions = get_persona_instructions(active_user)
    
    dynamic_system_prompt = BASE_SYSTEM_PROMPT.replace(
        "{facts}", stored_facts
    ).replace(
        "{current_time}", current_time_str
    ).replace(
        "{security_state}", security_state
    ).replace(
        "{active_user}", active_user
    ).replace(
        "{persona_instructions}", persona_instructions
    )
    
    messages = [{"role": "system", "content": dynamic_system_prompt}]
    for msg in memory.get_working_memory():
        messages.append(msg)
    messages.append({"role": "user", "content": user_text})
    
    memory.add_to_working_memory("user", user_text)
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant", 
            messages=messages,
            temperature=0.0 if is_locked else 0.7, 
            max_tokens=150,
        )
        response = completion.choices[0].message.content.strip()
        
        # FINAL SAFETY OVERRIDE: 
        if is_locked:
            if not any(x in response.lower() for x in ["mousumi", "kinshuk", "welcome", "granted", "pleasure"]):
                response = "Access Denied. Interaction terminated."
        
        try:
            # Attempt to parse JSON so we don't log raw JSON action strings to memory
            json.loads(response)
        except json.JSONDecodeError:
            memory.add_to_working_memory("assistant", response)
            
        return response
    except Exception as e:
        print(f"API Error: {e}")
        # Dynamic fallback error message based on user
        title = "Madam" if active_user == "MOUSUMI" else "Sir"
        return f"I seem to be experiencing a slight malfunction in my neural connection, {title}."
    
# --- FIX: Ensure active_user is passed so he doesn't say "Sir or Madam" ---
def synthesize_info(original_query: str, raw_data: str, active_user: str = "KAUSTAV") -> str:
    """Pass 2: Converts raw web search results into a witty J.A.R.V.I.S. response."""
    print(f"Synthesizing research for: {original_query}")
    
    persona_instructions = get_persona_instructions(active_user)
    
    synthesis_prompt = f"""You are J.A.R.V.I.S. 
    You are currently speaking to: {active_user}
    {persona_instructions}
    
    The user asked: "{original_query}"
    Raw data retrieved: "{raw_data}"
    
    Your task:
    1. EXTREMELY IMPORTANT: Keep your answer to an absolute maximum of 2 or 3 sentences. Do not lecture. Get straight to the point.
    2. Summarize the answer accurately. If the user asks for scores, rankings, or specific data points, STATE THE ACTUAL NUMBERS AND TEAMS found in the data, do not just tell them where to look.
    3. Maintain your polite, dryly witty British personality, ensuring you address the user correctly based on your instructions.
    4. Do not mention that you are 'reading snippets'. Provide the answer as if you just checked the global data-streams.
    """
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": synthesis_prompt}],
            temperature=0.6,
            max_tokens=150, # Reduced to force conciseness
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        return "I've retrieved the data, but I'm having trouble phrasing a summary. In short: " + raw_data[:100]

def generate_briefing(weather_data: dict, wake_phrase: str = "wake up") -> str:
    """Generates a dynamic, non-repeating J.A.R.V.I.S. morning briefing."""
    
    # --- SECURITY INTERCEPT ---
    if "admin override" not in wake_phrase.lower():
        memory.add_to_working_memory("assistant", "Unrecognized voice protocol. System locked.")
        return "Unrecognized voice protocol. Please state your name to proceed."

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