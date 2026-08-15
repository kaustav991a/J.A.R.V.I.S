"""
Phase 3 – The Code Specialist: Workspace Agent
Gives J.A.R.V.I.S. native, sandboxed read/write/patch access to project files.

Security model
──────────────
- WORKSPACE_ROOTS: the set of allowed root directories. Every path is resolved
  and checked against this set before any I/O is performed.
- Binary files are detected and refused for read/write operations.
- File size caps prevent flooding the LLM context or writing runaway files.
- The roots are configured via the JARVIS_WORKSPACE_ROOTS environment variable
  (comma-separated absolute paths). If unset, the project's work directory (derived
  from the location of this file) and the user's Documents folder are used as
  sane defaults.

All public methods return clean, LLM-readable strings.
"""

import os
import re
import difflib
import datetime
from pathlib import Path
from typing import Optional

# ── Workspace root resolution ─────────────────────────────────────────────────

def _build_workspace_roots() -> list[Path]:
    """
    Read roots from JARVIS_WORKSPACE_ROOTS env var, falling back to
    dynamically derived defaults: the repo's work directory, Documents, Desktop.
    """
    raw = os.getenv("JARVIS_WORKSPACE_ROOTS", "")
    if raw.strip():
        roots = [Path(p.strip()).resolve() for p in raw.split(",") if p.strip()]
    else:
        # Derive the project's work directory dynamically instead of hardcoding
        # a drive letter. __file__ = .../jarvis-backend/modules/workspace_agent.py
        # parents[2] = .../JARVIS-Project  (repo root)
        # parents[3] = .../work            (the workspace directory)
        _this = Path(__file__).resolve()
        _project_root = _this.parents[2]   # e.g. F:\work\JARVIS-Project
        _work_dir     = _this.parents[3]   # e.g. F:\work

        # Default roots — covers the JARVIS project tree and user documents
        roots = []
        for candidate in [
            _work_dir,
            _project_root,
            Path.home() / "Documents",
            Path.home() / "Desktop",
        ]:
            try:
                resolved = candidate.resolve()
                if resolved.exists():
                    roots.append(resolved)
            except Exception:
                continue

        # Optional extra directory via environment variable
        _extra = os.getenv("JARVIS_PROJECTS_DIR", "")
        if _extra.strip():
            try:
                _extra_path = Path(_extra.strip()).resolve()
                if _extra_path.exists():
                    roots.append(_extra_path)
            except Exception:
                pass
    return roots


WORKSPACE_ROOTS: list[Path] = _build_workspace_roots()

# ── Safety constants ──────────────────────────────────────────────────────────

# File extensions that are never read or written — binaries, executables, etc.
_BLOCKED_EXTENSIONS = frozenset({
    ".exe", ".dll", ".sys", ".bat", ".cmd", ".com", ".msi",
    ".bin", ".iso", ".img", ".dmg", ".apk", ".deb", ".rpm",
    ".so", ".dylib", ".obj", ".o", ".lib", ".a",
    ".zip", ".tar", ".gz", ".bz2", ".rar", ".7z",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".webp",
    ".mp3", ".mp4", ".wav", ".avi", ".mkv", ".mov",
    ".pdf", ".psd", ".ai",
    ".pyc", ".pyo",
})

_MAX_READ_BYTES  = 80_000    # ~80 KB — enough for most source files
_MAX_WRITE_BYTES = 200_000   # ~200 KB — room for generated components
_MAX_PATCH_MATCHES = 500     # avoid runaway replacements


