"""
Phase 6 Skill Pack — GitHub Specialist Agent
=============================================
Gives J.A.R.V.I.S. native, sandboxed access to local Git repositories.
All operations run in the *active workspace directory* — the same root
that WorkspaceAgent uses — so JARVIS always operates on the right project.

Security model
──────────────
- Only git sub-commands explicitly implemented here are reachable.
  No raw shell pass-through; no arbitrary command injection possible.
- The working directory is resolved from JARVIS_WORKSPACE_ROOTS (same
  env var as WorkspaceAgent) with a sensible default of G:\\work.
- Commit messages are sanitised to strip shell-special characters before
  being passed to git.
- subprocess runs are made with shell=False and a hard timeout.

All public methods return clean, LLM-readable strings for TTS consumption.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

# ── Workspace root resolution (mirrors WorkspaceAgent) ───────────────────────

def _resolve_workspace_dir() -> Path:
    """
    Return the primary workspace directory for git operations.
    Reads JARVIS_GIT_WORKSPACE first, then falls back to the first
    entry in JARVIS_WORKSPACE_ROOTS, then G:\\work.
    """
    # Dedicated override (most specific)
    dedicated = os.getenv("JARVIS_GIT_WORKSPACE", "").strip()
    if dedicated:
        p = Path(dedicated)
        if p.is_dir():
            return p.resolve()

    # Shared workspace roots list
    roots_raw = os.getenv("JARVIS_WORKSPACE_ROOTS", "").strip()
    if roots_raw:
        for candidate in roots_raw.split(","):
            p = Path(candidate.strip())
            if p.is_dir():
                return p.resolve()

    # Hard default — the JARVIS project tree
    default = Path("G:/work")
    if default.is_dir():
        return default.resolve()

    # Last resort: current process working directory
    return Path.cwd().resolve()


# Module-level constant — resolved once at import time
WORKSPACE_DIR: Path = _resolve_workspace_dir()

# ── Safety constants ──────────────────────────────────────────────────────────

_GIT_TIMEOUT    = 30       # seconds per git subprocess call
_MAX_OUTPUT_CH  = 3_000    # truncate noisy git output before returning

# Characters that must not appear in a commit message passed to git -m.
# We strip/replace them to prevent any form of argument injection even though
# we're using shell=False with a list-form command.
_MSG_UNSAFE_RE  = re.compile(r'[`$\\"\r\n]')

# ── GitHubAgent ───────────────────────────────────────────────────────────────

class GitHubAgent:
    """
    Wraps a small set of local git operations for J.A.R.V.I.S.

    Each method takes a target repo path (optional — defaults to WORKSPACE_DIR)
    and returns a clean, spoken-English-friendly string.
    """

    # ── Core runner ──────────────────────────────────────────────────────────

    def _run_git(self, args: list[str], cwd: Path) -> tuple[bool, str]:
        """
        Execute `git <args>` in `cwd`.

        Returns:
            (success: bool, output: str)

        Never raises — all exceptions are converted to error strings.
        """
        cmd = ["git"] + args
        print(f"[GITHUB AGENT] Running: {' '.join(cmd)} in {cwd}", flush=True)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_GIT_TIMEOUT,
                cwd=str(cwd),
                shell=False,          # never use shell=True — injection risk
            )
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()

            # git writes informational messages to stderr even on success
            # (e.g. "Enumerating objects…"). Merge both streams.
            combined = "\n".join(filter(None, [stdout, stderr])).strip()

            if len(combined) > _MAX_OUTPUT_CH:
                combined = combined[:_MAX_OUTPUT_CH] + f"\n…[output truncated]"

            success = (result.returncode == 0)
            return success, combined

        except FileNotFoundError:
            return False, (
                "Git is not installed or not on PATH. "
                "Please install Git for Windows and restart the server."
            )
        except subprocess.TimeoutExpired:
            return False, f"Git command timed out after {_GIT_TIMEOUT} seconds."
        except Exception as exc:
            return False, f"Git execution error: {exc}"

    # ── Repository path resolver ──────────────────────────────────────────────

    def _walk_up_for_git(self, start: Path) -> Optional[Path]:
        """
        Walk upward from `start` until a directory containing `.git` is found.
        Supports sub-folder invocation (user names a path inside the repo).
        """
        try:
            cur = start.resolve()
        except Exception:
            return None
        if cur.is_file():
            cur = cur.parent
        while True:
            git_meta = cur / ".git"
            if git_meta.exists():
                return cur
            if cur.parent == cur:
                break
            cur = cur.parent
        return None

    def _resolve_repo(self, path: Optional[str]) -> tuple[Optional[Path], str]:
        """
        Resolve a user-supplied path to an absolute directory that contains
        a .git folder.  If path is None/empty, uses WORKSPACE_DIR.

        Returns (resolved_path, error_string).
        error_string is empty on success.
        If path is empty: tries WORKSPACE_DIR then current working directory (auto-discovery).
        """
        if path and path.strip():
            candidate = Path(path.strip()).expanduser()
            if not candidate.is_absolute():
                candidate = WORKSPACE_DIR / candidate
            try:
                candidate = candidate.resolve()
            except Exception:
                return None, f"Invalid path: {path!r}"

            if not candidate.exists():
                return None, f"Path does not exist: {candidate}"

            if candidate.is_file():
                candidate = candidate.parent

            if not candidate.is_dir():
                return None, f"Path is not a directory: {candidate}"

            root = self._walk_up_for_git(candidate)
            if root is not None:
                if root != candidate:
                    print(
                        f"[GITHUB AGENT] Resolved repo root: {root} (from {candidate})",
                        flush=True,
                    )
                return root, ""

            return None, (
                f"No git repository found at or above '{candidate}'. "
                "Initialise one with 'git init' first."
            )

        # No path: try workspace root, then current working directory
        for seed in (WORKSPACE_DIR, Path.cwd()):
            try:
                if not seed.exists():
                    continue
                root = self._walk_up_for_git(seed)
                if root is not None:
                    return root, ""
            except Exception:
                continue

        return None, (
            "No git repository found from workspace or current directory. "
            "Pass an explicit repo path or run inside a git checkout."
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_status(self, repo_path: Optional[str] = None) -> str:
        """
        Run `git status` and return a clean summary.

        Args:
            repo_path: Optional path to the repo. Defaults to WORKSPACE_DIR.

        Returns a human-readable string describing the working tree state.
        """
        repo, err = self._resolve_repo(repo_path)
        if repo is None:
            return f"Git status failed: {err}"

        ok, output = self._run_git(["status"], repo)
        if not ok:
            return f"Git status error: {output}"

        # Make it a little more terse for TTS
        if not output:
            return "Git status returned no output. The repository may be empty."

        branch_line = self._parse_branch_from_status(output)
        branch_header = f"Branch: {branch_line}\n" if branch_line else ""

        return f"[git status — {repo.name}]\n{branch_header}{output}"

    @staticmethod
    def _parse_branch_from_status(status_text: str) -> Optional[str]:
        """Extract current branch (or detached HEAD summary) from `git status` output."""
        m = re.search(r"^On branch (.+)$", status_text, re.MULTILINE)
        if m:
            return m.group(1).strip()
        m = re.search(r"^HEAD detached at ([^\s]+)", status_text, re.MULTILINE)
        if m:
            return f"(detached at {m.group(1).strip()})"
        if re.search(r"^HEAD detached from", status_text, re.MULTILINE):
            return "(detached HEAD)"
        return None

    def commit(self, commit_message: str, repo_path: Optional[str] = None) -> str:
        """
        Stage all changes with `git add .` then commit with the given message.

        Args:
            commit_message: The commit message (will be sanitised).
            repo_path:      Optional repo path. Defaults to WORKSPACE_DIR.

        Returns a success or failure string.
        """
        if not commit_message or not commit_message.strip():
            return "No commit message provided. A commit message is required."

        repo, err = self._resolve_repo(repo_path)
        if repo is None:
            return f"Git commit failed: {err}"

        # Sanitise the message — strip characters that could escape the argument
        safe_msg = _MSG_UNSAFE_RE.sub("", commit_message).strip()
        if not safe_msg:
            return "Commit message became empty after sanitisation. Please use plain text."

        # Step 1: stage everything
        ok_add, out_add = self._run_git(["add", "."], repo)
        if not ok_add:
            return f"Git add failed: {out_add}"
        if out_add:
            print(f"[GITHUB AGENT] git add output: {out_add}", flush=True)

        # Step 2: commit
        ok_commit, out_commit = self._run_git(["commit", "-m", safe_msg], repo)

        if not ok_commit:
            combined_lower = out_commit.lower()
            # "nothing to commit" is a soft error — inform gracefully
            if "nothing to commit" in combined_lower and "working tree clean" in combined_lower:
                return (
                    "Nothing to commit — the working tree is clean. "
                    "No changes have been staged."
                )
            if "nothing added to commit" in combined_lower:
                return (
                    "Nothing added to commit — there are no staged changes. "
                    "The working tree may be clean or only contain untracked files."
                )
            if "please tell me who you are" in out_commit.lower():
                return (
                    "Git identity is not configured. "
                    "Run 'git config --global user.email' and 'git config --global user.name' "
                    "in your terminal before committing."
                )
            return f"Git commit failed: {out_commit}"

        return f"Committed successfully in '{repo.name}'.\n{out_commit}"

    def push(self, repo_path: Optional[str] = None, remote: str = "origin") -> str:
        """
        Push the current branch to the given remote.

        Args:
            repo_path: Optional repo path. Defaults to WORKSPACE_DIR.
            remote:    Remote name (default: 'origin').

        Returns a success or descriptive failure string.
        """
        repo, err = self._resolve_repo(repo_path)
        if repo is None:
            return f"Git push failed: {err}"

        ok, output = self._run_git(["push", remote], repo)

        if not ok:
            output_lower = output.lower()

            if (
                "no upstream branch" in output_lower
                or "has no upstream" in output_lower
                or "set-upstream" in output_lower
                or "no tracking information" in output_lower
            ):
                # Detect current branch name for a helpful message
                _, branch_out = self._run_git(
                    ["rev-parse", "--abbrev-ref", "HEAD"], repo
                )
                branch = branch_out.strip() or "current-branch"
                return (
                    f"Push failed: no upstream branch is set for '{branch}'. "
                    f"Run 'git push --set-upstream {remote} {branch}' once to link it."
                )

            if "authentication failed" in output_lower or "could not read" in output_lower:
                return (
                    "Push failed: authentication error. "
                    "Ensure your SSH key or personal access token is configured correctly."
                )

            if "rejected" in output_lower:
                return (
                    "Push rejected by the remote. "
                    "The remote has changes you don't have locally — run a git pull first."
                )

            if "repository not found" in output_lower:
                return (
                    "Push failed: remote repository not found. "
                    "Verify the remote URL with 'git remote -v'."
                )

            return f"Git push failed: {output}"

        return f"Push successful for '{repo.name}'.\n{output}"

    # ── Convenience helpers (future expansion) ────────────────────────────────

    def get_log(self, n: int = 5, repo_path: Optional[str] = None) -> str:
        """Return the last n commit log entries (one-line format)."""
        repo, err = self._resolve_repo(repo_path)
        if repo is None:
            return f"Git log failed: {err}"
        ok, output = self._run_git(
            ["log", f"--oneline", f"-{max(1, min(n, 20))}"], repo
        )
        if not ok:
            return f"Git log error: {output}"
        return f"[Last {n} commits — {repo.name}]\n{output}" if output else "No commits yet."

    def get_diff(self, repo_path: Optional[str] = None) -> str:
        """Return a summary of unstaged changes (git diff --stat)."""
        repo, err = self._resolve_repo(repo_path)
        if repo is None:
            return f"Git diff failed: {err}"
        ok, output = self._run_git(["diff", "--stat"], repo)
        if not ok:
            return f"Git diff error: {output}"
        return f"[Diff summary — {repo.name}]\n{output}" if output else "No unstaged changes."
