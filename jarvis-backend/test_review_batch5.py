"""Harness for the pre-Electron review, batch 5 — perception.

  P1  the ambient-vision loop had NO exception guard, so one bad frame ended
      perception for the session — silently, and unrestartably
  P2  a dead daemon left `camera_active: True`, so the brain described a room
      from a frame taken hours earlier
  P3  the face crop was written to a bare relative path and cleaned up by
      straight-line code an exception could skip

P1 and P2 are one failure wearing two hats: the thread that would correct the
flag is the thread that stopped running. `modules/gesture_camera` — the SIBLING
daemon on the same phone stream, hardened by finding 7 — has stall detection,
bounded reopen and a death record. This one had none of it.
"""

import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


def _reset_cache():
    import ambient_vision as av
    av.shared_optical_cache.update({
        "camera_active": False, "last_updated": 0, "daemon_error": None,
        "people_in_view": set(), "objects_in_view": set(), "detections": [],
    })


# ── P2: a populated cache is not a current one ──────────────────────────────

def test_a_frozen_cache_is_not_reported_as_sight():
    """THE ONE THAT MATTERS. `camera_active` is set by the daemon and un-set by
    nothing — so a dead daemon leaves it True forever, and the prompt block
    tells the model to answer "what do you see?" from it."""
    import ambient_vision as av

    _reset_cache()
    check(av.vision_is_fresh() is False, "a cold cache is not fresh")

    av.shared_optical_cache["camera_active"] = True
    av.shared_optical_cache["last_updated"] = time.time()
    check(av.vision_is_fresh() is True, "a reading from just now IS fresh")

    av.shared_optical_cache["last_updated"] = time.time() - 3600
    check(av.vision_is_fresh() is False,
          "an hour-old reading is NOT — this is the frozen-cache case")

    av.shared_optical_cache["last_updated"] = time.time()
    av.shared_optical_cache["camera_active"] = False
    check(av.vision_is_fresh() is False, "and a camera reporting offline never is")
    _reset_cache()


def test_a_timestamp_of_zero_is_never_fresh():
    """The daemon sets `last_updated` only after a frame was really analysed,
    so 0 means 'nothing has ever been seen' — not 'seen at the epoch'."""
    import ambient_vision as av

    _reset_cache()
    av.shared_optical_cache["camera_active"] = True
    av.shared_optical_cache["last_updated"] = 0
    check(av.vision_is_fresh() is False, "camera_active with no reading is not sight")
    _reset_cache()


def test_both_brain_paths_ask_for_freshness_not_the_flag():
    """One policy, two paths — the divergence that produced A2 in batch 3."""
    src = (HERE / "brain.py").read_text(encoding="utf-8", errors="replace")
    for fn in ("process_command", "process_stream"):
        body = src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
        check("vision_is_fresh()" in body,
              f"{fn} gates the visual block on freshness")
        check('if shared_optical_cache.get("camera_active")' not in body,
              f"...and {fn} no longer trusts the bare flag")


# ── P1: one bad frame must not end perception ───────────────────────────────

class _Boom(Exception):
    pass


def test_a_raising_pass_does_not_kill_the_loop():
    """`_daemon_loop` had no try at all. One raise out of model.predict, cv2 or
    DeepFace ended the thread — with `running` still True, so `start()` was a
    no-op ever after."""
    import ambient_vision as av

    _reset_cache()
    daemon = av.AmbientVisionDaemon(interval=0.01)
    daemon.idle_interval = 0.01
    calls = []

    def _flaky(cv2):
        calls.append(1)
        if len(calls) <= 2:
            raise _Boom("a malformed frame")
        if len(calls) >= 4:
            daemon.running = False      # let the test end
        # a good pass: touch the cache the way a real one would
        av.shared_optical_cache["camera_active"] = True
        av.shared_optical_cache["last_updated"] = time.time()

    daemon._one_pass = _flaky
    daemon.running = True
    daemon._daemon_loop()

    check(len(calls) >= 4,
          f"the loop survived two raising passes and kept going; {len(calls)} passes")
    check(av.shared_optical_cache.get("daemon_error") is None,
          "and did not declare itself dead over a transient fault")
    _reset_cache()


