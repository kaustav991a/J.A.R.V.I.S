import speech_recognition as sr

# ---> THE FIX: Added status_callback to talk to React <---
def listen_to_mic(status_callback=None):
    # Initialize the recognizer
    recognizer = sr.Recognizer()
    
    # Sensitivity and silence thresholds
    recognizer.energy_threshold = 300 
    recognizer.pause_threshold = 2.0
    
    # Force Python to use the ZEB-THUNDER Headset mic
    with sr.Microphone(device_index=0) as source:
        
        # 1. Tell React we are calibrating
        if status_callback: status_callback("calibrating", "Adjusting for background noise...")
        print("\n[EARS] Adjusting for background noise... Please wait 1 second.")
        recognizer.adjust_for_ambient_noise(source, duration=1)
        
        # 2. Tell React we are listening
        if status_callback: status_callback("listening", "Listening... (Speak clearly)")
        print("[EARS] Listening... (Speak clearly into your microphone)")
        
        try:
            # Listen for audio
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            
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