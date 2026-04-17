import edge_tts
import pygame
import asyncio
import os

VOICE = "en-GB-RyanNeural"

async def speak_text(text):
    print(f"[JARVIS] {text}")
    audio_file = "temp_speech.mp3"
    
    # 1. Generate the audio asynchronously
    communicate = edge_tts.Communicate(text, VOICE, rate="+5%")
    await communicate.save(audio_file)
    
    # 2. Play it in a background thread so it doesn't drop the WebSocket
    await asyncio.to_thread(_play_audio, audio_file)

def _play_audio(audio_file):
    pygame.mixer.init()
    pygame.mixer.music.load(audio_file)
    pygame.mixer.music.play()
    
    while pygame.mixer.music.get_busy():
        pygame.time.Clock().tick(10)
        
    pygame.mixer.quit()
    
    if os.path.exists(audio_file):
        try:
            os.remove(audio_file)
        except:
            pass