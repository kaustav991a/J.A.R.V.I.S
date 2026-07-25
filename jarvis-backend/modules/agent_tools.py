"""agent_tools.py — which of JARVIS's ~103 actions the agent loop may call.

Agentic core, phase 3 (roadmap §5 Tier C #12). `action_engine` already knows how
to do everything; what it lacks is a *description* of itself a model can read.
This module is that description, plus the three adapters the loop needs:

    tool_defs(...)   -> OpenAI function schemas for a curated subset
    make_authorizer  -> governance tier -> agent_core.Decision (before EVERY call)
    make_executor    -> ToolCall -> action_engine payload -> honest result

Three deliberate constraints:

1. **Curated sets, never "all 103".** Weak/free models degrade sharply past ~8
   tools, so tools ship in named sets (`TOOL_SETS`) sized to the loop's cap. The
   registry is the thing responsible for curation — `agent_core` refuses an
   oversized set rather than trimming it silently.
2. **Governance is not re-implemented here.** `governance_manager.get_tier()` is
   the single source of truth, read through an injectable lookup. This module
   only decides what a tier *means to an unattended loop*: AUTO runs, CONFIRM is
   refused (nobody is at the keyboard to approve mid-loop — the loop reports what
   it wanted so the user can approve it as a normal command), BLOCK is refused.
3. **Sentinels are failures, not output.** `ActionEngine.execute` answers
   `GOVERNANCE_BLOCKED:`/`GOVERNANCE_CONFIRM:`/`TIER_BLOCKED:` as ordinary
   strings. Handing those to a model as a tool *result* would read as success —
   the exact false-"Done, Sir" failure this project keeps stamping out. They are
   raised instead, so the loop records a failed run.

Pure except for the injected engine: the schemas, the payload mapping and the
sentinel interpretation are all exercised with fakes in test_agent_tools.py.
"""

from __future__ import annotations

from typing import Any, Callable

from modules.agent_core import Decision
from modules.tool_calls import ToolCall

# Sentinel prefixes emitted by ActionEngine.execute (mirrored, not imported, so
# this module stays importable without the whole action stack).
SENTINELS = ("GOVERNANCE_BLOCKED:", "GOVERNANCE_CONFIRM:", "TIER_BLOCKED:",
             "Validation Error:")


def _spec(action_type: str, description: str, properties: dict,
          required: list[str] | None = None, target_from: str | None = None,
          build_target: Callable[[dict], Any] | None = None) -> dict:
    """One registry entry.

    `target_from` names the argument that becomes `payload["target"]` (the shape
    every action_engine handler expects); `build_target` is for the handlers with
    a composite target, e.g. workspace_write's "path|content".
    """
    return {
        "action_type": action_type,
        "description": description,
        "properties": properties,
        "required": required or list(properties.keys())[:1],
        "target_from": target_from or (list(properties.keys())[0] if properties else None),
        "build_target": build_target,
    }


_QUERY = {"query": {"type": "string", "description": "What to look for."}}

#: name -> spec. Names are the action_engine action_types, so the governance
#: ruleset and the trace logs line up with what the model asked for.
TOOL_SPECS: dict[str, dict] = {
    "tavily_search": _spec(
        "tavily_search",
        "Search the live web and get a synthesised answer. Best first choice for "
        "current facts, news, prices, scores.",
        _QUERY),
    "web_browse": _spec(
        "web_browse",
        "Open a specific URL and read the page. Use when a search result must be "
        "verified or a named page has to be read in full.",
        {"url": {"type": "string", "description": "Full URL, including https://"}}),
    "search_documents": _spec(
        "search_documents",
        "Search Kaustav's own indexed documents and notes. Use for anything "
        "personal that would not be on the public web.",
        _QUERY),
    "memory_recall": _spec(
        "memory_recall",
        "Recall facts JARVIS has been told before (preferences, people, past "
        "decisions). Check here before claiming something is unknown.",
        _QUERY),
    "workspace_read": _spec(
        "workspace_read",
        "Read a file from the workspace so its exact contents are in context.",
        {"path": {"type": "string", "description": "File path, absolute or workspace-relative."}}),
    "list_directory": _spec(
        "list_directory",
        "List the contents of a directory (read-only, sandboxed to the user's home).",
        {"path": {"type": "string", "description": "Directory path."}}),
    "find_file": _spec(
        "find_file",
        "Locate a file by name when its directory is unknown.",
        {"name": {"type": "string", "description": "File name or fragment."}}),
    "system_status": _spec(
        "system_status",
        "Current machine telemetry: CPU, memory, disk, battery.",
        {}, required=[]),
    "read_screen": _spec(
        "read_screen",
        "Describe what is currently on Kaustav's screen. Use only when the answer "
        "depends on what he is looking at.",
        {}, required=[]),
    "workspace_write": _spec(
        "workspace_write",
        "Create or overwrite a workspace file. Requires the owner's confirmation.",
        {"path": {"type": "string", "description": "File path to write."},
         "content": {"type": "string", "description": "Full file contents."}},
        required=["path", "content"],
        build_target=lambda a: f"{a.get('path', '')}|{a.get('content', '')}"),
}

