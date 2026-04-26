import time
import json
import threading
import requests
import speech_recognition as sr
from modules.local_stt import get_stt
from modules.local_tts import get_tts
from brain import process_stream, extract_and_store_memory
from action_engine import ActionEngine

class StreamingDaemon:
    def __init__(self):
        print("[DAEMON] Initializing Phase 8 Streaming Voice Daemon...")
        self.stt = get_stt()
        self.tts = get_tts()
        self.recognizer = sr.Recognizer()
        
        # Adjust for rapid conversational detection
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.pause_threshold = 0.6  # Quick cutoff for fast response
        
        self.action_engine = ActionEngine()
        self.active_user = "KAUSTAV" # Will update from global state if needed
        self.is_listening = True

    def update_ui(self, status: str, message: str = ""):
        """Syncs the daemon state with the main React frontend."""
        try:
            requests.post("http://127.0.0.1:8000/api/ui_state", json={
                "status": status,
                "message": message,
                "user": self.active_user
            }, timeout=1)
        except Exception:
            pass

    def listen_and_process(self):
        with sr.Microphone() as source:
            print("[DAEMON] Adjusting for ambient noise...")
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            print("[DAEMON] Online and listening for commands.")

            while self.is_listening:
                try:
                    self.update_ui("online", "LISTENING...")
                    audio_data = self.recognizer.listen(source, phrase_time_limit=10)
                    
                    self.update_ui("processing", "TRANSCRIBING (LOCAL)...")
                    start_stt = time.time()
                    raw_audio = audio_data.get_raw_data()
                    text = self.stt.transcribe_audio_data(raw_audio, sample_rate=audio_data.sample_rate)
                    
                    if not text or len(text.strip()) < 2:
                        continue
                        
                    print(f"\n[YOU] {text} (STT: {time.time() - start_stt:.2f}s)")
                    self.update_ui("processing", f"[YOU] {text.upper()}")
                    
                    threading.Thread(target=extract_and_store_memory, args=(text, self.active_user)).start()

                    print(f"[JARVIS] ", end="", flush=True)
                    self.update_ui("speaking", "J.A.R.V.I.S. IS RESPONDING...")
                    
                    llm_stream = process_stream(text, self.active_user)
                    
                    def action_interceptor(stream):
                        full_text = ""
                        is_json = False
                        for i, chunk in enumerate(stream):
                            if i == 0 and "{" in chunk:
                                is_json = True
                            
                            full_text += chunk
                            
                            if not is_json:
                                print(chunk, end="", flush=True)
                                yield chunk
                                
                        if is_json:
                            try:
                                start = full_text.find("{")
                                end = full_text.rfind("}") + 1
                                json_str = full_text[start:end]
                                payload = json.loads(json_str)
                                
                                print(f"\n[ACTION ENGINE] Executing: {payload.get('action_type')}")
                                self.update_ui("processing", f"EXECUTING: {payload.get('action_type').upper()}")
                                result = self.action_engine.dispatch(payload)
                                
                                print(f"[JARVIS] {result}")
                                self.update_ui("speaking", result.upper()[:50] + "...")
                                yield result
                            except Exception as e:
                                pass

                    self.tts.stream_tts(action_interceptor(llm_stream))
                    print()
                    self.update_ui("online", "LISTENING...")
                    
                except sr.WaitTimeoutError:
                    continue
                except Exception as e:
                    print(f"[DAEMON] Error: {e}")

if __name__ == "__main__":
    daemon = StreamingDaemon()
    daemon.listen_and_process()
