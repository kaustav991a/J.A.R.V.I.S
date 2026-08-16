"""One transcription rule for both microphones.

Live-gate finding F-38. Cloud STT is the only transcriber in this build
(`USE_LOCAL_STT = False` in `wakeword.py` and `recorder.py` alike), so when the
machine lost DNS mid-session — `[BRIDGE] link down ([Errno 11001] getaddrinfo
failed)`, weather and calendar failing alongside it — every spoken word became
`[STT] Heard: ''`. The owner said "hello jarvis" repeatedly to a system that
could not hear him and could not tell him why. A local `tiny.en` model was
sitting on the same disk, already a dependency, loaded in under a second.

**The fallback fires on a NETWORK failure and not on a rejected utterance.**
That distinction is the whole design:

  * cloud timed out / refused / could not be reached → the audio was never
    judged, so ask the local model. This is the outage case.
  * cloud answered "I could not understand that" (`UnknownValueError`) → the
    audio WAS judged, by the better model, and found unintelligible. A
    `tiny.en` second opinion on audio the big model rejected is where whisper
    hallucinates — "Thank you." on silence is its signature failure — and this
    text can approve a CONFIRM-tier action or wake an admin session. A guess is
    not worth that.

Both doors call this, because "which other door reaches this verb" is the
question this project keeps having to ask itself late.
"""

from __future__ import annotations

import concurrent.futures

import speech_recognition as sr

# Cheap enough to run on every fallback: faster-whisper tiny.en, int8.
_local = None


def _get_local():
    global _local
    if _local is None:
        from modules.local_stt import get_stt
        _local = get_stt()
    return _local


def _local_transcribe(audio) -> str:
    try:
        text = _get_local().transcribe_audio_data(
            audio.get_raw_data(), sample_rate=audio.sample_rate)
        return (text or "").strip()
    except Exception as e:  # noqa: BLE001 — a dead fallback must not raise
        print(f"[STT] Local fallback unavailable: {e}", flush=True)
        return ""


def transcribe(recognizer, audio, *, cloud_timeout: float = 5.0,
               prefer_local: bool = False, tag: str = "STT") -> tuple[str, str]:
    """Return `(text, engine)` — engine is 'cloud', 'local' or '' on failure.

    Never raises. `UnknownValueError` comes back as `("", "cloud")`: the cloud
    was reachable and rejected the audio, which is a real answer and not an
    outage.
    """
    if prefer_local:
        return _local_transcribe(audio), "local"

    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(recognizer.recognize_google, audio)
            return future.result(timeout=cloud_timeout).strip(), "cloud"
    except sr.UnknownValueError:
        # Heard, judged, rejected. Not an outage — do not guess.
        return "", "cloud"
    except concurrent.futures.TimeoutError:
        print(f"[{tag}] Cloud STT timed out ({cloud_timeout:.0f}s) — "
              f"falling back to the local model.", flush=True)
    except sr.RequestError as e:
        print(f"[{tag}] Cloud STT unreachable ({e}) — "
              f"falling back to the local model.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[{tag}] Cloud STT failed ({type(e).__name__}: {e}) — "
              f"falling back to the local model.", flush=True)

    text = _local_transcribe(audio)
    if text:
        print(f"[{tag}] Local STT heard: '{text}'", flush=True)
    else:
        print(f"[{tag}] Local STT could not make it out either — "
              f"the microphone is working, the network is not.", flush=True)
    return text, "local"
