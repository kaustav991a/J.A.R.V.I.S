import speech_recognition as sr

def wait_for_wake_word(wake_word="hello jarvis"):
    recognizer = sr.Recognizer()
    recognizer.energy_threshold = 300
    
    with sr.Microphone(device_index=0) as source:
        recognizer.adjust_for_ambient_noise(source, duration=1)
        print(f"[SYSTEM] Standing by. Listening for '{wake_word}'...")
        
        while True:
            try:
                # Short timeouts keep the loop fast and CPU usage low
                audio = recognizer.listen(source, timeout=1, phrase_time_limit=3)
                text = recognizer.recognize_google(audio).lower()
                
                if wake_word in text:
                    print("\n[WAKE WORD DETECTED]")
                    return True
                    
            except sr.WaitTimeoutError:
                continue # No speech detected, keep looping silently
            except sr.UnknownValueError:
                continue # Mumbled noise, keep looping silently
            except Exception:
                continue