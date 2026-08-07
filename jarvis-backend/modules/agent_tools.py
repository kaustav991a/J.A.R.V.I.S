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

from modules import agent_files as af
from modules.agent_core import Decision, ToolFailure
from modules.tool_calls import ToolCall

AUTO, CONFIRM, BLOCK = "AUTO", "CONFIRM", "BLOCK"

#: Upper bound on a curated set. Mirrors AgentLimits.max_tools; kept as a module
#: constant so a set can be validated without importing the limits object.
MAX_SET_SIZE = 8

# Sentinel prefixes emitted by ActionEngine.execute (mirrored, not imported, so
# this module stays importable without the whole action stack).
SENTINELS = ("GOVERNANCE_BLOCKED:", "GOVERNANCE_CONFIRM:", "TIER_BLOCKED:",
             "Validation Error:",
             # terminal_agent's sandbox refusal. Live 2026-07-26 this came back as
             # an ordinary tool RESULT, so the loop read a refusal as data and
             # retried other roots until the step cap stopped it. As an error it
             # counts against the error streak and the model is told plainly.
             "Access denied")


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
    #: Optional adapter applied to a SUCCESSFUL result before the model sees it.
    #: Some handlers answer to the HUD first — `list_directory` returns a
    #: `render_file_list` payload with epoch floats — and a model reading that
    #: concludes the information it needs isn't there. Reshaping belongs here, in
    #: the agent layer, so the HUD's own contract is untouched.
    format_output: Callable[[Any], Any] | None = None
    #: Like `format_output`, but it also sees the CALL ARGUMENTS. That is what
    #: makes paging possible (§6.8.1 gap D): `offset`/`limit` are arguments, and
    #: the engine handler knows nothing about them. Applied after
    #: `format_output`, so a tool may use either or both.
    shape_output: Callable[[Any, dict], Any] | None = None
    #: §6.8.1 gap E/F, rule 3 — a precondition enforced IN CODE, checked by the
    #: authorizer before anything runs. Returns an instruction string to refuse,
    #: or None to allow. This is where "you have not read this file yet" and
    #: "that string matches 3 times and must be unique" live. A prompt rule
    #: degrades over a long session; a check here cannot.
    precondition: Callable[[dict], str | None] | None = None
    #: Called after a SUCCESSFUL execution with (arguments, output). The ledger
    #: recording "this path has been read" is written here, so it records only
    #: reads that actually happened.
    on_success: Callable[[dict, Any], None] | None = None

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
                 build_target: Callable[[dict], Any] | None = None,
                 format_output: Callable[[Any], Any] | None = None,
                 shape_output: Callable[[Any, dict], Any] | None = None,
                 precondition: Callable[[dict], str | None] | None = None,
                 on_success: Callable[[dict, Any], None] | None = None) -> ToolEntry:
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
            build_target=build_target, format_output=format_output,
            shape_output=shape_output, precondition=precondition,
            on_success=on_success,
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

    def schema_problem(self, call: ToolCall) -> str | None:
        """Type/enum/bounds check of the arguments — §6.8.1 gap A, rule 16.

        Runs AFTER `missing_required` in the authorizer, because "you left out
        `path`" is a better complaint than "`path` must be a string" when the
        argument is simply absent.

        This is the check that was missing entirely until 2026-08-08: presence
        was verified, shape was not, so `{"path": 42}` reached `action_engine`
        and failed far from its cause. Returns a ready-to-send instruction or
        None.
        """
        entry = self._tools.get(call.name)
        if entry is None:
            return None
        from modules.agent_schema import validate_arguments
        return validate_arguments(call.name, call.arguments, entry.input_schema)

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
            # Shape, not just presence (§6.8.1 gap A). Deliberately BEFORE the
            # tier branch: a malformed call should be corrected, not confirmed —
            # otherwise a CONFIRM-tier tool asks the owner to approve arguments
            # that were never going to work.
            if problem := self.schema_problem(call):
                return Decision(False, problem)
            # Tool-layer preconditions (§6.8.1 gap E/F, rule 3) — read-before-
            # write, and edit-uniqueness. Also before the tier branch: an edit
            # that would land in the wrong place must be corrected, never sent
            # to the owner for approval.
            if entry.precondition is not None:
                try:
                    refusal = entry.precondition(dict(call.arguments or {}))
                except Exception as exc:  # noqa: BLE001
                    # A broken precondition must FAIL CLOSED. It exists to stop
                    # a destructive call; if it cannot answer, the call does not
                    # proceed on the strength of its silence.
                    return Decision(False,
                                    f"could not verify the preconditions for "
                                    f"'{call.name}' ({type(exc).__name__}: {exc}) — "
                                    "not proceeding")
                if refusal:
                    return Decision(False, refusal)
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
            entry = self._tools.get(call.name)
            if entry is not None and entry.format_output is not None:
                try:
                    output = entry.format_output(output)
                except Exception:  # noqa: BLE001 — a formatter must never lose a
                    pass           # real result; the raw output still answers.
            args = dict(call.arguments or {})
            if entry is not None and entry.shape_output is not None:
                try:
                    output = entry.shape_output(output, args)
                except Exception:  # noqa: BLE001 — same rule as format_output.
                    pass
            if entry is not None and entry.on_success is not None:
                try:
                    entry.on_success(args, output)
                except Exception:  # noqa: BLE001 — bookkeeping must never lose a
                    pass           # result the tool genuinely produced.
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


