"""agent_tools.py — which of JARVIS's 103 actions the agent loop may call.

Agentic core, phase 3 (roadmap §5 Tier C #12). `action_engine` already knows how
to do everything; what it lacked is a *description of itself* a model can read.
`ToolRegistry` is that description, plus the two adapters the loop needs:

    registry.defs("research")   -> Anthropic tool definitions for one intent
    registry.authorizer()       -> ToolCall -> Decision, before EVERY call
    registry.executor(engine)   -> ToolCall -> action_engine payload -> result

Four constraints, each for a specific failure it prevents:

1. **Anthropic dialect only.** Entries author `input_schema` exactly as the
   Anthropic API takes it. Translation to OpenAI's `function.parameters` happens
   once at the wire, in `tool_calls.to_openai_tools` — so dropping in a paid
   Anthropic key later is a routing change, not a registry rewrite. Nothing in
   this file mentions OpenAI.

2. **The tier lives on the entry.** Resolved once at registration from
   `governance_manager.get_tier`, so the loop reads `entry.tier` without a second
   lookup per call. (Staleness: if the ruleset is reloaded, call
   `refresh_tiers()` — and note the engine re-checks governance on every
   execution anyway, so a stale AUTO cannot actually run a now-BLOCK action.)

3. **BLOCK is refused at REGISTRATION, not at call time.** A BLOCK-tier tool is
   not "registered but denied" — it cannot exist in the registry, so it can
   never appear in a schema handed to a model. Asking a model not to call
   `format_drive` is weaker than never telling it `format_drive` exists. The
   runtime governance check still stands underneath; this is the design-time
   half of the same guarantee. Because governance fails safe (unknown
   action_type → BLOCK), a TYPO in an action_type is also refused here — the
   registry cannot silently contain a tool the engine will never dispatch.

4. **Curated subsets, never all 103.** Tools ship in named sets sized to the
   loop's cap; `define_set` refuses an oversized set at definition time for the
   same reason `agent_core` refuses one at run time.

Registry entries CALL INTO action_engine — no tool logic is reimplemented here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from modules.agent_core import Decision, ToolFailure
from modules.tool_calls import ToolCall

AUTO, CONFIRM, BLOCK = "AUTO", "CONFIRM", "BLOCK"

#: Upper bound on a curated set. Mirrors AgentLimits.max_tools; kept as a module
#: constant so a set can be validated without importing the limits object.
MAX_SET_SIZE = 8

# Sentinel prefixes emitted by ActionEngine.execute (mirrored, not imported, so
# this module stays importable without the whole action stack).
SENTINELS = ("GOVERNANCE_BLOCKED:", "GOVERNANCE_CONFIRM:", "TIER_BLOCKED:",
             "Validation Error:")


class BlockedToolError(ValueError):
    """Raised when something tries to register a BLOCK-tier action as a tool."""


@dataclass(frozen=True)
class ToolEntry:
    """One registered tool: its schema, its action_type, and its tier."""

    name: str
    description: str
    input_schema: dict
    action_type: str
    tier: str
    target_from: str | None = None
    build_target: Callable[[dict], Any] | None = None

    @property
    def definition(self) -> dict:
        """The Anthropic-shaped tool definition handed to the model."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @property
    def required(self) -> list[str]:
        req = self.input_schema.get("required")
        return list(req) if isinstance(req, list) else []


