import pyttsx3
import threading

def speak_text(text):
    """
    Takes a string of text and speaks it out loud using the Windows TTS engine.
    Runs in a separate thread so it doesn't freeze the rest of the backend.
    """
    def _speak():
        # 1. Initialize the engine
        engine = pyttsx3.init()
        
        # 2. Configure the voice (Find a good male voice if possible)
        voices = engine.getProperty('voices')
        for voice in voices:
            # Look for a male English voice (often 'David' on Windows)
            if "david" in voice.name.lower() or "male" in voice.name.lower():
                engine.setProperty('voice', voice.id)
                break
        
        # 3. Set the speed (words per minute)
        engine.setProperty('rate', 175) 
        
        # 4. Speak!
        print(f"🔊 J.A.R.V.I.S. says: '{text}'")
        engine.say(text)
        engine.runAndWait()

    # We run the speech in a thread so your fastAPI server can keep listening
    thread = threading.Thread(target=_speak)
    thread.start()

# Quick test block
if __name__ == "__main__":
    speak_text("System online. Standing by for instructions, sir.")