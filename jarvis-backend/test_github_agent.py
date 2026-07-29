"""Phase 6 — GitHubAgent tests with mocked subprocess.run (shell=False).

Converted to a self-running harness 2026-07-30 (D#13): pytest is not installed
in the venv, so as a pytest file this never ran. The `tmp_path` fixture became
a tempfile the harness cleans up itself; no assertion changed.
"""

import shutil
import sys
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from modules.github_agent import GitHubAgent

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="jarvis_gh_"))
_counter = [0]


def _repo() -> Path:
    """A throwaway directory that looks like a git repo (tmp_path + .git)."""
    _counter[0] += 1
    path = _TMP_ROOT / f"repo{_counter[0]}"
    (path / ".git").mkdir(parents=True)
    return path


def _agent() -> GitHubAgent:
    return GitHubAgent()


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_get_status_parses_branch() -> None:
    agent, repo = _agent(), _repo()
    status_out = (
        "On branch feature-ai\n"
        "Your branch is up to date with 'origin/feature-ai'.\n\n"
        "nothing to commit, working tree clean\n"
    )

    def fake_run(cmd: list, **kwargs):
        assert kwargs.get("shell") is False
        assert cmd[:2] == ["git", "status"]
        return _completed(0, stdout=status_out)

    with patch("modules.github_agent.subprocess.run", side_effect=fake_run):
        out = agent.get_status(repo_path=str(repo))

    assert "Branch: feature-ai" in out
    assert "feature-ai" in out


def test_commit_clean_working_tree() -> None:
    agent, repo = _agent(), _repo()
    calls: list = []

    def fake_run(cmd: list, **kwargs):
        calls.append(cmd)
        assert kwargs.get("shell") is False
        if cmd[:3] == ["git", "add", "."]:
            return _completed(0)
        if cmd[:2] == ["git", "commit"]:
            return _completed(1, stderr="nothing to commit, working tree clean")
        raise AssertionError(f"unexpected cmd: {cmd}")

    with patch("modules.github_agent.subprocess.run", side_effect=fake_run):
        msg = agent.commit("noop message", repo_path=str(repo))

    assert len(calls) == 2
    assert "clean" in msg.lower()


def test_commit_with_staged_changes() -> None:
    agent, repo = _agent(), _repo()
    calls: list = []

    def fake_run(cmd: list, **kwargs):
        calls.append(cmd)
        assert kwargs.get("shell") is False
        if cmd[:3] == ["git", "add", "."]:
            return _completed(0)
        if cmd[:2] == ["git", "commit"]:
            return _completed(0, stdout="[main abcd123] Fix typo\n")
        raise AssertionError(f"unexpected cmd: {cmd}")

    with patch("modules.github_agent.subprocess.run", side_effect=fake_run):
        msg = agent.commit("Fix typo", repo_path=str(repo))

    assert "Committed successfully" in msg
    assert "abcd123" in msg
    assert len(calls) == 2


def test_push_graceful_no_upstream() -> None:
    agent, repo = _agent(), _repo()
    calls: list = []

    def fake_run(cmd: list, **kwargs):
        calls.append(cmd)
        assert kwargs.get("shell") is False
        if cmd[:2] == ["git", "push"]:
            return _completed(
                1,
                stderr="fatal: The current branch my-branch has no upstream branch.",
            )
        if cmd[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _completed(0, stdout="my-branch\n")
        raise AssertionError(f"unexpected cmd: {cmd}")

    with patch("modules.github_agent.subprocess.run", side_effect=fake_run):
        msg = agent.push(repo_path=str(repo))

    assert "no upstream" in msg.lower()
    assert "my-branch" in msg
    assert "set-upstream" in msg.lower()
    assert len(calls) == 2


def test_walk_up_finds_git_root_from_nested_dir() -> None:
    agent, repo = _agent(), _repo()
    nested = repo / "src" / "pkg"
    nested.mkdir(parents=True)
    assert agent._walk_up_for_git(nested) == repo.resolve()


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    try:
        for name, fn in tests:
            try:
                fn()
                print(f"PASS  {name}")
            except Exception:
                failed += 1
                print(f"FAIL  {name}")
                traceback.print_exc()
        print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    finally:
        shutil.rmtree(_TMP_ROOT, ignore_errors=True)
    sys.exit(1 if failed else 0)
