import speech_recognition as sr
import threading

# 1. Global kill-switch for graceful shutdowns
is_shutting_down = threading.Event()

def wait_for_wake_word(wake_word="wake up"):
    """STAGE 1: The Initial Boot (Only happens once)"""
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    
    print("[DEBUG] Attempting to connect to Microphone...", flush=True)
    
    with sr.Microphone(device_index=0) as source:
        print("[DEBUG] Microphone connected! Adjusting for noise...", flush=True)
        # Calibrate only on initial boot
        recognizer.adjust_for_ambient_noise(source, duration=1)
        
        print(f"[SYSTEM] Offline. Waiting for '{wake_word}'...", flush=True)
        
        while not is_shutting_down.is_set():
            try:
                # 5-second listen window for the initial wake
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
                text = recognizer.recognize_google(audio).lower()
                
                if wake_word in text:
                    print("\n[BOOT SEQUENCE INITIATED]", flush=True)
                    return True
                    
            except sr.WaitTimeoutError:
                continue 
            except sr.UnknownValueError:
                continue 
            except Exception:
                continue
                
    return False 

def wait_for_jarvis():
    """STAGE 2: Passive Background Listening"""
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    
    with sr.Microphone(device_index=0) as source:
        # We DO NOT calibrate here so the mic is instantly ready
        print("[SYSTEM] Passive Listening for 'Jarvis'...", flush=True)
        
        while not is_shutting_down.is_set():
            try:
                # Shorter 3-second timeout keeps the loop snappy
                audio = recognizer.listen(source, timeout=3, phrase_time_limit=3)
                text = recognizer.recognize_google(audio).lower()
                
                # Catch common Google STT misinterpretations of "Jarvis"
                if "jarvis" in text or "travis" in text or "jervis" in text or "service" in text:
                    print("\n[JARVIS CALLED]", flush=True)
                    return True
                    
            except sr.WaitTimeoutError:
                continue 
            except sr.UnknownValueError:
                continue 
            except Exception:
                continue
                
    return False