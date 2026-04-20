import speech_recognition as sr
import speaker # --- NEW: Import the speaker module to trigger the kill switch ---

# ---> THE FIX: Added status_callback to talk to React <---
def listen_to_mic(status_callback=None):
    # Initialize the recognizer
    recognizer = sr.Recognizer()
    
    # Sensitivity and silence thresholds
    recognizer.energy_threshold = 150 
    
    # --- SPEED TWEAK 1: Cut the silence delay from 2.0s down to 0.5s ---
    recognizer.pause_threshold = 0.5
    # Prevents the mic from dynamically adjusting mid-sentence and hanging
    recognizer.dynamic_energy_threshold = False 
    
    # Force Python to use the ZEB-THUNDER Headset mic
    with sr.Microphone(device_index=0) as source:
        
        # 1. Tell React we are calibrating
        if status_callback: status_callback("calibrating", "Adjusting for background noise...")
        print("\n[EARS] Adjusting for background noise... Please wait 1 second.")
        
        # --- SPEED TWEAK 2: Cut calibration wait time in half ---
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        
        # 2. Tell React we are listening
        if status_callback: status_callback("listening", "Listening... (Speak clearly)")
        print("[EARS] Listening... (Speak clearly into your microphone)")
        
        try:
            # Listen for audio
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            
            # --- NEW: THE BARGE-IN TRIGGER ---
            # The exact moment the microphone captures your interruption, it instantly sends 
            # the kill signal to the background thread, cutting off J.A.R.V.I.S. mid-sentence.
            speaker.stop_audio()
            
            # 3. Tell React we are processing
            if status_callback: status_callback("processing_llm", "Processing speech...")
            print("[EARS] Processing speech...")
            
            text = recognizer.recognize_google(audio)
            print(f"\n🗣️ You said: '{text}'")
            return text
            
        except sr.WaitTimeoutError:
            print("[EARS] Timeout: No speech detected.")
            return None
        except sr.UnknownValueError:
            print("[EARS] Could not understand the audio.")
            return None
        except sr.RequestError as e:
            print(f"[EARS] Network error with transcription service: {e}")
            return None

if __name__ == "__main__":
    listen_to_mic()