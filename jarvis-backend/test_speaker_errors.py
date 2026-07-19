r"""
test_speaker_errors.py — G5.7 speak_text error handling (no real audio)

Run: venv\Scripts\python.exe test_speaker_errors.py

A TTS failure must never crash the turn or vanish as an unhandled asyncio task
exception (speak_text is frequently fired via create_task). This monkeypatches
the synth path to raise and asserts speak_text logs the failure, does NOT
propagate it, and still resets is_system_speaking. Importing speaker initialises
pygame's mixer (falls back to the dummy driver if no device) — no audio is
actually played here.
"""

import asyncio
import io
from contextlib import redirect_stdout

import speaker

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _run(text):
    buf = io.StringIO()
    raised = [None]

    async def go():
        await speaker.speak_text(text)

    with redirect_stdout(buf):
        try:
            asyncio.run(go())
        except Exception as e:  # noqa: BLE001
            raised[0] = e
    return buf.getvalue(), raised[0]


def test_tts_error_is_logged_not_raised():
    saved = speaker._speak_cloud
    speaker.USE_LOCAL_TTS = False

    async def _raiser(_text):
        raise RuntimeError("edge-tts boom")

    speaker._speak_cloud = _raiser
    try:
        out, raised = _run("hello, Sir")
        check(raised is None, "a TTS error does not propagate out of speak_text")
        check("speak_text failed" in out, "the TTS error is logged")
        check(speaker.is_system_speaking is False,
              "is_system_speaking is reset to False after a failure")
    finally:
        speaker._speak_cloud = saved


def test_clean_synth_logs_no_error():
    saved = speaker._speak_cloud
    speaker.USE_LOCAL_TTS = False

    async def _ok(_text):
        return None

    speaker._speak_cloud = _ok
    try:
        out, raised = _run("all good")
        check(raised is None and "speak_text failed" not in out,
              "a clean synth logs no failure line")
        check(speaker.is_system_speaking is False, "flag reset after a clean run")
    finally:
        speaker._speak_cloud = saved


TESTS = [test_tts_error_is_logged_not_raised, test_clean_synth_logs_no_error]


def main():
    print("=" * 60)
    print("speaker speak_text error harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
