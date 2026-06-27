import torch
import numpy as np
import logging

_vad_model = None
_get_speech_timestamps = None

def _get_vad_model():
    global _vad_model, _get_speech_timestamps
    if _vad_model is None:
        print("[SYSTEM] Loading Silero VAD (Zero-Idle Wake Word Engine)...")
        logging.getLogger('torch').setLevel(logging.ERROR)
        _vad_model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True
        )
        _get_speech_timestamps = utils[0]
    return _vad_model, _get_speech_timestamps

def has_human_speech(audio_data, threshold=0.5):
    """
    Analyzes an sr.AudioData object using Silero VAD.
    Returns True if human speech is detected with probability > threshold.
    """
    model, get_timestamps = _get_vad_model()
    
    raw_data = audio_data.get_raw_data()
    sample_rate = audio_data.sample_rate
    sample_width = audio_data.sample_width
    
    if sample_width != 2:
        return True # Fallback to transcribe if format is unknown
        
    audio_np = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
    
    if sample_rate != 16000:
        import torchaudio.transforms as T
        resampler = T.Resample(sample_rate, 16000)
        audio_tensor = torch.from_numpy(audio_np)
        audio_tensor = resampler(audio_tensor)
    else:
        audio_tensor = torch.from_numpy(audio_np)
        
    with torch.no_grad():
        speech_timestamps = get_timestamps(audio_tensor, model, sampling_rate=16000, threshold=threshold)
        return len(speech_timestamps) > 0
