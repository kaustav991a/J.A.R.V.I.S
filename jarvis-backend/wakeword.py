import speech_recognition as sr
import threading
import speaker

# ==========================================
# PHASE 8: LOCAL STT TOGGLE
# Mirrors the toggle in recorder.py
# ==========================================
USE_LOCAL_STT = False

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
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(recognizer.recognize_google, audio)
                result = future.result(timeout=5)  # 5-second hard cap
                return result.lower()
        except concurrent.futures.TimeoutError:
            print("[STT] Google Cloud STT timed out (5s). Skipping.", flush=True)
            return ""
        except sr.UnknownValueError:
            return ""
        except Exception as e:
            print(f"[STT] Transcription error: {e}", flush=True)
            return ""

# 1. Global kill-switch for graceful shutdowns
is_shutting_down = threading.Event()

def wait_for_wake_word():
    """STAGE 1: The Initial Boot (Only happens once)"""
    # --- LAZY IMPORT FIX: Prevents Uvicorn infinite loops on Windows ---
    from modules.wake_engine import has_human_speech
    
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
                    
                    # --- NEW: Zero-Idle VAD Filter ---
                    if not has_human_speech(audio):
                        print("[VAD] Silence/Noise ignored.", flush=True)
                        continue
                        
                    print("[VAD] Speech detected. Transcribing...", flush=True)
                    text = _transcribe(recognizer, audio)
                    print(f"[STT] Heard: '{text}'", flush=True)
                    
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
    # --- LAZY IMPORT FIX: Prevents Uvicorn infinite loops on Windows ---
    from modules.wake_engine import has_human_speech
    
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
                # Uses instant VAD detection instead of slow Cloud STT
                if speaker.is_system_speaking:
                    try:
                        # Very short listen window — just enough to detect speech
                        audio = recognizer.listen(source, timeout=0.3, phrase_time_limit=1.0)
                        
                        # Instant VAD check — no transcription needed
                        if has_human_speech(audio):
                            print(f"\n[BARGE-IN] Speech detected during playback. Stopping audio.", flush=True)
                            speaker.stop_audio()
                            return True
                    except Exception:
                        pass
                    continue

                try:
                    # Shorter 3-second timeout keeps the loop snappy
                    audio = recognizer.listen(source, timeout=3, phrase_time_limit=3)
                    
                    # --- NEW: Zero-Idle VAD Filter ---
                    if not has_human_speech(audio):
                        print("[VAD] Silence/Noise ignored.", flush=True)
                        continue
                        
                    print("[VAD] Speech detected. Transcribing...", flush=True)
                    text = _transcribe(recognizer, audio)
                    print(f"[STT] Heard: '{text}'", flush=True)
                    
                    # --- Phonetic Net (trimmed to avoid false positives) ---
                    # Only trigger on words that actually sound like "Jarvis"
                    jarvis_aliases = [
                        "hello jarvis", "hello jervis", "hello chavis",
                        "hey jarvis", "hey jervis",
                        "hi jarvis", "hi jervis",
                        "jarvis", "jervis", "chavis", "charvis"
                    ]
                    
                    # Skip very short transcriptions (likely noise fragments)
                    if len(text.strip()) < 3:
                        continue
                    
                    if any(alias in text for alias in jarvis_aliases):
                        print("\n[JARVIS CALLED]", flush=True)
                        try:
                            from modules.sfx_manager import play_sfx
                            play_sfx("wake")
                        except Exception:
                            pass
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