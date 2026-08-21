"""Harness: the briefing the gateway now sends, because the phone cannot.

The morning briefing was a WorkManager job on the phone until 2026-08-20, when
the device said plainly why it never fired unprompted. Read off uid 10495 before
the app was opened:

    timeout-reg / timeout-total: countInWindow=0     (quota clean)
    UID: 10495; Network: 108 (blocked=REASON_APP_BACKGROUND|REASON_APP_STANDBY)
    UidStats{uid=10495 #run=0 #netAvail=0 #reg=0}
    standby bucket: 40 (RARE)

`expo-background-task` hardcodes `setRequiredNetworkType(NetworkType.CONNECTED)`,
so the work sat on a constraint Android would not satisfy. Not deferred —
stopped. Logcat then caught the pending worker running 200ms after a cold launch
and re-queueing into a window it would be blocked in again, which is exactly how
it was reported: "the briefing arrives after I open the app".

So the schedule moved here, and with it the wording. That is a second copy of
thresholds and prose, which is a real cost — these checks are what keeps the copy
honest, because the phone's version is still live as a fallback and for PREVIEW.

WHAT THIS PINS
--------------
Offline and deterministic. No provider, no Open-Meteo, no clock — every time is
passed in. The properties are the ones the phone got wrong at least once each:

  * the day index is Sunday-first, the way the phone counts, not Monday-first the
    way Python does;
  * a briefing fires at its time or shortly after and NEVER before, and never
    twice in a day;
  * a forecast that answered about the wrong hours is an absence of knowledge,
    not a quiet morning — it must not consume the day;
  * a quiet day still speaks, and carries figures rather than the word "fine";
  * every clock printed carries its meridiem.
"""

import datetime as dt
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Sunday off, Monday-Friday on, Saturday off — the shipped default.
WEEKDAYS = [False, True, True, True, True, True, False]

OFFICE = {"place_id": "office", "label": "Office", "hour": 19, "minute": 0,
          "lat": 22.5726, "lon": 88.3639}
HOME = {"place_id": "home", "label": "Home", "hour": 8, "minute": 0,
        "lat": 22.5, "lon": 88.3}
SCHED = {"tz": "Asia/Kolkata", "days": WEEKDAYS, "departures": [HOME, OFFICE]}


def setup_function(_):
    # the once-a-day marks are module state; a test must not inherit another's
    cg._briefed.clear()


# 2026-08-19 was a Wednesday, 2026-08-23 a Sunday. Both used below.
def at(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=IST)


# ── how the days are counted ─────────────────────────────────────────────────

def test_the_week_is_indexed_sunday_first_the_way_the_phone_counts():
    """`Date.getDay()` says 0 = Sunday; Python's `weekday()` says 0 = Monday.

    Getting this wrong is a briefing that arrives on the wrong days, which reads
    as a scheduling bug rather than as an off-by-one.
    """
    assert cg._js_weekday(at(2026, 8, 23, 12, 0)) == 0   # Sunday
    assert cg._js_weekday(at(2026, 8, 24, 12, 0)) == 1   # Monday
    assert cg._js_weekday(at(2026, 8, 19, 12, 0)) == 3   # Wednesday
    assert cg._js_weekday(at(2026, 8, 22, 12, 0)) == 6   # Saturday


def test_a_day_that_is_switched_off_gets_nothing():
    assert cg._due_departure(at(2026, 8, 23, 19, 0), SCHED) is None   # Sunday


# ── when it fires ────────────────────────────────────────────────────────────

def test_it_fires_at_the_departure_time():
    due = cg._due_departure(at(2026, 8, 19, 19, 0), SCHED)
    assert due is not None and due["place_id"] == "office"


def test_it_still_fires_shortly_after_but_not_indefinitely():
    """The window is for a redeploy landing on the wrong minute, not for slop.

    Past it the briefing is stale — a warning about rain on the way out is worth
    nothing once he has left.
    """
    assert cg._due_departure(at(2026, 8, 19, 19, 19), SCHED) is not None
    assert cg._due_departure(at(2026, 8, 19, 19, 21), SCHED) is None


def test_it_never_fires_early():
    """The phone's window was ±30 minutes because Android chose when its job ran.

    Nothing chooses for this loop, so an early briefing would be a decision
    rather than a symptom — and the earlier it lands the less it is worth.
    """
    assert cg._due_departure(at(2026, 8, 19, 18, 45), SCHED) is None


def test_the_morning_and_the_evening_are_told_apart():
    morning = cg._due_departure(at(2026, 8, 19, 8, 5), SCHED)
    assert morning is not None and morning["place_id"] == "home"


def test_the_same_departure_is_not_briefed_twice_in_a_day():
    """The same umbrella three times teaches you to swipe without reading."""
    when = at(2026, 8, 19, 19, 0)
    assert cg._due_departure(when, SCHED) is not None
    cg._briefed["office"] = "2026-08-19"
    assert cg._due_departure(when, SCHED) is None
    # and tomorrow is a new day, not a permanent silence
    assert cg._due_departure(at(2026, 8, 20, 19, 0), SCHED) is not None


