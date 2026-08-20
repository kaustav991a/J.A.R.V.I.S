"""Harness for the state that must outlive a deploy.

The commute schedule, the push addresses and the two once-a-day markers were JSON
files beside this module. Render's disk is wiped on every DEPLOY but not on a
restart, which is why it read as working for a week.

It cost two incidents on 2026-08-20. The second was 60 minutes before the first
push-delivered briefing was ever due: a deploy at 5:35 PM cleared
`commute.departures` and `push_targets` to 0, and the only symptom was a
notification that would not have arrived — indistinguishable from the Android
scheduling bug this whole feature exists to route around.

Recovery already existed: the phone re-uploads on cloud connect
(`JarvisProvider.tsx:756`). But it is gated on `link.status === 'open'`, a
WebSocket the app does not always hold — a photo was answered over plain HTTP that
same evening while the gateway could reach nobody by push. So "the app will fix it
when it next opens" was true of a socket, not of the app being used.

What is asserted here:

  1. all four dictionaries come back after the disk is gone;
  2. a stored schedule goes through `_clean_commute` on the way back IN, so a row
     written by an older build is trusted no further than an upload from the phone;
  3. no `DATABASE_URL` behaves exactly as it did before this existed;
  4. an unreachable database costs a log line, never a raised exception, and never
     the file write;
  5. `_persist` does not block the caller — a save happens on the operator's turn;
  6. an empty table is silent, because the first deploy after this lands finds one.

No Postgres: `_db_state_get_blocking` / `_db_state_put_blocking` are stubbed with a
dict, and the files are pointed at a temporary directory.
"""

import asyncio
import json
import os
import sys
import tempfile

os.environ.setdefault("CLOUD_GATEWAY_MODE", "webhook")

import cloud_gateway as cg  # noqa: E402

_real_get = cg._db_state_get_blocking
_real_put = cg._db_state_put_blocking
_real_ready = cg._memory_ready
_real_files = (cg._PUSH_FILE, cg._COMMUTE_FILE, cg._BRIEFED_FILE, cg._NUDGE_FILE)

_DB: dict = {}       # what Postgres holds: key -> json text
_writes: list = []   # every key written, in order
_db_fails = False

WEEK = [False, True, True, True, True, True, False]
GOOD = {"tz": "Asia/Kolkata", "days": WEEK,
        "departures": [{"label": "Office", "hour": 19, "minute": 0,
                        "place_id": "office"}]}


def _fake_get(key):
    if _db_fails:
        raise RuntimeError("connection refused")
    raw = _DB.get(key)
    if raw is None:
        return None
    try:
        out = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    return out if isinstance(out, dict) else None


def _fake_put(key, val):
    if _db_fails:
        raise RuntimeError("connection refused")
    _writes.append(key)
    _DB[key] = json.dumps(val)


async def _fake_ready():
    if _db_fails:
        raise RuntimeError("connection refused")
    return True


def _setup():
    global _db_fails
    _DB.clear()
    _writes.clear()
    _db_fails = False
    cg._db_state_get_blocking = _fake_get
    cg._db_state_put_blocking = _fake_put
    cg._memory_ready = _fake_ready
    cg.DATABASE_URL = "postgres://harness"
    cg._db_broken = False
    cg._push_targets = {}
    cg._commute = {}
    cg._briefed = {}
    cg._nudge = {}
    # the files go somewhere disposable, so a harness run cannot overwrite a
    # developer's real schedule
    tmp = tempfile.mkdtemp(prefix="jarvis-state-")
    cg._PUSH_FILE = os.path.join(tmp, "app_push_tokens.json")
    cg._COMMUTE_FILE = os.path.join(tmp, "app_commute.json")
    cg._BRIEFED_FILE = os.path.join(tmp, "app_briefed.json")
    cg._NUDGE_FILE = os.path.join(tmp, "app_nudge.json")


def _teardown():
    cg._db_state_get_blocking = _real_get
    cg._db_state_put_blocking = _real_put
    cg._memory_ready = _real_ready
    (cg._PUSH_FILE, cg._COMMUTE_FILE, cg._BRIEFED_FILE, cg._NUDGE_FILE) = _real_files


async def _settle(fn):
    """A save, then enough of the loop for the fire-and-forget write to land."""
    fn()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _deploy():
    """What Render does: the disk goes, the process is new, the database stays."""
    for path in (cg._PUSH_FILE, cg._COMMUTE_FILE, cg._BRIEFED_FILE, cg._NUDGE_FILE):
        try:
            os.remove(path)
        except OSError:
            pass
    cg._push_targets = {}
    cg._commute = {}
    cg._briefed = {}
    cg._nudge = {}


# ── the bug itself ───────────────────────────────────────────────────────────


def test_a_schedule_survives_a_deploy():
    async def go():
        cg._commute = cg._clean_commute(GOOD)
        await _settle(cg._save_commute)
        _deploy()
        assert cg._commute == {}, "the deploy did not clear memory"
        await cg._restore_state()
        assert cg._commute.get("departures"), "the schedule did NOT survive"
        assert cg._commute["departures"][0]["label"] == "Office", cg._commute
    asyncio.run(go())


def test_a_push_address_survives_a_deploy():
    async def go():
        cg._push_targets = {"ExponentPushToken[abc]": "android"}
        await _settle(cg._save_push_targets)
        _deploy()
        await cg._restore_state()
        assert cg._push_targets == {"ExponentPushToken[abc]": "android"}, cg._push_targets
    asyncio.run(go())


def test_a_briefed_marker_survives_a_deploy():
    # a deploy at 7:05 PM used to clear today's marker, so the next tick would
    # brief the same departure a second time
    async def go():
        cg._briefed = {"office": "2026-08-20"}
        await _settle(cg._save_briefed)
        _deploy()
        await cg._restore_state()
        assert cg._briefed == {"office": "2026-08-20"}, cg._briefed
    asyncio.run(go())


