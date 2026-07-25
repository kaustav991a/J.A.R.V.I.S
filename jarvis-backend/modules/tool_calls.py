"""tool_calls.py — provider-agnostic shape for a tool-calling turn.

Agentic core, phase 1 (roadmap §5 Tier C #12). The loop that will sit on top of
this (`modules/agent_core.py`, phase 2) must not care whether a turn came from
Groq, Gemini or OpenRouter, and must never be handed something ambiguous. So
every provider response is squeezed through `normalise_openai_message()` into one
`ToolTurn`, and every failure is an explicit `ok=False` rather than an empty
success — the same lesson as the ollama empty-200 fix: a silent nothing gets
narrated as "Done, Sir".

Deliberately dependency-free (json + dataclasses) so the whole normalisation
layer is harnessable with fake payloads, and so `agent_core` can import it
without dragging in `requests` or the Groq SDK.

All three tool-capable providers speak the OpenAI function-calling shape:
  * Groq — natively, through its SDK.
  * OpenRouter — natively, it IS an OpenAI-compatible gateway.
  * Gemini — through Google's OpenAI-compatibility endpoint, which is why we use
    that instead of translating to/from `FunctionDeclaration`.
Ollama is excluded from tool turns entirely (CPU-box tool-calling is slow and
unreliable, and a wrong tool call is worse than a slow answer).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Providers that can be asked for tools, in preference order. Groq first for
# latency, Gemini next (separate quota), OpenRouter as the aggregator tail.
TOOL_PROVIDERS = ("groq", "gemini", "openrouter")


@dataclass
class ToolCall:
    """One requested tool invocation, arguments already decoded where possible."""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    #: Set when the model emitted arguments we could not decode. The caller gets
    #: the raw string so it can attempt its ONE repair prompt instead of
    #: guessing — never silently treated as "no arguments".
    arguments_error: str | None = None
    raw_arguments: str | None = None

    @property
    def ok(self) -> bool:
        return self.arguments_error is None


@dataclass
class ToolTurn:
    """One assistant turn: some text, some tool calls, or an honest failure."""

    ok: bool = True
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    finish_reason: str | None = None
    error: str | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    @staticmethod
    def failed(reason: str, provider: str | None = None) -> "ToolTurn":
        """Explicit failure — `ok=False`, no text, no calls. Never mistakable for
        a successful turn in which the model simply chose not to call anything."""
        return ToolTurn(ok=False, error=reason, provider=provider)


def parse_arguments(raw: Any) -> tuple[dict, str | None]:
    """Decode a tool call's arguments. Returns `(args, error)`.

    OpenAI-shaped providers send arguments as a JSON *string*; some models send a
    dict, and weaker free models sometimes send truncated or fenced JSON. Only a
    dict is a valid argument object — a bare list or scalar is a model error, not
    something to coerce, because coercing invents a parameter name.
    """
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return dict(raw), None
    if not isinstance(raw, str):
        return {}, f"arguments must be an object, got {type(raw).__name__}"
    text = raw.strip()
    # Small models like to wrap JSON in a markdown fence even inside a tool call.
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except Exception as e:  # noqa: BLE001
        return {}, f"invalid JSON arguments: {e}"
    if not isinstance(parsed, dict):
        return {}, f"arguments must be an object, got {type(parsed).__name__}"
    return parsed, None


def normalise_openai_message(message: Any, *, provider: str | None = None,
                             model: str | None = None,
                             finish_reason: str | None = None) -> ToolTurn:
    """Turn one OpenAI-shaped assistant message into a `ToolTurn`.

    Accepts a plain dict (HTTP providers) or an SDK object with attributes
    (Groq), because both arrive here and neither should leak upward.
    """
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    if message is None:
        return ToolTurn.failed("provider returned no message", provider)

    text = _get(message, "content") or None
    if isinstance(text, str):
        text = text.strip() or None

    calls: list[ToolCall] = []
    for i, tc in enumerate(_get(message, "tool_calls") or []):
        fn = _get(tc, "function") or {}
        name = (_get(fn, "name") or "").strip()
        raw_args = _get(fn, "arguments")
        args, err = parse_arguments(raw_args)
        if not name:
            err = err or "tool call has no name"
        calls.append(ToolCall(
            id=str(_get(tc, "id") or f"call_{i}"),
            name=name,
            arguments=args,
            arguments_error=err,
            raw_arguments=raw_args if isinstance(raw_args, str) else None,
        ))

    if text is None and not calls:
        # An assistant turn with neither words nor an action is the empty-200
        # failure wearing a different hat: escalate instead of "succeeding".
        return ToolTurn.failed("provider returned an empty assistant turn", provider)

    return ToolTurn(ok=True, text=text, tool_calls=calls, provider=provider,
                    model=model, finish_reason=finish_reason)


def normalise_openai_response(data: Any, *, provider: str | None = None,
                              model: str | None = None) -> ToolTurn:
    """Normalise a whole `chat/completions` response body (dict or SDK object)."""
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    if data is None:
        return ToolTurn.failed("provider returned no response", provider)
    err = _get(data, "error")
    if err:
        return ToolTurn.failed(f"provider error: {err}", provider)
    choices = _get(data, "choices") or []
    if not choices:
        return ToolTurn.failed("provider returned no choices", provider)
    first = choices[0]
    return normalise_openai_message(
        _get(first, "message"),
        provider=provider,
        model=model or _get(data, "model"),
        finish_reason=_get(first, "finish_reason"),
    )


# --- messages the loop has to build back up ------------------------------- #

def assistant_message(turn: ToolTurn) -> dict:
    """The assistant turn, re-serialised for the next request's history.

    The tool-call ids must survive verbatim: a provider rejects a `tool` result
    whose `tool_call_id` it never issued.
    """
    msg: dict[str, Any] = {"role": "assistant", "content": turn.text or ""}
    if turn.tool_calls:
        msg["tool_calls"] = [{
            "id": c.id,
            "type": "function",
            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
        } for c in turn.tool_calls]
    return msg


def tool_result_message(call: ToolCall | str, content: Any) -> dict:
    """A tool's output, in the shape every OpenAI-compatible provider expects."""
    call_id = call.id if isinstance(call, ToolCall) else str(call)
    if not isinstance(content, str):
        try:
            content = json.dumps(content)
        except Exception:  # noqa: BLE001
            content = str(content)
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def validate_tool_defs(tools: Any) -> list[str]:
    """Cheap structural check of a tool list, returning human-readable problems.

    Catching a malformed registry here beats a 400 from the provider mid-task,
    and beats a weak model hallucinating around a tool whose schema never loaded.
    """
    problems: list[str] = []
    if not isinstance(tools, list) or not tools:
        return ["tools must be a non-empty list"]
    seen: set[str] = set()
    for i, t in enumerate(tools):
        if not isinstance(t, dict):
            problems.append(f"tool[{i}] is not an object")
            continue
        if t.get("type") != "function":
            problems.append(f"tool[{i}] type must be 'function'")
        fn = t.get("function")
        if not isinstance(fn, dict):
            problems.append(f"tool[{i}] has no function block")
            continue
        name = fn.get("name")
        if not name or not isinstance(name, str):
            problems.append(f"tool[{i}] has no name")
        elif name in seen:
            problems.append(f"duplicate tool name '{name}'")
        else:
            seen.add(name)
        params = fn.get("parameters")
        if params is not None and not isinstance(params, dict):
            problems.append(f"tool '{name}' parameters must be a JSON schema object")
    return problems
