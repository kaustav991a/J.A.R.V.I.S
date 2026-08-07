r"""agent_errors.py — turn an exception into an instruction the model can act on.

§6.8.1 gap B (rule 6 of AGENT-TOOLING-REFERENCE.md). The loop used to hand the
model this:

    ERROR: FileNotFoundError: 'notes.md'

which answers "what happened" and not "what do I do now". A frontier model
infers the next move anyway; a free-tier Groq model retries the identical call
until the consecutive-error cap kills the run. That cap firing is the symptom
this module removes the cause of.

THE RULE, stated once so every branch below can be checked against it:
**every message must name a next action the model can actually take**, using the
tools it currently holds. "The file does not exist" is a status. "The file does
not exist — call `find_file` with the name, or `list_directory` on the parent"
is an instruction.

WHAT THIS DOES NOT DO
---------------------
It does not swallow, rewrite or soften the original error. The underlying text
is always present in the output, because the loop's own audit trail
(`ToolRun.error`) and the owner-facing summary both read it, and a paraphrase
would make a real failure harder to diagnose than the raw one. This only APPENDS
the next move.

It also does not decide whether the run continues — that stays in `agent_core`'s
consecutive-error accounting. A better error message must never quietly change
which failures are fatal.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["explain", "REFUSAL_MARKERS"]

#: Prefixes `action_engine`/`workspace_agent` use for a REFUSAL rather than a
#: fault. These are terminal for the attempted path: retrying the same call
#: cannot succeed, so the instruction has to be "stop", not "try again".
REFUSAL_MARKERS = (
    "GOVERNANCE_BLOCKED:", "GOVERNANCE_CONFIRM:", "TIER_BLOCKED:",
    "Access denied", "Read refused", "Write refused", "Patch refused",
)


def _tool_names(available: Any) -> set[str]:
    """Normalise whatever the loop knows about its tool set into plain names."""
    names: set[str] = set()
    for t in available or ():
        if isinstance(t, str):
            names.add(t)
        elif isinstance(t, dict):
            fn = t.get("function")
            name = (fn or {}).get("name") if isinstance(fn, dict) else t.get("name")
            if name:
                names.add(str(name))
    return names


def _suggest(candidates: list[str], available: set[str]) -> str:
    """Name only tools the model actually holds this run.

    Pointing a model at `find_file` when the current intent set does not include
    it produces one wasted step and an "unknown tool" repair — which is the
    budget this module is trying to save.
    """
    usable = [c for c in candidates if not available or c in available]
    if not usable:
        return ""
    if len(usable) == 1:
        return f"Try `{usable[0]}`."
    return "Try " + " or ".join(f"`{u}`" for u in usable) + "."


def _missing_path(text: str) -> str | None:
    """Pull the path out of a not-found message, for a concrete instruction."""
    match = re.search(r"[Ff]ile not found:?\s*(.+)", text)
    if match:
        return match.group(1).strip().strip("'\"")
    match = re.search(r"\[Errno 2\][^:]*:\s*'([^']+)'", text)
    return match.group(1) if match else None


def explain(exc: BaseException, call: Any = None, *, available: Any = None) -> str:
    """One instruction-shaped line (or two) for a tool that blew up.

    `call` is the `ToolCall` that failed, `available` the tool list for this run
    — both optional, both used only to make the advice concrete rather than
    generic.
    """
    raw = str(exc).strip() or exc.__class__.__name__
    tools = _tool_names(available)
    name = getattr(call, "name", None)

    # A refusal by a gate. The distinguishing property is that retrying is
    # pointless, so the instruction must not invite it — this is the failure
    # mode that burned a whole run live on 2026-07-26, when a sandbox refusal
    # came back as ordinary data and the model tried three more roots.
    if any(raw.startswith(m) or m in raw[:80] for m in REFUSAL_MARKERS):
        return (f"{raw}\n"
                "This was REFUSED by a security boundary, not a transient fault. "
                "Retrying it — or trying a nearby path — will be refused too. "
                "Use a permitted location, or tell the owner what you need and why.")

    # An HONEST failure from the engine: `ToolFailure` carries the sentence the
    # OWNER would have heard, and `agent_core` deliberately keeps that wording.
    # Duck-typed rather than imported, because `agent_core` imports this module
    # and a cycle here would break the loop it is meant to improve. The class
    # name is NOT prefixed: "ToolFailure:" in front of a butler's sentence is
    # exactly the leak the phase-2 discipline removed.
    if type(exc).__name__ == "ToolFailure":
        return (f"{raw}\n"
                "That is the tool's own account of what went wrong. Do not repeat "
                "the identical call — change the approach, or tell the owner what "
                "is blocking you.")

    if isinstance(exc, FileNotFoundError) or "not found" in raw.lower():
        path = _missing_path(raw) or getattr(call, "arguments", {}).get("path")
        where = f" '{path}'" if path else ""
        # `str(FileNotFoundError("missing.txt"))` is just "missing.txt" — a
        # headline of a bare filename says nothing. Restore the diagnosis when
        # the raw text does not already carry one.
        headline = raw if "not found" in raw.lower() else f"File not found: {raw}"
        hint = _suggest(["find_file", "list_directory"], tools)
        return (f"{headline}\n"
                f"That path{where} does not exist, so do not read it again. "
                f"Locate the real one first. {hint}").strip()

    if isinstance(exc, PermissionError):
        return (f"{raw}\n"
                "Access to that path is not permitted. Do not retry it; choose a "
                "location inside the permitted roots.")

    if isinstance(exc, IsADirectoryError) or "is not a file" in raw.lower():
        hint = _suggest(["list_directory"], tools)
        return (f"{raw}\n"
                f"That is a directory, not a file. List it and read one of the "
                f"entries. {hint}").strip()

    if isinstance(exc, TimeoutError) or "timed out" in raw.lower():
        return (f"{raw}\n"
                "That took too long. Do not repeat the same call — narrow the "
                "request, or use a different tool to get the same fact.")

    if isinstance(exc, (KeyError, TypeError, ValueError)):
        which = f" for '{name}'" if name else ""
        return (f"{raw}\n"
                f"This looks like a malformed argument{which}. Re-read the tool's "
                "schema, correct the arguments, and call it once more.")

    # Unknown fault. Say plainly that the cause is not understood rather than
    # inventing a remedy — a confident wrong instruction is worse than none.
    which = f"`{name}` " if name else ""
    return (f"{exc.__class__.__name__}: {raw}\n"
            f"The {which}tool failed for a reason I cannot classify. Do not repeat "
            "the identical call — either change the arguments, use a different "
            "tool, or report what is blocking you.")
