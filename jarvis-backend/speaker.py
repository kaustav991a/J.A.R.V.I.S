import edge_tts
import pygame
import asyncio
import os
import threading
from collections import deque
import re
import uuid 
import time # --- NEW: Needed for file lock release ---
import glob
import io
import wave

# ==========================================
# PHASE 8: LOCAL TTS TOGGLE
# Set to True to use Piper (100% offline, ~250ms latency)
# Set to False to use Edge TTS (cloud, higher quality)
# ==========================================
USE_LOCAL_TTS = False

VOICE = "en-GB-RyanNeural"

# ==========================================
# §3.5 EMOTIONAL PROSODY
# The detected emotion (set per turn by brain.classify_intent via set_emotion)
# shifts the BASELINE pitch/rate of synthesis, so J.A.R.V.I.S. sounds somber when
# you're somber, brisk when things are urgent, etc. Inline [pitch:]/[rate:] tags
# still override per-segment. A higher Sass Index nudges a touch livelier.
# ==========================================
_EMOTION_PROSODY = {
    # emotion      : (pitch,    rate)
    "CASUAL":        ("+0Hz",   "+2%"),
    "INQUISITIVE":   ("+3Hz",   "+3%"),
    "URGENT":        ("+6Hz",   "+12%"),
    "FRUSTRATED":    ("-3Hz",   "+6%"),
    "SOMBER":        ("-12Hz",  "-12%"),
}
_current_emotion = "CASUAL"
_current_sass = 50


def set_emotion(emotion: str, sass_index: int = 50) -> None:
    """Set the prosody baseline for upcoming speech (called once per turn)."""
    global _current_emotion, _current_sass
    _current_emotion = (emotion or "CASUAL").upper()
    try:
        _current_sass = int(sass_index)
    except (TypeError, ValueError):
        _current_sass = 50


def _emotion_baseline() -> tuple[str, str]:
    """Return (pitch, rate) for the current emotion, nudged slightly by Sass Index."""
    pitch, rate = _EMOTION_PROSODY.get(_current_emotion, _EMOTION_PROSODY["CASUAL"])
    # High sass on a casual mood → a touch brighter/quicker.
    if _current_emotion == "CASUAL" and _current_sass >= 70:
        pitch, rate = "+4Hz", "+6%"
    return pitch, rate

# ==========================================
# §1.3 AEC REFERENCE BUFFER
# The full-duplex pipeline's echo canceller needs the "far-end" signal — i.e. the
# PCM J.A.R.V.I.S. is currently playing — to subtract it from the mic. Playback code
# pushes decoded PCM here; the AEC pulls the most-recent samples. If nothing is
# pushed (e.g. cloud MP3 path not tapped), the reference is silence and the AEC is a
# safe no-op (degrades to headphone mode).
# ==========================================
from collections import deque as _deque
_REF_SR = 16000
_ref_buffer = _deque(maxlen=_REF_SR * 5)   # ~5 s of int16 reference samples
_ref_lock = threading.Lock()


def push_reference_pcm(samples) -> None:
    """Playback feeds the int16 mono @16k PCM it is sending to the speakers."""
    try:
        with _ref_lock:
            _ref_buffer.extend(int(s) for s in samples)
    except Exception:
        pass


def pull_reference_pcm(n: int):
    """AEC pulls the n most-recent reference samples (int16 list), newest last."""
    with _ref_lock:
        if not _ref_buffer:
            return [0] * n
        buf = list(_ref_buffer)
    if len(buf) >= n:
        return buf[-n:]
    return [0] * (n - len(buf)) + buf

# Global Kill Switch for Barge-In
stop_speaking_flag = threading.Event()

# Global state flag to prevent self-interruption
is_system_speaking = False 

# --- Cleanup leftover audio files from previous sessions/crashes ---
for f in glob.glob("temp_speech_*.mp3") + glob.glob("temp_sigh_*.mp3") + glob.glob("temp_local_*.wav"):
    try:
        os.remove(f)
    except Exception:
        pass

# Initialize pygame mixer globally to prevent thread deadlocks
try:
    pygame.mixer.init()
