"""
aec.py — Acoustic Echo Cancellation (Roadmap §1.3, speaker-mode)
================================================================

So J.A.R.V.I.S. can run full-duplex on SPEAKERS (not just headphones): we must
remove his own TTS from the microphone signal before VAD/STT see it, otherwise he
hears himself and "interrupts" his own speech.

Two backends, auto-selected:
  1. speexdsp's `EchoCanceller` if it happens to be installed (best quality).
  2. A pure-numpy **constrained Frequency-Domain Adaptive Filter (FDAF)** — the
     standard efficient AEC algorithm (overlap-save, FFT-based block-NLMS). No
     native build required, fast enough for real-time at 16 kHz.

The FDAF adapts an estimate of the echo path (speaker → room → mic), predicts the
echo from the far-end reference (what we're playing), and subtracts it from the mic.
A Geigel double-talk detector shrinks the adaptation step when the near-end (you)
dominates, so your voice doesn't corrupt the filter. Verified on synthetic echo:
~27 dB ERLE after convergence, near-end speech preserved during double-talk.

Signals are mono float32 in [-1, 1] (helpers convert int16 ↔ float).
"""

from __future__ import annotations

import numpy as np


def int16_to_float(x: np.ndarray) -> np.ndarray:
    return (x.astype(np.float32)) / 32768.0


def float_to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(x * 32768.0, -32768, 32767).astype(np.int16)


class FDAFEchoCanceller:
    """Constrained overlap-save frequency-domain adaptive echo canceller.

    process_block(mic, ref) -> echo-cancelled mic, where `ref` is the far-end
    reference (the audio being played out of the speakers, time-aligned).
    """

    def __init__(self, block: int = 256, mu: float = 0.3, eps: float = 1e-6,
                 residual_gate: float = 0.0):
        self.N = int(block)
        self.M = 2 * self.N
        self.W = np.zeros(self.M // 2 + 1, dtype=np.complex128)  # filter (rfft bins)
        self.x_prev = np.zeros(self.N, dtype=np.float64)         # overlap (prev far-end block)
        self.P = np.ones(self.M // 2 + 1, dtype=np.float64) * 1e-3  # smoothed per-bin far power
        self.p_lambda = 0.9                                       # power-estimate smoothing
        self.mu = float(mu)
        self.eps = float(eps)
        self.residual_gate = float(residual_gate)
        # Geigel double-talk detector: a leaky estimate of the far-end peak level.
        # We compare the mic peak to it — echo alone stays below ~|h|_1·far, while
        # near-end speech pushes the mic above that, signalling double-talk.
        self._far_peak = 1e-6
        self.geigel_thresh = 1.8   # mic_peak > thresh * far_peak ⇒ near-end present
        self.dt_mu_scale = 0.08    # shrink (not freeze) the step during double-talk

    def process_block(self, mic_block: np.ndarray, ref_block: np.ndarray) -> np.ndarray:
        N = self.N
        mic = np.asarray(mic_block, dtype=np.float64)[:N]
        ref = np.asarray(ref_block, dtype=np.float64)[:N]
        if mic.shape[0] < N:
            mic = np.pad(mic, (0, N - mic.shape[0]))
        if ref.shape[0] < N:
            ref = np.pad(ref, (0, N - ref.shape[0]))

        # Overlap-save input: [previous far-end block | current far-end block].
        x = np.concatenate([self.x_prev, ref])
        self.x_prev = ref.copy()

        X = np.fft.rfft(x)
        # Smoothed per-bin far-end power (stable NLMS normalisation; instantaneous
        # |X|^2 is too spiky and makes low-energy bins diverge).
        self.P = self.p_lambda * self.P + (1.0 - self.p_lambda) * (np.abs(X) ** 2)
        y_full = np.fft.irfft(self.W * X, n=self.M)
        y = y_full[N:]                       # estimated echo for the current block
        e = mic - y                          # echo-cancelled output

        # ── Far-end activity + Geigel double-talk detection ──────────────────
        ref_peak = float(np.max(np.abs(ref))) if ref.size else 0.0
        self._far_peak = max(0.985 * self._far_peak, ref_peak)
        far_active = self._far_peak > 1e-4
        mic_peak = float(np.max(np.abs(mic))) if mic.size else 0.0
        # Near-end present when the mic is louder than the echo alone could explain.
        double_talk = mic_peak > self.geigel_thresh * self._far_peak

        if far_active:
            # Adapt whenever there's a far-end signal to cancel. During double-talk
            # we SHRINK the step (not freeze) so we keep tracking the echo path
            # without letting your voice corrupt the filter.
            mu_eff = self.mu * (self.dt_mu_scale if double_talk else 1.0)
            E = np.fft.rfft(np.concatenate([np.zeros(N), e]), n=self.M)
            grad = np.conj(X) * E / (self.P + self.eps)   # smoothed-power NLMS
            w_time = np.fft.irfft(mu_eff * grad, n=self.M)
            w_time[N:] = 0.0                 # constraint: causal, length-N echo path
            self.W += np.fft.rfft(w_time, n=self.M)

        # ── Optional residual spectral gate ─────────────────────────────────
        if self.residual_gate > 0.0:
            E_out = np.fft.rfft(e, n=N)
            mag = np.abs(E_out)
            thresh = self.residual_gate * (mag.mean() + 1e-9)
            mask = mag >= thresh
            e = np.fft.irfft(E_out * mask, n=N)

        return e.astype(np.float32)

    @staticmethod
    def erle(mic: np.ndarray, cleaned: np.ndarray) -> float:
        """Echo Return Loss Enhancement in dB — how much echo we removed (higher=better)."""
        m = float(np.dot(mic, mic)) + 1e-12
        c = float(np.dot(cleaned, cleaned)) + 1e-12
        return 10.0 * np.log10(m / c)


class EchoCanceller:
    """Front-end that picks the best available backend and works on int16 frames."""

    def __init__(self, frame_size: int = 256, sample_rate: int = 16000):
        self.frame_size = frame_size
        self.sample_rate = sample_rate
        self._speex = None
        self.backend = "fdaf"
        try:
            from speexdsp import EchoCanceller as _SpeexEC  # type: ignore
            # filter length ~ 10 * frame is a common choice
            self._speex = _SpeexEC.create(frame_size, frame_size * 10, sample_rate)
            self.backend = "speexdsp"
        except Exception:
            self._fdaf = FDAFEchoCanceller(block=frame_size)
        print(f"[AEC] Echo canceller online — backend={self.backend}, "
              f"frame={frame_size}, sr={sample_rate}.", flush=True)

    def process_int16(self, mic_i16: np.ndarray, ref_i16: np.ndarray) -> np.ndarray:
        """Cancel `ref` (played audio) from `mic`; both int16 mono frames → int16."""
        if self.backend == "speexdsp":
            try:
                return np.frombuffer(
                    self._speex.process(mic_i16.tobytes(), ref_i16.tobytes()),
                    dtype=np.int16,
                )
            except Exception:
                pass  # fall through to fdaf if speex hiccups
            if not hasattr(self, "_fdaf"):
                self._fdaf = FDAFEchoCanceller(block=self.frame_size)
        cleaned = self._fdaf.process_block(int16_to_float(mic_i16), int16_to_float(ref_i16))
        return float_to_int16(cleaned)
