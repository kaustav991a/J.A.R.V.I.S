import librosa
import numpy as np
from deepface import DeepFace

def analyze_facial_emotion(frame) -> str:
    """Analyzes a cropped face or full frame for dominant emotion using DeepFace."""
    try:
        # We set enforce_detection=False so it doesn't crash if it can't find a face
        result = DeepFace.analyze(frame, actions=['emotion'], enforce_detection=False, silent=True)
        
        # DeepFace can return a list if multiple faces are found
        if isinstance(result, list):
            result = result[0]
            
        dominant = result.get('dominant_emotion')
        return dominant
    except Exception as e:
        # Silently fail for background frames
        return "neutral"

def analyze_vocal_stress(audio_file_path: str) -> str:
    """Analyzes an audio file for signs of stress based on pitch and energy."""
    try:
        y, sr = librosa.load(audio_file_path, sr=None)
        
        # Calculate root-mean-square energy (loudness)
        rms = librosa.feature.rms(y=y)
        avg_energy = np.mean(rms)
        
        # Calculate fundamental frequency (pitch)
        pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
        avg_pitch = np.mean(pitches[pitches > 0])
        
        # High energy and high pitch usually indicate stress/excitement
        if avg_energy > 0.05 and avg_pitch > 250:
            return "stressed"
        elif avg_energy < 0.01 and avg_pitch < 150:
            return "tired"
        else:
            return "neutral"
            
    except Exception as e:
        print(f"[EMOTION DETECTOR] Voice analysis failed: {e}")
        return "neutral"
