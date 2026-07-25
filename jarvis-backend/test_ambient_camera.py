r"""
test_ambient_camera.py — shared camera-source resolution for ambient_vision (no camera, no network)

Run: venv\Scripts\python.exe test_ambient_camera.py

ambient_vision, gesture_daemon and vision.scan_for_faces must all follow the
SAME phone address. Each used to carry its own hardcoded IP and they drifted
apart (ambient_vision and vision.py both pointed at a 192.168.0.106 that no
longer existed, while the gesture daemon streamed fine from JARVIS_CAM_SOURCES).
This pins ambient_vision's resolver: it takes the first *URL* of the shared
JARVIS_CAM_SOURCES priority list, skips device indices (an int index is
meaningless as an MJPEG URL), falls back to legacy JARVIS_CAM, and still lets an
explicit JARVIS_CAMERA_URL win outright.

ambient_vision is import-safe by design (threading/time/os only), so reloading
it under a patched environment costs nothing — no TensorFlow, DeepFace, YOLO or
OpenCV is touched.
"""

import importlib
import os

import ambient_vision

_passed = 0
_failed = 0

_CAM_VARS = ("JARVIS_CAM_SOURCES", "JARVIS_CAM", "JARVIS_CAMERA_URL", "JARVIS_CAMERA_BASE")


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


class _env:
    """Set exactly the camera vars given; unset every other one; restore on exit."""

    def __init__(self, **vals):
        self.vals = vals
        self.saved = {}

    def __enter__(self):
        for k in _CAM_VARS:
            self.saved[k] = os.environ.get(k)
            if k in self.vals:
                os.environ[k] = self.vals[k]
            else:
                os.environ.pop(k, None)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # module constants are env-derived at import time — put them back
        importlib.reload(ambient_vision)
        return False


def _resolve():
    return ambient_vision._default_camera_url()


def test_first_url_of_priority_list_wins():
    with _env(JARVIS_CAM_SOURCES="http://10.0.0.5:8080/video,http://10.0.0.9:4747/video"):
        check(_resolve() == "http://10.0.0.5:8080/video",
              f"first list entry wins, got {_resolve()}")


def test_device_indices_are_skipped():
    with _env(JARVIS_CAM_SOURCES="0,1,http://10.0.0.5:8080/video"):
        check(_resolve() == "http://10.0.0.5:8080/video",
              f"leading device indices skipped, got {_resolve()}")


def test_all_indices_falls_back_to_literal():
    with _env(JARVIS_CAM_SOURCES="0,1,2"):
        got = _resolve()
        check(got.startswith("http://"), f"index-only list yields a URL, got {got}")


def test_blank_entries_and_whitespace():
    with _env(JARVIS_CAM_SOURCES=" , ,  http://10.0.0.5:8080/video  ,0"):
        check(_resolve() == "http://10.0.0.5:8080/video",
              f"blanks dropped and entry trimmed, got {_resolve()}")


def test_legacy_single_cam_var_is_the_fallback():
    with _env(JARVIS_CAM="http://10.0.0.7:8080/video"):
        check(_resolve() == "http://10.0.0.7:8080/video",
              f"legacy JARVIS_CAM used when SOURCES unset, got {_resolve()}")


def test_empty_sources_falls_through_to_legacy():
    with _env(JARVIS_CAM_SOURCES="", JARVIS_CAM="http://10.0.0.7:8080/video"):
        check(_resolve() == "http://10.0.0.7:8080/video",
              f"empty SOURCES falls through to JARVIS_CAM, got {_resolve()}")


def test_sources_outranks_legacy_cam():
    with _env(JARVIS_CAM_SOURCES="http://10.0.0.5:8080/video",
              JARVIS_CAM="http://10.0.0.7:8080/video"):
        check(_resolve() == "http://10.0.0.5:8080/video",
              f"SOURCES outranks legacy JARVIS_CAM, got {_resolve()}")


def test_nothing_set_yields_a_stream_url():
    with _env():
        got = _resolve()
        check(got.startswith("http://") and got.endswith("/video"),
              f"bare environment yields a literal stream URL, got {got}")


def test_explicit_camera_url_overrides_the_list():
    with _env(JARVIS_CAMERA_URL="http://10.0.0.99:8080/video",
              JARVIS_CAM_SOURCES="http://10.0.0.5:8080/video"):
        m = importlib.reload(ambient_vision)
        check(m.CAMERA_URL == "http://10.0.0.99:8080/video",
              f"JARVIS_CAMERA_URL wins outright, got {m.CAMERA_URL}")
        check(m.CAMERA_BASE == "http://10.0.0.99:8080",
              f"CAMERA_BASE drops the stream path for the reachability ping, got {m.CAMERA_BASE}")
        check(m.shared_optical_cache["camera_url"] == "http://10.0.0.99:8080/video",
              "HUD cache advertises the same stream URL")


def test_module_constants_follow_the_shared_list():
    with _env(JARVIS_CAM_SOURCES="0,http://10.0.0.5:8080/video"):
        m = importlib.reload(ambient_vision)
        check(m.CAMERA_URL == "http://10.0.0.5:8080/video",
              f"CAMERA_URL resolves off the shared list, got {m.CAMERA_URL}")
        check(m.CAMERA_BASE == "http://10.0.0.5:8080",
              f"CAMERA_BASE derived from it, got {m.CAMERA_BASE}")
        check(m.AmbientVisionDaemon().camera_url == "http://10.0.0.5:8080/video",
              "daemon default camera_url matches the resolved URL")


def test_explicit_camera_base_is_respected():
    with _env(JARVIS_CAM_SOURCES="http://10.0.0.5:8080/video",
              JARVIS_CAMERA_BASE="http://10.0.0.5:8080/ping"):
        m = importlib.reload(ambient_vision)
        check(m.CAMERA_BASE == "http://10.0.0.5:8080/ping",
              f"explicit JARVIS_CAMERA_BASE not overwritten, got {m.CAMERA_BASE}")


TESTS = [
    test_first_url_of_priority_list_wins,
    test_device_indices_are_skipped,
    test_all_indices_falls_back_to_literal,
    test_blank_entries_and_whitespace,
    test_legacy_single_cam_var_is_the_fallback,
    test_empty_sources_falls_through_to_legacy,
    test_sources_outranks_legacy_cam,
    test_nothing_set_yields_a_stream_url,
    test_explicit_camera_url_overrides_the_list,
    test_module_constants_follow_the_shared_list,
    test_explicit_camera_base_is_respected,
]


def main():
    print("=" * 60)
    print("ambient_vision camera-source harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
