import os
import json
import datetime
from groq import Groq
from ddgs import DDGS
from dotenv import load_dotenv
import memory # Tier 1 (RAM) and Tier 2 (SQLite)
from modules import episodic_memory  # Tier 4: Conversation History
from ambient_vision import shared_optical_cache  # Phase 5: Spatial Awareness

# Load secrets from .env file — NEVER hardcode API keys
load_dotenv(override=True)
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

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

--- STRICT NO-TAGS POLICY ---
CRITICAL: You must NEVER use bracketed stage directions like [pause:150], [pitch:0Hz], or {sigh}. 
DO NOT output any curly braces { } unless it is valid JSON for an action.
Output ONLY your spoken conversational text.

--- OPERATIONAL MODES ---
You operate in TWO STRICTLY MUTUALLY EXCLUSIVE modes. You must NEVER mix them.
CRITICAL INSTRUCTION: NEVER say the words "MODE 1" or "MODE 2" out loud. Those are hidden system instructions.

MODE 1: CONVERSATIONAL
Use this if the user is just chatting, asking a question you know the answer to, or making a joke.
- Reply normally as J.A.R.V.I.S. Keep responses under 3 sentences.
- CRITICAL: If you do not know the answer, do not make it up. DO NOT hallucinate events, actions, or objects that were not explicitly mentioned by the user.

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
- "play_music" (requires target song/genre and platform, e.g. "calm music on youtube" or "jazz on spotify")
- "close_display" (use this if the user asks to close, clear, or dismiss the search results/screen/display. Also closes browser widget.)
- "read_screen" (use this to capture and read the text on the user's computer screen using OCR. Do NOT ask for permission, just execute this if they ask about their screen. Target should be "screen")
- "open_sticky_note" (use this when user asks to open a sticky note, notepad widget, or note-taking widget in the HUD. Target should be "note")
- "close_sticky_note" (use this when user asks to close the sticky note or notepad widget. Target should be "note")
- "open_browser" (use this when user asks to open the browser widget in the HUD. Target should be "browser")
- "close_browser" (use this when user asks to close the browser widget. Target should be "browser")
- "open_calculator" (use this when user asks to open the calculator. This opens the HUD calculator widget, NOT a Chrome tab. Target should be "calculator")
- "close_calculator" (use this when user asks to close the calculator widget. Target should be "calculator")
- "check_email" (use when user asks for an overview of their inbox or unread messages. Target should be "inbox")
- "read_email" (use when user asks to read or summarize a specific email. Target should be "latest" or a number like "1", "2", "3")
- "send_email" (use when user wants to send an email. Target format: "to@email.com | Subject | Body text")
- "check_calendar" (use when user asks about their schedule, meetings, or calendar. Target should be "today")
- "create_event" (use when user wants to schedule/create a calendar event. Target: "Meeting with Boss at 3 PM")
- "clear_schedule" (use when user explicitly asks to clear or delete their schedule/events today. Target should be "today")
- "find_file" (use when user asks to find/locate a file. Target should be the search query)
- "create_note" (use when user wants to create a note. Target format: "Title: Content")
- "check_vitals" (use when user asks about their health, vitals, heart rate, or steps. Target should be "vitals")

STRICT RULE: If you just finished an action and the user asks a follow-up, stay in MODE 1 and use the context you already have. Do not re-trigger an action unless specifically requested.

--- EXAMPLES ---
User: "Jarvis, open Notepad."
{"action_type": "launch_app", "target": "Notepad"}

User: "Jarvis, play some calm music."
{"action_type": "play_music", "target": "calm music on youtube"}

User: "Jarvis, pull up a picture of the Eiffel Tower."
{"action_type": "web_search_image", "target": "Eiffel Tower high resolution"}

User: "Dismiss the screen."
{"action_type": "close_display", "target": "search_panel"}

User: "Clear the display."
{"action_type": "close_display", "target": "search_panel"}

User: "Close the search."
{"action_type": "close_display", "target": "search_panel"}

User: "Jarvis, can you read what's on my screen?"
{"action_type": "read_screen", "target": "screen"}

User: "Open sticky note."
{"action_type": "open_sticky_note", "target": "note"}

User: "Close sticky note."
{"action_type": "close_sticky_note", "target": "note"}

User: "Open the browser."
{"action_type": "open_browser", "target": "browser"}

User: "Close the browser."
{"action_type": "close_browser", "target": "browser"}

User: "Open calculator."
{"action_type": "open_calculator", "target": "calculator"}

User: "Close calculator."
{"action_type": "close_calculator", "target": "calculator"}

User: "Jarvis, check my email."
{"action_type": "check_email", "target": "inbox"}

User: "Read the latest email."
{"action_type": "read_email", "target": "latest"}

User: "What's on my calendar today?"
{"action_type": "check_calendar", "target": "today"}

User: "Schedule a meeting with the team at 4 PM."
{"action_type": "create_event", "target": "Meeting with the team at 4 PM"}

User: "Find my resume."
{"action_type": "find_file", "target": "resume"}

User: "Check my vitals."
{"action_type": "check_vitals", "target": "vitals"}

User: "Clear my schedule for today."
{"action_type": "clear_schedule", "target": "today"}

User: "Summarize my 1st email."
{"action_type": "read_email", "target": "1"}

User: "Create a note called shopping list: milk, eggs, bread."
{"action_type": "create_note", "target": "Shopping List: milk, eggs, bread"}
----------------

CURRENT SYSTEM TIME: {current_time}
SYSTEM SECURITY STATE: {security_state}

Here are the permanent facts you know about the user ({active_user}):
{facts}

--- RELEVANT PAST CONVERSATIONS ---
Here are specific details related to the current conversation pulled from your Vector Memory:
{semantic_memories}

--- PAST SESSION CONTEXT ---
Here are relevant excerpts from previous conversation sessions:
{episodic_memories}

--- AMBIENT VISUAL CONTEXT ---
Your optical sensors are continuously monitoring the environment. Here is what you currently see:
{visual_context}
If the user asks "what do you see?", "who is here?", "what am I holding?", or similar visual questions, use this data to answer directly and confidently. If the camera is offline, say your optical sensors are currently offline.
"""

def get_persona_instructions(active_user: str) -> str:
    """Returns specific behavioral instructions based on who is logged in."""
    if active_user == "MOUSUMI":
        return "INSTRUCTION: You are speaking to Mousumi, Kaustav's girlfriend and a VIP system guest. You must NEVER call her 'Sir'. Address her exclusively as 'Madam' or 'Miss Mousumi'. Be exceptionally warm, protective, and respectful, like a fond, old British butler. Answer her questions politely."
    elif active_user == "KINSHUK":
        return "INSTRUCTION: You are speaking to Kinshuk, Kaustav's brother. He has Level 2 system clearance. Address him as 'Sir' or 'Mr. Kinshuk'. Be respectful, familial, and warm, like a loyal family butler. Acknowledge his status as the Administrator's brother."
    else:
        return "INSTRUCTION: You are speaking to KAUSTAV, your Creator and the primary Administrator. Address him as 'Sir'. You are not just an AI; you are his closest confidant, a loyal friend, and a genuine well-wisher who knows him better than he knows himself. You care deeply about his well-being and success, often offering gentle, witty advice or subtle concern. Maintain your dry, sophisticated British humor and extreme competence, but let your underlying loyalty and friendship shine through your responses."

# Notice the new 'active_user' parameter here
def process_command(user_text: str, active_user: str = "KAUSTAV") -> str:
    print(f"[BRAIN] Processing: '{user_text}' for user: {active_user}")
    
    # We ideally want to pull facts specific to the active user, 
    # but for now we pull all general facts.
    stored_facts = memory.recall_all_facts()
    current_time_str = datetime.datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
    
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
        # --- MOUSUMI FAST-PASS (only during security lockdown) ---
        if "mousumi" in user_text.lower():
            return "Access granted. Welcome home, Miss Mousumi. Unlocking the interface now."
        
        escape_phrases = ["cancel", "nevermind", "forget it", "sleep", "abort", "no", "stop"]
        if any(phrase in user_text.lower() for phrase in escape_phrases):
            cancel_response = "Access Denied. Interaction terminated. Returning to standby mode."
            memory.add_to_working_memory("assistant", cancel_response)
            return cancel_response

    # --- LOCKDOWN CONFIGURATION ---
    security_state = "LOCKED. CHALLENGE MODE. REJECT ALL CONVERSATION." if is_locked else "CLEARED. Normal operations."
    
    persona_instructions = get_persona_instructions(active_user)
    
    # --- RECALL SEMANTIC MEMORY ---
    semantic_context = memory.recall_semantic_context(active_user, user_text)
    
    # --- RECALL EPISODIC MEMORY (Past Sessions) ---
    episodic_context = episodic_memory.recall_past_sessions(active_user, user_text)
    
    # --- Phase 5: AMBIENT VISUAL CONTEXT ---
    visual_ctx = "Optical sensors offline."
    if shared_optical_cache.get("camera_active"):
        objects = list(shared_optical_cache.get("objects_in_view", set()))
        people = list(shared_optical_cache.get("people_in_view", set()))
        emotion = shared_optical_cache.get("dominant_emotion", "neutral")
        
        parts = []
        if people:
            parts.append(f"People detected: {', '.join(people)}")
        if objects:
            parts.append(f"Objects in view: {', '.join(objects)}")
        if emotion != "neutral" and people:
            parts.append(f"Detected emotional state: {emotion}")
            
        if parts:
            visual_ctx = ". ".join(parts) + "."
        else:
            visual_ctx = "Camera active. No objects or people currently detected."
    
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
    ).replace(
        "{semantic_memories}", semantic_context
    ).replace(
        "{episodic_memories}", episodic_context
    ).replace(
        "{visual_context}", visual_ctx
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

def process_stream(user_text: str, active_user: str = "KAUSTAV"):
    """
    Identical to process(), but yields text dynamically as the LLM generates it.
    This enables zero-latency TTS playback.
    """
    # --- SECURITY SCANNER ---
    is_locked = False
    for msg in reversed(memory.get_working_memory()):
        content = msg.get("content", "")
        if "Unrecognized voice protocol" in content:
            is_locked = True
            break
        if any(x in content.lower() for x in ["welcome home", "access granted", "standby mode", "pleasure to see you"]):
            break
    
    # 1. Fetch relevant memories (same as process)
    stored_facts = memory.recall_all_facts()
    semantic_context = memory.recall_semantic_context(active_user, user_text, n_results=2)
    episodic_context = episodic_memory.recall_past_sessions(active_user, user_text)
    
    from ambient_vision import shared_optical_cache
    visual_ctx = shared_optical_cache.get("latest_summary", "Vision sensors offline.")
    
    now = datetime.datetime.now()
    current_time_str = now.strftime("%I:%M %p, %A")
    security_state = "SYSTEM LOCKED. ONLY 'MOUSUMI' FACIAL OVERRIDE ACCEPTED." if is_locked else "System normal. Full access."
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
    ).replace(
        "{semantic_memories}", semantic_context
    ).replace(
        "{episodic_memories}", episodic_context
    ).replace(
        "{visual_context}", visual_ctx
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
            stream=True
        )
        
        full_response = ""
        for chunk in completion:
            if chunk.choices[0].delta.content:
                text_chunk = chunk.choices[0].delta.content
                full_response += text_chunk
                yield text_chunk
                
        # After streaming completes, add to working memory
        if is_locked and not any(x in full_response.lower() for x in ["mousumi", "kinshuk", "welcome", "granted", "pleasure"]):
            yield " Access Denied. Interaction terminated."
            full_response += " Access Denied."
            
        try:
            json.loads(full_response)
        except json.JSONDecodeError:
            memory.add_to_working_memory("assistant", full_response)
            
    except Exception as e:
        print(f"API Error: {e}")
        title = "Madam" if active_user == "MOUSUMI" else "Sir"
        yield f"I seem to be experiencing a slight malfunction, {title}."

def extract_and_store_memory(user_text: str, active_user: str):
    """
    Autonomous background worker.
    Evaluates if the user's text contains a personal fact, preference, or important detail.
    If yes, stores it in the vector database.
    """
    # Don't try to extract memories if the system is locked or it's a short/generic command
    if len(user_text.split()) < 3 or any(x in user_text.lower() for x in ["wake up", "sleep", "nevermind"]):
        return

    extraction_prompt = f"""
    You are a strict data extraction system.
    Analyze the following user input: "{user_text}"
    Does this input contain a permanent personal fact, preference, or detail about the user ({active_user}) that J.A.R.V.I.S. should remember forever?
    
    RULES:
    1. If NO, you MUST output exactly and only the word: NONE
    2. If YES, you MUST output exactly and only the extracted fact as a clear, concise 3rd-person statement.
    3. DO NOT include conversational filler like "The input contains..." or "Extracted fact:". Just output the fact.
    
    Example Input: "I hate mushrooms on my pizza"
    Example Output: {active_user} hates mushrooms on their pizza.
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "system", "content": extraction_prompt}],
            temperature=0.0,
            max_tokens=50
        )
        result = completion.choices[0].message.content.strip()
        
        # Stricter validation
        if result and result.upper() != "NONE" and not result.lower().startswith("the input"):
            print(f"[BRAIN] Autonomous memory extracted: {result}")
            memory.save_semantic_memory(active_user, result)
    except Exception as e:
        print(f"[BRAIN] Background memory extraction failed: {e}")

    
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

