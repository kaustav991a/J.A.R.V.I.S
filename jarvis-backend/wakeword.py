import os
import speech_recognition as sr
import threading
import speaker

# ==========================================
# PHASE 8: LOCAL STT TOGGLE
# Mirrors the toggle in recorder.py
# ==========================================
USE_LOCAL_STT = False

# ==========================================
# §1.3 FULL-DUPLEX (headphone-safe)
# When enabled (JARVIS_FULL_DUPLEX=1), barge-in doesn't just STOP playback — it
# CAPTURES and transcribes what you said over J.A.R.V.I.S. and hands it back as the
# next command, so you can talk over him and he adapts to your actual words.
# Use headphones so the mic never hears his own TTS (cheap echo cancellation).
# ==========================================
FULL_DUPLEX = os.getenv("JARVIS_FULL_DUPLEX", "0").strip().lower() in ("1", "true", "yes", "on")

# A command captured during full-duplex over-talk, waiting to be consumed by the
# main loop (so the first words you speak over him are never lost).
_pending_utterance: str | None = None


def set_pending_utterance(text: str) -> None:
    global _pending_utterance
    _pending_utterance = text


def pop_pending_utterance() -> str | None:
    """Return and clear any over-talk utterance captured during full-duplex barge-in."""
    global _pending_utterance
    u = _pending_utterance
    _pending_utterance = None
    return u

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

# ==========================================
# CLICK-TO-TALK (POST /api/listen)
# ==========================================
# The HUD has no microphone of its own — the mic lives here, on the server — and
# both loops below block inside recognizer.listen(), so the event loop cannot
# interrupt them. The button therefore sets this flag and the loops consume it
# between listen windows. See modules/listen_request.py for why it expires.
from modules.listen_request import ListenRequest  # noqa: E402  (stdlib-only)

listen_request = ListenRequest()

# What a button press means at the offline stage. Deliberately the GUEST phrase,
# never "admin override": a click must not hand out admin, it goes through the
# same biometric boot a spoken "wake up" does.
CLICK_WAKE_PHRASE = "wake up"

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

                # Click-to-talk: the HUD mic button boots him exactly as saying
                # "wake up" does (biometric path — never the admin bypass).
                clicked = listen_request.consume()
                if clicked:
                    print(f"[WAKE] Boot requested by {clicked} (mic button).", flush=True)
                    return CLICK_WAKE_PHRASE

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
            # With no microphone there is no spoken way in at all, so the mic
            # button is the ONLY wake path left — honour it here too.
            clicked = listen_request.consume()
            if clicked:
                print(f"[WAKE] Boot requested by {clicked} (no microphone — text mode).", flush=True)
                return CLICK_WAKE_PHRASE
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
                # --- BARGE-IN DISABLED (deafen while speaking) ---
                # Barge-in let the mic listen WHILE J.A.R.V.I.S. spoke, which on a
                # headset picked up his own TTS / breath-noise and re-triggered an
                # endless talk loop. Now we simply wait until he finishes speaking
                # before listening again — no interruption, no self-trigger.
                if speaker.is_system_speaking:
                    import time
                    time.sleep(0.1)
                    continue

                # Click-to-talk: same effect as being called by name. Checked
                # AFTER the deafen guard on purpose — a click while he is
                # talking stays pending (that would be barge-in, not this) and
                # fires the moment he stops, if it hasn't expired by then.
                clicked = listen_request.consume()
                if clicked:
                    print(f"[WAKE] Listen requested by {clicked} (mic button).", flush=True)
                    try:
                        from modules.sfx_manager import play_sfx
                        play_sfx("wake")
                    except Exception:
                        pass
                    return True

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