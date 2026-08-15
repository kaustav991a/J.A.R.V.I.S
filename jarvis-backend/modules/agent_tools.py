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

import json
import urllib.parse
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
    #: Extra words `search_tools` matches at NAME weight. JARVIS's action_types
    #: are a decade of internal naming — `native_app_launcher`, `os_control`,
    #: `os_macro` — and the shelf ranks a name hit at 4 but a description hit at
    #: 1, so a tool whose name shares no word with the request needs TWO
    #: description coincidences to clear the floor. Live-shaped miss that added
    #: this: "open notepad on the computer" surfaced the HUD's notepad PANEL and
    #: the link opener, and not the thing that opens programs. Aliases are the
    #: spoken names, not synonyms for their own sake.
    aliases: tuple[str, ...] = ()
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
                 on_success: Callable[[dict, Any], None] | None = None,
                 aliases: tuple[str, ...] | None = None) -> ToolEntry:
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
            action_type=atype, tier=tier, aliases=tuple(aliases or ()),
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
                 governance_bypass: bool = False,
                 payload_sink: Callable[[dict], Any] | None = None
                 ) -> Callable[[ToolCall], Any]:
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

        `payload_sink` is how a HUD-EFFECT result reaches the screen — see
        `hud_frame`. Without one, a tool whose whole effect is the frame it
        returns fails honestly rather than reporting success.
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
            # A HUD-effect result: the frame IS the action. It has to be sent, or
            # nothing happened. See `hud_frame`.
            frames = hud_frames(output, call.name)
            if frames:
                if payload_sink is None:
                    raise ToolFailure(NO_DISPLAY)
                for frame in frames:
                    await _maybe_await_sink(payload_sink, frame)
                output = describe_hud_frames(frames)
            if entry is not None and entry.format_output is not None:
                try:
                    output = entry.format_output(output)
                except ToolFailure:
                    # A formatter is also where a handler's "I did nothing"
                    # sentinel is recognised (see `_tavily_guard`). That is a
                    # deliberate verdict, not a formatting bug, so it must reach
                    # the loop instead of being swallowed with the accidents.
                    raise
                except Exception:  # noqa: BLE001 — a formatter must never lose a
                    pass           # real result; the raw output still answers.
            args = dict(call.arguments or {})
            if entry is not None and entry.shape_output is not None:
                try:
                    output = entry.shape_output(output, args)
                except ToolFailure:
                    raise
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


# ── HUD-effect results (§6.8.2 wave 2) ─────────────────────────────────────
#
# Most handlers ANSWER: they return text and the answer is the whole result. A
# few INSTRUCT the screen instead — `play_music` returns
# `{"action_type": "play_youtube", "url": …}` and the music plays only because
# main.py's one-shot path forwards that frame to the HUD.
#
# The agent loop is not that path. A result handed back to the model is read,
# not forwarded, so registering such a tool without this bridge would produce
# the worst possible outcome: a run that reports "playing now" while nothing
# plays. Everything the model can call must either do the thing or say it
# didn't.
#
# Deliberately a NAMED MAPPING and not "forward any dict". `list_directory`
# also returns a dict, and its effect is information — already delivered as
# text by `format_directory_listing`. Broadcasting frames the HUD did not ask
# for is how a display starts flickering during an agent run.

#: What the model is told when a HUD-effect tool runs with no display attached.
NO_DISPLAY = ("this tool works by driving the desk display, and this run has no "
              "display attached — nothing would have happened. Do not report it "
              "as done; say it needs the desktop HUD.")


#: The bare string `close_app` returns for a HUD-embedded player, where there is
#: no OS process to kill. main.py answers it with two frames.
HUD_MEDIA_CLOSE = "HUD_MEDIA_CLOSE_REQUEST"


def hud_frames(result: Any, tool: str | None = None) -> list[dict]:
    """The frames main.py's one-shot path would have sent for this result.

    A LIST, because one result is not always one frame — clearing HUD media
    takes two. Empty for everything else, which is nearly everything.

    `tool` is the discriminator of last resort: `web_search_image` returns a
    bare `{"success", "url", "title"}` with nothing in it naming the action, and
    guessing from that shape alone would catch any future dict that happens to
    carry a url.
    """
    if tool == "web_search_image" and isinstance(result, dict):
        if result.get("success") and result.get("url"):
            return [{"status": "search_result_image", "url": result["url"],
                     "title": result.get("title", "")}]
        # An honest miss, not a frame: let the ordinary result reach the model.
        return []
    if tool == "render_chart" and isinstance(result, str):
        # The handler hands back its payload as a JSON STRING, which main.py
        # parses and broadcasts verbatim. It also answers "I don't have
        # structured data to chart, sir." in plain text — that is a real result
        # for the model to read, not a frame.
        try:
            import json as _json
            spec = _json.loads(result)
        except Exception:  # noqa: BLE001
            return []
        return [spec] if isinstance(spec, dict) and \
            spec.get("ui_action") == "render_chart" else []
    if isinstance(result, str) and result.strip() == HUD_MEDIA_CLOSE:
        return [{"status": "close_search", "message": "Clearing HUD media."},
                {"status": "toggle_browser", "visible": False}]
    if not isinstance(result, dict):
        return []
    kind = result.get("action_type")
    if kind == "play_youtube" and result.get("url"):
        return [{"status": "play_youtube", "url": result["url"]}]
    if kind == "hud_open_widget" and result.get("widget"):
        return [{"type": "ui_state", "open_widget": result["widget"]}]
    if kind == "hud_close_widget" and result.get("widget"):
        return [{"type": "ui_state", "close_widget": result["widget"]}]
    return []


