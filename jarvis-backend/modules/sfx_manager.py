import pygame
import os

_initialized = False
_sounds = {}

def init_sfx():
    global _initialized
    if _initialized:
        return
        
    try:
        # Initialize pygame mixer if not already initialized
        if not pygame.mixer.get_init():
            pygame.mixer.init()
            
        # Pre-load sounds from assets/sounds directory
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sfx_dir = os.path.join(base_dir, "assets", "sounds")
        
        if not os.path.exists(sfx_dir):
            os.makedirs(sfx_dir)
            
        sound_files = ["wake.wav", "listening.wav", "processing.wav", "error.wav", "success.wav"]
        
        for sf in sound_files:
            sf_path = os.path.join(sfx_dir, sf)
            if os.path.exists(sf_path):
                _sounds[sf.split('.')[0]] = pygame.mixer.Sound(sf_path)
                
        _initialized = True
        print("[SYSTEM] SFX Manager Initialized.")
    except Exception as e:
        print(f"[SYSTEM] SFX Manager Failed to Initialize: {e}")

def play_sfx(sound_name):
    """
    Plays a pre-loaded sound effect.
    Available sounds: 'wake', 'listening', 'processing', 'error', 'success'
    """
    if not _initialized:
        init_sfx()
        
    sound = _sounds.get(sound_name)
    if sound:
        try:
            # Play on an available channel
            sound.play()
        except Exception:
            pass
