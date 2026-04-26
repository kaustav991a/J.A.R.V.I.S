import speech_recognition as sr
import threading
import speaker

# ==========================================
# PHASE 8: LOCAL STT TOGGLE
# Mirrors the toggle in recorder.py
# ==========================================
USE_LOCAL_STT = True

# --- Phase 8: Lazy-load local STT model only when needed ---
_local_stt_instance = None
def _get_local_stt():
    global _local_stt_instance
    if _local_stt_instance is None:
        from modules.local_stt import get_stt
        _local_stt_instance = get_stt()
    return _local_stt_instance

def _transcribe(recognizer, audio):
    """Unified transcription that routes to local or cloud STT."""
    if USE_LOCAL_STT:
        stt = _get_local_stt()
        raw_data = audio.get_raw_data()
        text = stt.transcribe_audio_data(raw_data, sample_rate=audio.sample_rate)
        return text.lower().strip() if text else ""
    else:
        return recognizer.recognize_google(audio).lower()

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
    
    try:
        with sr.Microphone() as source:
            print("[DEBUG] Microphone connected! Adjusting for noise...", flush=True)
            # Calibrate only on initial boot
            recognizer.adjust_for_ambient_noise(source, duration=1)
            
            print("[SYSTEM] Offline. Waiting for 'wake up' or 'initiate admin override'...", flush=True)
            
            while not is_shutting_down.is_set():
                # --- DEAFEN LOOP: Disable Barge-in by ignoring wake words while speaking ---
                if speaker.is_system_speaking:
                    import time
                    time.sleep(0.1)
                    continue

                try:
                    # 5-second listen window for the initial wake
                    audio = recognizer.listen(source, timeout=5, phrase_time_limit=5)
                    text = _transcribe(recognizer, audio)
                    
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
    except Exception as e:
        print(f"[SYSTEM WARNING] No audio input detected ({e}). Running in TEXT-ONLY mode for backdoor testing.", flush=True)
        # Sleep infinitely so the system doesn't crash, allowing backdoor commands
        while not is_shutting_down.is_set():
            import time
            time.sleep(1)
            
    return None 

def wait_for_jarvis():
    """STAGE 2: Passive Background Listening"""
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    
    # --- NEW: Speed optimizations to make him listen instantly ---
    recognizer.pause_threshold = 0.5 
    recognizer.dynamic_energy_threshold = False
    
    try:
        with sr.Microphone() as source:
            # We DO NOT calibrate here so the mic is instantly ready
            print("[SYSTEM] Passive Listening for 'Hello Jarvis'...", flush=True)
            
            while not is_shutting_down.is_set():
                # --- PHASE 3: FAST INTERRUPTION ENGINE (BARGE-IN) ---
                if speaker.is_system_speaking:
                    try:
                        # Aggressive, short listen to catch interruptions without freezing
                        audio = recognizer.listen(source, timeout=0.5, phrase_time_limit=1.5)
                        text = _transcribe(recognizer, audio)
                        
                        barge_in_words = ["stop", "quiet", "shut up", "jarvis", "cancel", "enough"]
                        if any(word in text for word in barge_in_words):
                            print(f"\n[BARGE-IN] Interruption detected: '{text}'", flush=True)
                            speaker.stop_audio()
                            # Return True so he immediately listens for a follow-up command
                            return True
                    except Exception:
                        pass
                    continue

                try:
                    # Shorter 3-second timeout keeps the loop snappy
                    audio = recognizer.listen(source, timeout=3, phrase_time_limit=3)
                    text = _transcribe(recognizer, audio)
                    
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
    except Exception as e:
        # Prevent crash if mic disconnected while running
        while not is_shutting_down.is_set():
            import time
            time.sleep(1)
            
    return False