def describe_hud_frames(frames: list[dict]) -> str:
    """What the MODEL is told once the frames have actually been sent.

    Phrased as what happened — handed to the desk display — rather than "you are
    now hearing it" or "he can see it", which the loop cannot know.
    """
    first = frames[0] if frames else {}
    if first.get("status") == "play_youtube":
        return (f"Started on the desk display's player: {first.get('url')} . "
                f"It is playing on the desktop HUD, not through this "
                f"conversation.")
    if first.get("open_widget"):
        return (f"The {first['open_widget']} panel is now open on the desk "
                f"display.")
    if first.get("close_widget"):
        return f"The {first['close_widget']} panel is now closed."
    if first.get("ui_action") == "render_chart":
        return (f"Drew the chart \"{first.get('title')}\" on the desk display "
                f"({len(first.get('data') or [])} points, "
                f"{first.get('chart_type')}). He can see it; you cannot — "
                f"describe what the numbers say, not what the chart looks like.")
    if first.get("status") == "search_result_image":
        return (f"Put the image on the desk display: {first.get('title') or 'result'} "
                f"({first.get('url')}). You have NOT seen it — describe it only "
                f"from the title, or use `read_screen` if what it shows matters.")
    if first.get("status") == "close_search":
        return ("Stopped the HUD's own player. Nothing was closed at the "
                "operating-system level — that player is part of the display, "
                "not an application.")
    return "Sent to the desk display."


async def _maybe_await_sink(sink: Callable[[dict], Any], frame: dict) -> None:
    """Send a frame through a sink that may be sync (a test) or async (the WS)."""
    result = sink(frame)
    if hasattr(result, "__await__"):
        await result


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

# ── wave 2 target composition ───────────────────────────────────────────────
# Each of these mirrors ONE handler's documented split. They are separate named
# functions rather than lambdas because that is what a harness can quote the
# handler's format against, and a wrong separator here does not fail loudly —
# it plays the wrong thing.

def _tv_volume_target(args: dict) -> str:
    """`_tv_volume` takes "up" | "down" | "mute", or "up|5" for repeats. It
    partitions on the FIRST pipe and clamps the count to 1–20 itself. A bare
    trailing pipe would make it parse "" as the count."""
    direction = str(args.get("direction", "")).strip()
    steps = args.get("steps")
    if not steps or direction == "mute":     # mute ignores a count at the far end
        return direction
    return f"{direction}|{int(steps)}"


def _tv_media_target(args: dict) -> str:
    """`tv_play_media` splits on the FIRST colon into (app, query). Without a
    colon the far end lists the installed apps and asks which to use."""
    title = str(args.get("title", "")).strip()
    app = str(args.get("app", "")).strip()
    return f"{app}: {title}" if app else title


def _tv_media_precondition(args: dict) -> str | None:
    """Rule 3. A colon in the TITLE and no app is a mis-parse waiting to happen:
    `"Mission: Impossible"` splits into app "mission", which is not an app, and
    the TV answers "that app isn't wired up yet" — a dead end the model cannot
    diagnose from the message."""
    if str(args.get("app", "")).strip():
        return None
    if ":" in str(args.get("title", "")):
        return ("the title contains a colon, which the television reads as the "
                "app name. Pass the app explicitly (youtube, netflix, prime "
                "video, hotstar or spotify) so the colon stays part of the "
                "title.")
    return None


#: The HUD's panel ids, as `_normalize_hud_widget_id` resolves them. Used as a
#: schema enum, which makes that normaliser a no-op for agent calls — every
#: value here is one of its exact-match cases — and stops the model inventing a
#: panel that silently becomes "vitals" by default.
HUD_WIDGETS = ("vitals", "mail", "calendar", "calculator", "notepad",
               "browser", "camera", "map")


def _macro_target(args: dict) -> str:
    """`_os_macro` takes "deep_work", or "deep_work:<url>" to override the page
    that macro opens. It splits on the FIRST colon, so a url keeps its own."""
    macro = str(args.get("macro", "")).strip()
    url = str(args.get("url", "")).strip()
    return f"{macro}:{url}" if url else macro


