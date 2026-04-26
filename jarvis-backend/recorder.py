import speech_recognition as sr
import speaker 
import time # --- NEW: Required for the holding pattern ---

# ==========================================
# PHASE 8: LOCAL STT TOGGLE
# Set to True to use faster-whisper (100% offline, ~200ms latency)
# Set to False to use Google Cloud STT (requires internet)
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

def listen_to_mic(status_callback=None):
    # --- THE DEAFEN LOOP ---
    # If J.A.R.V.I.S. is actively speaking, trap the script here.
    # It checks every 100ms and only opens the mic once he finishes.
    if speaker.is_system_speaking:
        while speaker.is_system_speaking:
            time.sleep(0.1)

    # Initialize the recognizer
    recognizer = sr.Recognizer()
    
    # Sensitivity and silence thresholds
    recognizer.energy_threshold = 150 
    
    # Cut the silence delay from 2.0s down to 0.5s
    recognizer.pause_threshold = 0.5
    # Prevents the mic from dynamically adjusting mid-sentence and hanging
    recognizer.dynamic_energy_threshold = False 
    
    try:
        # Use the default system microphone
        with sr.Microphone() as source:
            
            # 1. Tell React we are calibrating
            if status_callback: status_callback("calibrating", "Adjusting for background noise...")
            print("\n[EARS] Adjusting for background noise... Please wait 1 second.")
            
            # Cut calibration wait time in half
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            
            # 2. Tell React we are listening
            if status_callback: status_callback("listening", "Listening... (Speak clearly)")
            print("[EARS] Listening... (Speak clearly into your microphone)")
            
            try:
                # Listen for audio
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
                
                # 3. Tell React we are processing
                if status_callback: status_callback("processing_llm", "Processing speech...")
                print("[EARS] Processing speech...")
                
                # --- Phase 8: Route to local or cloud STT ---
                if USE_LOCAL_STT:
                    stt = _get_local_stt()
                    raw_data = audio.get_raw_data()
                    text = stt.transcribe_audio_data(raw_data, sample_rate=audio.sample_rate)
                    if not text or len(text.strip()) < 2:
                        return "UNKNOWN"
                    print(f"\n🗣️ You said: '{text}' [LOCAL STT]")
                else:
                    text = recognizer.recognize_google(audio)
                    print(f"\n🗣️ You said: '{text}' [CLOUD STT]")
                    
                return text
                
            except sr.WaitTimeoutError:
                # Replaced print statement with silent timeout
                return "TIMEOUT"
            except sr.UnknownValueError:
                # Silent failure when hearing static or a cough
                return "UNKNOWN"
            except sr.RequestError as e:
                print(f"[EARS] Network error with transcription service: {e}")
                # --- Phase 8: If cloud STT fails, try local as fallback ---
                if not USE_LOCAL_STT:
                    print("[EARS] Attempting local STT fallback...")
                    try:
                        stt = _get_local_stt()
                        raw_data = audio.get_raw_data()
                        text = stt.transcribe_audio_data(raw_data, sample_rate=audio.sample_rate)
                        if text and len(text.strip()) >= 2:
                            print(f"\n🗣️ You said: '{text}' [LOCAL STT FALLBACK]")
                            return text
                    except Exception:
                        pass
                return "ERROR"
    except Exception as e:
        print(f"[EARS WARNING] Microphone disconnected or unavailable: {e}")
        time.sleep(2) # Prevent massive spinning loops if called repeatedly
        return "TIMEOUT"

if __name__ == "__main__":
    listen_to_mic()