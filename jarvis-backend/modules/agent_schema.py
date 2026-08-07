r"""agent_schema.py — validate a model's tool arguments against the tool's schema.

§6.8.1 gap A (rules 16 + 6 of AGENT-TOOLING-REFERENCE.md). Until this existed,
`ToolRegistry.authorizer` checked **presence only** — `missing_required`. A call
like `{"path": 42}` or `{"path": {"a": 1}}` passed authorisation, reached
`action_engine`, got stringified somewhere deep, and failed with a message that
pointed at the symptom rather than the cause. The model then had nothing to
correct against.

WHY THIS IS HAND-ROLLED AND NOT `jsonschema`
--------------------------------------------
The reference implementation uses `Draft202012Validator`. This does not, for two
reasons that outrank the convenience:

  * **No new dependency.** This tree pins `protobuf==6.33.6` and treats its
    dependency set as load-bearing; adding a package to validate ten small
    schemas is a poor trade. Everything here is stdlib.
  * **The error text IS the feature.** `jsonschema`'s messages are written for
    developers reading a stack trace ("42 is not of type 'string'"). Rule 6 wants
    a message written for a *model deciding what to do next*, naming the tool,
    the argument, what was expected, what arrived, and the corrective action.
    Wrapping a library to rewrite every message is more code than this file.

Supports the subset the registry actually authors: `type`, `enum`, `required`,
`properties`, `additionalProperties`, `items`, and the numeric/length bounds.
Anything it does not understand is IGNORED rather than rejected — an unknown
keyword must never turn a valid call into a refusal.

DELIBERATELY LENIENT IN ONE DIRECTION
-------------------------------------
Extra arguments are allowed unless the schema says `additionalProperties: false`.
That mirrors JSON Schema's own default and, more importantly, it means adding
this validator could not retroactively refuse a call that worked yesterday. The
one thing it is strict about is the thing that was silently broken: types.

`missing_required` stays in `agent_tools` and is NOT duplicated here — it already
encodes a deliberate rule (an empty string counts as absent) and the authorizer
runs it first, so its wording keeps priority for the most common failure.
"""

from __future__ import annotations

from typing import Any

__all__ = ["validate_arguments", "describe_schema", "TYPE_NAMES"]

#: JSON Schema type name -> the Python types that satisfy it.
#:
#: `bool` is excluded from "integer"/"number" on purpose: in Python `True` IS an
#: `int`, so a model sending `{"limit": true}` would otherwise validate as a
#: number and reach the handler as `1`. That is exactly the class of silent
#: coercion this module exists to stop.
_PY_TYPES: dict[str, tuple] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list, tuple),
    "null": (type(None),),
}

TYPE_NAMES = tuple(_PY_TYPES)


def _type_of(value: Any) -> str:
    """The JSON Schema type name for a Python value — for error messages."""
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    allowed = _PY_TYPES.get(expected)
    if allowed is None:
        return True                      # unknown keyword: not our business
    if expected in ("integer", "number") and isinstance(value, bool):
        return False                     # see the note on _PY_TYPES
    return isinstance(value, allowed)


def _preview(value: Any, limit: int = 60) -> str:
    """What the model actually sent, short enough to sit in an error line."""
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def describe_schema(schema: dict) -> str:
    """A one-line rendering of a schema, for pasting into an error message.

    Rule 6: the error carries the schema AND what was sent, so the model can
    diff them itself instead of guessing at the shape a second time.
    """
    props = (schema or {}).get("properties")
    if not isinstance(props, dict) or not props:
        return "{} (this tool takes no arguments)"
    required = set((schema or {}).get("required") or [])
    parts = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        kind = spec.get("type", "any")
        if isinstance(spec.get("enum"), list):
            kind = "|".join(repr(v) for v in spec["enum"])
        parts.append(f"{name}: {kind}" + ("" if name in required else " (optional)"))
    return "{" + ", ".join(parts) + "}"


