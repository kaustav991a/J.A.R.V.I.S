import os
import wave
import json
from piper.voice import PiperVoice
import sounddevice as sd
import numpy as np

class LocalTTS:
    def __init__(self, model_path="en-gb-alan-low.onnx"):
        print(f"[LOCAL TTS] Loading Piper model ({model_path})...")
        self.model_path = model_path
        self.voice = PiperVoice.load(model_path)
        print("[LOCAL TTS] Piper model loaded.")
        
        # Determine sample rate from config
        with open(f"{model_path}.json", "r", encoding="utf-8") as f:
            config = json.load(f)
        self.sample_rate = config["audio"]["sample_rate"]
        
    def stream_tts(self, text_stream):
        """
        Takes a generator (text_stream) that yields text chunks (words/sentences).
        Synthesizes audio on the fly and streams it instantly to the speakers.
        """
        print("[LOCAL TTS] Streaming audio started...")
        # Piper provides a synthesize_stream_raw which yields raw PCM bytes.
        
        stream = sd.OutputStream(samplerate=self.sample_rate, channels=1, dtype='int16')
        stream.start()
        
        try:
            sentence = ""
            for text_chunk in text_stream:
                sentence += text_chunk
                if any(p in text_chunk for p in ['.', '!', '?']):
                    sentence = sentence.strip()
                    if sentence:
                        import io
                        import wave
                        buf = io.BytesIO()
                        with wave.open(buf, 'wb') as wav_file:
                            wav_file.setnchannels(1)
                            wav_file.setsampwidth(2)
                            wav_file.setframerate(self.sample_rate)
                            self.voice.synthesize(sentence, wav_file)
                        buf.seek(0)
                        with wave.open(buf, 'rb') as wav_file:
                            audio_bytes = wav_file.readframes(wav_file.getnframes())
                            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
                            stream.write(audio_np)
                    sentence = ""
            
            # Synthesize whatever is left
            if sentence.strip():
                import io
                import wave
                buf = io.BytesIO()
                with wave.open(buf, 'wb') as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(self.sample_rate)
                    self.voice.synthesize(sentence.strip(), wav_file)
                buf.seek(0)
                with wave.open(buf, 'rb') as wav_file:
                    audio_bytes = wav_file.readframes(wav_file.getnframes())
                    audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
                    stream.write(audio_np)
                    
        except Exception as e:
            print(f"[LOCAL TTS] Streaming interrupted: {e}")
        finally:
            stream.stop()
            stream.close()
            print("[LOCAL TTS] Streaming finished.")

# Singleton instance
_instance = None

def get_tts() -> LocalTTS:
    global _instance
    if _instance is None:
        _instance = LocalTTS(model_path="en-gb-alan-low.onnx")
    return _instance

if __name__ == "__main__":
    tts = get_tts()
    print("Testing TTS pipeline...")
    
    # Simulate a streaming generator from an LLM
    def dummy_llm():
        words = ["Good", " morning", " sir.", " How", " may", " I", " assist", " you?"]
        for w in words:
            yield w
            import time
            time.sleep(0.1) # Simulate network delay
            
    tts.stream_tts(dummy_llm())
