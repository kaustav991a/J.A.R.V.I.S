import os
import io
import time
import wave
import numpy as np
from faster_whisper import WhisperModel

class LocalSTT:
    def __init__(self, model_size="tiny.en", compute_type="int8"):
        """
        Initializes the local faster-whisper model.
        tiny.en and base.en use very little RAM/CPU and run incredibly fast.
        """
        print(f"[LOCAL STT] Loading Whisper model ({model_size}) into memory...")
        start = time.time()
        # cpu device is forced to avoid needing CUDA setup, int8 keeps RAM usage low
        self.model = WhisperModel(model_size, device="cpu", compute_type=compute_type)
        print(f"[LOCAL STT] Model loaded in {time.time() - start:.2f} seconds.")

    def transcribe_audio_file(self, file_path: str) -> str:
        """Transcribes an audio file on disk."""
        segments, info = self.model.transcribe(file_path, beam_size=1)
        text = "".join([segment.text for segment in segments])
        return text.strip()

    def transcribe_audio_data(self, audio_data: bytes, sample_rate: int = 16000) -> str:
        """Transcribes raw PCM audio bytes in memory."""
        # Convert bytes to numpy array (faster-whisper expects a float32 array [-1.0, 1.0])
        audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
        
        segments, info = self.model.transcribe(audio_np, beam_size=1)
        text = "".join([segment.text for segment in segments])
        return text.strip()

# Singleton instance to avoid reloading the model
_instance = None

def get_stt() -> LocalSTT:
    global _instance
    if _instance is None:
        _instance = LocalSTT(model_size="tiny.en")
    return _instance

if __name__ == "__main__":
    # Test script
    stt = get_stt()
    print("STT Ready. (Pass audio data to transcribe)")
