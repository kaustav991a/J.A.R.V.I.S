r"""
test_watchdog_policy.py — G5.7 watchdog give-up logic (no processes)

Run: venv\Scripts\python.exe test_watchdog_policy.py

The watchdog used to respawn a permanently-broken server forever (backing off
30s each rapid-crash cycle, but never stopping). RespawnPolicy adds a give-up:
after MAX_GIVEUP_CYCLES rapid-crash cycles with no healthy run in between it
signals give_up so the loop stops and alerts the owner. A run that survived the
rapid window resets the strike count (transient crash, not a startup fault).
Pure decision logic — no subprocess, no server.
"""

import os

import watchdog
from watchdog import RespawnPolicy

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def test_first_rapid_cycle_backs_off_not_give_up():
    p = RespawnPolicy(max_rapid=3, rapid_window=60, max_giveup=3)
    now = 0.0
    r = None
    for _ in range(3):                 # 3 rapid crashes = 1 backoff cycle
        now += 1.0
        r = p.record_death(uptime=1.0, now=now)
    check(r["rapid_backoff"] and not r["give_up"], "first rapid cycle backs off, no give-up")
    check(r["strikes"] == 1, "first cycle records strike 1")


def test_gives_up_after_max_cycles():
    p = RespawnPolicy(max_rapid=3, rapid_window=60, max_giveup=2)
    now = 0.0
    results = []
    for _ in range(6):                 # two full rapid cycles of 3 crashes
        now += 1.0
        results.append(p.record_death(uptime=1.0, now=now))
    give_ups = [i for i, r in enumerate(results) if r["give_up"]]
    check(give_ups == [5], f"give_up fires on the 6th crash (2nd cycle), got {give_ups}")
    check(results[2]["rapid_backoff"] and not results[2]["give_up"],
          "the 1st cycle (crash #3) backs off without giving up")


def test_healthy_run_resets_strikes():
    p = RespawnPolicy(max_rapid=2, rapid_window=60, max_giveup=3)
    now = 0.0
    now += 1.0; p.record_death(uptime=1.0, now=now)
    now += 1.0; r = p.record_death(uptime=1.0, now=now)     # strike 1
    check(r["strikes"] == 1 and r["rapid_backoff"], "one rapid cycle -> strike 1")
    now += 200.0
    r2 = p.record_death(uptime=61.0, now=now)               # healthy run (>= window)
    check(p.giveup_strikes == 0, "a healthy run resets the give-up strike count")
    check(not r2["give_up"], "a healthy-then-crash never gives up")


def test_spaced_crashes_never_give_up():
    p = RespawnPolicy(max_rapid=3, rapid_window=10, max_giveup=2)
    now = 0.0
    ok = True
    for _ in range(20):
        now += 20.0                     # 20s apart, window 10s -> never rapid
        r = p.record_death(uptime=5.0, now=now)
        ok = ok and not r["give_up"] and not r["rapid_backoff"]
    check(ok, "crashes spaced beyond the window never flag rapid/give-up")


def test_notify_owner_down_unconfigured_is_safe():
    saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID")}
    try:
        watchdog._notify_owner_down("test reason")   # must not raise, no network
        check(True, "give-up alert with no Telegram config does not raise")
    except Exception as e:  # noqa: BLE001
        check(False, f"unconfigured alert raised: {e}")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


TESTS = [test_first_rapid_cycle_backs_off_not_give_up, test_gives_up_after_max_cycles,
         test_healthy_run_resets_strikes, test_spaced_crashes_never_give_up,
         test_notify_owner_down_unconfigured_is_safe]


def main():
    print("=" * 60)
    print("watchdog RespawnPolicy harness")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