# ── rule 3: a URL the model chose is not automatically a WEB url ─────────────
# Pre-Electron review, 2026-08-15. `web_browse`, `open_link` and `os_macro`'s
# optional `url` all took a bare string. Two things that is not:
#
#   file:///…/jarvis-backend/.env   Playwright renders it and hands the CONTENTS
#                                   back as page text. That reads any file on the
#                                   disk while bypassing `workspace_read` — and
#                                   the protected-file list, which only guards
#                                   writing and deleting.
#   http://127.0.0.1:8000/api/…     the desk's own API is unauthenticated ON
#                                   PURPOSE, because only local processes reach
#                                   it. A model steered into fetching localhost
#                                   is exactly the case that assumption excluded.
#
# Same root as findings 1, 2 and 6: governance approves `web_browse` by type and
# never looks at the argument. And since §6.8 the argument can come from a page
# the model was told to read.
# The rule itself now lives in `modules/url_safety.py`, NOT here.
#
# Finding 17 (2026-08-16): this precondition guarded the AGENT layer, and
# `web_browse` / `open_link` are also in the ONE-SHOT catalogue
# (`action_router.py`, `brain.py`) — the ordinary conversational path, which
# never reaches a tool-layer precondition. So the hole finding 10 closed stayed
# open through the door nobody had walked through. `action_engine` enforces the
# same function at the sink now; this stays because refusing BEFORE dispatch
# gives the model a correctable instruction instead of a spoken refusal.
from modules.url_safety import url_problem as _url_problem  # noqa: E402


def _mail_target(args: dict) -> str:
    """`gmail_send`'s target as JSON rather than "to | subject | body".

    The pipe form is parsed with `split("|", 2)`, so pipes in the BODY survive —
    but a pipe in the SUBJECT moved the rest of the subject into the body and
    sent it. "Re: Q3 | final" is an ordinary subject line, and a model composing
    one from a web page or a thread it just read can produce any character at
    all. Structure therefore stops being encoded in a character the content is
    allowed to contain. `_send_email` already parses this shape (it checks for a
    leading "{" before falling back to the delimiter).
    """
    return json.dumps({
        "to": str((args or {}).get("to", "")).strip(),
        "subject": str((args or {}).get("subject", "")).strip(),
        "body": str((args or {}).get("body", "")),
    })


def _url_precondition(args: dict) -> str | None:
    return _url_problem((args or {}).get("url"))


def _macro_url_precondition(args: dict) -> str | None:
    """`os_macro`'s url is OPTIONAL — absent is fine, present must be a web URL."""
    raw = str((args or {}).get("url") or "").strip()
    return _url_problem(raw) if raw else None


def _remember_target(args: dict) -> str:
    """`_remember_fact` splits on the FIRST colon into (category, fact) and
    falls back to category "Fact" for anything it does not recognise. The colon
    is always emitted so a fact containing one cannot be read as a category."""
    category = str(args.get("category") or "Fact").strip() or "Fact"
    return f"{category}: {str(args.get('fact', '')).strip()}"


def _note_target(args: dict) -> str:
    """`FileAgent.create_note` takes "title: content", splitting on the first
    colon; without one the whole string is the title and the note is empty."""
    title = str(args.get("title", "")).strip()
    content = str(args.get("content") or "").strip()
    return f"{title}: {content}" if content else title


def _note_precondition(args: dict) -> str | None:
    """A colon in the TITLE moves the split point, so "Meeting: Tuesday" becomes
    a note called "Meeting" whose body is the rest — quietly, and the title is
    also what the filename is made from."""
    if ":" in str(args.get("title", "")):
        return ("the title contains a colon, which is where the note's title "
                "ends and its body begins. Put everything after the colon in "
                "`content` instead.")
    return None


def _chart_target(args: dict) -> dict:
    """`_render_chart` accepts a dict directly, so the spec is handed over as
    one rather than serialised — a JSON string would have to survive the model's
    quoting as well as the handler's parse."""
    return {"title": str(args.get("title", "")),
            "type": str(args.get("chart_type") or "bar"),
            "data": args.get("data") or []}


#: What `_tavily_search` returns when TAVILY_API_KEY is absent.
TAVILY_UNCONFIGURED = "TAVILY_UNCONFIGURED"


def _tavily_guard(output: Any) -> Any:
    """Turn the unconfigured-search sentinel into an honest failure.

    Raising here rather than returning a message is deliberate: `format_output`
    runs inside the executor, so a raise becomes a tool ERROR the loop feeds
    back and counts, instead of a result the model reads as an answer.
    """
    if isinstance(output, str) and output.strip() == TAVILY_UNCONFIGURED:
        raise ToolFailure(
            "web search is not configured on this machine (no API key), so "
            "nothing was searched. Try `search_documents` or `memory_recall` "
            "for anything he has told you before, and otherwise say plainly "
            "that you cannot look it up right now.")
    return output


def _git_log_target(args: dict) -> str:
    """`_github_log` takes "", "N", or "repo_path|N" — it partitions on the first
    pipe and only then checks whether what is left is a digit."""
    repo = str(args.get("repo_path") or "").strip()
    count = args.get("count")
    tail = str(int(count)) if count else ""
    return f"{repo}|{tail}" if repo else tail


def _git_commit_target(args: dict) -> str:
    """`_github_commit` takes "message" or "repo_path|message", partitioning on
    the FIRST pipe — so the repo path is only a repo path when one was given."""
    repo = str(args.get("repo_path") or "").strip()
    message = str(args.get("message", "")).strip()
    return f"{repo}|{message}" if repo else message


def _git_commit_precondition(args: dict) -> str | None:
    """Rule 3. Without a repo path, a pipe ANYWHERE in the message makes the
    handler read everything before it as a directory — so the commit either
    fails in a confusing place or lands in the wrong repository, and the message
    is silently truncated either way."""
    if str(args.get("repo_path") or "").strip():
        return None
    if "|" in str(args.get("message", "")):
        return ("the commit message contains a pipe, which the handler reads as "
                "the end of a repository path. Rewrite the message without a "
                "pipe, or pass repo_path explicitly.")
    return None


