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
  test_screen_reader.py                  live VLM script (screenshots + a real
                                         model call) — not a deterministic harness

PART A3 is now EMPTY — the last pytest-only files were converted 2026-07-30 (D#13):
  test_android_tv_agent.py (6), test_github_agent.py (5), test_gmail_agent.py (3)
  are self-running and included above. Converting them found a real defect in the
  last one: its `reply_email` mock put the thread headers behind a second
  `messages.get` the implementation never calls, so the test had been asserting
  against a shape the code does not produce. It had never run, so nobody knew.

RETIRED 2026-07-30 (D#13) — `tests/` is gone; each was run first, and each failed
for a structural reason, not a flake:
  tests/test_briefing.py   patched `action_engine.CalendarAgent`, which no longer
                           exists — the agent imports were restructured.
  tests/test_hardware.py   `ActionEngine.execute` is a coroutine now; the test
                           asserted on the un-awaited object ("'coroutine' object
                           has no attribute 'lower'"). The genuine pre-async fossil.
  tests/test_scheduler.py  patched `background_monitor.speaker`, an attribute the
                           module no longer has.
  COVERAGE HONESTLY LOST: briefing concurrency + degradation, TV intent routing +
  unreachable-hardware fallback, scheduler dedup + midnight flush. Rewriting these
  against the current async API is worth doing; it was not part of this chore.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Same hardening as main.py / watchdog.py: piped stdout falls back to cp1252 on
# Windows and dies on any non-ASCII log line.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# The block above only protects THIS process. Each harness runs as a SEPARATE
# process that picks its own stdout encoding from the locale, so a print with a
# '→' in the code under test kills the child before the parent sees a byte —
# which is exactly how test_governance and test_android_tv_agent "failed" with
# every assertion still correct. The `encoding="utf-8"` on subprocess.run below
# only says how the PARENT decodes; the child had already crashed encoding.
# Forcing the child's encoding is the real fix, and it makes the suite
# deterministic instead of depending on whichever shell invoked it.
_CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

# A harness that HANGS is worse than one that fails, and until 2026-08-15 there
# was no ceiling here at all. `test_app_link.py` blocked forever inside a
# TestClient `receive_json()` — the stubbed brain raised, so the frame it was
# waiting for was never sent — and the suite simply stopped, mid-run, with no
# failure, no summary and no clue which file it was in. "0 failed" is a known
# trap in this file's docstring; "no output at all" is a worse one, because the
# run looks like it is still working.
#
# Generous on purpose: the slowest legitimate harnesses do real work. This is a
# deadlock detector, not a performance budget.
HARNESS_TIMEOUT_S = float(os.getenv("JARVIS_HARNESS_TIMEOUT_S", "600") or 600)

HERE = Path(__file__).resolve().parent

# Harnesses are DISCOVERED, not listed. Until 2026-08-08 this was a hand-kept
# list with the comment "add new ones here when they land", and `test_agent_wave2.py`
# (29 checks) did not land in it — the suite reported all-green while a whole
# harness sat outside it, which is the failure a suite exists to prevent. A file
# that is not in EXCLUDED and is not run is now impossible.
#
# EXCLUDED is the deliberate opposite list: files that are named `test_*` but are
# not deterministic harnesses. It is checked for staleness on every run, so a
# rename cannot leave a silent hole here either.
EXCLUDED = {
    "test_ping.py": "needs the backend running (TEST_PLAN part A2)",
    "test_ui_bridge_e2e.py": "needs the backend running (TEST_PLAN part A2)",
    "test_screen_reader.py": "live VLM script — screenshots and a real model call",
    "test_mcp_server_fake.py": "an MCP SERVER, not a harness — test_mcp_bridge.py "
                               "spawns it as a subprocess fixture",
}


def discover() -> list[str]:
    """Every `test_*.py` beside this file that is not deliberately excluded."""
    found = sorted(p.name for p in HERE.glob("test_*.py"))
    stale = [name for name in EXCLUDED if name not in found]
    if stale:
        print(f"[HARNESS] EXCLUDED names no longer on disk: {', '.join(stale)} "
              f"— fix the exclusion list", flush=True)
    return [name for name in found if name not in EXCLUDED]


# Kept only as the historical record of what the list held on 2026-08-08, so a
# discovery bug is visible as a DIFFERENCE rather than as a quiet shrink.
_KNOWN_AT_LAST_EDIT = [
    "test_action_parser.py",
    "test_agent_core.py",
    "test_android_tv_agent.py",
    "test_agent_runner.py",
    "test_agent_subagents.py",
    "test_agent_tools.py",
    "test_agent_yield.py",
    "test_ambient_camera.py",
    "test_auth_status.py",
    "test_backdoor_gate.py",
    "test_boot_preflight.py",
    "test_calibrate_gesture.py",
    "test_camera_stream.py",
    "test_chroma_crypto.py",
    "test_cursor_overlay.py",
    "test_enroll_face.py",
    "test_face_gate.py",
    "test_fact_governance.py",
    "test_fact_seal.py",
    "test_fact_transport.py",
    "test_failure_detection.py",
    "test_frame_bus.py",
    "test_gesture_arbiter.py",
    "test_gesture_calibration.py",
    "test_gesture_camera.py",
    "test_gesture_engine.py",
    "test_gesture_roi.py",
    "test_github_agent.py",
    "test_gmail_agent.py",
    "test_governance.py",
    "test_listen_request.py",
    "test_agent_errors.py",
    "test_agent_files.py",
    "test_agent_schema.py",
    "test_agent_search.py",
    "test_agent_wave1.py",
    "test_llm_failover.py",
    "test_memory_crypto.py",
    "test_memory_extraction_guard.py",
    "test_memory_source.py",
    "test_memory_store_encryption.py",
    "test_owner_notify.py",
    "test_partner_contact.py",
    "test_partner_messaging.py",
    "test_partner_send_gate.py",
    "test_presence_probe.py",
    "test_speaker_errors.py",
    "test_store_retirement.py",
    "test_tool_call.py",
    "test_watchdog_policy.py",
    "test_web_freshness.py",
    "test_working_memory_lock.py",
]

HARNESSES = discover()

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
    # Say what discovery added since the list was last written down. Not a
    # failure — new harnesses are the normal case — but it should be VISIBLE
    # that the count moved, because a silently growing suite is how a shrinking
    # one goes unnoticed too.
    added = [h for h in HARNESSES if h not in _KNOWN_AT_LAST_EDIT]
    dropped = [h for h in _KNOWN_AT_LAST_EDIT if h not in HARNESSES]
    if added:
        print(f"[HARNESS] discovered since the last written list: {', '.join(added)}")
    if dropped:
        print(f"[HARNESS] GONE since the last written list: {', '.join(dropped)}")

    total_passed = total_failed = 0
    broken: list[str] = []
    started = time.monotonic()

    for name in HARNESSES:
        path = HERE / name
        if not path.exists():
            broken.append(f"{name} (missing)")
            continue
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, str(path)],
                cwd=HERE,
                env=_CHILD_ENV,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=HARNESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as e:
            # The child is already killed by the time this lands. Print the tail
            # it did manage — the last PASS line names the test BEFORE the one
            # that hung, which is how tonight's hang was located.
            dt = time.monotonic() - t0
            out = e.stdout or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            tail = "\n".join(out.splitlines()[-15:])
            broken.append(f"{name} (TIMEOUT after {HARNESS_TIMEOUT_S:g}s)")
            print(f"\n----- {name} TIMED OUT -----\n{tail}\n"
                  f"(no summary line: this harness never finished. The last PASS above "
                  f"is the test BEFORE the one that hung.)\n")
            print(f"[HANG] {name:<32} {0:>4} checks  {dt:5.1f}s")
            continue
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
