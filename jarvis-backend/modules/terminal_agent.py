"""
Phase 8.8 – Terminal Agent (Security-Hardened)
Provides a secure, sandboxed interface to the Windows OS shell.

Safety model:
  - BLOCKED_PATTERNS: regex list of commands that are never permitted
  - Path confinement: file operations are checked against the user home directory
  - Timeout enforcement: no command runs longer than _DEFAULT_TIMEOUT seconds
  - Output cap: responses are truncated to _MAX_OUTPUT_CHARS before returning

Phase 8.8 changes:
  - kill_process() → NO LONGER executes taskkill directly.
    Returns a sentinel "__ROUTE_TO_CLOSE_APP__:<id>" so action_engine
    can delegate to _close_app() which enforces the _WEB_ONLY_SERVICES
    and explorer.exe blacklists.
  - move_file() / copy_file() → replaced shell strings with shutil.
    Auto-appends "_copy" suffix if destination already exists — prevents
    Windows overwrite-dialog hangs and accidental data loss.
  - list_processes() → uses psutil, sorts by RAM desc, caps at 15.
    Returns structured JSON {"ui_action": "render_process_list", "data": [...]}
    for HUD rendering rather than raw text the synthesis layer would read aloud.
  - list_directory() → returns structured JSON {"ui_action": "render_file_list",
    "data": [...]} for HUD rendering.

All methods return clean strings (or JSON strings) suitable for LLM/TTS consumption.
"""

import os
import re
import json
import shutil
import subprocess
import ctypes
import psutil
from pathlib import Path
from typing import Optional

# ── Safety: Patterns that are NEVER permitted ─────────────────────────────────
# Matches are checked case-insensitively against the full command string.
_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in [
        r'\bformat\s+[a-z]:',                      # format drive
        r'\bdiskpart\b',                            # disk partitioner
        r'\bdel\b.*/[sf]',                          # del /f or /s (force/recursive)
        r'\brd\b.*/s',                              # rd /s (recursive dir remove)
        r'\brmdir\b.*/s',                           # rmdir /s
        r'\brm\b\s+-[rf]{1,2}',                    # rm -rf / rm -r
        r'\bcipher\s+/w',                           # secure wipe
        r'\bbcdedit\b',                             # boot config editor
        r'\bnet\s+user\b',                          # user account management
        r'\breg\s+(delete|add|import|export)\b',   # registry write/delete
        r'\bregedit\b',                             # registry editor GUI
        r'\btakeown\b',                             # ownership takeover
        r'\bicacls\b.*/.(grant|deny)',              # ACL changes
        r'\bsfc\s+/scannow\b',                     # long-running system scan
        r'\bchkdsk\b.*/[fr]\b',                    # disk repair
        r'\bwmic\b.*\bdelete\b',                   # WMI delete
        r'\bsc\s+(delete|stop\s+.*system|create)\b',  # dangerous service ops
        r'\btaskkill\b.*/f.*/im\s+(system|lsass|csrss|winlogon|wininit|services|smss)',
        r'\bshutdown\b.*/[rfsg]\b',               # shutdown (use dedicated method)
        r'\bstart\b.*(cmd|powershell|wscript|cscript)\b',  # shell escalation
        r'\bpowercfg\b.*/hibernate\b',             # hibernate toggle
    ]
]

# Processes that can NEVER be killed by the terminal agent
_PROTECTED_PROCESSES = frozenset({
    "system", "lsass.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "smss.exe", "svchost.exe",
})

_SAFE_CWD        = Path.home()       # working dir for all shell commands
_MAX_OUTPUT_CHARS = 3000
_DEFAULT_TIMEOUT  = 15               # seconds