def test_a_failing_pass_reports_BLIND_rather_than_empty():
    """"I see nobody" and "I cannot see" are different answers, and only one of
    them is true when the analysis never ran."""
    import ambient_vision as av

    _reset_cache()
    av.shared_optical_cache["camera_active"] = True
    av.shared_optical_cache["last_updated"] = time.time()
    daemon = av.AmbientVisionDaemon(interval=0.01)
    daemon.idle_interval = 0.01

    def _always_boom(cv2):
        daemon.running = False
        raise _Boom("cv2 exploded")

    daemon._one_pass = _always_boom
    daemon.running = True
    daemon._daemon_loop()

    check(av.shared_optical_cache["camera_active"] is False,
          "a failed pass reports the camera as OFFLINE, not as seeing nothing")
    check(av.vision_is_fresh() is False, "so nothing stale reaches the prompt")
    _reset_cache()


def test_persistent_failure_stands_the_daemon_down_loudly():
    import ambient_vision as av

    _reset_cache()
    daemon = av.AmbientVisionDaemon(interval=0.001)
    daemon.idle_interval = 0.001
    seen = []

    def _always_boom(cv2):
        seen.append(1)
        raise _Boom("the camera is gone")

    daemon._one_pass = _always_boom
    daemon.running = True
    daemon._daemon_loop()

    check(len(seen) == 5, f"it gave up after five consecutive failures; {len(seen)}")
    check(daemon.running is False, "and stopped rather than spinning")
    check(av.shared_optical_cache.get("daemon_error") is not None,
          "recording WHY, so the state is diagnosable")
    _reset_cache()


def test_a_dead_thread_can_be_restarted():
    """`if not self.running` was a one-way door: the thread died with running
    still True, so every later start() did nothing."""
    import ambient_vision as av

    _reset_cache()
    daemon = av.AmbientVisionDaemon(interval=0.01)
    passes = []

    def _one(cv2):
        passes.append(1)
        daemon.running = False

    daemon._one_pass = _one
    daemon.start()
    for _ in range(200):                      # let the thread finish
        if daemon.thread and not daemon.thread.is_alive():
            break
        time.sleep(0.01)
    check(daemon.thread is not None and not daemon.thread.is_alive(),
          "the first thread has exited")

    daemon.running = True                     # the exact stuck state
    daemon.start()
    for _ in range(200):
        if len(passes) >= 2:
            break
        time.sleep(0.01)
    daemon.running = False
    check(len(passes) >= 2, f"start() revived a dead thread; {len(passes)} passes")
    _reset_cache()


# ── P3: the face crop ───────────────────────────────────────────────────────

def test_the_face_crop_is_anchored_and_removed_in_a_finally():
    """A cropped photo of whoever is in the room, written unencrypted. The path
    was relative, so it followed whoever launched the process — the same defect
    memory.py's CHROMA_PATH had, with a much worse payload."""
    import ambient_vision as av

    check(os.path.isabs(av._TEMP_DIR), "the temp directory is absolute")
    check(pathlib.Path(av._TEMP_DIR).resolve() == HERE.resolve(),
          "and anchored on the backend directory, not the CWD")

    src = (HERE / "ambient_vision.py").read_text(encoding="utf-8", errors="replace")
    check('temp_path = "temp_ambient_face.jpg"' not in src,
          "the bare relative path is gone")
    check("_TEMP_DIR" in src and "os.path.join(_TEMP_DIR" in src,
          "the write goes through the anchored directory")
    block = src.split("temp_path = os.path.join(_TEMP_DIR", 1)[1]
    check("finally:" in block.split("def ", 1)[0],
          "and the removal is in a finally, not straight-line code")


def test_no_face_crop_is_left_lying_in_the_repo():
    """The property that actually matters, checked on the real disk."""
    stray = list(HERE.glob("temp_ambient_face.jpg")) + \
        list(HERE.parent.glob("temp_ambient_face.jpg"))
    check(not stray, f"no face crop left behind; found {[str(s) for s in stray]}")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Pre-Electron review, batch 5 — perception")
    print("=" * 62)
    for t in TESTS:
        t()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
