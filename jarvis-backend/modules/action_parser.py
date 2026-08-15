"""Canonical LLM-reply → action(s) extraction for J.A.R.V.I.S.

THE single source of truth for turning a raw LLM reply into executable actions.
Every dispatch path — voice/HUD (main.py), remote/Telegram (run_remote_command),
the ReAct planner, and the streaming daemon — must route through here.

Why this exists
---------------
Before this module there were four independent, incompatible parsers:
  * main.py               expected  {"actions": [ {action_type, target}, ... ]}
  * streaming_daemon.py   expected  a bare {action_type, target}
  * planner.py            expected  a singular {action: {...}}
  * brain.py stub         called raw json.loads() with no fence-strip
The SAME model output would therefore execute on one path and be spoken aloud as
literal JSON (or silently dropped) on another — the root of the "sometimes it
works, sometimes it doesn't" behaviour.

This parser is deliberately forgiving. It accepts every shape the model actually
emits in the wild and normalises them to one canonical list of action dicts:
  * {"actions": [ ... ]}                         (brain contract)
  * {"action_type": "...", "target": "..."}      (bare single action)
  * {"action": {"action_type": "..."}}           (planner ReAct shape)
  * [ {"action_type": "..."}, ... ]              (top-level array)
It tolerates code fences (```json / ```JSON / ```), leading/trailing prose,
double-encoded (quoted) JSON, trailing commas, and truncation (unclosed braces
from the model hitting max_tokens). If it finds no action JSON at all, it reports
the reply as purely conversational — the caller then just speaks it.

Design rule: NEVER raise. A parser fault must degrade to "conversational reply",
never crash a dispatch path.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# action_type aliases — common model typos / synonyms → canonical action name.
# The model occasionally emits a near-miss (e.g. "websearch" for "web_search");
# without remapping these silently no-op at the engine. Keep this conservative:
# only map unambiguous synonyms, never guess.
# ---------------------------------------------------------------------------
_ACTION_ALIASES: dict[str, str] = {
    "websearch": "web_search",
    "web-search": "web_search",
    "search_web": "web_search",
    "searchweb": "web_search",
    "tavilysearch": "tavily_search",
    "tavily": "tavily_search",
    "tavily_lookup": "tavily_search",
    "search_image": "web_search_image",
    "websearchimage": "web_search_image",
    "openapp": "native_app_launcher",
    "open_app": "native_app_launcher",
    "launch_app": "native_app_launcher",
    "app_launcher": "native_app_launcher",
    "type_text": "ghost_type",
    "ghosttype": "ghost_type",
    "save_file": "ghost_save_file",
    "readfile": "workspace_read",
    "writefile": "workspace_write",
    "runcommand": "run_terminal_command",
    "run_command": "run_terminal_command",
    "terminal": "run_terminal_command",
}

_SENTINEL = object()


@dataclass
class ParsedReply:
    """Result of parsing one LLM reply.

    actions:   normalised list of action dicts (each has a truthy 'action_type').
               Empty  ⇒  the reply is purely conversational.
    preamble:  any prose the model emitted *before* the JSON (already fence-clean).
               Some callers speak this as the lead-in line.
    is_action: convenience — True iff `actions` is non-empty.
    """

    actions: list[dict] = field(default_factory=list)
    preamble: str = ""

    @property
    def is_action(self) -> bool:
        return bool(self.actions)


# ---------------------------------------------------------------------------
# Fence / quote stripping
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*")


def strip_fences(text: str) -> str:
    """Remove markdown code fences (```json, ```JSON, ```py, or bare ```)
    anywhere in the string, case-insensitive, leaving the fenced content."""
    if not text:
        return ""
    # `re.sub` removes '```json' / '```py' / etc.; a trailing bare '```' also
    # matches the pattern (```+ zero trailing chars), so one pass is enough.
    return _FENCE_RE.sub("", text)


def _unwrap_quotes(text: str) -> str:
    """If the whole reply is a double-encoded JSON string (some transports
    double-serialise), decode it once so the inner JSON is exposed."""
    t = text.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        try:
            inner = json.loads(t)
            if isinstance(inner, str):
                return inner
        except (json.JSONDecodeError, ValueError):
            # Best-effort manual unescape.
            return t[1:-1].replace('\\"', '"').replace("\\n", "\n")
    return text


# ---------------------------------------------------------------------------
# Balanced JSON span finder — respects string literals & escapes so that braces
# inside a string value (e.g. code passed to workspace_write) don't confuse it.
# ---------------------------------------------------------------------------
def _find_json_span(text: str) -> Optional[str]:
    """Return the first balanced {...} or [...] value in `text`, or, if the
    value is truncated (unbalanced), everything from the first opener onward so
    the healer can attempt to close it. Returns None if there's no opener."""
    start = -1
    open_ch = ""
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            open_ch = ch
            break
    if start == -1:
        return None

    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
    # Unbalanced (truncated by max_tokens) — hand the tail to the healer.
    return text[start:]


