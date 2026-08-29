"""Harness: where "today" begins, and the hours that fell off the end of it.

WHY THIS EXISTS
---------------
F-76, measured on the desk 2026-08-29 **while re-auditing a row that had already
been marked complete** — which is the finding behind the finding.

`HealthAgent.get_today_health_data` computed its window from **UTC midnight**. He
is in IST (UTC+5:30), so "today" began at 05:30 local and everything before it
was discarded. Measured at 15:21 local against the same Fit account:

    UTC-midnight window (what the agent used) ...    0 steps
    real local day .............................   64 steps
    dropped 00:00-05:30 IST ....................   64 steps

**Every step he had taken that day was inside the discarded hours.** So the desk
said *"No health data has been recorded yet today, Sir"* while Fit held 64 steps
and 277.1 kcal — and row `10.8` had been passed an hour earlier on the strength
of that sentence being an "honest empty". It was not empty. It was a false
statement about his day, produced by a window that begins five and a half hours
late, and it is exactly the class of failure the goal above it forbids.

**The calendar had already fixed this for itself** — `_ist_day_bounds` carries
the comment *"Fixes the UTC-boundary bug that dropped midnight-5:30am IST
events"*. Same defect, found once, fixed in one of the two places that had it.
Root cause #4, which is why `modules/local_day.py` exists rather than a second
copy of the arithmetic.

WHAT THIS PINS
--------------
Offline and deterministic. No Google, no network: every instant is injected.

  * the day starts at LOCAL midnight, not UTC midnight;
  * the hours between them are inside the window, not outside it — the 64 steps
    are the whole of the defect;
  * the offset is configurable, because a fixed +5:30 in the code is the same
    class of bug one move away;
  * a malformed override falls back rather than raising, since a bad `.env` must
    not take out the health module;
  * **the calendar and the health agent agree on the boundary.** They are the two
    places that had this arithmetic, and a harness that pins only one of them
    would let them drift apart again;
  * the health agent no longer computes a window of its own.

Run standalone: `python test_local_day.py`
"""

import datetime
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules import local_day as ld  # noqa: E402

_fails: list = []
_checks = 0


def check(ok: bool, why: str) -> None:
    global _checks
    _checks += 1
    if ok:
        print(f"PASS  {why}")
    else:
        print(f"FAIL  {why}")
        _fails.append(why)


IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
# 15:21 IST on the day this was measured — the exact instant of the finding.
AT_MEASUREMENT = datetime.datetime(2026, 8, 29, 15, 21, tzinfo=IST)


def test_the_day_starts_at_local_midnight():
    start, end = ld.day_bounds(AT_MEASUREMENT)
    check(start.hour == 0 and start.minute == 0,
          f"the window opens at local midnight: {start.isoformat()}")
    check(start.utcoffset() == datetime.timedelta(hours=5, minutes=30),
          "...in HIS timezone, not the server's")
    check((end - start) == datetime.timedelta(days=1), "and it is one day long")


def test_the_discarded_hours_are_inside_the_window():
    """The whole defect in one assertion: 04:00 local is today, and the UTC-
    midnight window said it was not."""
    start, _ = ld.day_bounds(AT_MEASUREMENT)
    four_am = AT_MEASUREMENT.replace(hour=4, minute=0)
    check(start <= four_am, "04:00 local falls inside today")

    utc_midnight = AT_MEASUREMENT.astimezone(datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    check(utc_midnight > four_am,
          "...and the OLD window began after it — which is why the steps vanished")
    gap = (utc_midnight - start)
    check(gap == datetime.timedelta(hours=5, minutes=30),
          f"the two boundaries are 5h30 apart, which is the offset: {gap}")


def test_elapsed_today_stops_at_now_not_at_midnight_tomorrow():
    start_ms, now_ms = ld.elapsed_today_ms(AT_MEASUREMENT)
    start, end = ld.day_bounds(AT_MEASUREMENT)
    check(start_ms == int(start.timestamp() * 1000), "it starts at local midnight")
    check(now_ms == int(AT_MEASUREMENT.timestamp() * 1000),
          "...and ends NOW — 'so far today' is the question being answered")
    check(now_ms < int(end.timestamp() * 1000), "...which is before tomorrow")


def test_the_offset_is_configurable():
    """A fixed +5:30 in the code is the same bug one move away."""
    was = os.environ.get("JARVIS_TZ_OFFSET_MIN")
    os.environ["JARVIS_TZ_OFFSET_MIN"] = "0"
    try:
        check(ld.local_tz().utcoffset(None) == datetime.timedelta(0),
              "an override is honoured")
    finally:
        if was is None:
            os.environ.pop("JARVIS_TZ_OFFSET_MIN", None)
        else:
            os.environ["JARVIS_TZ_OFFSET_MIN"] = was
    check(ld.local_tz().utcoffset(None) == datetime.timedelta(hours=5, minutes=30),
          "...and the default is his own offset")


def test_a_malformed_override_falls_back_rather_than_raising():
    was = os.environ.get("JARVIS_TZ_OFFSET_MIN")
    os.environ["JARVIS_TZ_OFFSET_MIN"] = "half past five"
    try:
        check(ld.local_tz().utcoffset(None) == datetime.timedelta(hours=5, minutes=30),
              "a bad .env value does not take the health module out")
    finally:
        if was is None:
            os.environ.pop("JARVIS_TZ_OFFSET_MIN", None)
        else:
            os.environ["JARVIS_TZ_OFFSET_MIN"] = was


def test_the_calendar_and_the_health_agent_agree_on_the_boundary():
    """The two places that had this arithmetic. Pinning one and not the other is
    how they drifted apart in the first place."""
    from modules import calendar_agent as ca
    cal_min, _cal_max = ca._ist_day_bounds()
    start, _ = ld.day_bounds()
    check(cal_min.startswith(start.isoformat()[:13]),
          f"same day, same hour: calendar={cal_min[:16]} helper={start.isoformat()[:16]}")
    check(ca._IST.utcoffset(None) == ld.local_tz().utcoffset(None),
          "and the same offset, so a move changes both or neither")


def test_the_health_agent_no_longer_computes_its_own_window():
    src = (HERE / "modules" / "health_agent.py").read_text(encoding="utf-8",
                                                           errors="replace")
    check("elapsed_today_ms" in src, "it asks the shared helper")
    check("datetime.timezone.utc)\n            start_of_day" not in src,
          "...and the UTC-midnight arithmetic is gone")
    check("F-76" in src, "...with the reason recorded where the next reader looks")


if __name__ == "__main__":
    import traceback

    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)),
                   key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        try:
            fn()
        except Exception:
            _fails.append(name)
            print(f"FAIL  {name} raised")
            traceback.print_exc()
    print(f"\n{_checks - len(_fails)}/{_checks} passed.")
    sys.exit(1 if _fails else 0)
