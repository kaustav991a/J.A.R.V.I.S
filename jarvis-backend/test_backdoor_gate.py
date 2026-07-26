"""Harness for modules/backdoor_gate.py — the test bypass is off by default.

No server, no camera: the decision is pure, and env is injected. The claim under
test is the one that matters live — typing "wake up" into the HUD command line
while JARVIS is locked must NOT reach the dispatcher unless someone consciously
switched the bypass on.

The last test greps main.py, because a correct decision that is wired in *after*
the command already ran would still be a bypass.
"""

import pathlib
import re

from modules import backdoor_gate as bg


# ── the flag itself ─────────────────────────────────────────────────────────

def test_flag_defaults_off_when_unset():
    assert bg.flag_enabled({}) is False


def test_flag_off_for_falsey_spellings():
    for raw in ("0", "", "  ", "false", "no", "off", "2", "yes please"):
        assert bg.flag_enabled({bg.ENV_FLAG: raw}) is False, f"{raw!r} enabled the bypass"


def test_flag_on_for_the_documented_spellings():
    for raw in ("1", "true", "TRUE", "  yes ", "On"):
        assert bg.flag_enabled({bg.ENV_FLAG: raw}) is True, f"{raw!r} did not enable the bypass"


def test_flag_reads_real_environ_when_no_mapping_given(monkeypatch=None):
    """Default source is os.environ — read per call, so a restart-less change is
    honestly reflected."""
    import os

    prev = os.environ.get(bg.ENV_FLAG)
    try:
        os.environ[bg.ENV_FLAG] = "1"
        assert bg.flag_enabled() is True
        os.environ[bg.ENV_FLAG] = "0"
        assert bg.flag_enabled() is False
        del os.environ[bg.ENV_FLAG]
        assert bg.flag_enabled() is False
    finally:
        if prev is None:
            os.environ.pop(bg.ENV_FLAG, None)
        else:
            os.environ[bg.ENV_FLAG] = prev


# ── direction 1: flag OFF ───────────────────────────────────────────────────

def test_locked_and_flag_off_is_refused():
    d = bg.decide("wake up", enabled=False, system_online=False)
    assert d.allowed is False
    assert d.reason == bg.REASON_LOCKED
    assert d.status == 403
    assert d.message, "a refusal must say why"


def test_test_hooks_get_no_special_pass_when_locked():
    """`test:` prefixed hooks reach the same dispatcher, so they are gated too —
    an allowlist here would just be a softer bypass."""
    for cmd in ("test:morning_briefing", "test:deep_work_ui", "test:enqueue_task: weather",
                "", "   ", "SLEEP"):
        d = bg.decide(cmd, enabled=False, system_online=False)
        assert d.allowed is False, f"{cmd!r} slipped through the locked gate"


def test_authenticated_session_may_use_the_command_line_with_flag_off():
    """Flag off does not kill the HUD terminal — it demotes it to a text input on
    an ALREADY authenticated session, which is what every other path gets."""
    d = bg.decide("what's the weather", enabled=False, system_online=True)
    assert d.allowed is True
    assert d.reason == bg.REASON_AUTHENTICATED


# ── direction 2: flag ON ────────────────────────────────────────────────────

def test_flag_on_restores_the_bypass_while_locked():
    d = bg.decide("wake up", enabled=True, system_online=False)
    assert d.allowed is True
    assert d.reason == bg.REASON_FLAGGED
    assert d.status == 200


def test_flag_on_also_allows_an_authenticated_session():
    d = bg.decide("wake up", enabled=True, system_online=True)
    assert d.allowed is True


def test_the_full_truth_table():
    expected = {
        (True, True): (True, bg.REASON_FLAGGED),
        (True, False): (True, bg.REASON_FLAGGED),
        (False, True): (True, bg.REASON_AUTHENTICATED),
        (False, False): (False, bg.REASON_LOCKED),
    }
    for (enabled, online), (allowed, reason) in expected.items():
        d = bg.decide("wake up", enabled=enabled, system_online=online)
        assert (d.allowed, d.reason) == (allowed, reason), \
            f"enabled={enabled} online={online} -> {d}"


# ── the refusal payload ─────────────────────────────────────────────────────

def test_refusal_payload_names_the_flag_and_is_not_success():
    p = bg.decide("wake up", enabled=False, system_online=False).as_payload()
    assert p["status"] == "refused"          # never the endpoint's "success"
    assert p["reason"] == bg.REASON_LOCKED
    assert p["flag"] == "JARVIS_ALLOW_BACKDOOR"
    assert p["message"]


def test_decision_is_immutable():
    d = bg.decide("wake up", enabled=False, system_online=False)
    try:
        d.allowed = True
    except Exception:
        return
    raise AssertionError("a Decision must not be mutable after the fact")


def test_decide_does_not_consult_the_environment():
    """`enabled` is passed IN. If decide() read os.environ itself, a test-time
    export would silently change production behaviour."""
    import inspect

    src = inspect.getsource(bg.decide)
    assert "environ" not in src and "getenv" not in src


# ── the wiring (a correct decision applied too late is still a bypass) ──────

def test_main_gates_the_endpoint_before_dispatch():
    src = pathlib.Path(__file__).with_name("main.py").read_text(encoding="utf-8")
    assert "from modules import backdoor_gate" in src, "gate not imported"

    body = src.split('@app.post("/api/backdoor")', 1)
    assert len(body) == 2, "/api/backdoor endpoint not found"
    head = body[1][:1400]

    assert "backdoor_gate.decide(" in head, "gate not called at the top of the endpoint"
    assert re.search(r"system_online\s*=\s*SYSTEM_ONLINE", head), \
        "gate must be fed the real auth state, not a literal"
    assert "JSONResponse" in head, "a refusal must be a real HTTP failure, not a 200 body"

    # The refusal must come BEFORE anything that mutates state or dispatches.
    gate_at = head.index("backdoor_gate.decide(")
    for later in ("_last_command_time = ", "classify_intent", "process_command"):
        pos = head.find(later)
        if pos != -1:
            assert pos > gate_at, f"{later!r} runs before the gate"


def test_gate_does_not_touch_tiers_or_governance():
    """This flag gates AUTHENTICATION only. Risk tiers stay exactly as they were."""
    src = pathlib.Path(__file__).parent.joinpath("modules", "backdoor_gate.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    body = code.split('"""')[-1]          # drop the module docstring
    for forbidden in ("tier_allows", "ADMIN_TIER", "governance_manager"):
        assert forbidden not in body, f"gate must not reference {forbidden}"


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
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
