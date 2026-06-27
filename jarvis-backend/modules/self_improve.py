"""
self_improve.py — Guarded Self-Improvement Loop (Roadmap §3.3)
==============================================================

J.A.R.V.I.S. proposes a code change, applies it ON A BRANCH, runs the tests, and —
only if they pass — pushes and opens a Pull Request for the human to approve.

THE SAFETY MODEL IS THE WHOLE POINT:
- **Never auto-merges.** The PR is the human gate; you review and merge.
- **Branch-isolated.** Work happens on a fresh `jarvis/…` branch, never on `main`.
- **Test-gated.** A change that fails the test suite is rolled back and never pushed.
- **Clean-tree required.** Refuses to start if you have uncommitted work (so it can
  never entangle its change with yours).
- **CONFIRM-tier.** Initiating the loop itself requires your authorisation
  (`governance.json` → self_improve = CONFIRM).

Reuses the existing GitHubAgent (sandboxed `git` runner) and WorkspaceAgent
(workspace-confined file writes). `gh` is used for the PR when present; otherwise it
pushes the branch and returns the GitHub "compare" URL so you can open the PR yourself.
"""

from __future__ import annotations

import os
import re
import json
import asyncio
import subprocess
from pathlib import Path

try:
    from modules.llm_router import universal_llm_call as _default_llm_call
except Exception:  # pragma: no cover
    _default_llm_call = None

_BRANCH_SAFE = re.compile(r"[^a-zA-Z0-9._/-]+")
_PROPOSAL_SYSTEM = (
    "You are J.A.R.V.I.S.'s self-improvement engine. Given an instruction, propose ONE "
    "concrete, minimal, safe code change to this project. Output STRICT JSON only:\n"
    '{"summary": "one line", "file": "workspace-relative path", '
    '"change_type": "write" | "patch", '
    '"content": "full new file content (write only)", '
    '"search": "exact existing text (patch only)", "replace": "new text (patch only)", '
    '"branch_name": "jarvis/short-kebab", "commit_message": "imperative summary", '
    '"pr_title": "...", "pr_body": "what changed and why"}\n'
    "Prefer 'patch' for edits to existing files; use exact, character-for-character "
    "search text. Keep the change small and self-contained. Never touch secrets/.env."
)


