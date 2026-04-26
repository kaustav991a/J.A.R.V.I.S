import edge_tts
import pygame
import asyncio
import os
import threading
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
USE_LOCAL_TTS = True

VOICE = "en-GB-RyanNeural"

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
    if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
        print("[SPEAKER] System interrupted previous audio.")
        stop_speaking_flag.set()

async def speak_text(text):
    global is_system_speaking
    
    # Queue up the speech so he finishes sentences naturally
    async with speech_lock:
        is_system_speaking = True 
        
        print(f"[JARVIS] {text}")
        
        stop_speaking_flag.clear()
        
        try:
            if USE_LOCAL_TTS:
                await _speak_local(text)
            else:
                await _speak_cloud(text)
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
                tts.voice.synthesize(segment, wav_file)
            
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
    
    current_pitch = "+0Hz"
    current_rate = "+2%"
    
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
            audio_file = f"temp_speech_{unique_id}_{i}.mp3"
            try:
                communicate = edge_tts.Communicate(segment, VOICE, rate=current_rate, pitch=current_pitch)
                await communicate.save(audio_file)
            except Exception as e:
                print(f"[SPEAKER WARNING] Invalid tag format. Reverting to default voice. Error: {e}")
                communicate = edge_tts.Communicate(segment, VOICE)
                await communicate.save(audio_file)
                
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