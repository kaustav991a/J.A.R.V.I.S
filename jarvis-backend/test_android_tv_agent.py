"""Phase 6 — AndroidTVAgent tests (mocked subprocess + mDNS).

Converted to a self-running harness 2026-07-30 (D#13): pytest is not installed
in the venv, so as a pytest file this was dead weight — it had not run in the
suite for its whole life. The fixtures became plain helpers and `monkeypatch`
became an explicit save/restore context manager; no assertion changed.
"""

import os
import sys
from contextlib import contextmanager
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from modules.android_tv_agent import AndroidTVAgent, discover_tv_via_mdns


def _agent_connected() -> AndroidTVAgent:
    tv = AndroidTVAgent(ip_port="192.168.0.10:5555")
    tv._connected = True
    return tv


@contextmanager
def _env(**pairs):
    """monkeypatch.setenv, minus pytest. Restores exactly what was there."""
    previous = {k: os.environ.get(k) for k in pairs}
    os.environ.update({k: str(v) for k, v in pairs.items()})
    try:
        yield
    finally:
        for key, was in previous.items():
            if was is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = was


def test_tv_volume_parses_pipe_steps() -> None:
    agent_connected = _agent_connected()
    mock_run = MagicMock(return_value=(True, ""))
    agent_connected._run = mock_run

    out = agent_connected.tv_volume("up", steps=5)
    assert "up by 5" in out.lower()
    assert mock_run.call_count == 5
    for call in mock_run.call_args_list:
        argv = call[0][0]
        assert argv[:4] == ["shell", "input", "keyevent", "24"]


def test_tv_volume_action_engine_pipe_contract() -> None:
    """Mirrors action_engine._tv_volume parsing for targets like 'up|5'."""
    agent_connected = _agent_connected()
    target = "up|5"
    direction = target.strip()
    steps = 1
    if "|" in direction:
        direction, _, n_str = direction.partition("|")
        direction = direction.strip()
        try:
            steps = max(1, min(int(n_str.strip()), 20))
        except ValueError:
            pass

    mock_run = MagicMock(return_value=(True, ""))
    agent_connected._run = mock_run
    out = agent_connected.tv_volume(direction, steps=steps)
    assert mock_run.call_count == 5
    assert "up by 5" in out.lower()


def test_tv_launch_app_youtube_package() -> None:
    agent_connected = _agent_connected()
    mock_run = MagicMock(return_value=(True, "Events injected: 1"))
    agent_connected._run = mock_run

    out = agent_connected.tv_launch_app("youtube")
    assert "Launching" in out
    monkey_argv = None
    for call in mock_run.call_args_list:
        argv = call[0][0]
        if "monkey" in argv:
            monkey_argv = argv
            break
    assert monkey_argv is not None
    pkg_idx = monkey_argv.index("-p") + 1
    assert monkey_argv[pkg_idx] == "com.google.android.youtube.tv"


def test_connect_falls_back_to_env_ip_when_discovery_empty() -> None:
    with _env(JARVIS_TV_IP="192.168.99.1:40123"):
        def fake_run(cmd, timeout=None, **kwargs):
            assert cmd[0] == "adb"
            assert cmd[1] == "connect"
            assert cmd[2] == "192.168.99.1:40123"
            return CompletedProcess(cmd, 0, stdout="connected to 192.168.99.1:40123")

        with patch("modules.android_tv_agent.discover_tv_via_mdns", return_value=None):
            with patch("modules.android_tv_agent.subprocess.run", side_effect=fake_run):
                tv = AndroidTVAgent()
                msg = tv.connect()

    assert "192.168.99.1:40123" in msg
    assert tv._target == "192.168.99.1:40123"
    assert tv.discovered_host is None
    assert tv.discovered_port is None


def test_connect_prefers_mdns_over_env() -> None:
    with _env(JARVIS_TV_IP="10.10.10.10:5555"):
        discovered = ("172.16.0.5", 8765, "2KTV-3MH._adb-tls-connect._tcp.local.")

        def fake_run(cmd, timeout=None, **kwargs):
            assert cmd[0] == "adb"
            assert cmd[1] == "connect"
            assert cmd[2] == "172.16.0.5:8765"
            return CompletedProcess(cmd, 0, stdout="connected")

        with patch("modules.android_tv_agent.discover_tv_via_mdns", return_value=discovered):
            with patch("modules.android_tv_agent.subprocess.run", side_effect=fake_run):
                tv = AndroidTVAgent()
                tv.connect()

    assert tv.discovered_host == "172.16.0.5"
    assert tv.discovered_port == 8765
    assert tv._target == "172.16.0.5:8765"


def test_discover_prefers_jarvis_tv_name_match() -> None:
    """Exercise zeroconf callback logic with patched browser + sleep."""
    from zeroconf import ServiceStateChange

    with _env(JARVIS_TV_NAME="2KTV-3MH"):
        fake_info_other = MagicMock()
        fake_info_other.addresses = [bytes([192, 168, 1, 2])]
        fake_info_other.port = 1111

        fake_info_match = MagicMock()
        fake_info_match.addresses = [bytes([10, 0, 0, 7])]
        fake_info_match.port = 2222

        zc_inst = MagicMock()

        def fake_get_info(_stype, name):
            if "2KTV-3MH" in name:
                return fake_info_match
            return fake_info_other

        zc_inst.get_service_info.side_effect = fake_get_info

        def browser_side_effect(zc, stype, handlers=None):
            if handlers:
                handlers[0](zc, stype, "generic._adb-tls-connect._tcp.local.",
                            ServiceStateChange.Added)
                handlers[0](zc, stype, "2KTV-3MH._adb-tls-connect._tcp.local.",
                            ServiceStateChange.Added)
            return MagicMock()

        with patch("modules.android_tv_agent.time.sleep", lambda _t: None):
            with patch("zeroconf.Zeroconf", return_value=zc_inst):
                with patch("zeroconf.ServiceBrowser", side_effect=browser_side_effect):
                    row = discover_tv_via_mdns(timeout_s=0.01)

    assert row is not None
    ip, port, name = row
    assert ip == "10.0.0.7"
    assert port == 2222
    assert "2KTV-3MH" in name


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
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
