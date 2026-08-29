"""Where "today" begins, in one place.

WHY THIS EXISTS
---------------
F-76, measured on the desk 2026-08-29. `HealthAgent.get_today_health_data`
computed its window from **UTC midnight**:

    now = datetime.datetime.now(datetime.timezone.utc)
    start_of_day = now.replace(hour=0, ...)

He is in IST (UTC+5:30), so that window begins at **05:30 local** and silently
discards everything between midnight and half past five. Measured at 15:21 local,
against the same Fit account, on the same afternoon:

    UTC-midnight window (what the agent used) ...    0 steps
    real local day .............................   64 steps
    dropped 00:00-05:30 IST ....................   64 steps

**Every step he had taken that day fell inside the discarded hours** — which for
someone who works late is precisely where his day's activity lives. So the desk
said *"No health data has been recorded yet today, Sir"* while Fit held 64 steps,
and that sentence is false.

**The calendar had already fixed this bug for itself.** `calendar_agent._ist_day_bounds`
carries the comment *"Fixes the UTC-boundary bug that dropped midnight-5:30am IST
events"* — the same defect, found once, fixed in one of the two places that had
it. Root cause #4, which is why the answer is a shared helper rather than a
second copy of the arithmetic.

`JARVIS_TZ_OFFSET_MIN` overrides the offset for a machine that moves. It is an
offset rather than a zone name because that is all this needs, and a zone
database is one more thing to be wrong about a `.env` away.
"""

from __future__ import annotations

import datetime
import os

# +5:30 unless told otherwise. Read at call time, not at import, so a change to
# the environment does not need a restart to take effect.
_DEFAULT_OFFSET_MIN = 330


def local_tz() -> datetime.timezone:
    """The operator's timezone, as a fixed offset."""
    try:
        minutes = int(os.getenv("JARVIS_TZ_OFFSET_MIN", "").strip() or _DEFAULT_OFFSET_MIN)
    except ValueError:
        minutes = _DEFAULT_OFFSET_MIN
    return datetime.timezone(datetime.timedelta(minutes=minutes))


def day_bounds(now: datetime.datetime | None = None
               ) -> tuple[datetime.datetime, datetime.datetime]:
    """(start of today, start of tomorrow) in the operator's own timezone.

    `now` is injectable so a harness can pin the boundary without waiting for
    one — the whole defect was about which instant counts as the start of a day,
    and a test that cannot choose the instant cannot test it.
    """
    tz = local_tz()
    current = (now or datetime.datetime.now(tz)).astimezone(tz)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + datetime.timedelta(days=1)


def day_bounds_ms(now: datetime.datetime | None = None) -> tuple[int, int]:
    """The same window as epoch milliseconds, which is what Google Fit takes."""
    start, end = day_bounds(now)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def elapsed_today_ms(now: datetime.datetime | None = None) -> tuple[int, int]:
    """Local midnight to NOW, rather than to the end of the day.

    Fit is asked for elapsed time rather than the whole day on purpose: a bucket
    that runs into the future is not wrong, but "so far today" is the question
    being answered, and asking it plainly keeps the answer honest.
    """
    tz = local_tz()
    current = (now or datetime.datetime.now(tz)).astimezone(tz)
    start, _ = day_bounds(current)
    return int(start.timestamp() * 1000), int(current.timestamp() * 1000)
