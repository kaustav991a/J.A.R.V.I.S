import speech_recognition as sr

def listen_to_mic():
    # Initialize the recognizer
    recognizer = sr.Recognizer()
    
    # Add this line to make it much more sensitive to your voice
    recognizer.energy_threshold = 300 
    
    # Force Python to use the ZEB-THUNDER Headset mic
    with sr.Microphone(device_index=0) as source:
        print("\n[EARS] Adjusting for background noise... Please wait 1 second.")
        recognizer.adjust_for_ambient_noise(source, duration=1)
        
        print("[EARS] Listening... (Speak clearly into your microphone)")
        try:
            # Listen for audio. It will automatically stop when you stop talking.
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            print("[EARS] Processing speech...")
            
            # For now, we use Google's free cloud STT for instant gratification.
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

# Quick test block: If you run this file directly, it will test your mic.
if __name__ == "__main__":
    listen_to_mic()