def _check_value(tool: str, path: str, value: Any, spec: dict) -> list[str]:
    """Validate one value against one property schema. Returns problem strings."""
    problems: list[str] = []
    if not isinstance(spec, dict):
        return problems

    expected = spec.get("type")
    # A list of types ("string" or "null") is legal JSON Schema; accept any.
    if isinstance(expected, list):
        if expected and not any(_matches_type(value, str(t)) for t in expected):
            problems.append(
                f"'{path}' must be one of {expected}, but you sent a "
                f"{_type_of(value)}: {_preview(value)}")
            return problems
    elif isinstance(expected, str):
        if not _matches_type(value, expected):
            hint = ""
            if expected == "string" and isinstance(value, (int, float, bool)):
                # The single most common mid-tier-model mistake, and the fix is
                # mechanical — say it rather than leaving it to be inferred.
                hint = f" Send it quoted, as \"{value}\"."
            problems.append(
                f"'{path}' must be a {expected}, but you sent a "
                f"{_type_of(value)}: {_preview(value)}.{hint}")
            return problems     # every later check assumes the type is right

    choices = spec.get("enum")
    if isinstance(choices, list) and choices and value not in choices:
        problems.append(
            f"'{path}' must be one of {choices}, but you sent {_preview(value)}")

    if isinstance(value, str):
        low, high = spec.get("minLength"), spec.get("maxLength")
        if isinstance(low, int) and len(value) < low:
            problems.append(f"'{path}' must be at least {low} character(s); "
                            f"you sent {len(value)}")
        if isinstance(high, int) and len(value) > high:
            problems.append(f"'{path}' must be at most {high} character(s); "
                            f"you sent {len(value)}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        low, high = spec.get("minimum"), spec.get("maximum")
        if isinstance(low, (int, float)) and value < low:
            problems.append(f"'{path}' must be >= {low}; you sent {value}")
        if isinstance(high, (int, float)) and value > high:
            problems.append(f"'{path}' must be <= {high}; you sent {value}")

    items = spec.get("items")
    if isinstance(value, (list, tuple)) and isinstance(items, dict):
        for i, element in enumerate(value):
            problems.extend(_check_value(tool, f"{path}[{i}]", element, items))

    nested = spec.get("properties")
    if isinstance(value, dict) and isinstance(nested, dict):
        problems.extend(_validate_object(tool, path + ".", value, spec))

    return problems


def _validate_object(tool: str, prefix: str, args: dict, schema: dict) -> list[str]:
    problems: list[str] = []
    props = schema.get("properties")
    props = props if isinstance(props, dict) else {}

    for name, value in args.items():
        spec = props.get(name)
        if spec is None:
            # Unknown argument. Refused ONLY when the schema closes the object —
            # see the leniency note in the module docstring.
            if schema.get("additionalProperties") is False:
                known = ", ".join(props) or "(none)"
                problems.append(
                    f"'{prefix}{name}' is not an argument of this tool. "
                    f"Accepted arguments: {known}")
            continue
        problems.extend(_check_value(tool, f"{prefix}{name}", value, spec))
    return problems


def validate_arguments(tool_name: str, arguments: Any, schema: Any) -> str | None:
    """Validate one tool call's arguments. Returns an instruction, or None.

    The return is deliberately a **single ready-to-send string** rather than a
    list of problems: it goes straight back to the model as the tool result, and
    rule 6 says that text has to answer "what do I do now?". It names the tool,
    every problem, the full schema, and what was actually sent.

    Returns None when the arguments are acceptable — the caller treats None as
    "proceed", so a schema this module cannot understand can never block a call.
    """
    if not isinstance(schema, dict):
        return None
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return (f"Arguments for '{tool_name}' must be a JSON object, but you sent a "
                f"{_type_of(arguments)}: {_preview(arguments)}. "
                f"Expected: {describe_schema(schema)}")

    problems = _validate_object(tool_name, "", arguments, schema)
    if not problems:
        return None

    lines = [f"Invalid arguments for '{tool_name}':"]
    lines += [f"  - {p}" for p in problems]
    lines.append(f"Expected: {describe_schema(schema)}")
    lines.append(f"You sent: {_preview(arguments, 300)}")
    lines.append("Correct the arguments and call the tool once more.")
    return "\n".join(lines)
