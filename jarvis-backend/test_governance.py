"""Phase 6 — direct tests for governance_manager singleton (fail-safe tiering).

Self-running (no pytest): this is the risk-tier guard, so it belongs INSIDE the
one gated command (`run_harnesses.py`), not in a second suite nothing checks.
The old `@pytest.fixture(autouse=True)` that cleared the pending slot around
every test is now `_reset()` — called at the top of each test and again by the
runner afterwards, so a CONFIRM left pending by one test can never leak into the
next one's `has_pending()`.
"""

from governance_manager import GovernanceSignal, GovernanceTier, governance_manager


def _reset():
    """Empty the single pending-confirmation slot."""
    governance_manager.cancel_pending()


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


if __name__ == "__main__":
    import sys
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