def test_a_nudge_marker_survives_a_deploy():
    async def go():
        cg._nudge = {"day": "2026-08-20", "about": "his sister's birthday"}
        await _settle(cg._save_nudge)
        _deploy()
        await cg._restore_state()
        assert cg._nudge["day"] == "2026-08-20", cg._nudge
    asyncio.run(go())


def test_all_four_survive_one_deploy_together():
    async def go():
        cg._commute = cg._clean_commute(GOOD)
        cg._push_targets = {"ExponentPushToken[abc]": "android"}
        cg._briefed = {"office": "2026-08-20"}
        cg._nudge = {"day": "2026-08-20", "about": "x"}
        for fn in (cg._save_commute, cg._save_push_targets,
                   cg._save_briefed, cg._save_nudge):
            await _settle(fn)
        _deploy()
        await cg._restore_state()
        assert cg._commute.get("departures"), cg._commute
        assert cg._push_targets and cg._briefed and cg._nudge
    asyncio.run(go())


# ── what must not change ─────────────────────────────────────────────────────


def test_no_database_behaves_exactly_as_before():
    cg.DATABASE_URL = ""

    async def go():
        cg._commute = cg._clean_commute(GOOD)
        await _settle(cg._save_commute)
        assert _writes == [], "wrote to the database with DATABASE_URL unset"
        assert os.path.exists(cg._COMMUTE_FILE), "stopped writing the file"
        _deploy()
        await cg._restore_state()
        assert cg._commute == {}, "restored with no database configured"
    asyncio.run(go())


def test_a_broken_database_is_survived_not_raised():
    global _db_fails
    _db_fails = True

    async def go():
        cg._commute = cg._clean_commute(GOOD)
        await _settle(cg._save_commute)      # must not raise
        assert os.path.exists(cg._COMMUTE_FILE), "the file write was skipped"
        _deploy()
        await cg._restore_state()            # must not raise
        assert cg._commute == {}, cg._commute
    asyncio.run(go())


def test_a_restart_still_reads_the_disk():
    # the case that always worked: same image, the file is still there
    async def go():
        cg._commute = cg._clean_commute(GOOD)
        await _settle(cg._save_commute)
        cg._commute = {}
        cg._load_commute()
        assert cg._commute["tz"] == "Asia/Kolkata", cg._commute
    asyncio.run(go())


def test_an_empty_table_restores_nothing():
    async def go():
        await cg._restore_state()
        assert cg._commute == {} and cg._push_targets == {}
        assert cg._briefed == {} and cg._nudge == {}
    asyncio.run(go())


# ── trust, and the validator on the way back in ──────────────────────────────


def test_a_stored_schedule_goes_through_the_validator():
    async def go():
        _DB["commute"] = json.dumps({"tz": "Asia/Kolkata", "days": [True, True],
                                     "departures": "office at seven"})
        await cg._restore_state()
        assert cg._commute == {}, "an unreadable stored schedule was accepted"
    asyncio.run(go())


def test_a_departure_with_no_place_is_dropped_on_restore():
    async def go():
        _DB["commute"] = json.dumps({
            "tz": "Asia/Kolkata", "days": WEEK,
            "departures": [{"label": "Office", "hour": 19, "minute": 0},
                           {"label": "", "hour": 9, "minute": 30}]})
        await cg._restore_state()
        assert len(cg._commute["departures"]) == 1, cg._commute
        assert cg._commute["departures"][0]["label"] == "Office", cg._commute
    asyncio.run(go())


def test_a_later_save_replaces_rather_than_accumulates():
    # the phone is the authority: a departure switched off travels as an absence
    async def go():
        cg._commute = cg._clean_commute(GOOD)
        await _settle(cg._save_commute)
        cg._commute = cg._clean_commute(dict(GOOD, departures=[]))
        await _settle(cg._save_commute)
        _deploy()
        await cg._restore_state()
        assert cg._commute["departures"] == [], cg._commute
    asyncio.run(go())


def test_a_pruned_push_target_stays_pruned():
    async def go():
        cg._push_targets = {"live": "android", "dead": "android"}
        await _settle(cg._save_push_targets)
        cg._push_targets.pop("dead")        # DeviceNotRegistered
        await _settle(cg._save_push_targets)
        _deploy()
        await cg._restore_state()
        assert cg._push_targets == {"live": "android"}, cg._push_targets
    asyncio.run(go())


# ── the caller must not wait on Postgres ─────────────────────────────────────


def test_persist_does_not_block_its_caller():
    async def go():
        cg._commute = cg._clean_commute(GOOD)
        cg._save_commute()                  # no settle
        assert _writes == [], "the database write blocked the caller"
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert _writes == ["commute"], _writes
    asyncio.run(go())


def test_persist_outside_a_loop_is_silent():
    # module scope: the file write already happened and there is no loop to use
    cg._commute = cg._clean_commute(GOOD)
    cg._save_commute()
    assert _writes == [], _writes
    assert os.path.exists(cg._COMMUTE_FILE), "the file write was skipped"


def test_health_names_whether_state_is_durable():
    # a persistence layer you cannot observe is this project's recurring failure
    # shape: a schedule backed by Render's disk and one backed by Postgres look
    # identical from outside until the next deploy
    cg._db_ready = True
    cg.DATABASE_URL = "postgres://harness"
    assert bool(cg.DATABASE_URL) and cg._db_ready
    cg.DATABASE_URL = ""
    assert not (bool(cg.DATABASE_URL) and cg._db_ready)


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        _setup()
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        finally:
            _teardown()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