def _music_target(args: dict) -> str:
    """`_play_music` picks Spotify when the word appears anywhere in the target
    and treats the rest as the search string (see `modules.media_query`)."""
    query = str(args.get("query", "")).strip()
    service = str(args.get("service", "")).strip().lower()
    if service == "spotify":
        return f"{query} spotify".strip()
    return query


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
               _obj(_QUERY),
               aliases=("google", "internet", "online", "news", "lookup",
                        "current"),
               # `_tavily_search` returns the bare string "TAVILY_UNCONFIGURED"
               # when the key is missing. Handed back as a RESULT it reads as
               # data, and a model asked for today's news would either narrate
               # the sentinel or invent an answer around it. As a failure it
               # counts against the error streak and is said plainly.
               format_output=_tavily_guard)
    r.register("web_browse",
               "Open a specific URL and read the page. Use when a search result "
               "must be verified or a named page has to be read in full.",
               _obj({"url": {"type": "string",
                             "description": "Full URL, including https://"}}),
               # file:// would render a local file and hand its CONTENTS back as
               # page text — a read of any file on the disk, around
               # `workspace_read` and around the protected-file list.
               precondition=_url_precondition)
    r.register("search_documents",
               "Search Kaustav's own indexed documents and notes. Use for "
               "anything personal that would not be on the public web.",
               _obj(_QUERY))
    r.register("memory_recall",
               "Recall facts JARVIS has been told before (preferences, people, "
               "past decisions). Check here before claiming something is unknown.",
               _obj(_QUERY),
               aliases=("remember", "told", "tell", "said", "know", "about",
                        "forgot", "mentioned"))
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
               _obj({}, []),
               aliases=("machine", "computer", "cpu", "memory", "disk", "ram",
                        "battery", "performance", "load"))
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
               build_target=lambda a: str(a.get("count") or ""),
               # "email" is not in this tool's NAME, so before these aliases
               # "check my email for anything new" ranked it sixth, behind every
               # tool that happens to have the word in its name (§6.8.4).
               aliases=("email", "mail", "inbox", "unread", "new", "check",
                        "anything"))

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
               build_target=_gmail_query_target,
               aliases=("email", "mail", "from", "sender", "subject", "find",
                        "older"))

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
               # JSON, not "to | subject | body". The handler splits the pipe
               # form with maxsplit=2, so a pipe in the BODY is harmless — but a
               # pipe in the SUBJECT silently moved half the subject into the
               # body and sent it that way. "Re: Q3 | final" is an ordinary
               # subject line, so the fix is to stop encoding structure in a
               # character the content is allowed to contain, rather than to
               # forbid the character. `_send_email` already accepts this shape.
               build_target=_mail_target)

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

    # ── Wave 2 (§6.8.2): the television, and music on the desk ───────────────
    # Every one of these is AUTO in `governance.json` — a keypress on a TV is
    # undone by another keypress — so unlike wave 1's writing half they ARE
    # findable in an unattended run. That is governance's ruling, not this
    # file's; re-tiering belongs in the ruleset.
    #
    # `tv_search` is deliberately NOT registered. Its handler is one line —
    # `tv_agent.tv_play_media(f"youtube:{query}")` — so it is `tv_play_media`
    # with the app pre-chosen. The same rule that kept `check_email` out of
    # wave 1 keeps it out of this one.
    #
    # The confusion this wave has to prevent is not email-shaped. It is "play
    # X" meaning the TELEVISION across the room versus the desk display in
    # front of him, so every description here names its screen.

    r.register("tv_power",
               "Toggle the television's power. This is a TOGGLE and there is no "
               "way to read the current state — it wakes a sleeping TV and puts "
               "an awake one to sleep. Use it when he says to turn the TV on or "
               "off; do not use it to \"make sure\" it is on, because that is how "
               "you turn it off.",
               _obj({}, []),
               aliases=("television", "turn", "switch", "standby", "screen",
                        "off"))

    r.register("tv_volume",
               "Change the television's volume. `mute` is a toggle. This is the "
               "TV across the room, not the desktop's own volume.",
               _obj({"direction": {"type": "string",
                                   "enum": ["up", "down", "mute"],
                                   "description": "Which way, or mute."},
                     "steps": {"type": "integer", "minimum": 1, "maximum": 20,
                               "description": "How many notches (default 1). "
                                              "Ignored for mute."}},
                    ["direction"]),
               build_target=_tv_volume_target,
               aliases=("louder", "quieter", "sound", "television"))

    r.register("tv_control",
               "Press a navigation key on the television's remote — move the "
               "highlight around a menu, go back, select what is highlighted, "
               "pause or resume playback. For power use `tv_power`, for volume "
               "or mute use `tv_volume`, and to open an app use "
               "`tv_launch_app`; this tool is only for moving around a screen "
               "that is already open.",
               _obj({"key": {"type": "string",
                             "enum": ["up", "down", "left", "right", "select",
                                      "back", "home", "play_pause"],
                             "description": "The remote key to press."}}),
               aliases=("remote", "navigate", "menu", "television", "arrow"))

    r.register("tv_launch_app",
               "Open an app on the television. Known names: youtube, netflix, "
               "prime video, hotstar, disney+, sonyliv, zee5, spotify, plex, "
               "vlc, tubi, apple tv, chrome, settings, home. An Android package "
               "id works too. This only opens the app — to open something AND "
               "search for a title in one go, use `tv_play_media`.",
               _obj({"app": {"type": "string",
                             "description": "App name, e.g. \"netflix\"."}}))

    r.register("tv_play_media",
               "Put something on the television: opens the app and searches for "
               "the title inside it. Name the app whenever you know it — without "
               "one, this cannot choose, and it comes back asking which app to "
               "use, which costs a step. Disney+ content is under `hotstar`. "
               "This is the TV, not the desk display; for music at the desk use "
               "`play_music`.",
               _obj({"title": {"type": "string",
                               "description": "What to play, e.g. \"Stranger "
                                              "Things\"."},
                     "app": {"type": "string",
                             "enum": ["youtube", "netflix", "prime video",
                                      "hotstar", "spotify"],
                             "description": "Which app to play it in."}},
                    ["title"]),
               build_target=_tv_media_target,
               precondition=_tv_media_precondition,
               aliases=("watch", "stream", "television", "movie", "show",
                        "episode"))

    r.register("tv_type",
               "Type text into whatever text box is focused on the television, "
               "then press Enter. Only useful when a search box is already open "
               "and focused — it types into the current focus, so check what is "
               "on screen first. It cannot open the box for you: "
               "`tv_play_media` searches inside an app in one step and is "
               "usually the better tool.",
               _obj({"text": {"type": "string",
                              "description": "Text to type. Enter is pressed "
                                             "for you."}}))

    r.register("play_music",
               "Play music on the DESK DISPLAY — it opens the track in the "
               "HUD's player on the desktop, not on the television. Give the "
               "song or artist as plain words; leave it out to just open the "
               "service. For the TV across the room use `tv_play_media`.",
               _obj({"query": {"type": "string",
                               "description": "Song, artist or album. Plain "
                                              "words, no search syntax."},
                     "service": {"type": "string",
                                 "enum": ["youtube", "spotify"],
                                 "description": "Where to play it "
                                                "(default youtube)."}},
                    []),
               build_target=_music_target,
               aliases=("song", "listen", "track", "album", "artist", "audio"))

    # ── Wave 3 (§6.8.2): applications, the desk display, the machine ─────────
    # All AUTO. Two reachable actions were left out, and for DIFFERENT reasons
    # worth keeping apart:
    #
    #   * `launch_app` — a duplicate of `native_app_launcher` for the model's
    #     purposes ("open X"), and the weaker of the two: it drives a GUI search
    #     box, where the launcher resolves against the Start-Menu index with
    #     fuzzy matching AND records the new window so a later `ghost_type`
    #     targets the right one.
    #   * `enable_focus_mode` / `disable_focus_mode` — NOT a duplicate: they are
    #     unregisterable. `action_engine` returns the sentence "Focus mode
    #     enabled. Notifications silenced." and does nothing; the actual
    #     silencing lives in main.py's dispatcher, which builds a `RoutineEngine`
    #     from objects the agent layer does not hold. Registering it would make
    #     the loop announce a state change that never happened — the same
    #     failure the HUD bridge exists to prevent, one layer further up.

    r.register("native_app_launcher",
               "Open an application on the desktop. Spoken names are fine and "
               "near-misses are tolerated — it matches against the installed "
               "app index. This opens a PROGRAM on the desk machine; for a "
               "website use `open_link`, and for an app on the television use "
               "`tv_launch_app`.",
               _obj({"app": {"type": "string",
                             "description": "App name, e.g. \"notepad\", "
                                            "\"vs code\"."}}),
               aliases=("open", "start", "run", "program", "application",
                        "desktop", "computer"))

    r.register("close_app",
               "Close a running application on the desktop by name. Windows "
               "Explorer is protected and will not be killed. If the thing "
               "playing is the HUD's own player rather than an application, "
               "say so — this stops that instead, and nothing at the "
               "operating-system level is closed.",
               _obj({"app": {"type": "string",
                             "description": "App name, e.g. \"chrome\"."}}))

    r.register("hud_open_widget",
               "Open a panel on the desk display: his vitals, mail, calendar, "
               "calculator, notepad, browser, camera feed or map. This SHOWS "
               "him something on screen — it does not read anything back to "
               "you, so if you need the data yourself, call the tool that "
               "returns it (`check_calendar`, `gmail_read_unread`, "
               "`system_status`).",
               _obj({"widget": {"type": "string", "enum": list(HUD_WIDGETS),
                                "description": "Which panel to open."}}),
               aliases=("panel", "widget", "show", "display", "screen"))

    r.register("hud_close_widget",
               "Close a panel on the desk display.",
               _obj({"widget": {"type": "string", "enum": list(HUD_WIDGETS),
                                "description": "Which panel to close."}}),
               aliases=("panel", "widget", "hide", "dismiss", "display"))

    r.register("os_control",
               "Control the desk machine itself: lock the workstation, or drive "
               "system audio and whatever is currently playing on it. This is "
               "the DESK machine's audio, not the television's — for that use "
               "`tv_volume`. `lock_screen` locks him out until he signs back "
               "in, so use it only when he asks.",
               _obj({"command": {"type": "string",
                                 "enum": ["lock_screen", "mute", "unmute",
                                          "volume_up", "volume_down",
                                          "play_pause", "next_track",
                                          "prev_track"],
                                 "description": "What to do."}}),
               build_target=lambda a: str(a.get("command", "")).strip(),
               aliases=("lock", "workstation", "volume", "mute", "pause",
                        "track", "audio", "computer", "machine"))

    r.register("os_macro",
               "Run a named desktop routine that opens and closes several "
               "things at once: `deep_work` (editor plus the dev page, "
               "distractions closed), `shallow_work`, `diagnostic` (task "
               "manager and a terminal) or `entertainment`. Use it when he asks "
               "to set up a MODE; opening one app is `native_app_launcher`.",
               _obj({"macro": {"type": "string",
                               "enum": ["deep_work", "shallow_work",
                                        "diagnostic", "entertainment"],
                               "description": "Which routine to run."},
                     "url": {"type": "string",
                             "description": "Optional page for deep_work to "
                                            "open instead of its default."}},
                    ["macro"]),
               build_target=_macro_target,
               # the optional url override reaches the same browser open as
               # `open_link`, so it answers to the same rule
               precondition=_macro_url_precondition,
               aliases=("mode", "routine", "setup", "workspace", "focus"))

    r.register("open_link",
               "Open a web page in his desktop browser so HE can look at it. "
               "You do not get the contents back — if you need to READ the "
               "page, use `web_browse`, and if you need an answer from the web, "
               "use `tavily_search`.",
               _obj({"url": {"type": "string",
                             "description": "The address. https:// is added if "
                                            "you leave it off."}}),
               precondition=_url_precondition,
               aliases=("website", "page", "browser", "url", "site"))

    # ── Wave 4 (§6.8.2): git ─────────────────────────────────────────────────
    # Three reads AUTO, two writes CONFIRM, and the split is the point: reading
    # a repo is free, and the two that change history or publish it need a
    # human. `github_create_pr`, `github_create_repo` and `github_merge_pr` are
    # in governance but have NO `action ==` branch in the engine, so they cannot
    # be registered at all — `test_agent_tools` fails the build if one is.
    #
    # Every one of these takes an OPTIONAL repo path and defaults to the active
    # workspace repo. The descriptions say so, because a model that assumes
    # otherwise starts inventing paths.

    r.register("github_status",
               "What has changed in a code repository: modified, staged and "
               "untracked files, and the current branch. Defaults to the active "
               "workspace repo. This is a GIT repository — for how the machine "
               "itself is doing (CPU, memory, disk) use `system_status`.",
               _obj({"repo_path": {"type": "string",
                                   "description": "ABSOLUTE repo path. Omit for "
                                                  "the active workspace repo."}},
                    []),
               aliases=("git", "repo", "repository", "uncommitted", "branch",
                        "working", "tree"),
               build_target=lambda a: str(a.get("repo_path") or "").strip())

    r.register("github_diff",
               "The actual changes in a repository, as a `git diff --stat` "
               "summary — which files moved and by how many lines. Use it after "
               "`github_status` when the file names are not enough. It does not "
               "show the full patch text.",
               _obj({"repo_path": {"type": "string",
                                   "description": "ABSOLUTE repo path. Omit for "
                                                  "the active workspace repo."}},
                    []),
               aliases=("git", "repo", "changes", "modified", "patch"),
               build_target=lambda a: str(a.get("repo_path") or "").strip())

    r.register("github_log",
               "Recent commits, newest first, one line each. Use it to answer "
               "\"what did I do last\", or to find the commit a change landed "
               "in.",
               _obj({"count": {"type": "integer", "minimum": 1, "maximum": 50,
                               "description": "How many commits (default 5)."},
                     "repo_path": {"type": "string",
                                   "description": "ABSOLUTE repo path. Omit for "
                                                  "the active workspace repo."}},
                    []),
               aliases=("git", "repo", "commits", "history", "recent"),
               build_target=_git_log_target)

    r.register("github_commit",
               "Stage EVERY change in the repository and commit them together. "
               "Requires the owner's confirmation. It cannot commit a subset — "
               "if only some of the changes should go in, say so instead of "
               "committing. Look at `github_status` first; committing without "
               "reading what is uncommitted is how unrelated work ends up in "
               "one commit. This does NOT publish anything — that is "
               "`github_push`.",
               _obj({"message": {"type": "string",
                                 "description": "Commit message."},
                     "repo_path": {"type": "string",
                                   "description": "ABSOLUTE repo path. Omit for "
                                                  "the active workspace repo."}},
                    ["message"]),
               aliases=("git", "repo", "save", "record"),
               build_target=_git_commit_target,
               precondition=_git_commit_precondition)

    r.register("github_push",
               "Publish the current branch's commits to the remote. Requires "
               "the owner's confirmation. This is the step that makes work "
               "public and is the hardest to take back — never push work you "
               "have not been asked to push, and never push to a branch you "
               "were not told to.",
               _obj({"repo_path": {"type": "string",
                                   "description": "ABSOLUTE repo path. Omit for "
                                                  "the active workspace repo."}},
                    []),
               aliases=("git", "repo", "publish", "upload", "remote", "origin"),
               build_target=lambda a: str(a.get("repo_path") or "").strip())

    # ── Wave 5 (§6.8.2): driving a real browser ──────────────────────────────
    # `web_browse` (already registered) opens a page and returns its text WITH a
    # numbered map of the interactive elements. These five act on that map, so
    # every description points back at where the numbers come from — and says
    # that they go stale, because `_mark_and_extract_dom` RENUMBERS from 1 on
    # every render. An id remembered from two steps ago is not "probably still
    # right", it is a different element.
    #
    # `web_search` is NOT registered: it is `tavily_search` (already registered,
    # and in the wired `research` set) with a DuckDuckGo fallback behind it.
    # Registering both would make the model choose between two spellings of one
    # job. The fallback is the better behaviour, which is why the Tavily tool now
    # fails honestly when it is unconfigured instead of handing back a sentinel.

    r.register("web_click",
               "Click an element on the page the browser is currently showing. "
               "Use ONLY an id from the element list in the most recent tool "
               "output — the page renumbers its elements every time it changes, "
               "so an id from an earlier step points at something else now. "
               "Returns the page as it looks after the click, with a fresh list.",
               _obj({"element_id": {"type": "string",
                                    "description": "Id from the CURRENT element "
                                                   "list, e.g. \"12\"."}}),
               aliases=("browser", "page", "press", "button", "link"))

    r.register("web_type",
               "Type into a field on the page the browser is showing. Same rule "
               "as clicking: the id must come from the most recent element "
               "list, because the page renumbers its elements whenever it "
               "changes. Enter is pressed for you ONLY in a search box — in an "
               "ordinary field the text is filled and left there, so submit it "
               "with `web_click` on the form's button.",
               _obj({"element_id": {"type": "string",
                                    "description": "Id from the CURRENT element "
                                                   "list."},
                     "text": {"type": "string", "description": "Text to type."}},
                    ["element_id", "text"]),
               aliases=("browser", "page", "fill", "input", "field", "form"),
               build_target=lambda a: (f"{str(a.get('element_id', '')).strip()}|"
                                       f"{a.get('text', '')}"))

    r.register("web_scroll",
               "Scroll the browser page by about one screen and return what is "
               "now visible. Use it when the answer is further down the page "
               "than what came back.",
               _obj({"direction": {"type": "string", "enum": ["down", "up"],
                                   "description": "Which way to scroll."}}),
               aliases=("browser", "page", "further", "more"))

    r.register("web_back",
               "Go back one page in the browser, and return what is there. Use "
               "it after following a link that turned out to be wrong.",
               _obj({}, []),
               aliases=("browser", "page", "previous", "return"))

    r.register("web_close",
               "Close the browser and free its memory. Do this when the "
               "browsing is finished — it is not needed between pages, and "
               "closing mid-task means starting the next page from nothing.",
               _obj({}, []),
               aliases=("browser", "quit", "finish", "done"))

    r.register("web_search_image",
               "Find a picture of something and put it on the desk display. "
               "This SHOWS him an image — you never see it, so do not describe "
               "what is in it. For information rather than a picture, use "
               "`tavily_search`.",
               _obj(_QUERY),
               aliases=("picture", "photo", "image", "visual", "look like"))

    # ── Wave 6 (§6.8.2): the people, the house, and what is left ─────────────
    # The remainder, and the most heavily governed part of the catalogue: two of
    # these reach another human being.
    #
    # DELIBERATELY NOT REGISTERED, each for a stated reason:
    #   * `get_telemetry` — the same call as `system_status`, line for line
    #     (`telemetry_agent.get_summary_string`). An exact duplicate.
    #   * `close_display` — like focus mode (wave 3): the engine returns
    #     "Display clear command received." and main.py does the work, so the
    #     agent layer cannot deliver it honestly.
    #   * `self_improve` — an agent that can rewrite its own source mid-run is a
    #     different project with its own guard rails (post-Electron backlog
    #     item 6), not a catalogue entry.
    #   * `run_autopilot` — a multi-minute Figma→code pipeline. The loop's
    #     wall-clock cap is 120 s, so registering it guarantees a run that dies
    #     halfway through work it cannot resume.
    #   * `gui_action`, `agentic_gui_task`, `ghost_type`, `ghost_save_file` —
    #     they drive the real mouse and keyboard against whatever window has
    #     focus. Reachable, and left for a wave that can pair them with a way to
    #     verify the target window first.

    # `message_partner` IS NOT HERE, and that is a standing decision rather than
    # an oversight of this wave. `test_partner_messaging` has asserted since
    # 2026-07-26 that the action name does not appear in this file at all —
    # "the agentic loop must not be able to message a person on its own" — and
    # the CONFIRM tier is not an answer to it: away, a CONFIRM does not die, it
    # PARKS and pings his phone, so an approval tapped at a bus stop would send
    # a private message whose text the loop wrote. The voice path is different
    # in the way that matters: there he dictates the words.
    #
    # Registering it needs Kaustav's explicit call, not a wave's judgement.
    # Reading about her is a separate question and is allowed below: both of
    # those answer only because he asked, and neither reaches her.

    r.register("partner_contact_status",
               "Whether a registered partner has been in touch, when, and "
               "whether it seemed urgent — with NO content. This is the "
               "discreet answer to \"did she message me\". If he asks what she "
               "actually SAID, that is `summarize_partner_chat`, a different "
               "and more explicit request.",
               _obj({"who": {"type": "string",
                             "description": "Registered name. Omit for the "
                                            "default partner."}},
                    []),
               aliases=("heard", "contact", "messaged", "called", "her", "him",
                        "partner", "girlfriend", "today"),
               build_target=lambda a: str(a.get("who") or "").strip())

    r.register("summarize_partner_chat",
               "Read back what a registered partner has actually told JARVIS. "
               "This returns CONTENT, so use it only when he asks what she "
               "said — for \"has she been in touch\" use "
               "`partner_contact_status`, which answers without repeating a "
               "word. It works only if he switched transcript logging on, and "
               "says so plainly when he has not.",
               _obj({"who": {"type": "string",
                             "description": "Registered name. Omit for the "
                                            "default partner."}},
                    []),
               aliases=("said", "told", "conversation", "chat", "her", "him",
                        "partner", "girlfriend"),
               build_target=lambda a: str(a.get("who") or "").strip())

    r.register("telegram_send_file",
               "Send a file to HIS OWN phone over Telegram. It has exactly one "
               "recipient — the owner — so this is how you get a document, "
               "report or image off the desk machine to him, and it can never "
               "reach anyone else.",
               _obj({"path": {"type": "string",
                              "description": "ABSOLUTE path of the file."},
                     "caption": {"type": "string",
                                 "description": "Optional line to send with it."}},
                    ["path"]),
               aliases=("phone", "document", "attachment", "pdf", "share"),
               build_target=lambda a: {"path": str(a.get("path", "")).strip(),
                                       "caption": str(a.get("caption") or "")},
               precondition=lambda a: af.absolute_path_problem(a.get("path")))

    r.register("remember_fact",
               "Store something about him for later — a preference, a "
               "correction, or a plain fact. Use it when he says to remember "
               "something, or states a lasting preference. Do NOT store "
               "passing details of the current task; this is long-term memory, "
               "and it is read back by `memory_recall`.",
               _obj({"fact": {"type": "string",
                              "description": "The fact, in one sentence, as he "
                                             "would want it read back."},
                     "category": {"type": "string",
                                  "enum": ["Fact", "Preference", "Correction"],
                                  "description": "Which kind (default Fact)."}},
                    ["fact"]),
               aliases=("remember", "memorise", "note", "store", "keep"),
               build_target=_remember_target)

    r.register("check_vitals",
               "His health and fitness figures — heart rate, steps, sleep — "
               "from his own health data. This is the PERSON; for how the "
               "MACHINE is doing use `system_status`.",
               _obj({}, []),
               aliases=("health", "heart", "steps", "sleep", "fitness",
                        "biometrics"))

    r.register("movie_protocol",
               "Set the room up for watching something: wakes the television "
               "and prepares it. Use it for \"movie time\" or \"set up for a "
               "film\" — to play one specific thing use `tv_play_media`.",
               _obj({}, []),
               aliases=("film", "cinema", "watch", "room", "evening"))

    r.register("sleep_protocol",
               "Wind the room down for the night: clears the desk displays and "
               "pauses whatever is playing on the machine.",
               _obj({}, []),
               aliases=("bed", "night", "goodnight", "wind", "down"))

    r.register("create_note",
               "Write a short note into his notes folder. Requires the owner's "
               "confirmation. For a file anywhere else, or for a file whose "
               "exact path matters, use `workspace_write`.",
               _obj({"title": {"type": "string",
                               "description": "Short title — becomes the "
                                              "filename."},
                     "content": {"type": "string",
                                 "description": "Body of the note."}},
                    ["title"]),
               aliases=("note", "jot", "write", "reminder", "memo"),
               build_target=_note_target,
               precondition=_note_precondition)

    r.register("organize_downloads",
               "Tidy his Downloads folder by MOVING every file into a "
               "subfolder for its type. Requires the owner's confirmation. "
               "Files move, so anything he was about to open is no longer "
               "where he left it — only run it when he asks for the tidy-up.",
               _obj({}, []),
               aliases=("tidy", "sort", "clean", "downloads", "folder"))

    r.register("render_chart",
               "Draw a chart on the desk display from data YOU have already "
               "gathered. Give it the numbers — it does no fetching of its own. "
               "He sees the chart; you do not, so say what it shows rather "
               "than describing how it looks.",
               _obj({"title": {"type": "string",
                               "description": "Chart title."},
                     "chart_type": {"type": "string",
                                    "enum": ["bar", "line", "pie"],
                                    "description": "Which kind of chart."},
                     "data": {"type": "array",
                              "description": "Up to 24 points. Anything past "
                                             "24 is dropped.",
                              "items": {"type": "object",
                                        "properties": {
                                            "label": {"type": "string"},
                                            "value": {"type": "number"}}}}},
                    ["title", "data"]),
               aliases=("chart", "graph", "plot", "visualise", "show", "trend"),
               build_target=_chart_target)

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
