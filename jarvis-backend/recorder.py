import os
import speech_recognition as sr
import speaker
import time # --- NEW: Required for the holding pattern ---

# ==========================================
# CAPTURE TIMING — ACCESSIBILITY DEFAULTS. DO NOT TUNE DOWN FOR LATENCY.
# (live-gate finding F-33, 2026-08-16)
# ==========================================
# THE OWNER STUTTERS. These values exist so he can speak the way he speaks, and
# every one of them is a correctness requirement, not a comfort setting. The
# previous values were chosen to cut latency — the comment here used to read
# "Cut the silence delay from 2.0s down to 0.5s" — and the cost was that a
# command had to be delivered in a single unbroken breath or its back half was
# discarded. Anyone tempted to lower these to make JARVIS feel snappier is
# trading the owner's ability to use the system at all for a few hundred
# milliseconds. Don't.
#
# `pause_threshold` — how long silence must run before the phrase is considered
# over. A stutter block routinely lasts 1–3 seconds, so anything near the
# library's 0.8s default cuts mid-word. 2.5s clears a normal block; a fluent
# short command is unaffected because the phrase still ends on silence, just
# 2.5s later.
#
# This is also the root of F-23. The identity challenge asks "please state your
# name" — "my name is … Kaustav" is the single most pause-prone utterance in the
# system, capture ended at "my name is", and a failed challenge TERMINATES the
# interaction rather than retrying. A short pause threshold there locked the
# owner out of his own house.
#
# `phrase_time_limit` — hard ceiling on one utterance. With longer internal
# pauses a normal sentence occupies more wall-clock, so the old 10s truncated
# instructions mid-word regardless of how they were spoken.
#
# `START_TIMEOUT_S` — how long to wait for speech to BEGIN. A block at the start
# of an utterance is common; at 5s the recogniser gave up before he began and
# returned TIMEOUT as though he had said nothing.
def _envf(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


PAUSE_THRESHOLD_S = _envf("JARVIS_STT_PAUSE_S", 2.5)
PHRASE_LIMIT_S = _envf("JARVIS_STT_PHRASE_LIMIT_S", 30.0)
START_TIMEOUT_S = _envf("JARVIS_STT_START_TIMEOUT_S", 12.0)
# Floor for the ambient calibration. `adjust_for_ambient_noise` OVERWRITES
# `energy_threshold`, so a value assigned before it is silently discarded — the
# hand-set 150 below the calibration call never applied to anything.
ENERGY_FLOOR = _envf("JARVIS_STT_ENERGY_FLOOR", 150.0)

# ==========================================
# PHASE 8: LOCAL STT TOGGLE
# Set to True to use faster-whisper (100% offline, ~200ms latency)
# Set to False to use Google Cloud STT (requires internet)
# ==========================================
USE_LOCAL_STT = False

# ==========================================
# JUNK / FILLER FILTER (headphone-noise guard)
# Google STT transcribes breath/background noise as short filler words ("huh",
# "uh", "um"…). In the seamless conversation loop these were being fed to the LLM
# as real commands, making J.A.R.V.I.S. talk non-stop. Any utterance that is just
# a filler word is treated as noise (returned as UNKNOWN → silently ignored).
# ==========================================
_FILLER_UTTERANCES = {
    "huh", "uh", "um", "umm", "uhh", "hmm", "hm", "mm", "mmm", "mhm",
    "ah", "ahh", "oh", "ohh", "eh", "ehh", "er", "err", "uh huh", "mm hmm",
    "huh?", "uh.", "um.", "hmm.", "ah.", "oh.",
}

def _is_filler(text: str) -> bool:
    if not text:
        return True
    t = text.strip().lower().rstrip(".!?,")
    return t in _FILLER_UTTERANCES

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
    
    # Sensitivity and silence thresholds — see the module header. These are
    # accessibility defaults; the owner stutters and a short pause threshold
    # eats the back half of his sentences.
    recognizer.pause_threshold = PAUSE_THRESHOLD_S
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
            # `adjust_for_ambient_noise` OVERWRITES energy_threshold, so it has
            # to be floored AFTER the call, not before it. A quiet room can
            # calibrate the threshold low enough that a stutter block's own
            # breath re-triggers speech detection.
            if recognizer.energy_threshold < ENERGY_FLOOR:
                recognizer.energy_threshold = ENERGY_FLOOR

            # 2. Tell React we are listening
            if status_callback: status_callback("listening", "Listening... (Speak clearly)")
            print("[EARS] Listening... (Speak clearly into your microphone)")
            try:
                from modules.sfx_manager import play_sfx
                play_sfx("listening")
            except Exception:
                pass
            
            try:
                # Listen for audio
                audio = recognizer.listen(source, timeout=START_TIMEOUT_S,
                                          phrase_time_limit=PHRASE_LIMIT_S)
                
                # 3. Tell React we are processing
                if status_callback: status_callback("processing_llm", "Processing speech...")
                print("[EARS] Processing speech...")
                try:
                    from modules.sfx_manager import play_sfx
                    play_sfx("processing")
                except Exception:
                    pass
                
                # --- Phase 8: Route to local or cloud STT ---
                if USE_LOCAL_STT:
                    stt = _get_local_stt()
                    raw_data = audio.get_raw_data()
                    text = stt.transcribe_audio_data(raw_data, sample_rate=audio.sample_rate)
                    if not text or len(text.strip()) < 2:
                        return "UNKNOWN"
                    if _is_filler(text):
                        print(f"[EARS] Filler/noise ignored: '{text}'")
                        return "UNKNOWN"
                    print(f"\n🗣️ You said: '{text}' [LOCAL STT]")
                else:
                    try:
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as pool:
                            future = pool.submit(recognizer.recognize_google, audio)
                            text = future.result(timeout=7)  # 7-second hard cap
                    except concurrent.futures.TimeoutError:
                        print("[EARS] Google Cloud STT timed out (7s).")
                        return "UNKNOWN"
                    if _is_filler(text):
                        print(f"[EARS] Filler/noise ignored: '{text}'")
                        return "UNKNOWN"
                    print(f"\n🗣️ You said: '{text}' [CLOUD STT]")

                return text
                
            except sr.WaitTimeoutError:
                # Replaced print statement with silent timeout
                return "TIMEOUT"
            except sr.UnknownValueError:
                # Was silent. Live-gate F-35: the owner answered a governance
                # prompt with "yes", the recogniser could not make out a single
                # short word, and the log showed "Processing speech…" followed
                # by nothing at all — an answered question that looked, in the
                # only record there is, like an unanswered one.
                print("[EARS] Speech not understood — no transcript.")
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