import speech_recognition as sr
import threading

# 1. Global kill-switch for graceful shutdowns
is_shutting_down = threading.Event()

def wait_for_wake_word():
    """STAGE 1: The Initial Boot (Only happens once)"""
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    
    # --- NEW: Speed optimizations to stop the mic hanging ---
    recognizer.pause_threshold = 0.5 
    recognizer.dynamic_energy_threshold = False
    
    print("[DEBUG] Attempting to connect to Microphone...", flush=True)
    
    with sr.Microphone(device_index=0) as source:
        print("[DEBUG] Microphone connected! Adjusting for noise...", flush=True)
        # Calibrate only on initial boot
        recognizer.adjust_for_ambient_noise(source, duration=1)
        
        print("[SYSTEM] Offline. Waiting for 'wake up' or 'initiate admin override'...", flush=True)
        
        while not is_shutting_down.is_set():
            try:
                # 5-second listen window for the initial wake
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
                text = recognizer.recognize_google(audio).lower()
                
                # Check for either the guest trigger or the admin bypass (Added 'wakeup' as one word just in case)
                if "wake up" in text or "admin override" in text or "wakeup" in text:
                    print(f"\n[BOOT SEQUENCE INITIATED VIA: {text}]", flush=True)
                    return text  # CRITICAL: We return the string, not a boolean
                
            except sr.WaitTimeoutError:
                continue 
            except sr.UnknownValueError:
                continue 
            except Exception:
                continue
                
    return None 

def wait_for_jarvis():
    """STAGE 2: Passive Background Listening"""
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    
    # --- NEW: Speed optimizations to make him listen instantly ---
    recognizer.pause_threshold = 0.5 
    recognizer.dynamic_energy_threshold = False
    
    with sr.Microphone(device_index=0) as source:
        # We DO NOT calibrate here so the mic is instantly ready
        print("[SYSTEM] Passive Listening for 'Hello Jarvis'...", flush=True)
        
        while not is_shutting_down.is_set():
            try:
                # Shorter 3-second timeout keeps the loop snappy
                audio = recognizer.listen(source, timeout=3, phrase_time_limit=3)
                text = recognizer.recognize_google(audio).lower()
                
                # --- NEW: Massively expanded Phonetic Net ---
                # Catching misinterpretations and allowing "Hey", "Hi", or just "Jarvis"
                jarvis_aliases = [
                    "hello jarvis", "hello travis", "hello jervis", "hello service",
                    "hey jarvis", "hi jarvis", "jarvis", "travis", "jervis", "service", 
                    "garbage", "chavis", "charvis"
                ]
                
                if any(alias in text for alias in jarvis_aliases):
                    print("\n[JARVIS CALLED]", flush=True)
                    return True
                
            except sr.WaitTimeoutError:
                continue 
            except sr.UnknownValueError:
                continue 
            except Exception:
                continue
                
    return False