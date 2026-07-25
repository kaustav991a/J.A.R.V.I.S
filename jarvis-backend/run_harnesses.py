"""Run every self-running test harness and report one total.

The repo convention is self-running harnesses (no pytest in the venv), so this is
the single command PART A of TEST_PLAN.md refers to:

    .\\venv\\Scripts\\python.exe run_harnesses.py

Each harness is run as a subprocess with the SAME interpreter. Pass/fail is taken
from the exit code (authoritative); the check count is parsed best-effort from the
harness's own summary line, which comes in two historical shapes:

    24/24 passed.
    15 passed, 0 failed

Exits 1 if any harness fails, so this is safe to use as a gate.

NOT included (and why):
  test_ping.py, test_ui_bridge_e2e.py    need the backend running  (PART A2)
  test_governance.py, test_android_tv_agent.py, test_github_agent.py,
  test_gmail_agent.py, tests/*           need pytest, not in the venv (PART A3)
  test_screen_reader.py                  live VLM script (screenshots + a real
                                         model call) — not a deterministic harness
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The self-running harnesses, alphabetical. Add new ones here when they land.
HARNESSES = [
    "test_action_parser.py",
    "test_ambient_camera.py",
    "test_auth_status.py",
    "test_boot_preflight.py",
    "test_calibrate_gesture.py",
    "test_camera_stream.py",
    "test_cursor_overlay.py",
    "test_enroll_face.py",
    "test_face_gate.py",
    "test_failure_detection.py",
    "test_frame_bus.py",
    "test_gesture_arbiter.py",
    "test_gesture_calibration.py",
    "test_gesture_camera.py",
    "test_gesture_engine.py",
    "test_gesture_roi.py",
    "test_llm_failover.py",
    "test_owner_notify.py",
    "test_presence_probe.py",
    "test_speaker_errors.py",
    "test_watchdog_policy.py",
    "test_working_memory_lock.py",
]

# "24/24 passed." | "15 passed, 0 failed"
_SLASH = re.compile(r"(\d+)\s*/\s*(\d+)\s+passed", re.I)
_WORDS = re.compile(r"(\d+)\s+passed,\s*(\d+)\s+failed", re.I)


def parse_counts(output: str) -> tuple[int, int]:
    """Return (passed, failed) from a harness's summary line, (0, 0) if unparsed."""
    passed = failed = 0
    for line in output.splitlines():
        m = _SLASH.search(line)
        if m:
            passed, failed = int(m.group(1)), int(m.group(2)) - int(m.group(1))
            continue
        m = _WORDS.search(line)
        if m:
            passed, failed = int(m.group(1)), int(m.group(2))
    return passed, max(failed, 0)


def main() -> int:
    missing = [h for h in HARNESSES if not (HERE / h).exists()]
    if missing:
        print(f"[HARNESS] MISSING FILES: {', '.join(missing)}")

    total_passed = total_failed = 0
    broken: list[str] = []
    started = time.monotonic()

    for name in HARNESSES:
        path = HERE / name
        if not path.exists():
            broken.append(f"{name} (missing)")
            continue
        t0 = time.monotonic()
        proc = subprocess.run(
            [sys.executable, str(path)],
            cwd=HERE,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        dt = time.monotonic() - t0
        passed, failed = parse_counts((proc.stdout or "") + (proc.stderr or ""))
        total_passed += passed
        total_failed += failed
        ok = proc.returncode == 0 and failed == 0
        if not ok:
            broken.append(f"{name} (exit={proc.returncode}, failed={failed})")
            tail = "\n".join((proc.stdout or "").splitlines()[-15:])
            print(f"\n----- {name} FAILED -----\n{tail}\n{(proc.stderr or '')[-1500:]}\n")
        mark = "OK  " if ok else "FAIL"
        print(f"[{mark}] {name:<32} {passed:>4} checks  {dt:5.1f}s")

    elapsed = time.monotonic() - started
    print("-" * 62)
    print(
        f"{len(HARNESSES) - len(broken)}/{len(HARNESSES)} harnesses green  |  "
        f"{total_passed} checks passed, {total_failed} failed  |  {elapsed:.1f}s"
    )
    if broken:
        print("BROKEN: " + "; ".join(broken))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
