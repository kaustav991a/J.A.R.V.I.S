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

import io
import os
import shutil
import tempfile
from contextlib import redirect_stdout

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
    """The alert must not raise or hit the network when Telegram is unconfigured.

    `watchdog.LOG_PATH` is redirected to a temp file for the duration: this test
    used to append real "the server keeps crashing" lines to the live
    jarvis-backend/watchdog.log, which then reads as a genuine production
    incident when someone opens the log. Asserting on the temp file turns that
    leak into coverage of what the alert actually writes.
    """
    saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID")}
    tmpdir = tempfile.mkdtemp(prefix="jarvis_watchdog_test_")
    real_log = watchdog.LOG_PATH
    real_before = os.path.getmtime(real_log) if os.path.exists(real_log) else None
    watchdog.LOG_PATH = os.path.join(tmpdir, "watchdog.log")
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):       # log() also prints; keep it out of the report
            watchdog._notify_owner_down("test reason")   # must not raise, no network
        check(True, "give-up alert with no Telegram config does not raise")

        written = ""
        if os.path.exists(watchdog.LOG_PATH):
            with open(watchdog.LOG_PATH, encoding="utf-8") as f:
                written = f.read()
        check("stopped restarting it" in written, "give-up reason is logged")
        check("test reason" in written, "the caller's reason is included")
        check("owner alert not sent" in written,
              "logs that no alert went out, so silence isn't mistaken for delivery")
        check("stopped restarting it" in buf.getvalue(),
              "the alert also reaches stdout for a console-attached operator")

        real_after = os.path.getmtime(real_log) if os.path.exists(real_log) else None
        check(real_after == real_before,
              "the LIVE watchdog.log is untouched (no test lines in production logs)")
    except Exception as e:  # noqa: BLE001
        check(False, f"unconfigured alert raised: {e}")
    finally:
        watchdog.LOG_PATH = real_log
        shutil.rmtree(tmpdir, ignore_errors=True)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# ── F-03: a watchdog must not run blind about its own config ─────────────────
# On 2026-08-08 the watchdog was launched under an interpreter without
# python-dotenv. `except Exception: pass` swallowed it, so it ran with NO
# environment, then gave up on a crash loop and reported "No TELEGRAM_BOT_TOKEN
# / TELEGRAM_USER_ID — owner alert not sent" while .env contained both. The one
# signal that says the server is unrecoverable failed silently and blamed the
# wrong thing — the most misleading shape a failure can take.

import ast as _ast
import pathlib as _pathlib

_WD_SRC = (_pathlib.Path(__file__).resolve().parent / "watchdog.py").read_text(
    encoding="utf-8", errors="replace")


def test_a_missing_dotenv_is_not_silently_swallowed():
    tree = _ast.parse(_WD_SRC)
    bare_pass_on_dotenv = False
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Try):
            continue
        if "load_dotenv" not in _ast.dump(node):
            continue
        for handler in node.handlers:
            body = handler.body
            if len(body) == 1 and isinstance(body[0], _ast.Pass):
                bare_pass_on_dotenv = True
    check(not bare_pass_on_dotenv,
          "the dotenv import is no longer swallowed by a bare `except: pass`")
    check("DOTENV_ERROR" in _WD_SRC,
          "the failure is REMEMBERED, so later code can say which thing went wrong")


def test_the_boot_banner_says_config_was_not_loaded():
    check("CONFIG NOT LOADED" in _WD_SRC,
          "a config failure is announced loudly at boot, before anything depends on it")


def test_the_owner_alert_does_not_blame_a_token_it_never_looked_for():
    # The specific lie from 2026-08-08: reporting the credentials as missing
    # when the process never managed to read the file that holds them.
    check("The credentials may" in _WD_SRC and "could not read it" in _WD_SRC,
          "when .env was unreadable the alert says so, instead of calling the token missing")
    check("in a .env that WAS" in _WD_SRC,
          "and the genuinely-absent case is worded distinctly from the unreadable one")


TESTS = [test_first_rapid_cycle_backs_off_not_give_up, test_gives_up_after_max_cycles,
         test_healthy_run_resets_strikes, test_spaced_crashes_never_give_up,
         test_notify_owner_down_unconfigured_is_safe,
         test_a_missing_dotenv_is_not_silently_swallowed,
         test_the_boot_banner_says_config_was_not_loaded,
         test_the_owner_alert_does_not_blame_a_token_it_never_looked_for]


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
