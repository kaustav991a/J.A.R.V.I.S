"""
audio_pipeline.py — Continuous Full-Duplex Audio Engine (Roadmap §1.3)
=====================================================================

The streaming refactor of the voice path. Instead of "wait for wake → record a
window → transcribe", this engine listens CONTINUOUSLY and processes audio in small
frames, so J.A.R.V.I.S. can converse while he speaks:

    mic frame ──▶ AEC (subtract his own TTS via the reference buffer)
              ──▶ VAD (speech onset? → barge-in)
              ──▶ streaming STT (partial captions + final utterances)
              ──▶ callbacks: on_partial / on_final / on_speech_start

Design:
- **Composable & testable.** The per-frame logic (`process_frame`) is pure given its
  injected AEC / STT / reference / VAD, so it's unit-tested with synthetic frames —
  no microphone needed. `run()` is the thin PyAudio adapter that feeds real frames.
- **Echo-cancelled, speaker-safe.** Pulls the far-end reference from
  `speaker.pull_reference_pcm()` and runs the FDAF AEC, so it works on speakers
  (headphones still work too — the AEC just sees silence and no-ops).
- **Opt-in.** Enabled by `JARVIS_FULL_DUPLEX_PIPELINE=1`; the classic wakeword/recorder
  path remains the default so nothing existing breaks.

NOTE: final on-device verification (real mic + speaker echo) is required — acoustic
delay/filter-length tuning is hardware-specific (see aec.py).
"""

from __future__ import annotations

import os
import numpy as np

SAMPLE_RATE = 16000
FRAME = int(os.getenv("JARVIS_FD_FRAME", "320"))  # 20 ms @ 16 kHz


def energy_vad(frame_i16: np.ndarray, threshold: float = 500.0) -> bool:
    """Cheap RMS speech-onset detector (vosk does the real endpointing)."""
    if frame_i16.size == 0:
        return False
    rms = float(np.sqrt(np.mean(frame_i16.astype(np.float64) ** 2)))
    return rms >= threshold


class FullDuplexEngine:
    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        frame: int = FRAME,
        aec=None,
        stt=None,
        reference_fn=None,         # (n) -> list[int] far-end reference samples
        vad=energy_vad,
        on_partial=None,           # (text) -> None
        on_final=None,             # (text) -> None
        on_speech_start=None,      # () -> None  (barge-in trigger)
        speech_hangover_frames: int = 8,
    ):
        self.sample_rate = sample_rate
        self.frame = frame
        self.aec = aec
        self.stt = stt
        self.reference_fn = reference_fn
        self.vad = vad
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_speech_start = on_speech_start
        self.speech_hangover = speech_hangover_frames
        self._in_speech = False
        self._silence_run = 0
        self.is_running = True

    # ── Per-frame core (pure given injected deps — unit-tested) ───────────────
    def process_frame(self, mic_i16: np.ndarray) -> dict:
        mic_i16 = np.asarray(mic_i16, dtype=np.int16)

        # 1. Echo cancellation against what we're currently playing.
        if self.aec is not None and self.reference_fn is not None:
            ref = np.asarray(self.reference_fn(len(mic_i16)), dtype=np.int16)
            clean = self.aec.process_int16(mic_i16, ref)
        else:
            clean = mic_i16

        # 2. Speech-onset detection → barge-in.
        speech = self.vad(clean) if self.vad else True
        if speech:
            self._silence_run = 0
            if not self._in_speech:
                self._in_speech = True
                if self.on_speech_start:
                    self.on_speech_start()
        else:
            self._silence_run += 1
            if self._in_speech and self._silence_run >= self.speech_hangover:
                self._in_speech = False

        # 3. Streaming recognition.
        partial, final = "", None
        if self.stt is not None:
            evt = self.stt.accept(clean.tobytes())
            partial, final = evt.get("partial", ""), evt.get("final")
            if partial and self.on_partial:
                self.on_partial(partial)
            if final and self.on_final:
                self.on_final(final)

        return {"clean": clean, "speech": speech, "partial": partial, "final": final}

    # ── Real PyAudio capture loop (thin adapter; needs hardware) ──────────────
    def run(self) -> None:
        try:
            import pyaudio
        except Exception as e:
            print(f"[FD-PIPELINE] PyAudio unavailable: {e}", flush=True)
            return
        pa = pyaudio.PyAudio()
        stream = pa.open(format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
                         input=True, frames_per_buffer=self.frame)
        print(f"[FD-PIPELINE] Continuous full-duplex listening @ {self.sample_rate} Hz, "
              f"{self.frame}-sample frames.", flush=True)
        try:
            while self.is_running:
                raw = stream.read(self.frame, exception_on_overflow=False)
                self.process_frame(np.frombuffer(raw, dtype=np.int16))
        except Exception as e:
            print(f"[FD-PIPELINE] capture loop error: {e}", flush=True)
        finally:
            try:
                stream.stop_stream(); stream.close(); pa.terminate()
            except Exception:
                pass

    def stop(self) -> None:
        self.is_running = False


def build_default_engine(*, on_partial=None, on_final=None, on_speech_start=None):
    """Wire AEC + streaming STT + the speaker reference buffer into an engine.

    Returns None if streaming STT isn't available (no vosk model) so the caller can
    fall back to the classic batch path.
    """
    from modules.streaming_stt import get_streaming_stt
    stt = get_streaming_stt(sample_rate=SAMPLE_RATE)
    if not stt.is_available():
        print("[FD-PIPELINE] Streaming STT not available — full-duplex pipeline disabled.", flush=True)
        return None
    aec = None
    if os.getenv("JARVIS_AEC", "1").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from modules.aec import EchoCanceller
            aec = EchoCanceller(frame_size=FRAME, sample_rate=SAMPLE_RATE)
        except Exception as e:
            print(f"[FD-PIPELINE] AEC unavailable ({e}); continuing without it.", flush=True)
    import speaker
    return FullDuplexEngine(
        aec=aec, stt=stt, reference_fn=speaker.pull_reference_pcm,
        on_partial=on_partial, on_final=on_final, on_speech_start=on_speech_start,
    )


def is_enabled() -> bool:
    return os.getenv("JARVIS_FULL_DUPLEX_PIPELINE", "0").strip().lower() in ("1", "true", "yes", "on")