def test_briefing_the_morning_does_not_silence_the_evening():
    """Per departure, not per day. Two doors, two answers."""
    cg._briefed["home"] = "2026-08-19"
    assert cg._due_departure(at(2026, 8, 19, 8, 5), SCHED) is None
    assert cg._due_departure(at(2026, 8, 19, 19, 0), SCHED) is not None


# ── what it says ─────────────────────────────────────────────────────────────

def forecast(hours, **series):
    """An Open-Meteo reply for 2026-08-19, one entry per hour given."""
    out = {"time": [f"2026-08-19T{h:02d}:00" for h in hours]}
    for key, values in series.items():
        out[key] = list(values)
    return {"hourly": out}


def test_rain_earns_an_umbrella_and_the_figure_comes_first():
    """The measurement leads and the remark follows.

    These lines are read half-awake on a lock screen by someone deciding whether
    to pick something up on the way out. A recommendation with no figure behind
    it cannot be disagreed with.
    """
    data = forecast(
        [19, 20, 21],
        temperature_2m=[27, 26, 26],
        precipitation_probability=[80, 60, 40],
        precipitation=[1.2, 0.4, 0.0],
        weather_code=[61, 61, 3],
        wind_speed_10m=[12, 10, 8],
    )
    title, body = cg._briefing_text(data, OFFICE, "2026-08-19")
    assert title == "Before you leave Office, sir"
    assert body.startswith("A 80% chance of rain")
    assert "umbrella" in body
    assert "!" not in body


def test_a_thunderstorm_is_said_before_anything_else():
    data = forecast(
        [19, 20, 21],
        temperature_2m=[27, 27, 27],
        precipitation_probability=[90, 90, 90],
        precipitation=[4.0, 3.0, 1.0],
        weather_code=[95, 61, 61],
        wind_speed_10m=[20, 20, 20],
    )
    _, body = cg._briefing_text(data, OFFICE, "2026-08-19")
    assert body.startswith("Thunderstorms forecast.")


def test_a_quiet_evening_still_speaks_and_carries_numbers():
    """Silence was read as the feature being broken, for four days straight.

    Overruled 2026-08-18. The reassurance is not the word "fine" — it names the
    temperature, the rain chance and the wind, which is enough to disagree with.
    An empty "nothing to worry about" would be the same unfalsifiable silence
    with a buzz attached.
    """
    data = forecast(
        [19, 20, 21],
        temperature_2m=[26, 25, 25],
        precipitation_probability=[5, 0, 0],
        precipitation=[0.0, 0.0, 0.0],
        weather_code=[0, 1, 1],
        wind_speed_10m=[6, 5, 5],
    )
    title, body = cg._briefing_text(data, OFFICE, "2026-08-19")
    assert title == "Nothing in your way from Office, sir"
    assert "26°C" in body and "5% chance of rain" in body and "km/h" in body
    assert "Do try to enjoy it." in body


def test_a_forecast_about_the_wrong_hours_is_not_a_quiet_evening():
    """An absence of knowledge, so the day must not be consumed.

    This is the distinction that cost the phone four days: `Briefing | null`
    collapsed "nothing worth saying" and "could not find out" into one value, the
    task read both as the former, and marked the departure briefed until tomorrow
    — where it failed identically.
    """
    data = forecast(
        [3, 4, 5],
        temperature_2m=[22, 22, 22],
        precipitation_probability=[0, 0, 0],
        precipitation=[0.0, 0.0, 0.0],
        weather_code=[0, 0, 0],
        wind_speed_10m=[4, 4, 4],
    )
    assert cg._briefing_text(data, OFFICE, "2026-08-19") is None


def test_an_empty_forecast_says_nothing_rather_than_all_clear():
    assert cg._briefing_text({}, OFFICE, "2026-08-19") is None
    assert cg._briefing_text({"hourly": {"time": []}}, OFFICE, "2026-08-19") is None


# ── the clock it prints ──────────────────────────────────────────────────────

def test_every_clock_carries_its_meridiem():
    """`08:00-11:00` appeared on a briefing its owner had set for the evening.

    24-hour digits are unambiguous only to a reader already thinking in them, and
    the redundancy is what would have shown him the mistake.
    """
    assert cg._hour_label(19) == "7 PM"
    assert cg._hour_label(8) == "8 AM"
    assert cg._hour_label(0) == "12 AM"
    assert cg._hour_label(12) == "12 PM"


def test_the_window_names_both_ends_with_their_meridiem():
    data = forecast(
        [22, 23, 0],
        temperature_2m=[24, 24, 23],
        precipitation_probability=[0, 0, 0],
        precipitation=[0.0, 0.0, 0.0],
        weather_code=[0, 0, 0],
        wind_speed_10m=[5, 5, 5],
    )
    late = dict(OFFICE, hour=22)
    _, body = cg._briefing_text(data, late, "2026-08-19")
    # 10 PM through 1 AM — the wrap has to survive, and both ends say which half
    assert "(10 PM–1 AM)" in body