def _gmail_query_target(args: dict) -> str:
    """`_gmail_read` takes "query" or "query|N" — the count is optional and the
    handler defaults it to 5. Emitting a bare "query|" when no count was given
    would make the handler parse an empty string as the limit."""
    query = str(args.get("query", "")).strip()
    count = args.get("max_results")
    return f"{query}|{int(count)}" if count else query

#: Entries shown to the model per directory. Newest-first, so a truncated tail is
#: the OLD end of the list — the part a "most recent" question never needs.
MAX_LISTED_ENTRIES = 40


def format_directory_listing(raw: Any) -> Any:
    """Turn `list_directory`'s HUD payload into something a model can reason over.

    The handler answers to the HUD first: it returns
    `{"ui_action": "render_file_list", "data": [{"modified": 1785048651.07, …}]}`.
    Every fact needed is in there, but a 70B model reading epoch floats wrapped in
    a render instruction concluded (live, 2026-07-26) that modification times were
    "not provided" and gave up on "which file is most recent" — the exact question
    the wired intent asks. So: sort newest-first, print ISO minutes, and label the
    columns. Anything unexpected passes through untouched.
    """
    if not isinstance(raw, str) or "render_file_list" not in raw:
        return raw
    import datetime
    import json as _json
    try:
        payload = _json.loads(raw)
        entries = payload.get("data")
        if not isinstance(entries, list):
            return raw
    except Exception:  # noqa: BLE001
        return raw

    def when(e):
        try:
            return float(e.get("modified") or 0)
        except (TypeError, ValueError):
            return 0.0

    rows = sorted((e for e in entries if isinstance(e, dict)), key=when, reverse=True)
    base = str(payload.get("path") or "").rstrip("\\/")
    lines = [f"Contents of {base or '?'}, NEWEST FIRST ({len(rows)} entries). "
             f"Columns: modified | type | size | FULL PATH (pass these verbatim to "
             f"other tools)"]
    for e in rows[:MAX_LISTED_ENTRIES]:
        stamp = (datetime.datetime.fromtimestamp(when(e)).strftime("%Y-%m-%d %H:%M")
                 if when(e) else "unknown")
        size = e.get("size")
        name = e.get("name", "?")
        # FULL path, not the bare name: live 2026-07-26 the model passed `.claude.json`
        # to workspace_read, which resolved it against a DIFFERENT root and reported
        # "File not found: F:\work\.claude.json". A name alone is a trap.
        full = f"{base}\\{name}" if base else name
        lines.append(f"{stamp} | {e.get('type', '?')} | "
                     f"{size if size is not None else '-'} | {full}")
    if len(rows) > MAX_LISTED_ENTRIES:
        lines.append(f"… {len(rows) - MAX_LISTED_ENTRIES} older entries not shown.")
    return "\n".join(lines)


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
    # §6.8.1 gaps C/D/G. The schema grew `offset`/`limit` because the paging is
    # real now: `workspace_agent.read_file` used to advise "consider reading a
    # specific line range" for a parameter that did not exist.
    r.register("workspace_read",
               "Read a file. Returns NUMBERED lines, so you can cite exact "
               "locations as path:line. Long files come back one window at a "
               "time — the footer tells you the offset to pass to continue. "
               "The path must be ABSOLUTE.",
               _obj({"path": {"type": "string",
                              "description": "ABSOLUTE file path. Relative paths "
                                             "are refused — different tools "
                                             "resolve them against different "
                                             "roots."},
                     "offset": {"type": "integer", "minimum": 0,
                                "description": "First line to show, 0-based. "
                                               "Omit to start at the top."},
                     "limit": {"type": "integer", "minimum": 1,
                               "maximum": af.MAX_READ_LIMIT,
                               "description": f"How many lines to show "
                                              f"(default {af.DEFAULT_READ_LIMIT})."}},
                    ["path"]),
               shape_output=af.paginate_read,
               on_success=af.note_read,
               precondition=lambda a: af.absolute_path_problem(a.get("path")))
    r.register("list_directory",
               "List a directory (read-only, sandboxed to the user's home). Each "
               "entry comes back with its LAST-MODIFIED time, type and size, "
               "sorted newest first — so this is how you find the most recent "
               "file. Folders are listed too; read a file, not a folder.",
               _obj({"path": {"type": "string", "description": "Directory path."}}),
               format_output=format_directory_listing)
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
               "Create a file, or replace one ENTIRELY. Requires the owner's "
               "confirmation. To change part of an existing file use "
               "`edit_file` — a whole-file write loses anything you do not "
               "re-emit. An existing file must be read before it can be "
               "overwritten. The path must be ABSOLUTE.",
               _obj({"path": {"type": "string",
                              "description": "ABSOLUTE file path to write."},
                     "content": {"type": "string",
                                 "description": "Full file contents."}},
                    ["path", "content"]),
               build_target=lambda a: f"{a.get('path', '')}|{a.get('content', '')}",
               precondition=af.write_precondition)

    # §6.8.1 gap F (rule 4). The engine's `workspace_patch` replaces EVERY
    # occurrence by default — `_workspace_patch` never passes a count — so an
    # ambiguous edit used to land in every match silently. `edit_precondition`
    # makes that structurally impossible: an `old_string` matching more than
    # once is refused unless the model says explicitly that it means all of them.
    r.register("edit_file",
               "Replace an exact string in a file, leaving everything else "
               "untouched. `old_string` must match the file EXACTLY (including "
               "indentation) and must be UNIQUE, or the edit is refused — strip "
               "the line-number prefix from read output before matching. Read "
               "the file first. Prefer this over rewriting a whole file. "
               "Requires the owner's confirmation.",
               _obj({"path": {"type": "string",
                              "description": "ABSOLUTE path of the file to edit."},
                     "old_string": {"type": "string",
                                    "description": "Exact text to replace. Must "
                                                   "be unique in the file unless "
                                                   "replace_all is true."},
                     "new_string": {"type": "string",
                                    "description": "Replacement text."},
                     "replace_all": {"type": "boolean",
                                     "description": "Replace every occurrence. "
                                                    "Only for a deliberate rename."}},
                    ["path", "old_string", "new_string"]),
               action_type="workspace_patch",
               build_target=af.build_patch_target,
               precondition=af.edit_precondition)

    # ── Wave 1 (§6.8.2): email + calendar ────────────────────────────────────
    # Reachable through `search_tools` rather than wired into every intent —
    # that is the point of the shelf. Descriptions carry when-to-use AND
    # when-NOT (rule 1), because these are the tools a model is most likely to
    # reach for on a vague "check my stuff".
    #
    # Two reachable actions are deliberately NOT registered:
    #   * `check_email` — its own handler says `gmail_read_unread` is "the
    #     primary action for check my email", and two tools for one job make the
    #     model guess which it wants.
    #   * `send_email` — superseded by `gmail_send`, which takes the same
    #     "to | Subject | Body" target and also accepts a dict.

    r.register("gmail_read_unread",
               "Check for new mail: returns a summary of the most recent UNREAD "
               "emails. This is the right tool for \"any new email?\", \"check my "
               "inbox\", \"did anything come in\". For mail that has already been "
               "read, or mail from a particular person or about a topic, use "
               "`gmail_read` with a search query instead.",
               _obj({"count": {"type": "integer", "minimum": 1, "maximum": 20,
                               "description": "How many to fetch (default 5)."}},
                    []),
               build_target=lambda a: str(a.get("count") or ""))

    r.register("gmail_read",
               "Search the mailbox with a Gmail search query and read what comes "
               "back. Use this for anything specific: mail from a person "
               "(from:name@x.com), about a subject (subject:invoice), by state "
               "(is:unread, is:starred), or by age (newer_than:2d). Returns the "
               "thread id of each result, which `gmail_reply` needs. For a plain "
               "\"anything new?\" use `gmail_read_unread`.",
               _obj({"query": {"type": "string",
                               "description": "Gmail search syntax, e.g. "
                                              "\"from:mum newer_than:7d\"."},
                     "max_results": {"type": "integer", "minimum": 1, "maximum": 20,
                                     "description": "How many to return (default 5)."}},
                    ["query"]),
               build_target=_gmail_query_target)

    r.register("search_email",
               "Find emails matching plain-words text. Simpler than `gmail_read` "
               "and takes no query syntax — use it when you have a phrase to look "
               "for rather than a structured filter.",
               _obj(_QUERY))

    r.register("read_email",
               "Open ONE email in full, by its position in the last listing. Use "
               "after `gmail_read_unread` or `gmail_read` when a summary is not "
               "enough and the body matters.",
               _obj({"which": {"type": "string",
                               "description": "Position from the last listing "
                                              "(\"1\", \"2\", …) or \"latest\"."}},
                    []),
               build_target=lambda a: str(a.get("which") or "latest"))

    r.register("check_calendar",
               "What is on Kaustav's calendar. Use for \"what's on today\", "
               "\"am I free at 4\", \"when is the meeting\". Read-only — it "
               "cannot add or move anything.",
               _obj({}, []))

    r.register("morning_briefing",
               "The assembled daily briefing — calendar, mail and system state "
               "together, in one call. Use when he asks for the overview rather "
               "than one specific thing; it is cheaper than calling the "
               "individual tools and reads better than stitching them together.",
               _obj({}, []))

    # -- the writing half. All CONFIRM: each one leaves the machine or changes
    # -- something he would have to undo by hand.

    r.register("gmail_send",
               "Send a NEW email. Requires the owner's confirmation. Use only "
               "when he has asked for mail to be sent and you have all three of "
               "recipient, subject and body — never invent a recipient, and never "
               "send to an address you inferred rather than were given. To answer "
               "an existing thread use `gmail_reply`, which keeps the "
               "conversation together.",
               _obj({"to": {"type": "string",
                            "description": "Recipient address. Must be one he "
                                           "gave you."},
                     "subject": {"type": "string", "description": "Subject line."},
                     "body": {"type": "string", "description": "Message body."}},
                    ["to", "subject", "body"]),
               build_target=lambda a: (f"{str(a.get('to', '')).strip()} | "
                                       f"{str(a.get('subject', '')).strip()} | "
                                       f"{a.get('body', '')}"))

    r.register("gmail_reply",
               "Reply to an existing email thread. Requires the owner's "
               "confirmation. The thread id comes from `gmail_read` output — "
               "read the thread first; a reply written without seeing what it "
               "answers is a guess.",
               _obj({"thread_id": {"type": "string",
                                   "description": "Thread id from gmail_read."},
                     "body": {"type": "string", "description": "Reply text."}},
                    ["thread_id", "body"]),
               build_target=lambda a: (f"{str(a.get('thread_id', '')).strip()} | "
                                       f"{a.get('body', '')}"))

    r.register("create_event",
               "Put an event on the calendar. Requires the owner's confirmation. "
               "Describe it in one natural phrase including the time — \"dentist "
               "Thursday 4pm\" — rather than as separate fields. Check the "
               "calendar first if the slot might already be taken.",
               _obj({"description": {"type": "string",
                                     "description": "The event and its time, in "
                                                    "one phrase."}}))

    r.register("clear_schedule",
               "Clear TODAY'S calendar entirely. Requires the owner's "
               "confirmation. This removes every event for today at once and he "
               "would have to re-enter them by hand — only for an explicit "
               "\"clear my day\". To remove one event, do not use this.",
               _obj({}, []))

    # Answer a question that needs looking things up. Read-only by construction:
    # nothing in this set can change the machine, so it is the safe first intent
    # to hand to the loop (phase 4).
    r.define_set("research", ["tavily_search", "web_browse", "search_documents",
                              "memory_recall", "workspace_read", "system_status"])
    # "Where did I put that file" — local filesystem, still read-only.
    r.define_set("files", ["find_file", "list_directory", "workspace_read",
                           "search_documents", "memory_recall"])
    # Read-only plus the two writing tools, to exercise the confirmation path.
    # `edit_file` is listed BEFORE `workspace_write` deliberately: a model
    # reading the set in order meets the surgical tool first, and rewriting a
    # whole file to change one line is the more damaging default.
    r.define_set("authoring", ["workspace_read", "list_directory",
                               "search_documents", "memory_recall",
                               "edit_file", "workspace_write"])
    return r


_default: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    """Process-wide registry, built on first use (so imports stay cheap)."""
    global _default
    if _default is None:
        _default = build_default_registry()
    return _default
