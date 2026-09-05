"""Harness: the desk must notice when its own clock is wrong.

WHY THIS EXISTS
---------------
2026-09-05. The desk ran an entire session with its system clock **six days
behind** — every log line stamped `2026-08-30`. Windows corrected it only after
the machine was power-cycled.

For that whole session:

* *"what's on my calendar today"* asked about the wrong day;
* the health day window covered the wrong twenty-four hours;
* every memory row was written with a timestamp six days old;
* the briefing told him the date, confidently and wrongly.

Nothing anywhere said the clock might be wrong, because **the clock is the one
input nothing thinks to doubt.** Every other source in this project has been
taught to admit when it cannot answer; the clock always answers, and always
looks like an answer.

It is the same shape as the rest of this week's findings — a source that is wrong
*quietly* while the layers above report results anyway — and it is worse than
most of them, because it silently invalidates **any measurement taken during the
session, including the gate's own**. A row that passed on that desk that day
proved less than it appeared to.

WHAT THIS PINS
--------------
That the check exists, that it can tell "offline" from "wrong", and that a
day-level error is reported as a day-level error rather than a number of seconds
nobody will read.
"""

from __future__ import annotations

import datetime as dt
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules import boot_preflight as bp  # noqa: E402

_checks = 0
_fails: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print(f"FAIL  {label}")


def _http_date(offset_days: float = 0.0) -> str:
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=offset_days)
    return when.strftime("%a, %d %b %Y %H:%M:%S GMT")


def test_a_correct_clock_passes_quietly():
    rep = bp.check_clock(fetch_date=lambda: _http_date(0))
    check(rep["checked"], "the check ran")
    check(rep["ok"], f"a correct clock is ok (skew {rep.get('skew_s')})")
    check(not rep["wrong_day"], "and the day matches")
    check("agrees" in bp.format_clock(rep),
          "and it says so without alarming anyone")


def test_the_six_day_error_is_caught():
    """The actual event, reproduced."""
    rep = bp.check_clock(fetch_date=lambda: _http_date(+6))
    check(rep["checked"] and not rep["ok"], "a six-day error is NOT ok")
    check(rep["wrong_day"], "and is reported as the DATE being wrong")
    msg = bp.format_clock(rep)
    check("6.0 days" in msg, f"the message says days, not just seconds: {msg[:60]}")
    check("BEHIND" in msg, "and which direction it is wrong in")
    check("calendar" in msg and "health" in msg,
          "and names what it makes wrong, because 'clock skew' alone does not "
          "tell the reader that today's briefing was fiction")
    check("w32tm /resync" in msg, "and how to fix it")


def test_a_clock_ahead_is_caught_too():
    rep = bp.check_clock(fetch_date=lambda: _http_date(-3))
    check(rep["checked"] and not rep["ok"], "a clock three days fast is not ok")
    check("AHEAD OF" in bp.format_clock(rep), "and is named as ahead")


def test_small_drift_is_not_alarming():
    """Seconds of drift are normal and must not cry wolf, or the real warning
    gets ignored."""
    rep = bp.check_clock(
        fetch_date=lambda: (dt.datetime.now(dt.timezone.utc)
                            - dt.timedelta(seconds=20)).strftime(
                                "%a, %d %b %Y %H:%M:%S GMT"))
    check(rep["ok"], f"20 seconds of drift is fine (skew {rep.get('skew_s')})")


def test_offline_is_not_reported_as_a_wrong_clock():
    """The distinction this project keeps having to relearn: 'I could not check'
    is not 'it is wrong'."""
    def boom():
        raise OSError("no route to host")

    rep = bp.check_clock(fetch_date=boom)
    check(rep["checked"] is False, "an unreachable source means NOT CHECKED")
    check("ok" not in rep, "and no verdict is invented")
    msg = bp.format_clock(rep)
    check("not checked" in msg, f"and it says so plainly: {msg[:60]!r}")
    check("no route to host" in msg, "naming why, so it can be fixed")


def test_a_missing_or_unparseable_header_is_also_not_a_verdict():
    check(bp.check_clock(fetch_date=lambda: None)["checked"] is False,
          "a missing Date header is not a wrong clock")
    check(bp.check_clock(fetch_date=lambda: "not a date")["checked"] is False,
          "an unparseable Date header is not a wrong clock")


def test_it_actually_runs_at_boot():
    """A check nothing calls is a check nobody has."""
    src = (HERE / "main.py").read_text(encoding="utf-8")
    check("boot_preflight.log_clock" in src, "boot runs the clock check")


if __name__ == "__main__":
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