# ---------------------------------------------------------------------------
# Lenient load with healing
# ---------------------------------------------------------------------------
def _try_load(s: str) -> Any:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return _SENTINEL


def _close_truncated(s: str) -> str:
    """Append exactly the closers needed to balance a value truncated by the
    model hitting max_tokens: terminate an unterminated string, then close every
    still-open object/array in LIFO order. String-aware so braces inside string
    values aren't miscounted."""
    stack: list[str] = []
    in_str = False
    esc = False
    for c in s:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            stack.append("}")
        elif c == "[":
            stack.append("]")
        elif c in "}]" and stack:
            stack.pop()
    suffix = ('"' if in_str else "") + "".join(reversed(stack))
    return s + suffix


def heal_and_load(span: str, report: bool = False):
    """json.loads with progressive repair for the failure modes 8B/70B models
    actually produce: trailing commas and truncation (unclosed strings/brackets).
    Returns the parsed object, or None if unrecoverable.

    With `report=True` returns `(obj, truncated)` instead, where `truncated` is
    True only when the value had to be CLOSED to parse — i.e. the model was cut
    off mid-value. Callers need that distinction because a healed truncation
    changes what a value MEANS, where a stripped trailing comma does not.
    """
    def _out(obj, truncated=False):
        return (obj, truncated) if report else obj

    if not span:
        return _out(None)

    obj = _try_load(span)
    if obj is not _SENTINEL:
        return _out(obj)

    # 1) Strip trailing commas before a closer:  {"a":1,}  →  {"a":1}
    #    Lossless: nothing about any value changes.
    fixed = re.sub(r",\s*([}\]])", r"\1", span)
    obj = _try_load(fixed)
    if obj is not _SENTINEL:
        return _out(obj)

    # 2) Truncation repair — close the unterminated string and every open
    #    bracket at the exact depth the model was cut off at.
    #    NOT lossless: the value that was being written is now whatever prefix
    #    survived. See `_TRUNCATION_UNSAFE` for why that is reported upward.
    obj = _try_load(_close_truncated(fixed))
    if obj is not _SENTINEL:
        return _out(obj, True)

    # 3) Same, but first drop a dangling trailing comma / partial key fragment.
    trimmed = re.sub(r",\s*$", "", fixed.rstrip())
    obj = _try_load(_close_truncated(trimmed))
    if obj is not _SENTINEL:
        return _out(obj, True)

    return _out(None)


# Actions whose TARGET is a thing in the world that gets destroyed or replaced.
# A truncated target is not a broken target — it is a DIFFERENT, valid one, and
# for a path it is a PARENT:
#
#     {"action_type":"delete_file","target":"C:\\Users\\K\\Docs\\Project\\a.txt
#
# cut off by max_tokens heals to  ...\\Docs\\Project"  — and `_delete_file`
# calls shutil.rmtree on a directory. The action succeeds, reports success, and
# removed the wrong thing. That is the failure shape this project keeps finding:
# indistinguishable from working.
#
# So these are refused when, and only when, the JSON had to be CLOSED to parse.
# A trailing-comma repair is lossless and stays allowed, as does a truncated
# non-destructive action — speaking a half-finished search is harmless.
_TRUNCATION_UNSAFE = frozenset({
    "delete_file", "workspace_write", "workspace_patch", "ghost_save_file",
    "close_app", "kill_process", "move_file", "rename_file", "empty_recycle_bin",
})