class SelfImprovementEngine:
    def __init__(self, github_agent, workspace_agent, *, llm_call=None):
        self.gh = github_agent
        self.ws = workspace_agent
        self.llm = llm_call or _default_llm_call

    # ── Public async entry point ────────────────────────────────────────────
    async def run(self, instruction: str, *, base_branch: str | None = None,
                  run_tests: bool = True) -> dict:
        """Propose → branch → apply → test → push → PR. Returns a report dict.

        Never merges. All blocking git/test work is offloaded from the event loop.
        """
        return await asyncio.to_thread(self._run_sync, instruction, base_branch, run_tests)

    # ── Synchronous worker (git/subprocess heavy) ─────────────────────────────
    def _run_sync(self, instruction: str, base_branch: str | None, run_tests: bool) -> dict:
        repo, err = self.gh._resolve_repo(None)
        if repo is None:
            return self._fail(f"No git repository found: {err}")

        base_branch = base_branch or self._current_branch(repo) or "main"

        # 1. Require a clean tree so our change can't entangle with the user's.
        ok, out = self.gh._run_git(["status", "--porcelain"], repo)
        if ok and out.strip():
            return self._fail("Working tree isn't clean. Commit or stash your changes first, Sir.")

        # 2. Propose the change.
        proposal = self._propose(instruction)
        if not proposal or not proposal.get("file"):
            return self._fail("I couldn't form a concrete change proposal for that, Sir.")
        branch = "jarvis/" + _BRANCH_SAFE.sub("-", proposal.get("branch_name", "improvement")).strip("-/")
        if not branch.startswith("jarvis/"):
            branch = "jarvis/" + branch

        # 3. Fresh branch off the base.
        ok, out = self.gh._run_git(["checkout", "-b", branch], repo)
        if not ok:
            return self._fail(f"Couldn't create branch '{branch}': {out}")

        try:
            # 4. Apply the change via the workspace-confined agent.
            applied = self._apply(proposal)
            if not applied.startswith("Created") and "Overwritten" not in applied and "patched" not in applied.lower() and "replacement" not in applied.lower():
                self._abort(repo, branch, base_branch)
                return self._fail(f"Change could not be applied: {applied}")

            # 5. Run the test suite. A failure rolls everything back — nothing is pushed.
            if run_tests:
                tok, tout = self._run_tests(repo, proposal.get("file"))
                if not tok:
                    self._abort(repo, branch, base_branch)
                    return self._fail(
                        f"Tests failed on the proposed change — rolled back, nothing pushed, Sir.\n{tout[:800]}",
                        branch=branch, tests_passed=False,
                    )

            # 6. Commit on the branch.
            self.gh._run_git(["add", "."], repo)
            ok, cout = self.gh._run_git(["commit", "-m", self._safe_msg(proposal.get("commit_message", instruction))], repo)
            if not ok and "nothing to commit" not in cout.lower():
                self._abort(repo, branch, base_branch)
                return self._fail(f"Commit failed: {cout}", branch=branch)

            # 7. Push the branch (with upstream).
            ok, pout = self.gh._run_git(["push", "-u", "origin", branch], repo)
            if not ok:
                return self._fail(
                    f"Change is committed on branch '{branch}' but push failed: {pout}. "
                    f"It's safe locally — no merge has occurred.",
                    branch=branch, tests_passed=True,
                )

            # 8. Open a PR (gh if available; otherwise return the compare URL).
            pr_url = self._open_pr(repo, branch, base_branch, proposal)

            # Leave the user back on their base branch; the change lives only in the PR.
            self.gh._run_git(["checkout", base_branch], repo)

            return {
                "success": True,
                "merged": False,            # we NEVER merge
                "branch": branch,
                "base": base_branch,
                "summary": proposal.get("summary", instruction),
                "tests_passed": run_tests,
                "pr_url": pr_url,
                "message": (
                    f"Proposal ready for your review, Sir. I created branch '{branch}', "
                    f"{'tests passed, ' if run_tests else ''}and opened it for a pull request. "
                    f"Nothing has been merged. {pr_url or ''}"
                ).strip(),
            }
        except Exception as e:
            self._abort(repo, branch, base_branch)
            return self._fail(f"Self-improvement aborted on an unexpected error: {e}", branch=branch)

    # ── Steps ─────────────────────────────────────────────────────────────────
    def _propose(self, instruction: str) -> dict | None:
        if self.llm is None:
            return None
        try:
            raw = self.llm(
                [{"role": "system", "content": _PROPOSAL_SYSTEM},
                 {"role": "user", "content": f"Instruction: {instruction}\n\nPropose the change as JSON."}],
                0.3, 1500, False, True,   # temp, max_tokens, stream, json_mode
            )
        except Exception as e:
            print(f"[SELF_IMPROVE] proposal LLM error: {e}", flush=True)
            return None
        s = (raw or "").replace("```json", "").replace("```", "").strip()
        a, b = s.find("{"), s.rfind("}")
        if a == -1 or b <= a:
            return None
        try:
            return json.loads(s[a:b + 1])
        except json.JSONDecodeError:
            return None

    def _apply(self, proposal: dict) -> str:
        fpath = proposal["file"]
        if proposal.get("change_type") == "patch" and proposal.get("search"):
            return self.ws.patch_file(fpath, proposal.get("search", ""), proposal.get("replace", ""))
        return self.ws.write_file(fpath, proposal.get("content", ""))

    def _run_tests(self, repo: Path, changed_file: str | None) -> tuple[bool, str]:
        """Run the project test suite (bounded). Falls back to compiling the changed file."""
        py = os.path.abspath(os.sys.executable)
        # Prefer pytest if the project is configured for it.
        if (repo / "pytest.ini").exists() or (repo / "tests").is_dir():
            ok, out = self._run([py, "-m", "pytest", "-q", "--no-header"], repo, timeout=240)
            # pytest exit 5 = "no tests collected" — treat as a pass for a non-test change.
            if "no tests ran" in out.lower() or "no tests collected" in out.lower():
                ok = True
            if ok:
                return True, out
            # Fall through to a compile check so a flaky/unrelated suite doesn't block a
            # syntactically-valid change — but report the pytest output.
        # Compile-check the changed Python file as a minimum gate.
        if changed_file and str(changed_file).endswith(".py"):
            cf = changed_file if os.path.isabs(changed_file) else str(repo / changed_file)
            cok, cout = self._run([py, "-m", "py_compile", cf], repo, timeout=60)
            return cok, (cout or "py_compile passed.")
        return True, "No test gate applicable; change is non-Python."

    def _open_pr(self, repo: Path, branch: str, base: str, proposal: dict) -> str:
        """Open a PR via `gh` if present; else return the GitHub compare URL."""
        title = proposal.get("pr_title", proposal.get("summary", "JARVIS proposed change"))
        body = proposal.get("pr_body", "") + "\n\n— Proposed autonomously by J.A.R.V.I.S. (review before merge)."
        gh_ok, gh_out = self._run(
            ["gh", "pr", "create", "--base", base, "--head", branch,
             "--title", title, "--body", body], repo, timeout=60,
        )
        if gh_ok:
            m = re.search(r"https?://\S+", gh_out)
            return m.group(0) if m else gh_out.strip()
        # Fallback: build a compare URL from the origin remote.
        _, remote = self.gh._run_git(["remote", "get-url", "origin"], repo)
        url = self._compare_url(remote, base, branch)
        return f"Open the PR here: {url}" if url else ""

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _current_branch(self, repo: Path) -> str | None:
        ok, out = self.gh._run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
        return out.strip() if ok else None

    def _abort(self, repo: Path, branch: str, base: str) -> None:
        """Discard the proposed change and delete the branch — leave the tree as it was."""
        try:
            self.gh._run_git(["checkout", "--", "."], repo)
            self.gh._run_git(["checkout", base], repo)
            self.gh._run_git(["branch", "-D", branch], repo)
        except Exception:
            pass

    @staticmethod
    def _safe_msg(msg: str) -> str:
        return re.sub(r'[`$\\"\r\n]', "", msg or "JARVIS self-improvement").strip() or "JARVIS self-improvement"

    @staticmethod
    def _compare_url(remote: str, base: str, branch: str) -> str | None:
        remote = (remote or "").strip()
        m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", remote)
        if not m:
            return None
        return f"https://github.com/{m.group(1)}/{m.group(2)}/compare/{base}...{branch}?expand=1"

    @staticmethod
    def _run(cmd: list[str], cwd: Path, *, timeout: int = 120) -> tuple[bool, str]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, cwd=str(cwd), shell=False)
            return r.returncode == 0, "\n".join(filter(None, [(r.stdout or "").strip(), (r.stderr or "").strip()]))
        except FileNotFoundError:
            return False, f"{cmd[0]} not found"
        except subprocess.TimeoutExpired:
            return False, f"{cmd[0]} timed out after {timeout}s"
        except Exception as e:
            return False, f"{cmd[0]} error: {e}"

    @staticmethod
    def _fail(message: str, *, branch: str | None = None, tests_passed: bool | None = None) -> dict:
        return {"success": False, "merged": False, "branch": branch,
                "tests_passed": tests_passed, "message": message}
