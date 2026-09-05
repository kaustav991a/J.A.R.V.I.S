r"""agent_search.py — make every tool ADDRESSABLE while keeping few RESIDENT.

Roadmap §6.8.2 (rule 13 of AGENT-TOOLING-REFERENCE.md). The registry caps a
curated set at 8 tools because small models degrade sharply past that, and that
cap is right. But curation is not the same as addressability: it meant the loop
could reach **11 of JARVIS's 72 reachable actions**, and the other 61 did not
exist as far as any model was concerned.

A `ToolShelf` holds the whole catalogue, keeps a small BASE set resident, and
exposes one extra tool — `search_tools` — that promotes matching schemas into
the resident set for the rest of the run.

THREE THINGS THIS DOES THAT THE REFERENCE DOES NOT
--------------------------------------------------
1. **Search is governance-filtered.** A BLOCK-tier action cannot be registered
   at all (`ToolRegistry.register` refuses it), so it cannot be found here
   either — but the filtering is asserted rather than assumed, because "the
   model learned a name it may never use" costs a wasted step and a repair
   every time it tries. In an UNATTENDED run, CONFIRM-tier tools are excluded
   too: nobody can approve them, so surfacing them only teaches the model to
   ask for things that will be refused.
2. **The resident cap is never exceeded.** `AgentLimits.max_tools` (8) was
   chosen deliberately; promotion evicts the oldest PROMOTED tool rather than
   raising the ceiling. The base set is never evicted — those are the tools the
   intent was wired with, and losing one mid-run would be silent damage.
3. **Anthropic's `defer_loading` is not available here.** JARVIS runs on Groq,
   Gemini's compatibility endpoint and OpenRouter, none of which have
   server-side tool search, so this is the roll-your-own route the reference
   describes as the alternative. That also means promotion changes the tool
   list mid-conversation, which those providers accept.

WHY `search_tools` IS NOT IN THE REGISTRY
-----------------------------------------
It is not an `action_engine` action — it changes what the MODEL can see and
never touches the machine. `ToolRegistry.register` would refuse it outright,
because governance has no rule for it and unknown types fail closed to BLOCK.
That refusal is correct and is left alone: the shelf owns this tool, the loop
intercepts it before dispatch, and it never reaches the engine or the
authorizer. Nothing it does needs a governance decision, because everything it
reveals is still gated when it is actually called.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from modules.agent_schema import validate_arguments

# `Decision` is imported INSIDE `CompositeRegistry.authorizer`, not here:
# `agent_core` imports this module, so a top-level import would make the pair
# circular and the failure would depend on which one a caller imported first.

__all__ = ["ToolShelf", "SEARCH_TOOL", "SEARCH_TOOL_NAME", "MAX_RESULTS",
           "CompositeRegistry"]

SEARCH_TOOL_NAME = "search_tools"

#: How many schemas one search may promote. Small on purpose: the resident cap
#: is 8, so a search returning six tools would evict most of the base set's
#: worth of room and leave the model worse off than before it asked.
MAX_RESULTS = 3

#: Rule 1 — the description carries WHEN to call it and when not to. The
#: "you already have" clause exists because the cheapest possible search is the
#: one the model does not make.
SEARCH_TOOL = {
    "name": SEARCH_TOOL_NAME,
    "description": (
        "Find a tool you do not currently have and load it for the rest of this "
        "task. You start with a small set of tools; many more exist.\n\n"
        "Call this when the task needs a capability none of your current tools "
        "provide — sending a message, controlling the TV, checking email, "
        "opening an app. Describe the capability in plain words "
        "(\"send a telegram message\", \"what is playing on the tv\").\n\n"
        "Do NOT call it for something you can already do with a tool you hold — "
        f"that wastes a step. At most {MAX_RESULTS} tools are loaded per search, "
        "and loading tools may drop ones loaded by an earlier search, so search "
        "for what you need NOW rather than everything you might need."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The capability you need, in plain words.",
            },
        },
        "required": ["query"],
    },
}

#: Words that carry no signal in a capability query. Kept tiny and literal
#: rather than a real stopword list — this is matching over ~72 short
#: descriptions, not information retrieval.
_NOISE = frozenset({
    "a", "an", "the", "to", "for", "of", "on", "in", "is", "it", "and", "or",
    "my", "me", "i", "do", "can", "how", "what", "with", "that", "this", "get",
    "use", "using", "tool", "tools", "please", "need", "want", "you", "thing",
    "something", "anything", "some", "any", "there", "here", "make", "let",
})

#: A hit must score at least this much to be offered. A term appearing only in
#: a DESCRIPTION scores 1, and one such coincidence is not evidence — every
#: description shares ordinary English with every other. A name or action_type
#: match scores 4, so any genuine hit clears this easily. Without the floor,
#: "can you do the thing for me" loaded a real tool on the strength of one
#: common word, which is worse than finding nothing: it consumes a slot and
#: tells the model it is now equipped.
MIN_SCORE = 2.0


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _NOISE and len(w) > 1]


def _variants(term: str) -> tuple[str, ...]:
    """The term, plus its singular. People pluralise and tools do not.

    Found by the eval set (§6.8.4), which is exactly the kind of thing an eval
    is for: *"any emails from my accountant"* matched NOTHING, because matching
    is `term in haystack` and "emails" is not inside "gmail_read". One letter,
    and the whole mail catalogue was unreachable from a plural.
    """
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return (term, term[:-1])
    return (term,)


@dataclass
class SearchHit:
    name: str
    score: float
    tier: str


@dataclass
class CompositeRegistry:
    """Two catalogues behind one set of accessors, so the shelf cannot tell them
    apart — JARVIS's own actions, and whatever MCP servers are connected.

    §6.8.3. Deferral is the whole reason this exists: a handful of MCP servers
    is easily 60 tools, and 60 resident tools is worse than none. Composing the
    catalogues here means foreign tools are searched, ranked, promoted and
    evicted by exactly the code that already does it for local ones — including
    the governance filter, since an `McpToolEntry` carries a real tier.

    LOCAL WINS ON A NAME CLASH. It cannot happen today (foreign names are
    namespaced `mcp__server__tool`) but the rule is stated rather than left to
    dict ordering: a server that manages to claim the name of a JARVIS action
    must not shadow it.
    """

    primary: Any                # ToolRegistry — JARVIS's own actions
    secondary: Any              # McpRegistry — external servers

    def names(self) -> list[str]:
        local = self.primary.names()
        return local + [n for n in self.secondary.names() if n not in local]

    def get(self, name: str):
        return self.primary.get(name) or self.secondary.get(name)

    def tier_of(self, name: str):
        tier = self.primary.tier_of(name)
        return tier if tier is not None else self.secondary.tier_of(name)

    def set_names(self, set_name: str) -> list[str]:
        """Curated sets are a LOCAL idea: an intent is wired with JARVIS's own
        tools, and a foreign tool is reached by searching for it."""
        return self.primary.set_names(set_name)

    def defs(self, names) -> list[dict]:
        if isinstance(names, str):          # a set name — always local
            return self.primary.defs(names)
        local_names = set(self.primary.names())
        # Order is preserved from the caller's list, because position carries
        # weight for small models and the shelf hands them in a deliberate one.
        return [d for name in names
                for d in (self.primary.defs([name]) if name in local_names
                          else self.secondary.defs([name]))]

    def authorizer(self, allow_confirm: bool = False):
        """Local calls take the local path; foreign calls are gated on the tier
        governance gave `mcp_call`, which is the whole of §6.8.3's first
        constraint. An external server never gets a softer check than an
        `action_engine` action."""
        from modules.agent_core import Decision

        local = self.primary.authorizer(allow_confirm)

        def authorize(call):
            if self.primary.get(call.name) is not None:
                return local(call)
            entry = self.secondary.get(call.name)
            if entry is None:
                return Decision(False, f"'{call.name}' is not a registered tool")
            problem = validate_arguments(call.name, call.arguments,
                                         entry.input_schema)
            if problem:
                return Decision(False, problem)
            if entry.tier == "AUTO":
                return Decision(True, "AUTO")
            if entry.tier == "CONFIRM":
                if allow_confirm:
                    return Decision(True, "CONFIRM (attended)")
                return Decision(
                    False,
                    f"'{entry.server}' is an external tool server and this run "
                    "is unattended — its tools need the owner's approval")
            return Decision(False,
                            f"external tools are blocked by governance "
                            f"({entry.tier})")

        return authorize


@dataclass
class ToolShelf:
    """The catalogue, a small resident set, and the search that bridges them."""

    registry: Any
    #: Tools the run starts with. Never evicted — the intent was wired with them.
    base: list[str] = field(default_factory=list)
    #: Ceiling on tools sent to the model per turn, base + promoted together.
    max_tools: int = 8
    #: Is a human available to approve a CONFIRM-tier action this run?
    allow_confirm: bool = False
    #: Raw tool definitions that belong to no registry entry — today just the
    #: sub-agent delegate. They are always resident and count against the cap,
    #: because a shelf that rebuilt the tool list each turn would otherwise drop
    #: the delegate the caller had just added to it.
    extra: list[dict] = field(default_factory=list)
    #: Promoted names, oldest first — the eviction order.
    _promoted: list[str] = field(default_factory=list, init=False)
    #: Every search this run made, for the audit trail and the HUD.
    searches: list[dict] = field(default_factory=list, init=False)

    # -- what the model can see ------------------------------------------- #

    def resident(self) -> list[str]:
        """Base set plus everything promoted, in a stable order."""
        return list(self.base) + [n for n in self._promoted if n not in self.base]

    def defs(self) -> list[dict]:
        """Tool definitions for this turn, including `search_tools` itself.

        `search_tools` is appended LAST so the base set keeps the head of the
        list — position carries weight for small models, and the tools the
        intent was wired with should not be pushed down by a meta-tool.
        """
        return (self.registry.defs(self.resident())
                + [dict(d) for d in self.extra]
                + [dict(SEARCH_TOOL)])

    def room(self) -> int:
        """Free slots, remembering that `search_tools` occupies one of them."""
        return max(0, self.max_tools - len(self.resident()) - len(self.extra) - 1)

    # -- finding ----------------------------------------------------------- #

    def _visible(self, name: str) -> bool:
        """May this tool be offered to THIS run at all?

        A BLOCK tool cannot be in the registry, so the tier check here is about
        CONFIRM in an unattended run — see point 1 in the module docstring.
        """
        tier = self.registry.tier_of(name)
        if tier is None or tier == "BLOCK":
            return False
        return self.allow_confirm or tier != "CONFIRM"

    def search(self, query: str) -> list[SearchHit]:
        """Rank catalogue tools against a plain-words capability query.

        Deterministic and dependency-free: exact name, then name/action_type
        substring, then term overlap with the description. No embeddings — the
        catalogue is ~72 short entries, and a model that has to guess which of
        two similar tools it got is worse off than one given a clear ranking.

        `select:a,b` asks for exact names, mirroring the reference's ToolSearch,
        and is the form a model should use when it already knows the name.
        """
        query = (query or "").strip()
        candidates = [n for n in self.registry.names()
                      if n not in self.resident() and self._visible(n)]
        return self._rank(query, candidates)

    def already_have(self, query: str) -> list[SearchHit]:
        """The tools ALREADY on the shelf that answer this query.

        `search` deliberately hides resident tools - there is no point offering
        what is already loaded. But that turns the one case the preload exists
        for into a dead end.

        Measured 2026-09-05 on eval task `web-02`, "click the sign in button on
        the page". The runner preloaded exactly the right tool:

            [AGENT] shelf preload for this goal: web_click
            [AGENT] shelf: 6 resident of 56 catalogued, 0 free slot(s)

        The model then searched for it. `web_click` is rank 1 for that query with
        a score of 12.0 - and being resident, it was filtered out, so the model
        was shown `web_type`, `web_back`, `web_scroll`, and with no free slots it
        got "there is no room to load any of them". It searched again. Six times,
        then gave up. `web-03` failed the same way.

        **The preload and the search were fighting each other**, and the model
        was never told the thing that would have ended it in one step: you are
        already holding it. So the two mechanisms are reconciled here rather than
        one being weakened - hiding resident tools stays right, and saying "you
        already have this one" is what was missing.
        """
        return self._rank(query, [n for n in self.resident()
                                  if self._visible(n)])

    def _rank(self, query: str, candidates: list) -> list[SearchHit]:

        query = (query or "").strip()
        if query.lower().startswith("select:"):
            wanted = [w.strip() for w in query.split(":", 1)[1].split(",") if w.strip()]
            return [SearchHit(n, 100.0, self.registry.tier_of(n))
                    for n in wanted if n in candidates]

        terms = _terms(query)
        if not terms:
            return []

        hits: list[SearchHit] = []
        for name in candidates:
            entry = self.registry.get(name)
            # Three weights, and the ORDER is the point: a name match is the
            # strongest evidence, an alias is a hand-written synonym, and a
            # description hit is often a coincidence of ordinary English.
            #
            # Aliases sat at name weight until the eval set caught what that
            # does: `tv_power` carries the alias "turn", so *"turn the tv volume
            # up"* tied it with `tv_volume` — which matched TWO words of the
            # request in its own name — and the tie-break handed the volume
            # request to the power toggle. A synonym must not outweigh the
            # thing itself.
            haystack_name = f"{name} {entry.action_type}".lower()
            haystack_alias = " ".join(getattr(entry, "aliases", ())).lower()
            description = (entry.description or "").lower()
            score = 0.0
            for term in terms:
                forms = _variants(term)
                if term == name.lower():
                    score += 10.0
                elif any(f in haystack_name for f in forms):
                    score += 4.0
                elif any(f in haystack_alias for f in forms):
                    score += 3.0
                if any(f in description for f in forms):
                    score += 1.0
            if score >= MIN_SCORE:
                # Tie-break on name length so the more specific of two equally
                # scoring tools wins, and so the order is stable across runs.
                hits.append(SearchHit(name, score - len(name) / 1000.0,
                                      entry.tier))
        hits.sort(key=lambda h: (-h.score, h.name))
        return hits

    # -- loading ----------------------------------------------------------- #

    def promote(self, names: list[str]) -> tuple[list[str], list[str]]:
        """Make tools resident. Returns `(promoted, evicted)`.

        Eviction is oldest-promoted-first and never touches the base set. The
        alternative — raising `max_tools` to fit — would quietly undo the
        deliberate 8-tool ceiling that exists because small models degrade past
        it, and would do so at exactly the moment the model is already
        struggling enough to go looking for a tool.

        **A tool promoted by THIS search is never the victim.** `names` arrives
        best-first, so evicting from the front of the queue used to throw away
        the highest-ranked hit to make room for a worse one — and it stayed in
        the returned `promoted` list, so the model was told "you can call
        tv_volume now" about a tool that was no longer in its tool list, and got
        `unknown tool` when it tried. Overflow now drops the WORSE tail of this
        search instead, which is what "no room" honestly means.
        """
        promoted, evicted = [], []
        protected: set[str] = set()
        for name in names:
            if name in self.resident() or self.registry.get(name) is None:
                continue
            self._promoted.append(name)
            promoted.append(name)
            protected.add(name)
            while self._over_cap():
                victim = next((n for n in self._promoted if n not in protected),
                              None)
                if victim is None:
                    # Nothing older left to drop: this promotion cannot fit.
                    # Undo it rather than report a tool the model does not have.
                    self._promoted.remove(name)
                    promoted.remove(name)
                    protected.discard(name)
                    break
                self._promoted.remove(victim)
                evicted.append(victim)
        return promoted, evicted

    def _over_cap(self) -> bool:
        """Would this turn's tool list exceed the cap? Counts what `defs()`
        actually sends: residents, the extras, and `search_tools` itself."""
        return len(self.resident()) + len(self.extra) + 1 > self.max_tools

    def handle(self, arguments: dict) -> str:
        """Run one `search_tools` call and return what the model should read.

        The result is written as an OBSERVATION, not a status: it names what is
        now available, what was dropped to make room, and — when nothing
        matched — what to do instead. A bare "no results" sends a model into a
        second and third identical search.
        """
        query = (arguments or {}).get("query") or ""
        hits = self.search(query)
        record = {"query": query, "found": [h.name for h in hits[:MAX_RESULTS]]}

        # Said FIRST, and said even when there are other matches: a model that is
        # already holding the right tool needs to stop searching, not to be
        # handed three more. See `already_have`.
        holding = [h.name for h in self.already_have(query)[:MAX_RESULTS]]
        if holding:
            self.searches.append(record | {"promoted": [], "evicted": [],
                                           "already": holding})
            return (f"You already have {', '.join(holding)} loaded - call it "
                    f"directly rather than searching again. "
                    + (f"(Also available to load: "
                       f"{', '.join(h.name for h in hits[:2])}.)"
                       if hits else ""))

        if not hits:
            self.searches.append(record | {"promoted": [], "evicted": []})
            return (f"No tool matches \"{query}\". Do not search again with "
                    "similar words — JARVIS has no tool for this. Use the tools "
                    "you already have, or tell the owner what capability is "
                    "missing.")

        chosen = [h.name for h in hits[:MAX_RESULTS]]
        promoted, evicted = self.promote(chosen)
        record |= {"promoted": promoted, "evicted": evicted}
        self.searches.append(record)

        if not promoted:
            return (f"Found {', '.join(chosen)}, but there is no room to load "
                    "any of them alongside the tools you already have. Work "
                    "with your current tools, or say what you cannot do.")

        lines = [f"Loaded {len(promoted)} tool(s); you can call them now:"]
        for name in promoted:
            entry = self.registry.get(name)
            summary = (entry.description or "").split(".")[0]
            note = " (needs the owner's confirmation)" if entry.tier == "CONFIRM" else ""
            lines.append(f"  - {name}: {summary}.{note}")
        if evicted:
            lines.append(f"Dropped to make room: {', '.join(evicted)}. "
                         "Search again if you need one back.")
        # Everything that matched and is NOT now callable, in one list: the tail
        # beyond MAX_RESULTS, plus anything the cap refused room for. A match
        # dropped silently reads to the model as a tool it has.
        skipped = ([n for n in chosen if n not in promoted]
                   + [h.name for h in hits[MAX_RESULTS:]])
        if skipped:
            lines.append(f"Also matched but not loaded: {', '.join(skipped)}.")
        return "\n".join(lines)