class ToolRegistry:
    """The set of action_engine actions an agent run is allowed to see."""

    def __init__(self, get_tier: Callable[[str], str] | None = None):
        if get_tier is None:
            from governance_manager import governance_manager
            get_tier = governance_manager.get_tier
        self._get_tier = get_tier
        self._tools: dict[str, ToolEntry] = {}
        self._sets: dict[str, list[str]] = {}

    # -- registration ---------------------------------------------------- #

    def register(self, name: str, description: str, input_schema: dict, *,
                 action_type: str | None = None,
                 target_from: str | None = None,
                 build_target: Callable[[dict], Any] | None = None) -> ToolEntry:
        """Add one tool. Raises `BlockedToolError` if governance says BLOCK.

        `target_from` names the argument that becomes `payload["target"]` (the
        shape every action_engine handler reads); `build_target` covers the
        handlers with a composite target, e.g. workspace_write's "path|content".
        Neither given: the first declared property is used.
        """
        if not name or not isinstance(name, str):
            raise ValueError("a tool needs a name")
        if name in self._tools:
            raise ValueError(f"tool '{name}' is already registered")
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            raise ValueError(f"tool '{name}' needs an object input_schema")

        atype = action_type or name
        tier = (self._get_tier(atype) or BLOCK).upper()
        if tier == BLOCK:
            # Design-time half of the guarantee — see constraint 3 above. Note
            # this also catches a typo'd action_type, because governance
            # fail-safes unknown types to BLOCK.
            raise BlockedToolError(
                f"'{atype}' is BLOCK-tier (or unknown to governance) and cannot be "
                "registered as an agent tool")
        if tier not in (AUTO, CONFIRM):
            raise ValueError(f"'{atype}' has an unrecognised tier '{tier}'")

        props = input_schema.get("properties") or {}
        entry = ToolEntry(
            name=name, description=description, input_schema=input_schema,
            action_type=atype, tier=tier,
            target_from=target_from or (next(iter(props), None) if not build_target else None),
            build_target=build_target,
        )
        self._tools[name] = entry
        return entry

    def define_set(self, set_name: str, tool_names: list[str]) -> None:
        """Name a curated subset. Refuses unknown tools and oversized sets."""
        unknown = [n for n in tool_names if n not in self._tools]
        if unknown:
            raise KeyError(f"set '{set_name}' references unregistered tools: {unknown}")
        if len(tool_names) > MAX_SET_SIZE:
            raise ValueError(
                f"set '{set_name}' has {len(tool_names)} tools; the cap is "
                f"{MAX_SET_SIZE} — small models degrade sharply past that")
        if not tool_names:
            raise ValueError(f"set '{set_name}' is empty")
        self._sets[set_name] = list(tool_names)

    def refresh_tiers(self) -> list[str]:
        """Re-read every tier after a governance reload.

        Returns the names DROPPED because they became BLOCK (or vanished from
        the ruleset). Re-tiering a tool to BLOCK must remove it from the
        registry, not merely deny it later.
        """
        dropped: list[str] = []
        for name, entry in list(self._tools.items()):
            tier = (self._get_tier(entry.action_type) or BLOCK).upper()
            if tier == BLOCK:
                del self._tools[name]
                dropped.append(name)
            elif tier != entry.tier:
                self._tools[name] = replace(entry, tier=tier)
        if dropped:
            for s, names in self._sets.items():
                self._sets[s] = [n for n in names if n not in dropped]
        return dropped

    # -- reading ---------------------------------------------------------- #

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def tier_of(self, name: str) -> str | None:
        entry = self._tools.get(name)
        return entry.tier if entry else None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def sets(self) -> list[str]:
        return sorted(self._sets)

    def set_names(self, set_name: str) -> list[str]:
        """The tool names in one curated set (a copy — callers may filter it)."""
        if set_name not in self._sets:
            raise KeyError(f"unknown tool set '{set_name}'; have {self.sets()}")
        return list(self._sets[set_name])

    def defs(self, names: list[str] | str) -> list[dict]:
        """Anthropic tool definitions for a set name or an explicit name list."""
        if isinstance(names, str):
            if names not in self._sets:
                raise KeyError(f"unknown tool set '{names}'; have {self.sets()}")
            names = self._sets[names]
        out = []
        for n in names:
            entry = self._tools.get(n)
            if entry is None:
                raise KeyError(f"unknown tool '{n}'")
            out.append(entry.definition)
        return out

    # -- adapters for the loop -------------------------------------------- #

    def to_payload(self, call: ToolCall) -> dict:
        """Turn a model tool call into an `action_engine` payload."""
        entry = self._tools.get(call.name)
        if entry is None:
            raise KeyError(f"unknown tool '{call.name}'")
        args = dict(call.arguments or {})
        if entry.build_target:
            target = entry.build_target(args)
        elif entry.target_from:
            target = args.get(entry.target_from, "")
        else:
            target = ""
        payload = {"action_type": entry.action_type, "target": target}
        if "query" in args:
            payload["query"] = args["query"]
        return payload

    def missing_required(self, call: ToolCall) -> list[str]:
        """Required arguments the model left out — checked before execution so
        the loop can spend its ONE repair on a specific complaint."""
        entry = self._tools.get(call.name)
        if entry is None:
            return []
        args = call.arguments or {}
        return [r for r in entry.required if r not in args or args[r] in (None, "")]

    def authorizer(self, allow_confirm: bool = False) -> Callable[[ToolCall], Decision]:
        """Governance adapter: one `Decision` per call, consulted every time.

        Reads `entry.tier` — no second governance lookup per call. BLOCK never
        appears here because it cannot be registered; the branch exists anyway
        as a belt-and-braces guard if a tier is ever mutated at runtime.

        `allow_confirm=True` is for an ATTENDED run where the caller has arranged
        to collect approval (the AT_DESK path). Off by default: an unattended
        loop that self-approves a CONFIRM action defeats the tier system.
        """
        def authorize(call: ToolCall) -> Decision:
            entry = self._tools.get(call.name)
            if entry is None:
                return Decision(False, f"'{call.name}' is not a registered tool")
            missing = self.missing_required(call)
            if missing:
                return Decision(False,
                                f"missing required argument(s): {', '.join(missing)}")
            if entry.tier == AUTO:
                return Decision(True, AUTO)
            if entry.tier == CONFIRM:
                if allow_confirm:
                    return Decision(True, "CONFIRM (attended)")
                return Decision(False,
                                f"'{entry.action_type}' is CONFIRM-tier and this run is "
                                "unattended — ask the owner to approve it directly")
            return Decision(False,
                            f"'{entry.action_type}' is blocked by governance "
                            f"({entry.tier})")

        return authorize

    def executor(self, engine, *, permission_tier: str = "admin",
                 governance_bypass: bool = False) -> Callable[[ToolCall], Any]:
        """Adapter from a `ToolCall` to `ActionEngine.execute_with_retry`.

        Async, so `agent_core` awaits it directly — no second event loop, and the
        engine's coroutines stay on the loop that owns the app's state. The
        engine lock is NOT taken here: `agent_core` holds it around this call and
        releases it immediately after, so a later human-confirm wait can happen
        with the lock free.

        Two failure translations, both deliberate:
          * a governance/tier sentinel RAISES (see `interpret_result`);
          * `state == "FAILED"` raises `ToolFailure` carrying the engine's own
            wording, so the Phase-2 `_is_failure` verdict reaches the model
            instead of being re-derived here.
        """
        async def execute(call: ToolCall) -> Any:
            payload = self.to_payload(call)
            meta = await engine.execute_with_retry(
                payload, True, None,
                governance_bypass=governance_bypass,
                permission_tier=permission_tier)
            if isinstance(meta, dict):
                result, state = meta.get("result", meta), meta.get("state")
            else:
                result, state = meta, None
            output = interpret_result(result)
            if state == "FAILED":
                raise ToolFailure(str(output))
            return output

        return execute


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


