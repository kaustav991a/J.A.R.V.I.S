"""Phase 6 — direct tests for governance_manager singleton (fail-safe tiering)."""

import pytest

from governance_manager import GovernanceSignal, GovernanceTier, governance_manager


@pytest.fixture(autouse=True)
def _clear_governance_pending():
    governance_manager.cancel_pending()
    yield
    governance_manager.cancel_pending()


def test_workspace_read_returns_pass():
    r = governance_manager.check({"action_type": "workspace_read", "target": "."})
    assert r["signal"] == GovernanceSignal.PASS.value
    assert r["tier"] == GovernanceTier.AUTO.value


def test_run_terminal_command_returns_blocked():
    r = governance_manager.check(
        {"action_type": "run_terminal_command", "target": "list_directory: ."}
    )
    assert r["signal"] == GovernanceSignal.BLOCKED.value
    assert r["tier"] == GovernanceTier.BLOCK.value


def test_workspace_write_pending_then_consume_clears_slot():
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
    r = governance_manager.check({"action_type": "launch_missiles", "target": "alpha"})
    assert r["signal"] == GovernanceSignal.BLOCKED.value
    assert r["tier"] == GovernanceTier.BLOCK.value
