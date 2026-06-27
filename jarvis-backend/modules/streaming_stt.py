"""
streaming_stt.py — True Streaming Speech-to-Text (Roadmap §1.3)
==============================================================

Transcribes *as you speak* (interim/partial results) instead of the legacy
record-then-transcribe batch flow. Backed by **vosk** — a fully offline streaming
recogniser that emits partial hypotheses while audio is still arriving and a final
result when an utterance ends.

Usage (frame-by-frame, 16 kHz mono int16):
    stt = StreamingSTT()                 # lazy-loads the vosk model
    for frame in mic_frames:             # ~20 ms each
        evt = stt.accept(frame.tobytes())
        if evt["partial"]: ...           # live caption (optional)
        if evt["final"]:   ...           # an utterance completed → act on it
    tail = stt.flush()                   # final words when the stream stops

Model: set `JARVIS_VOSK_MODEL` to a downloaded vosk model directory, or drop one at
`models/vosk/` beside the backend (e.g. vosk-model-small-en-us-0.15, ~40 MB). If no
model is present, `is_available()` is False and the caller falls back to batch STT.
"""

from __future__ import annotations

import os
import json


def _default_model_path() -> str:
    env = os.getenv("JARVIS_VOSK_MODEL", "").strip()
    if env:
        return env
    return os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "vosk")
    )


class StreamingSTT:
    def __init__(self, sample_rate: int = 16000, model_path: str | None = None,
                 recognizer=None):
        """`recognizer` may be injected (testing); otherwise a vosk KaldiRecognizer
        is created lazily from the model directory."""
        self.sample_rate = sample_rate
        self.model_path = model_path or _default_model_path()
        self._rec = recognizer
        self._available = recognizer is not None
        self._load_error: str | None = None
        if recognizer is None:
            self._try_load()

    def _try_load(self) -> None:
        try:
            from vosk import Model, KaldiRecognizer, SetLogLevel
            SetLogLevel(-1)  # silence vosk's chatty logs
            if not os.path.isdir(self.model_path):
                self._load_error = f"vosk model not found at {self.model_path}"
                self._available = False
                return
            model = Model(self.model_path)
            self._rec = KaldiRecognizer(model, self.sample_rate)
            self._rec.SetWords(False)
            self._available = True
            print(f"[STREAM_STT] vosk streaming recogniser online ({self.model_path}).", flush=True)
        except Exception as e:
            self._load_error = f"{type(e).__name__}: {e}"
            self._available = False
            print(f"[STREAM_STT] streaming STT unavailable — {self._load_error}. "
                  f"Falling back to batch STT.", flush=True)

    def is_available(self) -> bool:
        return self._available and self._rec is not None

    def accept(self, pcm_int16_bytes: bytes) -> dict:
        """Feed one audio chunk. Returns {"partial": str, "final": str|None}.

        `final` is non-None exactly on the frame where vosk decides the utterance
        ended (a natural pause), carrying the completed transcript.
        """
        if not self.is_available():
            return {"partial": "", "final": None}
        try:
            if self._rec.AcceptWaveform(pcm_int16_bytes):
                text = json.loads(self._rec.Result()).get("text", "").strip()
                return {"partial": "", "final": text or None}
            partial = json.loads(self._rec.PartialResult()).get("partial", "").strip()
            return {"partial": partial, "final": None}
        except Exception as e:
            print(f"[STREAM_STT] accept error: {e}", flush=True)
            return {"partial": "", "final": None}

    def flush(self) -> str:
        """Return any buffered final words (call when the stream stops)."""
        if not self.is_available():
            return ""
        try:
            return json.loads(self._rec.FinalResult()).get("text", "").strip()
        except Exception:
            return ""

    def reset(self) -> None:
        """Forget the current utterance (e.g. after a barge-in handoff)."""
        if self.is_available():
            try:
                self._rec.Reset()
            except Exception:
                pass


# Lazy process-wide singleton (model load is heavy).
_singleton: StreamingSTT | None = None


def get_streaming_stt(sample_rate: int = 16000) -> StreamingSTT:
    global _singleton
    if _singleton is None:
        _singleton = StreamingSTT(sample_rate=sample_rate)
    return _singleton