except pygame.error as e:
    print(f"[SPEAKER WARNING] Primary audio driver failed: {e}. Falling back to DirectSound...")
    os.environ["SDL_AUDIODRIVER"] = "directsound"
    try:
        pygame.mixer.init()
    except pygame.error as e2:
        print(f"[SPEAKER WARNING] DirectSound failed: {e2}. Falling back to dummy driver (No Audio)...")
        os.environ["SDL_AUDIODRIVER"] = "dummy"
        pygame.mixer.init()

# Ensure speech happens in order without interrupting
speech_lock = asyncio.Lock()

# Phase 4 regression: ring buffer of utterances passed to speak_text (when enabled via env).
_REGRESSION_UTTERANCES: deque[str] = deque(maxlen=120)


def regression_append_utterance(text: str) -> None:
    if os.getenv("JARVIS_REGRESSION_ROUTES") != "1":
        return
    t = (text or "").strip()
    if t:
        _REGRESSION_UTTERANCES.append(t)


def regression_get_spoken(*, clear: bool = False) -> list[str]:
    """Snapshot of recent TTS lines for persona / leak regression tests."""
    out = list(_REGRESSION_UTTERANCES)
    if clear:
        _REGRESSION_UTTERANCES.clear()
    return out

# --- Phase 8: Lazy-load local TTS model only when needed ---
_local_tts_instance = None
def _get_local_tts():
    global _local_tts_instance
    if _local_tts_instance is None:
        from modules.local_tts import get_tts
        _local_tts_instance = get_tts()
    return _local_tts_instance

def stop_audio():
    """Instantly kills any currently playing audio."""
    global is_system_speaking
    stop_speaking_flag.set()
    try:
        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
            print("[SPEAKER] Audio interrupted immediately.")
    except Exception:
        pass
    is_system_speaking = False

async def speak_text(text):
    global is_system_speaking
    
    # Queue up the speech so he finishes sentences naturally
    async with speech_lock:
        is_system_speaking = True 
        
        print(f"[JARVIS] {text}")
        regression_append_utterance(text)

        stop_speaking_flag.clear()
        
        try:
            if USE_LOCAL_TTS:
                await _speak_local(text)
            else:
                await _speak_cloud(text)
        except Exception as e:
            # G5.7: a TTS failure must never crash the turn or silently vanish as
            # an unhandled asyncio task exception (speak_text is often fired via
            # create_task). Log it loudly and stay silent for this line — the
            # conversation continues; only this utterance is lost.
            print(f"[SPEAKER] speak_text failed — staying silent this line: "
                  f"{type(e).__name__}: {e}", flush=True)
        finally:
            # --- FIX 1: The Echo Buffer ---
            # Give the physical headset 0.5 seconds to go silent before opening the mic
            await asyncio.sleep(0.5)
            is_system_speaking = False

async def _speak_local(text):
    """Phase 8: Synthesize speech using local Piper TTS → pygame playback."""
    unique_id = uuid.uuid4().hex[:6]
    segments = re.split(r'(\[.*?\])', text)
    
    tts = _get_local_tts()
    
    for i, segment in enumerate(segments):
        if stop_speaking_flag.is_set():
            break
            
        segment = segment.strip()
        if not segment:
            continue
            
        # Handle control tags
        if segment.startswith('[') and segment.endswith(']'):
            tag = segment[1:-1].lower()
            if tag.startswith('pause:'):
                try:
                    ms = int(tag.split(':')[1])
                    await asyncio.sleep(ms / 1000.0)
                except ValueError:
                    pass
            elif tag == "sigh":
                # Local TTS doesn't support expressive sighs — use a brief pause instead
                await asyncio.sleep(0.5)
            # pitch/rate tags are ignored for Piper (single-voice model)
            continue
        
        # Synthesize text to WAV via BytesIO buffer
        audio_file = f"temp_local_{unique_id}_{i}.wav"
        try:
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(tts.sample_rate)
                for chunk in tts.voice.synthesize(segment):
                    wav_file.writeframes(chunk.audio_int16_bytes)
            
            # Write buffer to temp file for pygame (pygame can't play BytesIO directly)
            buf.seek(0)
            with open(audio_file, 'wb') as f:
                f.write(buf.read())
            
            await asyncio.to_thread(_play_audio, audio_file)
        except Exception as e:
            print(f"[LOCAL TTS] Synthesis error: {e}. Falling back to cloud TTS for this segment.")
            # Fallback: use Edge TTS for this one segment
            fallback_file = f"temp_speech_{unique_id}_{i}.mp3"
            try:
                communicate = edge_tts.Communicate(segment, VOICE)
                await communicate.save(fallback_file)
                await asyncio.to_thread(_play_audio, fallback_file)
            except Exception as e2:
                print(f"[SPEAKER] Both local and cloud TTS failed: {e2}")