#: Curated per-intent sets. Each stays at or under `AgentLimits.max_tools`.
TOOL_SETS: dict[str, list[str]] = {
    # Answer a question that needs looking things up. Read-only by construction:
    # nothing in this set can change the machine, so it is the safe first intent
    # to hand to the loop (phase 4).
    "research": ["tavily_search", "web_browse", "search_documents",
                 "memory_recall", "workspace_read", "system_status"],
    # Same, plus the local filesystem — "where did I put that file".
    "files": ["find_file", "list_directory", "workspace_read", "search_documents",
              "memory_recall"],
    # Read-only plus ONE writing tool, to exercise the confirmation path.
    "authoring": ["workspace_read", "list_directory", "search_documents",
                  "memory_recall", "workspace_write"],
}


def tool_defs(names: list[str] | str) -> list[dict]:
    """OpenAI function definitions for a set name or an explicit list of names."""
    if isinstance(names, str):
        if names not in TOOL_SETS:
            raise KeyError(f"unknown tool set '{names}'; have {sorted(TOOL_SETS)}")
        names = TOOL_SETS[names]
    defs = []
    for n in names:
        spec = TOOL_SPECS.get(n)
        if spec is None:
            raise KeyError(f"unknown tool '{n}'")
        defs.append({
            "type": "function",
            "function": {
                "name": n,
                "description": spec["description"],
                "parameters": {
                    "type": "object",
                    "properties": spec["properties"],
                    "required": spec["required"],
                },
            },
        })
    return defs


def to_payload(call: ToolCall) -> dict:
    """Turn a model tool call into an `action_engine` payload.

    Unknown arguments are passed through (ActionIntent allows extras) but the
    `target` is built explicitly, because that is the one field every handler
    actually reads.
    """
    spec = TOOL_SPECS.get(call.name)
    if spec is None:
        raise KeyError(f"unknown tool '{call.name}'")
    args = dict(call.arguments or {})
    if spec["build_target"]:
        target = spec["build_target"](args)
    elif spec["target_from"]:
        target = args.get(spec["target_from"], "")
    else:
        target = ""
    payload = {"action_type": spec["action_type"], "target": target}
    if "query" in args:
        payload["query"] = args["query"]
    return payload


def missing_required(call: ToolCall) -> list[str]:
    """Required arguments the model left out — checked before execution so the
    loop can spend its ONE repair on a specific complaint."""
    spec = TOOL_SPECS.get(call.name)
    if spec is None:
        return []
    args = call.arguments or {}
    return [r for r in spec["required"]
            if r not in args or args[r] in (None, "")]


def make_authorizer(get_tier: Callable[[str], str] | None = None,
                    allow_confirm: bool = False) -> Callable[[ToolCall], Decision]:
    """Governance adapter: one `Decision` per call, consulted every time.

    `allow_confirm=True` is for an attended run where the caller has already
    arranged to collect approval; it is off by default because the loop runs
    unattended and a CONFIRM action that silently self-approves is precisely the
    thing the governance tiers exist to prevent.
    """
    if get_tier is None:
        from governance_manager import governance_manager
        get_tier = governance_manager.get_tier

    def authorize(call: ToolCall) -> Decision:
        spec = TOOL_SPECS.get(call.name)
        if spec is None:
            return Decision(False, f"'{call.name}' is not a registered tool")
        missing = missing_required(call)
        if missing:
            return Decision(False, f"missing required argument(s): {', '.join(missing)}")
        tier = (get_tier(spec["action_type"]) or "BLOCK").upper()
        if tier == "AUTO":
            return Decision(True, "AUTO")
        if tier == "CONFIRM":
            if allow_confirm:
                return Decision(True, "CONFIRM (attended)")
            return Decision(False,
                            f"'{spec['action_type']}' is CONFIRM-tier and this run is "
                            "unattended — ask the owner to approve it directly")
        return Decision(False, f"'{spec['action_type']}' is blocked by governance ({tier})")

    return authorize


def interpret_result(text: Any) -> str:
    """Raise on an action_engine sentinel, otherwise return the output.

    A refusal string handed back as a tool RESULT reads to the model as "it
    worked", and the run ends up narrating a success that never happened.
    """
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    for s in SENTINELS:
        if stripped.startswith(s):
            if s == "Validation Error:":
                raise RuntimeError(stripped)      # malformed payload, our bug
            raise PermissionError(stripped)       # refused by a gate
    return text


def make_executor(engine, runner: Callable[[Any], Any] | None = None,
                  permission_tier: str = "admin") -> Callable[[ToolCall], Any]:
    """Adapter from a `ToolCall` to `ActionEngine.execute`.

    `ActionEngine.execute` is a coroutine and `agent_core` is synchronous (it is
    meant to run in a worker thread), so the await is done by an injected
    `runner`. Phase 4 supplies one that hands the coroutine to the MAIN event
    loop — the engine's handlers share state with the running app, so spinning up
    a second loop for them is not something to do by default.
    """
    if runner is None:
        import asyncio

        def runner(coro):  # noqa: F811 — deliberate default
            return asyncio.run(coro)

    def execute(call: ToolCall) -> Any:
        payload = to_payload(call)
        result = runner(engine.execute(payload, permission_tier=permission_tier))
        return interpret_result(result)

    return execute