def _drop_unsafe_truncated(actions: list) -> list:
    """Remove destructive actions whose JSON was repaired from a truncation."""
    kept = []
    for a in actions:
        atype = str((a or {}).get("action_type") or "").lower()
        # the named set, plus anything that reads as a removal — the registry
        # grows and a list nobody updates is the thing that fails quietly
        if atype in _TRUNCATION_UNSAFE or "delete" in atype or "remove" in atype:
            print(f"[ACTION PARSER] refusing truncated '{atype}': the reply was cut "
                  "off mid-value, so its target is a prefix of what was meant.",
                  flush=True)
            continue
        kept.append(a)
    return kept


# ---------------------------------------------------------------------------
# Schema normalisation
# ---------------------------------------------------------------------------
def _canonical_action_type(atype: Any) -> str:
    at = str(atype or "").strip()
    return _ACTION_ALIASES.get(at.lower(), at)


def _clean_action(a: dict) -> Optional[dict]:
    """Return a copy of an action dict with a canonical action_type, or None if
    it has no usable action_type."""
    if not isinstance(a, dict):
        return None
    at = _canonical_action_type(a.get("action_type"))
    if not at:
        return None
    out = dict(a)
    out["action_type"] = at
    return out


def normalize_to_actions(obj: Any) -> list[dict]:
    """Flatten every shape the model emits into a list of action dicts."""
    if obj is None:
        return []

    # Top-level array of actions.
    if isinstance(obj, list):
        return [c for c in (_clean_action(a) for a in obj) if c]

    if isinstance(obj, dict):
        # Brain contract: {"actions": [...]}
        acts = obj.get("actions")
        if isinstance(acts, list):
            return [c for c in (_clean_action(a) for a in acts) if c]
        # Bare single action: {"action_type": "...", ...}
        if obj.get("action_type"):
            c = _clean_action(obj)
            return [c] if c else []
        # Planner ReAct shape: {"action": {"action_type": "..."}}
        act = obj.get("action")
        if isinstance(act, dict) and act.get("action_type"):
            c = _clean_action(act)
            return [c] if c else []

    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse(raw: str) -> ParsedReply:
    """Parse one raw LLM reply into a ParsedReply. Never raises."""
    if not raw or not isinstance(raw, str):
        return ParsedReply()

    try:
        text = _unwrap_quotes(strip_fences(raw)).strip()
        span = _find_json_span(text)
        if not span:
            return ParsedReply(actions=[], preamble=text)

        obj, truncated = heal_and_load(span, report=True)
        actions = normalize_to_actions(obj)
        if truncated and actions:
            # The reply was cut off mid-value. Anything destructive is now
            # pointing at a prefix of its real target, so it is dropped rather
            # than executed — an empty list is spoken, never actioned.
            actions = _drop_unsafe_truncated(actions)

        # Prose the model wrote before the JSON block (rare but happens when
        # json_mode is off). Cleaned of the fence markers already.
        idx = text.find(span[:1]) if span else -1
        preamble = text[:idx].strip() if idx > 0 else ""

        return ParsedReply(actions=actions, preamble=preamble)
    except Exception:
        # Absolute belt-and-braces: a parser fault must never crash dispatch.
        return ParsedReply(actions=[], preamble=(raw or "").strip())


def extract_actions(raw: str) -> list[dict]:
    """Convenience: just the normalised action list (empty ⇒ conversational)."""
    return parse(raw).actions


def extract_react_decision(raw: str) -> Optional[dict]:
    """Planner helper: return the healed decision dict as-is (preserving
    'thought' / 'final_answer' / 'action'), not normalised to actions. Uses the
    same fence-strip + healing so the planner stops aborting on recoverable
    JSON. Returns None only when nothing JSON-like is present."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        text = _unwrap_quotes(strip_fences(raw)).strip()
        span = _find_json_span(text)
        if not span:
            return None
        obj = heal_and_load(span)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None
