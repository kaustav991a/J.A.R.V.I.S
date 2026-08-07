r"""agent_files.py — the file discipline the agent loop was missing.

§6.8.1 gaps C, D, E, F, G (rules 3, 4, 5, 7, 8 of AGENT-TOOLING-REFERENCE.md).
Five holes, one module, because they all concern the same three tools and share
one piece of state — the read ledger.

  C (rule 5)  `workspace_read` returned raw content. No line numbers, so the
              model could not cite `file:line` and invented them instead.
  D (rule 8)  It read the WHOLE file and the loop truncated at 4 000 characters.
              Announced, but with no way to continue — a 5 000-line file was
              permanently unreadable past its first few KB. Worse,
              `workspace_agent.read_file` refuses a file over 80 KB outright
              with "Consider reading a specific line range" — advice for a
              parameter that did not exist.
  E (rule 3)  `workspace_write` overwrote blind. The agent could destroy a file
              it had never read, or clobber a change made since it read one.
  F (rule 4)  `patch_file` defaults to `count=0`, meaning REPLACE EVERY
              OCCURRENCE, and `_workspace_patch` never passes a count. A
              "surgical" edit silently rewrote every match in the file.
  G (rule 7)  Paths were accepted "absolute or workspace-relative", so the same
              string resolved differently depending on which tool received it.
              This bit us live on 2026-07-26: the model passed `.claude.json`,
              it resolved against a different root, and the tool reported
              "File not found" for a file that exists.

NOTHING HERE DOES FILE I/O
--------------------------
`modules/workspace_agent.py` keeps every byte of that: the sandbox roots, the
binary-extension block, the size caps. It is the strong part and it is not
touched. This module only decides **what may be attempted** (preconditions) and
**how the result is shaped** (anchors and paging), which is exactly the split
rule 3 and rule 10 ask for.

WHY THE LEDGER IS PROCESS-WIDE AND NOT PER-RUN
----------------------------------------------
A single ledger keyed by resolved path, shared by every agent run in the
process. Per-run state would mean a file read in step 2 of a delegated sub-run
is "unread" to the parent that spawned it, and the model would be told to
re-read something it just read — advice that is wrong, which is worse than
advice that is missing. The staleness check (mtime) is what keeps a
process-wide ledger honest: a stale entry is caught by the clock, not by scope.
"""

from __future__ import annotations

import os
import threading
from typing import Any

__all__ = [
    "ReadLedger", "ledger", "number_lines", "paginate_read",
    "absolute_path_problem", "write_precondition", "edit_precondition",
    "build_patch_target", "DEFAULT_READ_LIMIT", "MAX_READ_LIMIT",
]

#: Lines returned when the model does not ask for a window. Deliberately smaller
#: than the loop's 4 000-character output cap, so a default read is complete
#: rather than complete-then-truncated: the model sees a whole window and an
#: honest "call again with offset=N", instead of a sentence cut mid-word.
DEFAULT_READ_LIMIT = 200
MAX_READ_LIMIT = 2000

#: The header `workspace_agent.read_file` puts above every file. It is real
#: information (path, line count, size) but it is NOT part of the file, so the
#: line numbering has to start after it or every citation is off by three.
_HEADER_RULE = "─" * 60


class ReadLedger:
    """Which paths have been read, and what their mtime was at the time.

    This is rule 3 made concrete: the precondition lives in code, so it cannot
    decay the way a system-prompt instruction does over a long session.
    """

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def key(path: str) -> str:
        """Normalise a path so two spellings of one file are one entry.

        Case-folded on Windows, where `F:\\work\\X.py` and `f:/work/x.py` are the
        same file and a case-sensitive ledger would demand a second read of a
        file already in context.
        """
        try:
            resolved = os.path.realpath(os.path.abspath(str(path)))
        except Exception:  # noqa: BLE001
            resolved = str(path)
        return os.path.normcase(resolved)

    def mark_read(self, path: str) -> None:
        with self._lock:
            self._seen[self.key(path)] = self._mtime(path)

    def has_read(self, path: str) -> bool:
        with self._lock:
            return self.key(path) in self._seen

    def read_mtime(self, path: str) -> float | None:
        with self._lock:
            return self._seen.get(self.key(path))

    def forget(self, path: str) -> None:
        with self._lock:
            self._seen.pop(self.key(path), None)

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except Exception:  # noqa: BLE001 — a file that does not exist yet reads
            return 0.0     # as mtime 0, which is younger than any real stamp.

    def staleness_problem(self, path: str) -> str | None:
        """Has the file changed on disk since it was read? Instruction or None."""
        recorded = self.read_mtime(path)
        if recorded is None:
            return None
        current = self._mtime(path)
        # A whole-second tolerance: some filesystems (and every network share)
        # round mtime, and a spurious "it changed" would block a legitimate edit
        # with a complaint the model cannot act on.
        if current - recorded > 1.0:
            return (f"'{path}' has changed on disk since you read it. "
                    "Read it again, then reapply your change against the current "
                    "content — the edit you planned may no longer fit.")
        return None