class TerminalAgent:
    """
    Executes vetted OS-level commands in a sandboxed environment.
    Returns clean string output for the Supervisor to read or speak.
    """

    # ── Core execution ───────────────────────────────────────────────────────

    def run_command(self, command: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
        """
        Execute a shell command and return its combined stdout/stderr as a string.
        Returns a security-block message if the command matches a blocked pattern.
        """
        if not command or not command.strip():
            return "No command provided."

        blocked = self._check_blocked(command)
        if blocked:
            print(f"[TERMINAL AGENT] Security block triggered by pattern: {blocked}")
            return f"Security block: that command pattern is restricted."

        try:
            result = subprocess.run(
                command,
                shell=True,            # required for built-ins (dir, echo, etc.)
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(_SAFE_CWD),
            )
            output = (result.stdout or "") + (result.stderr or "")
            output = output.strip()

            if not output:
                return f"Command completed (exit {result.returncode}) with no output."
            if len(output) > _MAX_OUTPUT_CHARS:
                output = output[:_MAX_OUTPUT_CHARS] + f"\n…[output truncated — {len(output)} chars total]"
            return output

        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s — aborted."
        except Exception as e:
            return f"Terminal execution error: {e}"

    # ── File-system convenience methods ─────────────────────────────────────

    def list_directory(self, path: str = ".") -> str:
        """
        Phase 8.8: Lists files/folders at the given path and returns a structured
        JSON payload for HUD rendering instead of raw dir output.
        The synthesis engine sees this as a ui_action and says 'displayed on screen'
        rather than reading the file list aloud.
        """
        safe = self._resolve_safe_path(path)
        if safe is None:
            return "Access denied: path is outside the permitted user directory."

        target_path = Path(safe)
        if not target_path.exists():
            return f"Directory not found: {safe}"
        if not target_path.is_dir():
            return f"Not a directory: {safe}"

        try:
            entries = []
            for item in sorted(target_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                try:
                    stat = item.stat()
                    entry = {
                        "name": item.name,
                        "type": "file" if item.is_file() else "folder",
                        "size": stat.st_size if item.is_file() else None,
                        "modified": stat.st_mtime,
                    }
                    entries.append(entry)
                except (PermissionError, OSError):
                    continue

            payload = {
                "ui_action": "render_file_list",
                "path": safe,
                "data": entries,
            }
            print(f"[TERMINAL AGENT] list_directory: {len(entries)} entries in '{safe}'", flush=True)
            return json.dumps(payload)

        except PermissionError:
            return f"Access denied reading directory: {safe}"
        except Exception as e:
            return f"Failed to list directory: {e}"

    def create_folder(self, path: str) -> str:
        """Create a directory (and any missing parents) inside the user home."""
        safe = self._resolve_safe_path(path)
        if safe is None:
            return "Access denied: path is outside the permitted user directory."
        try:
            Path(safe).mkdir(parents=True, exist_ok=True)
            return f"Folder created: {safe}"
        except Exception as e:
            return f"Failed to create folder: {e}"

    def move_file(self, src: str, dst: str) -> str:
        """
        Phase 8.8: Move/rename a file using shutil.move() instead of shell commands.
        Both paths must be within the user home. If the destination already exists,
        auto-appends '_copy' to the filename stem to prevent data loss and avoid
        any Windows overwrite-confirmation dialog that could hang the backend.
        """
        safe_src = self._resolve_safe_path(src)
        safe_dst = self._resolve_safe_path(dst)
        if safe_src is None or safe_dst is None:
            return "Access denied: paths must remain within the user home directory."

        src_path = Path(safe_src)
        dst_path = Path(safe_dst)

        if not src_path.exists():
            return f"Source not found: {safe_src}"

        # If destination is a directory, compute the final file path within it
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name

        # Auto-rename if destination file already exists
        if dst_path.exists():
            dst_path = self._safe_rename_dest(dst_path)

        try:
            shutil.move(str(src_path), str(dst_path))
            return f"Moved to: {dst_path}"
        except Exception as e:
            return f"Move failed: {e}"

    def copy_file(self, src: str, dst: str) -> str:
        """
        Phase 8.8: Copy a file using shutil.copy2() instead of shell commands.
        Both paths must be within the user home. If the destination already exists,
        auto-appends '_copy' to the filename stem to prevent data loss and avoid
        any Windows overwrite-confirmation dialog that could hang the backend.
        """
        safe_src = self._resolve_safe_path(src)
        safe_dst = self._resolve_safe_path(dst)
        if safe_src is None or safe_dst is None:
            return "Access denied: paths must remain within the user home directory."

        src_path = Path(safe_src)
        dst_path = Path(safe_dst)

        if not src_path.exists():
            return f"Source not found: {safe_src}"
        if src_path.is_dir():
            return "Refusing to copy a directory through this interface. Specify a file."

        # If destination is a directory, compute the final file path within it
        if dst_path.is_dir():
            dst_path = dst_path / src_path.name

        # Auto-rename if destination file already exists
        if dst_path.exists():
            dst_path = self._safe_rename_dest(dst_path)

        try:
            shutil.copy2(str(src_path), str(dst_path))
            return f"Copied to: {dst_path}"
        except Exception as e:
            return f"Copy failed: {e}"

    def delete_file(self, path: str) -> str:
        """Delete a single file (directories are refused — not recursive delete)."""
        safe = self._resolve_safe_path(path)
        if safe is None:
            return "Access denied: path is outside the permitted user directory."
        p = Path(safe)
        if p.is_dir():
            return "Refusing to delete a directory through this interface. Specify a file."
        if not p.exists():
            return f"File not found: {safe}"
        try:
            p.unlink()
            return f"Deleted: {safe}"
        except Exception as e:
            return f"Delete failed: {e}"

    # ── Process management ───────────────────────────────────────────────────

    def list_processes(self, filter_name: Optional[str] = None) -> str:
        """
        Phase 8.8: Lists running processes using psutil, sorted by RAM usage
        (RSS, descending), capped at 15. Returns a structured JSON payload
        for HUD rendering — {"ui_action": "render_process_list", "data": [...]}
        — so the synthesis engine says 'displayed on screen' instead of reading
        all 15 process names aloud.
        """
        try:
            procs = []
            for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
                try:
                    info = proc.info
                    pname = info.get("name") or ""
                    if not pname:
                        continue
                    if filter_name and filter_name.lower() not in pname.lower():
                        continue
                    mem_rss = info.get("memory_info").rss if info.get("memory_info") else 0
                    procs.append({
                        "pid":  info["pid"],
                        "name": pname,
                        "ram_bytes": mem_rss,
                        "ram_mb": round(mem_rss / (1024 * 1024), 1),
                        "cpu_pct": round(info.get("cpu_percent") or 0.0, 1),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # Sort by RAM descending, take top 15
            procs.sort(key=lambda p: p["ram_bytes"], reverse=True)
            top = procs[:15]

            payload = {
                "ui_action": "render_process_list",
                "data": top,
            }
            print(
                f"[TERMINAL AGENT] list_processes: returning top {len(top)} procs "
                f"(filter={filter_name!r})", flush=True
            )
            return json.dumps(payload)

        except Exception as e:
            return f"Process list error: {e}"

    def kill_process(self, identifier: str) -> str:
        """
        Phase 8.8 SANDBOX FIX: Process termination is NO LONGER executed here.

        Direct taskkill calls bypass the _WEB_ONLY_SERVICES and explorer.exe
        blacklists that live in action_engine._close_app(). To enforce those
        protections unconditionally, this method returns a routing sentinel string
        that action_engine._run_terminal_command() intercepts and re-dispatches
        to self._close_app(identifier).

        The only local enforcement kept here is the _PROTECTED_PROCESSES check
        to short-circuit obviously dangerous requests before they even reach the
        action engine.
        """
        identifier_lower = identifier.lower().strip()
        if identifier_lower in _PROTECTED_PROCESSES:
            return f"Security block: '{identifier}' is a protected system process."

        # Sentinel: action_engine will route this to _close_app()
        print(
            f"[TERMINAL AGENT] kill_process('{identifier}') -> routing sentinel emitted "
            f"(will be handled by action_engine._close_app)", flush=True
        )
        return f"__ROUTE_TO_CLOSE_APP__:{identifier}"

    # ── Network helpers ──────────────────────────────────────────────────────

    def get_network_info(self) -> str:
        """Returns current IP configuration summary."""
        return self.run_command("ipconfig", timeout=8)

    def ping(self, host: str, count: int = 4) -> str:
        """Ping a host and return latency results."""
        count = max(1, min(count, 10))
        return self.run_command(f"ping -n {count} {host}", timeout=20)

    # ── Power / session management ───────────────────────────────────────────

    def lock_workstation(self) -> str:
        """Lock the Windows session immediately."""
        try:
            ctypes.windll.user32.LockWorkStation()
            return "Workstation locked, sir."
        except Exception as e:
            return f"Lock failed: {e}"

    def sleep_system(self) -> str:
        """Put the system into sleep/suspend."""
        return self.run_command(
            "rundll32.exe powrprof.dll,SetSuspendState 0,1,0", timeout=5
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _check_blocked(self, command: str) -> Optional[str]:
        """Returns the pattern string if the command is blocked, else None."""
        for pat in _BLOCKED_PATTERNS:
            if pat.search(command):
                return pat.pattern
        return None

    def _resolve_safe_path(self, raw: str) -> Optional[str]:
        """
        Resolve a user-supplied path.  Returns the absolute path string only if
        it resolves to somewhere inside the user home directory; None otherwise.
        """
        try:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = _SAFE_CWD / p
            p = p.resolve()
            p.relative_to(_SAFE_CWD)   # raises ValueError if outside home
            return str(p)
        except (ValueError, Exception):
            return None

    def _safe_rename_dest(self, dst: Path) -> Path:
        """
        If dst already exists, append '_copy' (then '_copy_2', '_copy_3', …)
        to the stem until a free filename is found. Never clobbers existing files.
        """
        stem   = dst.stem
        suffix = dst.suffix
        parent = dst.parent
        candidate = parent / f"{stem}_copy{suffix}"
        counter = 2
        while candidate.exists():
            candidate = parent / f"{stem}_copy_{counter}{suffix}"
            counter += 1
        return candidate
