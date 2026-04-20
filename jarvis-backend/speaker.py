import edge_tts
import pygame
import asyncio
import os
import threading
import re

VOICE = "en-GB-RyanNeural"

# Global Kill Switch for Barge-In
stop_speaking_flag = threading.Event()

def stop_audio():
    """Instantly kills any currently playing audio."""
    if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
        print("[SPEAKER] Barge-in detected! Aborting audio playback.")
        stop_speaking_flag.set()

async def speak_text(text):
    print(f"[JARVIS] {text}")
    stop_speaking_flag.clear()
    
    # --- THE DIRECTOR PATTERN PARSER ---
    segments = re.split(r'(\[.*?\])', text)
    
    current_pitch = "+0Hz"
    current_rate = "+2%"
    
    for segment in segments:
        if stop_speaking_flag.is_set():
            break # Abort if the user barges in
            
        segment = segment.strip()
        if not segment:
            continue
            
        # If the segment is a stage direction tag
        if segment.startswith('[') and segment.endswith(']'):
            tag = segment[1:-1].lower()
            
            if tag.startswith('pause:'):
                try:
                    ms = int(tag.split(':')[1])
                    await asyncio.sleep(ms / 1000.0)
                except ValueError:
                    pass
                    
            elif tag == "sigh":
                audio_file = "temp_sigh.mp3"
                comm = edge_tts.Communicate("Haaaah.", VOICE, rate="-20%", pitch="-15Hz")
                await comm.save(audio_file)
                await asyncio.to_thread(_play_audio, audio_file)
                
            elif tag.startswith('pitch:'):
                current_pitch = tag.split(':')[1]
                
            elif tag.startswith('rate:'):
                current_rate = tag.split(':')[1]
        
        # If the segment is regular text to be spoken
        else:
            audio_file = "temp_speech.mp3"
            try:
                # Attempt to generate with custom LLM tags
                communicate = edge_tts.Communicate(segment, VOICE, rate=current_rate, pitch=current_pitch)
                await communicate.save(audio_file)
            except Exception as e:
                # --- FIX: Safe Fallback if LLM hallucinates a bad tag format ---
                print(f"[SPEAKER WARNING] Invalid tag format. Reverting to default voice. Error: {e}")
                communicate = edge_tts.Communicate(segment, VOICE)
                await communicate.save(audio_file)
                
            await asyncio.to_thread(_play_audio, audio_file)

def _play_audio(audio_file):
    if not pygame.mixer.get_init():
        pygame.mixer.init()
        
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
        pygame.mixer.quit()
        if os.path.exists(audio_file):
            try:
                os.remove(audio_file)
            except OSError:
                pass