# ═══════════════════════════════════════════════════════════════════════════
# The default registry — 10 tools over real action_engine handlers
# ═══════════════════════════════════════════════════════════════════════════

def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required if required is not None else list(properties)[:1]}


_QUERY = {"query": {"type": "string", "description": "What to look for."}}


def build_default_registry(get_tier: Callable[[str], str] | None = None) -> ToolRegistry:
    """Register the curated tools and the per-intent sets.

    Nothing here invents behaviour: every entry names an existing action_type and
    the engine does the work. Kept as a function rather than import-time state so
    a harness can inject a fake tier lookup and no import touches governance.json.
    """
    r = ToolRegistry(get_tier)

    r.register("tavily_search",
               "Search the live web and get a synthesised answer. Best first "
               "choice for current facts, news, prices, scores.",
               _obj(_QUERY))
    r.register("web_browse",
               "Open a specific URL and read the page. Use when a search result "
               "must be verified or a named page has to be read in full.",
               _obj({"url": {"type": "string",
                             "description": "Full URL, including https://"}}))
    r.register("search_documents",
               "Search Kaustav's own indexed documents and notes. Use for "
               "anything personal that would not be on the public web.",
               _obj(_QUERY))
    r.register("memory_recall",
               "Recall facts JARVIS has been told before (preferences, people, "
               "past decisions). Check here before claiming something is unknown.",
               _obj(_QUERY))
    r.register("workspace_read",
               "Read a file from the workspace so its exact contents are in context.",
               _obj({"path": {"type": "string",
                              "description": "File path, absolute or workspace-relative."}}))
    r.register("list_directory",
               "List the contents of a directory (read-only, sandboxed to the "
               "user's home).",
               _obj({"path": {"type": "string", "description": "Directory path."}}))
    r.register("find_file",
               "Locate a file by name when its directory is unknown.",
               _obj({"name": {"type": "string",
                              "description": "File name or fragment."}}))
    r.register("system_status",
               "Current machine telemetry: CPU, memory, disk, battery.",
               _obj({}, []))
    r.register("read_screen",
               "Describe what is currently on Kaustav's screen. Use only when the "
               "answer depends on what he is looking at.",
               _obj({}, []))
    r.register("workspace_write",
               "Create or overwrite a workspace file. Requires the owner's "
               "confirmation.",
               _obj({"path": {"type": "string", "description": "File path to write."},
                     "content": {"type": "string",
                                 "description": "Full file contents."}},
                    ["path", "content"]),
               build_target=lambda a: f"{a.get('path', '')}|{a.get('content', '')}")

    # Answer a question that needs looking things up. Read-only by construction:
    # nothing in this set can change the machine, so it is the safe first intent
    # to hand to the loop (phase 4).
    r.define_set("research", ["tavily_search", "web_browse", "search_documents",
                              "memory_recall", "workspace_read", "system_status"])
    # "Where did I put that file" — local filesystem, still read-only.
    r.define_set("files", ["find_file", "list_directory", "workspace_read",
                           "search_documents", "memory_recall"])
    # Read-only plus ONE writing tool, to exercise the confirmation path.
    r.define_set("authoring", ["workspace_read", "list_directory",
                               "search_documents", "memory_recall",
                               "workspace_write"])
    return r


_default: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    """Process-wide registry, built on first use (so imports stay cheap)."""
    global _default
    if _default is None:
        _default = build_default_registry()
    return _default