#: Process-wide instance. See the module docstring for why it is not per-run.
ledger = ReadLedger()


# ── rule 7: absolute paths, no ambient state ─────────────────────────────────

def absolute_path_problem(path: Any) -> str | None:
    """Refuse a relative path, with the reason and the fix. §6.8.1 gap G.

    The value of this check is not tidiness. A relative path is resolved against
    a *different* root by different tools, so the same string succeeds in one
    tool and fails in another — which reads to a model as a flaky filesystem and
    sends it looping.
    """
    if not isinstance(path, str) or not path.strip():
        return ("A file path is required, as an absolute path "
                r"(for example F:\work\project\notes.md).")
    text = path.strip()
    # Three accepted shapes, and the last two are why `os.path.isabs` alone is
    # not enough on Windows:
    #   * whatever the platform calls absolute;
    #   * a drive-qualified path (`F:\…`, `F:/…`);
    #   * a rooted or UNC path (`/etc/…`, `\\server\share`) — Python 3.13
    #     changed `ntpath.isabs` so a single leading slash is NO LONGER absolute,
    #     and a path JARVIS would happily read must not be refused because the
    #     interpreter was upgraded.
    if (os.path.isabs(text)
            or (len(text) > 1 and text[1] == ":")
            or text[0] in "/\\"):
        return None
    return (f"'{text}' is a relative path, and tools here resolve relative paths "
            "against different roots — the same name can succeed in one tool and "
            "fail in another. Give the ABSOLUTE path. If you do not know it, use "
            "`find_file` or `list_directory` first; their output prints full "
            "paths you can pass on verbatim.")


# ── rules 5 + 8: anchors and paging ──────────────────────────────────────────

def _split_header(raw: str) -> tuple[str, str]:
    """Separate `workspace_agent`'s header from the file body."""
    marker = _HEADER_RULE
    index = raw.find(marker)
    if index == -1:
        return "", raw
    end = raw.find("\n", index)
    if end == -1:
        return raw, ""
    return raw[:end + 1], raw[end + 1:]


def number_lines(body: str, start: int = 1) -> str:
    """`cat -n` style anchors. Rule 5: a citation the model makes is then real."""
    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    width = max(4, len(str(start + len(lines) - 1)))
    return "\n".join(f"{start + i:{width}d}\t{line}" for i, line in enumerate(lines))


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def paginate_read(raw: Any, args: dict) -> Any:
    """Number and window a `workspace_read` result. §6.8.1 gaps C + D.

    Applied as the tool's `shape_output`, so it sees the call arguments — which
    is the whole reason paging is possible at all: `offset` and `limit` are
    arguments and the engine handler never sees them.

    Anything that is not a successful read (a refusal, a not-found, an error
    string) passes through untouched: those are already instructions, and
    numbering their lines would be nonsense.
    """
    if not isinstance(raw, str) or _HEADER_RULE not in raw:
        return raw

    header, body = _split_header(raw)
    offset = _clamp_int(args.get("offset"), 0, 0, 10_000_000)
    limit = _clamp_int(args.get("limit"), DEFAULT_READ_LIMIT, 1, MAX_READ_LIMIT)

    lines = body.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)

    if offset >= total and total:
        # Announced, and actionable — rule 8 again. An empty result here would
        # read as "the file ends at line N", which is a different claim.
        return (f"{header}"
                f"[offset={offset} is past the end of this file — it has {total} "
                f"lines. Call again with a smaller offset, or offset=0 to start.]")

    window = lines[offset:offset + limit]
    numbered = number_lines("\n".join(window), start=offset + 1)
    shown_to = offset + len(window)
    remaining = total - shown_to

    out = f"{header}{numbered}"
    if remaining > 0:
        # Truncation is ALWAYS announced AND always continuable. The old path
        # announced it and offered no way forward, which is only half the rule.
        out += (f"\n\n[Showing lines {offset + 1}-{shown_to} of {total}. "
                f"{remaining} more line(s) below — call this tool again with "
                f"offset={shown_to} to continue.]")
    elif offset:
        out += f"\n\n[Showing lines {offset + 1}-{shown_to} of {total} — end of file.]"
    return out