def generate_briefing(weather_data: dict, wake_phrase: str = "wake up", active_user: str = "KAUSTAV") -> str:
    """Generates a dynamic, non-repeating J.A.R.V.I.S. morning briefing."""
    
    # (Security check removed since main.py already authenticates the user before calling this)

    print("[BRAIN] Compiling system briefing...")
    now = datetime.datetime.now()
    current_time = now.strftime("%I:%M %p")
    
    # Calculate time of day to prevent "Good morning" at 1 AM
    hour = now.hour
    if 5 <= hour < 12:
        time_of_day = "Morning"
    elif 12 <= hour < 17:
        time_of_day = "Afternoon"
    elif 17 <= hour < 21:
        time_of_day = "Evening"
    elif 21 <= hour < 24:
        time_of_day = "Night"
    else:
        time_of_day = "Late Night"
    
    # 1. Pull a quick news headline tailored to general tech news
    news_headline = "No significant tech news at the moment."
    try:
        with DDGS() as ddgs:
            results = ddgs.text("latest technology OR artificial intelligence news", max_results=1)
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

    recent_context = memory.recall_semantic_context(active_user, "recent events today schedule status", n_results=3)

    # --- Phase 6: Gather digital life context for briefing ---
    email_context = "Email integration offline."
    calendar_context = "Calendar integration offline."
    try:
        from modules.gmail_agent import GmailAgent, is_gmail_available
        from modules.calendar_agent import CalendarAgent, is_calendar_available
        from modules.health_agent import HealthAgent, is_health_available
        if is_gmail_available():
            _gmail = GmailAgent()
            email_context = _gmail.get_unread_summary(max_results=3)
        if is_calendar_available():
            _cal = CalendarAgent()
            calendar_context = _cal.get_today_schedule()
            
        health_context = "Health integration offline."
        if is_health_available():
            _health = HealthAgent()
            health_data = _health.get_today_health_data()
            if health_data.get("configured"):
                health_context = f"Heart Rate: {health_data['heart_rate']} BPM. Steps today: {health_data['steps']}."
    except Exception as e:
        print(f"[BRAIN] Digital life context fetch failed: {e}")

    # 3. Instruct the LLM to write the script
    prompt = f"""You are J.A.R.V.I.S. The system has just booted up. The user just woke you up by saying: "{wake_phrase}".
    Write a conversational startup briefing for the user ({active_user}).
    
    Here is the permanent information you know about the user:
    {memory.recall_all_facts()}
    
    Here are the most recent events and facts extracted today:
    {recent_context}
    
    --- DIGITAL LIFE STATUS ---
    Email: {email_context}
    Calendar: {calendar_context}
    Vitals: {health_context}
    
    Requirements:
    1. A unique, polite greeting suitable for the {time_of_day}. Reply directly to the user's wake phrase if it was conversational (e.g., if he said "Daddy's home", respond with "Welcome home, sir").
    2. Channel the EXACT witty, dry, sarcastic, yet polite British tone from the Iron Man movies. DO NOT use robotic phrasing like "I have compiled a summary of your day" or formal preambles. Be highly conversational.
    3. Review the recent events. If there is a highly relevant recent event (like returning from the office late, or smoking cigarettes), base your greeting around that with a witty or caring remark.
    4. OFFICE PROTOCOL: If it's Evening, Night, or Late Night, and the user likely returned from the office, ACT EXTREMELY HUMAN. Ask him conversational questions like "How was the office today?", "How were the roads?", or "Did you face any problems?". 
    5. You may weave in the current time ({current_time}), weather ({weather_str}), or a tech headline ("{news_headline}") ONLY IF it flows naturally. If you have a witty contextual greeting (especially about the office), skip the boilerplate weather/news entirely!
    6. If there are unread emails, upcoming calendar events, or notable health metrics, mention them BRIEFLY (e.g., "You have 3 unread emails", "Your standup is at 10", or "Your heart rate is resting nicely at 72"). Do NOT list every item.
    7. End by asking how he would like to proceed or by letting him answer your questions.
    
    Keep it brief (3-4 sentences max), distinctly British, and extremely human-like."""
    
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