class WorkspaceAgent:
    """
    Sandboxed file I/O agent for project workspace manipulation.
    All paths are verified to reside within WORKSPACE_ROOTS before any
    read or write operation is performed.
    """

    # ── Core public API ───────────────────────────────────────────────────────

    def read_file(self, filepath: str) -> str:
        """
        Read the contents of a workspace file and return them as a string.
        Returns an error string (not an exception) on any failure.
        """
        safe = self._resolve_safe(filepath)
        if safe is None:
            return f"Access denied: '{filepath}' is outside the permitted workspace roots."

        if safe.suffix.lower() in _BLOCKED_EXTENSIONS:
            return f"Read refused: binary/executable file type '{safe.suffix}' is not readable as text."

        if not safe.exists():
            return f"File not found: {safe}"

        if not safe.is_file():
            return f"Path is not a file: {safe}"

        try:
            size = safe.stat().st_size
            if size > _MAX_READ_BYTES:
                return (
                    f"File too large to read in full ({size:,} bytes). "
                    f"Consider reading a specific line range."
                )
            content = safe.read_text(encoding="utf-8", errors="replace")
            line_count = content.count("\n") + 1
            return (
                f"FILE: {safe}\n"
                f"LINES: {line_count} | SIZE: {size:,} bytes\n"
                f"{'─'*60}\n"
                f"{content}"
            )
        except Exception as e:
            return f"Read error: {e}"

    def write_file(self, filepath: str, content: str) -> str:
        """
        Write `content` to `filepath`, creating parent directories as needed.
        Overwrites an existing file. Creates a new file if it does not exist.
        Returns a success or error string.
        """
        safe = self._resolve_safe_for_write(filepath)
        if safe is None:
            return f"Access denied: '{filepath}' is outside the permitted workspace roots."

        if safe.suffix.lower() in _BLOCKED_EXTENSIONS:
            return f"Write refused: binary/executable file type '{safe.suffix}' cannot be written."

        if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
            return (
                f"Content too large ({len(content):,} chars). "
                f"Maximum write size is {_MAX_WRITE_BYTES:,} bytes."
            )

        is_new = not safe.exists()
        try:
            safe.parent.mkdir(parents=True, exist_ok=True)
            safe.write_text(content, encoding="utf-8")
            verb = "Created" if is_new else "Overwritten"
            line_count = content.count("\n") + 1
            return (
                f"{verb}: {safe} "
                f"({line_count} lines, {len(content):,} chars)"
            )
        except Exception as e:
            return f"Write error: {e}"

    def patch_file(
        self,
        filepath: str,
        search_string: str,
        replace_string: str,
        *,
        count: int = 0,
        replace_all: bool = False,
    ) -> str:
        """
        Surgical find-and-replace within a workspace file.

        Args:
            filepath:       Target file path (must be within workspace roots).
            search_string:  Exact string to find (supports \\n for newlines).
            replace_string: Replacement string (supports \\n for newlines).
            count:          Replace exactly this many occurrences. 0 means
                            "unspecified", which is REFUSED when the string
                            matches more than once — see the check below.
            replace_all:    Say so explicitly to change every occurrence. This
                            is the only way to get the pre-2026-08-08 behaviour,
                            and it now has to be asked for.

        Returns a diff summary or an error string.
        """
        safe = self._resolve_safe_for_write(filepath)
        if safe is None:
            return f"Access denied: '{filepath}' is outside the permitted workspace roots."

        if safe.suffix.lower() in _BLOCKED_EXTENSIONS:
            return f"Patch refused: binary/executable file type '{safe.suffix}'."

        if not safe.exists():
            return f"File not found: {safe}"

        # Interpret \n escape sequences from LLM output
        search_string  = search_string.replace("\\n", "\n")
        replace_string = replace_string.replace("\\n", "\n")

        # Review finding R8, 2026-08-16 — this used to read with
        # `errors="replace"` and write the result back as UTF-8. A patch is a
        # ROUND TRIP, so on a cp1252 or latin-1 file every byte that would not
        # decode became U+FFFD and was then written back as one: changing a
        # single ASCII line silently rewrote every accented character, dash and
        # curly quote in the rest of the file.
        #
        # The diff below cannot show that, structurally — it compares `original`
        # against `patched`, and both are already mojibake — so the reply said
        # "Patched X: 1 replacement(s)" over a file that had been damaged
        # throughout. That is the same class as a truncated result read as
        # complete: the report is honest about the edit and silent about the harm.
        #
        # Reading STRICTLY is also the real binary check this module's docstring
        # claims. `_BLOCKED_EXTENSIONS` is a name list; a decode is a fact.
        try:
            original = safe.read_bytes().decode("utf-8")
        except UnicodeDecodeError as e:
            return (f"Patch refused: {safe.name} is not valid UTF-8 (byte "
                    f"{e.start}). Patching it would rewrite every character I "
                    f"could not decode, which is a change you did not ask for "
                    f"and the diff could not show you.")
        except Exception as e:
            return f"Read error before patch: {e}"

        occurrences = original.count(search_string)
        if occurrences == 0:
            # Always embed a file preview so the LLM can self-correct on retry
            suggestion = self._fuzzy_hint(original, search_string)
            preview = original[:300].replace("\n", "↵")
            msg = (
                f"Patch failed: search string not found in {safe.name}.\n"
                f"FILE PREVIEW (use one of these exact strings as your search):\n{preview}"
            )
            if suggestion:
                msg += f"\nClosest match found: {suggestion!r}"
            return msg

        if occurrences > _MAX_PATCH_MATCHES:
            return (
                f"Patch aborted: search string matches {occurrences} locations "
                f"(max {_MAX_PATCH_MATCHES}). Be more specific."
            )

        # An AMBIGUOUS patch is refused (roadmap §6.8.1 gap F, rule 4). Until
        # 2026-08-08 `count=0` meant "replace every occurrence" and NO caller
        # passed a count — so "change timeout = 30 to timeout = 60" rewrote all
        # three matches silently, and the diff preview below (40 lines) could
        # not even show it on a large file.
        #
        # Refusing is the honest failure: the caller has to say which one, or
        # say it means all of them. `count > 0` still means "replace exactly
        # this many" — an explicit number was always deliberate, so it is left
        # alone and only the SILENT default changed.
        if occurrences > 1 and count <= 0 and not replace_all:
            return (
                f"Patch refused: the search string matches {occurrences} places "
                f"in {safe.name}, so it is ambiguous which one to change. Include "
                f"more surrounding text so it identifies exactly one location — "
                f"or say explicitly that all {occurrences} should change."
            )

        effective_count = occurrences if (replace_all or count <= 0) else count
        patched = original.replace(search_string, replace_string, effective_count)

        # Bytes out, as bytes in. `write_text` translates "\n" to the platform
        # line ending, so on Windows a one-line patch to an LF file rewrote every
        # line ending in it — the second half of finding R8, and equally invisible
        # in the diff.
        try:
            safe.write_bytes(patched.encode("utf-8"))
        except Exception as e:
            return f"Write error after patch: {e}"

        # Build a compact diff for the LLM to confirm the change
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                patched.splitlines(keepends=True),
                fromfile=f"{safe.name} (before)",
                tofile=f"{safe.name} (after)",
                n=2,
            )
        )
        diff_preview = "".join(diff_lines[:40])
        if len(diff_lines) > 40:
            diff_preview += f"\n…({len(diff_lines) - 40} more diff lines)"

        return (
            f"Patched {safe.name}: {effective_count} replacement(s).\n"
            f"{diff_preview}"
        )

    # ── Listing helper (useful for Supervisor context-building) ──────────────

    def list_workspace(self, subpath: str = "", extensions: Optional[list[str]] = None) -> str:
        """
        List files inside the workspace (optionally filtered by subpath and
        file extension). Returns a tree-style string.
        """
        if subpath:
            safe = self._resolve_safe(subpath)
            if safe is None:
                return f"Access denied: '{subpath}' is outside the workspace roots."
            roots_to_scan = [safe] if safe.is_dir() else [safe.parent]
        else:
            roots_to_scan = WORKSPACE_ROOTS

        ext_filter = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or [])}
        lines: list[str] = []

        for root in roots_to_scan:
            if not root.exists():
                continue
            lines.append(f"📁 {root}")
            count = 0
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    if ext_filter and p.suffix.lower() not in ext_filter:
                        continue
                    if p.suffix.lower() in _BLOCKED_EXTENSIONS:
                        continue
                    rel = p.relative_to(root)
                    depth = len(rel.parts) - 1
                    indent = "  " * depth
                    lines.append(f"{indent}└─ {p.name}  ({p.stat().st_size:,} B)")
                    count += 1
                    if count >= 200:
                        lines.append("  …(truncated — too many files)")
                        break

        return "\n".join(lines) if lines else "No workspace files found."

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_safe_for_write(self, raw: str) -> Optional[Path]:
        """`_resolve_safe`, plus the rules that may not rewrite themselves.

        Review finding R3, 2026-08-16. `_resolve_safe` refuses the key store and
        the encrypted memory, and the comment in `protected_paths` says the
        backend directory is deliberately not a jail — right for notes and
        scratch code, and wrong for one specific set of files. The workspace
        roots include this repo, so a CONFIRM-tier `workspace_patch` could
        rewrite `governance.json`, `url_safety.py` or `shell_safety.py`. Every
        other guard in the project is downstream of those.

        Separate from `_resolve_safe` because these files are not secret and
        READING them is fine — JARVIS explaining its own rules is a feature.
        Only writing is refused.
        """
        resolved = self._resolve_safe(raw)
        if resolved is None:
            return None
        from modules.protected_paths import enforcement_write_problem

        if enforcement_write_problem(str(resolved)) is not None:
            print(f"[WORKSPACE] refused a write to enforcement code: "
                  f"{resolved.name}", flush=True)
            return None
        return resolved

    def _resolve_safe(self, raw: str) -> Optional[Path]:
        """
        Resolve `raw` to an absolute Path and verify it is inside at least
        one WORKSPACE_ROOT. Returns None if the path is outside all roots.

        Also refuses J.A.R.V.I.S.'s own key store and encrypted memory. Being
        inside a workspace root is NOT sufficient: the default roots are the
        repo and its parent, so `jarvis-backend/jarvis_key.dpapi` sits squarely
        within them, and `_BLOCKED_EXTENSIONS` covers `.exe` and `.zip` but not
        `.dpapi`, `.recovery`, `.enc`, `.db` or `.env`.

        Applied HERE rather than at each caller because this one function is
        what `read_file`, `write_file` and `patch_file` all funnel through — and
        a rule enforced at three call sites is a rule with a fourth call site in
        its future.
        """
        resolved = self._resolve_within_roots(raw)
        if resolved is None:
            return None
        # Checked on the RESOLVED path, not the raw string. A relative name is
        # resolved against a workspace ROOT here but against the CWD by
        # `protected_path_problem`, so testing the raw string would let
        # "jarvis-backend/jarvis_key.dpapi" through whenever the two differ.
        from modules.protected_paths import protected_path_problem

        if protected_path_problem(str(resolved)) is not None:
            print(f"[WORKSPACE] refused a protected path: {resolved.name}",
                  flush=True)
            return None
        return resolved

    @staticmethod
    def _resolve_within_roots(raw: str) -> Optional[Path]:
        """The original root check, unchanged — resolve and confirm containment."""
        try:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                # Try each workspace root as a base
                for root in WORKSPACE_ROOTS:
                    candidate = (root / p).resolve()
                    try:
                        candidate.relative_to(root)
                        return candidate
                    except ValueError:
                        continue
                return None
            p = p.resolve()
            for root in WORKSPACE_ROOTS:
                try:
                    p.relative_to(root)
                    return p
                except ValueError:
                    continue
            return None
        except Exception:
            return None

    @staticmethod
    def _fuzzy_hint(text: str, search: str, context_chars: int = 60) -> Optional[str]:
        """
        Return a nearby excerpt from `text` that resembles `search` so the
        LLM can correct its search string on a retry.
        """
        search_head = search[:20].strip()
        if not search_head:
            return None
        idx = text.lower().find(search_head.lower())
        if idx == -1:
            return None
        start = max(0, idx - 10)
        end   = min(len(text), start + context_chars)
        return text[start:end].replace("\n", "↵")