# ── rule 3: read before you write ────────────────────────────────────────────

def write_precondition(args: dict) -> str | None:
    """Refuse a blind overwrite of an existing file. §6.8.1 gap E.

    A file that does not exist yet needs no prior read — creating one destroys
    nothing. Overwriting one that does exist, without having read it, is how an
    agent silently drops the parts of a file it did not think to re-emit.
    """
    path = args.get("path")
    if problem := absolute_path_problem(path):
        return problem
    path = str(path).strip()

    if not os.path.exists(path):
        return None                      # creating a new file: nothing at risk
    if not ledger.has_read(path):
        return (f"'{path}' already exists and you have not read it in this "
                "session. Read it first, then write — a whole-file write "
                "replaces everything, and anything you did not re-emit would be "
                "lost. If you only need to change part of it, use the edit tool "
                "instead of rewriting the file.")
    return ledger.staleness_problem(path)


def edit_precondition(args: dict) -> str | None:
    """Read-before-edit, staleness, and UNIQUENESS. §6.8.1 gaps E + F.

    The uniqueness half is rule 4, and it is the one that changes behaviour most:
    `workspace_agent.patch_file` replaces EVERY occurrence by default and
    `_workspace_patch` never passes a count, so an ambiguous edit used to land
    everywhere silently. Here an ambiguous edit cannot be attempted at all
    unless the model says explicitly that it means all of them.
    """
    path = args.get("path")
    if problem := absolute_path_problem(path):
        return problem
    path = str(path).strip()

    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or old == "":
        return ("`old_string` must be the exact text to replace, and cannot be "
                "empty — an empty match would apply everywhere.")
    if old == new:
        return "`old_string` and `new_string` are identical — nothing to change."

    if not os.path.exists(path):
        return (f"'{path}' does not exist, so there is nothing to edit. "
                "Check the path, or write the file instead of editing it.")
    if not ledger.has_read(path):
        return (f"You have not read '{path}' in this session. Read it first — an "
                "exact-match edit against text you have not seen is a guess.")
    if problem := ledger.staleness_problem(path):
        return problem

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError as exc:
        return (f"Could not open '{path}' to check the edit: {exc}. "
                "Confirm the path, then try again.")

    occurrences = content.count(old)
    if occurrences == 0:
        return (f"`old_string` does not appear in '{path}'. The commonest cause "
                "is copying the line-number prefix out of the read output — strip "
                "it and match the file's own text, including its exact "
                "indentation.")
    if occurrences > 1 and not args.get("replace_all"):
        # Rule 4: the ambiguous edit is structurally impossible, not discouraged.
        return (f"`old_string` matches {occurrences} places in '{path}' and must "
                "be unique. Include more surrounding context so it identifies one "
                f"location — or pass replace_all: true if all {occurrences} should "
                "change.")
    return None


def build_patch_target(args: dict) -> str:
    r"""Compose `action_engine`'s "path|search|replace" target for an edit.

    `_workspace_patch` splits on `|` with `maxsplit=2`, so a pipe inside the
    REPLACEMENT is safe (it lands in the third field) while a pipe inside the
    SEARCH string would silently shift the fields and edit the wrong text. That
    is checked in `edit_precondition`'s caller schema rather than repaired here:
    building a target is not the place to discover the arguments are unusable.
    """
    return (f"{str(args.get('path', '')).strip()}|"
            f"{args.get('old_string', '')}|{args.get('new_string', '')}")


def note_read(args: dict, output: Any) -> None:
    """Ledger hook: record a read that actually produced file content.

    Deliberately NOT recorded when the result is a refusal or a not-found — a
    ledger entry for a file that was never delivered would satisfy the
    read-before-write check without the model having seen a single byte.
    """
    if not isinstance(output, str) or _HEADER_RULE not in output:
        return
    path = args.get("path")
    if isinstance(path, str) and path.strip():
        ledger.mark_read(path.strip())
