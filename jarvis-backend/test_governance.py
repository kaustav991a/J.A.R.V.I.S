"""Phase 6 — direct tests for governance_manager singleton (fail-safe tiering).

Self-running (no pytest): this is the risk-tier guard, so it belongs INSIDE the
one gated command (`run_harnesses.py`), not in a second suite nothing checks.
The old `@pytest.fixture(autouse=True)` that cleared the pending slot around
every test is now `_reset()` — called at the top of each test and again by the
runner afterwards, so a CONFIRM left pending by one test can never leak into the
next one's `has_pending()`.
"""

import contextlib
import io
import sys

from governance_manager import GovernanceSignal, GovernanceTier, governance_manager


def _reset():
    """Empty the single pending-confirmation slot."""
    governance_manager.cancel_pending()


def _strict_cp1252_stdout():
    """A stdout that behaves exactly like a redirected Windows console.

    `errors="strict"` is the whole point: this is what Python hands a process
    whose stdout is a pipe, a file, or a Windows service on a cp1252 locale.
    Any character outside cp1252 raises UnicodeEncodeError on write instead of
    being silently replaced.
    """
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="")


def test_workspace_read_returns_pass():
    _reset()
    r = governance_manager.check({"action_type": "workspace_read", "target": "."})
    assert r["signal"] == GovernanceSignal.PASS.value
    assert r["tier"] == GovernanceTier.AUTO.value


def test_run_terminal_command_returns_blocked():
    _reset()
    r = governance_manager.check(
        {"action_type": "run_terminal_command", "target": "list_directory: ."}
    )
    assert r["signal"] == GovernanceSignal.BLOCKED.value
    assert r["tier"] == GovernanceTier.BLOCK.value


def test_workspace_write_pending_then_consume_clears_slot():
    _reset()
    payload = {"action_type": "workspace_write", "target": "notes.txt"}
    r = governance_manager.check(payload)
    assert r["signal"] == GovernanceSignal.PENDING_CONFIRMATION.value
    assert r["tier"] == GovernanceTier.CONFIRM.value
    assert r.get("confirmation_id")
    assert governance_manager.has_pending() is True

    consumed = governance_manager.consume_pending()
    assert consumed == payload
    assert governance_manager.has_pending() is False
    assert governance_manager.consume_pending() is None


def test_unknown_action_launch_missiles_blocked():
    _reset()
    r = governance_manager.check({"action_type": "launch_missiles", "target": "alpha"})
    assert r["signal"] == GovernanceSignal.BLOCKED.value
    assert r["tier"] == GovernanceTier.BLOCK.value


def test_check_survives_a_strict_cp1252_stdout():
    """The safety spine must not be killable by its own log line.

    This is the third time a non-ASCII print has aborted a live operation, so it
    gets a guard. Every tier is exercised, because each one has its own print:
    AUTO/BLOCK log inline, CONFIRM logs the pending id. Before the fix these used
    a '->' arrow, and this test failed with UnicodeEncodeError.
    """
    _reset()
    stream = _strict_cp1252_stdout()

    # Control: prove the stream really is armed. If a future edit makes this
    # lenient, the test below would pass vacuously and guard nothing.
    try:
        stream.write("→")
        stream.flush()
    except UnicodeEncodeError:
        pass
    else:
        raise AssertionError("cp1252 test stream is not strict - the guard below proves nothing")

    stream = _strict_cp1252_stdout()
    with contextlib.redirect_stdout(stream):
        governance_manager.check({"action_type": "workspace_read", "target": "."})          # AUTO
        governance_manager.check({"action_type": "launch_missiles", "target": "alpha"})     # BLOCK
        governance_manager.check({"action_type": "workspace_write", "target": "n.txt"})     # CONFIRM
        governance_manager.check({})                                                       # no action_type
        cid = governance_manager.consume_pending()
        governance_manager.check({"action_type": "workspace_write", "target": "n.txt"})
        governance_manager.cancel_pending()
        stream.flush()

    assert cid is not None, "CONFIRM tier did not pend - the tiers under test were not real"
    # And the ruleset loader, which prints on its own path.
    stream = _strict_cp1252_stdout()
    with contextlib.redirect_stdout(stream):
        governance_manager.reload_ruleset()
        stream.flush()


def test_run_harnesses_forces_utf8_on_children():
    """Each harness is a separate process that picks its own stdout encoding.

    The parent's `reconfigure` does not reach it, so `run_harnesses.py` must keep
    passing PYTHONIOENCODING=utf-8 down. Dropping either the variable or the
    `env=` argument silently re-opens the crash it was added to close.
    """
    _reset()
    import inspect

    import run_harnesses

    assert run_harnesses._CHILD_ENV.get("PYTHONIOENCODING") == "utf-8"

    src = inspect.getsource(run_harnesses.main)
    assert "env=_CHILD_ENV" in src, "run_harnesses.main() no longer passes _CHILD_ENV to the child"


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
        finally:
            _reset()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