async def _speak_cloud(text):
    """Original Edge TTS cloud-based synthesis (fallback)."""
    unique_id = uuid.uuid4().hex[:6]
    
    # --- FIX: Audio Clipping ---
    # Prepend a comma to force a tiny ~200ms silence at the start of the audio file.
    text = ", " + text
    
    segments = re.split(r'(\[.*?\])', text)

    # §3.5: start from the emotion-driven baseline (inline tags still override).
    current_pitch, current_rate = _emotion_baseline()

    for i, segment in enumerate(segments):
        if stop_speaking_flag.is_set():
            break 
            
        segment = segment.strip()
        if not segment:
            continue
            
        if segment.startswith('[') and segment.endswith(']'):
            tag = segment[1:-1].lower()
            
            if tag.startswith('pause:'):
                try:
                    ms = int(tag.split(':')[1])
                    await asyncio.sleep(ms / 1000.0)
                except ValueError:
                    pass
                    
            elif tag == "sigh":
                audio_file = f"temp_sigh_{unique_id}_{i}.mp3"
                comm = edge_tts.Communicate("Haaaah.", VOICE, rate="-20%", pitch="-15Hz")
                await comm.save(audio_file)
                await asyncio.to_thread(_play_audio, audio_file)
                
            elif tag.startswith('pitch:'):
                val = tag.split(':')[1]
                if not val.startswith('+') and not val.startswith('-'):
                    val = '+' + val
                current_pitch = val
                
            elif tag.startswith('rate:'):
                val = tag.split(':')[1]
                if not val.startswith('+') and not val.startswith('-'):
                    val = '+' + val
                current_rate = val
        
        else:
            # Skip segments that are too short for TTS (e.g. lone punctuation from the clipping fix)
            if len(segment.strip(' ,.-!?')) < 2:
                continue
                
            audio_file = f"temp_speech_{unique_id}_{i}.mp3"
            try:
                communicate = edge_tts.Communicate(segment, VOICE, rate=current_rate, pitch=current_pitch)
                await communicate.save(audio_file)
            except Exception as e:
                print(f"[SPEAKER WARNING] TTS synthesis failed for segment, retrying with defaults: {e}")
                try:
                    communicate = edge_tts.Communicate(segment, VOICE)
                    await communicate.save(audio_file)
                except Exception as e2:
                    print(f"[SPEAKER WARNING] Segment skipped (unsynthesizable): '{segment[:40]}'")
                    continue
                
            await asyncio.to_thread(_play_audio, audio_file)

def _play_audio(audio_file):
    try:
        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        
        while pygame.mixer.music.get_busy():
            if stop_speaking_flag.is_set():
                pygame.mixer.music.stop()
                break
            pygame.time.Clock().tick(10)
            
    except Exception as e:
        print(f"[SPEAKER] Audio playback error: {e}")
    finally:
        try:
            # Force Windows to release the file lock
            pygame.mixer.music.unload()
        except AttributeError:
            pass # Failsafe for older Pygame versions
            
        # Give the OS a breather to process the unlock
        time.sleep(0.05)
        
        if os.path.exists(audio_file):
            for _ in range(5): # Retry up to 5 times (0.5 seconds)
                try:
                    os.remove(audio_file)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                print(f"[SPEAKER DEBUG] Could not delete {audio_file} after retries. File locked.")