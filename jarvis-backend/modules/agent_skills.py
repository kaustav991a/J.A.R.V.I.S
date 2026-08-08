r"""agent_skills.py — progressive disclosure for INSTRUCTIONS, not just tools.

Roadmap §6.8.2 (rule 18 of AGENT-TOOLING-REFERENCE.md), and the last of the
eighteen rules to land. `agent_search` made every TOOL addressable while few
stay resident; this does the same for the procedures that say how to use them.

WHY THIS IS NOT "PUT IT IN THE SYSTEM PROMPT"
---------------------------------------------
JARVIS runs on a FREE Groq cascade, and **Groq has no prompt caching**. Every
token of the system prompt is paid for on every request, and one agent task is
5–20 requests carrying a growing transcript. A 900-word procedure pasted into
the prompt is therefore bought 5–20 times per task — including on the turns that
have nothing to do with it. A one-line description is bought instead, and the
body only when the model asks for it.

The saving is not the whole point. The other half is that a prompt which
explains file editing, TV apps, mail and git at once is a prompt in which none of
them stand out; a small model reading it does every task slightly wrong.

FIVE THINGS THIS DOES DELIBERATELY
----------------------------------
1. **A skill is instructions, never capability.** Loading one cannot add a tool,
   raise a tier, or bypass a gate. A skill that says "use `delete_file`" changes
   nothing: `delete_file` is BLOCK, so it is not registered, so it cannot be
   called. Tools are capability and governance owns them; skills only ever
   change what the model KNOWS. This is why `load_skill` — like `search_tools` —
   is answered inside the loop and never reaches the authorizer or the engine.
2. **The index is the disclosure, and it is bounded.** One line per skill goes
   into the system prompt; nothing else does. `NAME_PATTERN` and a length cap on
   `description` keep a badly-written file from quietly making the index
   expensive, which would undo the entire reason this exists.
3. **A body is capped and the cap is ANNOUNCED** (rule 8). A skill that grew
   past the cap is truncated with a line saying so, rather than ending
   mid-sentence and reading as a complete procedure that happens to stop.
4. **Files are re-read when they change.** `mtime` is checked per load, so
   editing a playbook takes effect on the next call instead of on the next
   restart. A skill you cannot iterate on during a live session is a skill that
   stays wrong.
5. **A name cannot escape the directory.** Names are matched against
   `[a-z0-9-]+` and resolved paths must stay inside the skills root — the model
   picks this string, and `../../.env` is exactly the kind of thing a confused
   model produces when a load fails twice.

FILE FORMAT — deliberately not YAML
-----------------------------------
    ---
    name: workspace-edit
    description: How to change a file safely, and when a whole-file write is wrong.
    ---

    <the body, in markdown>

Parsed by hand: `key: value` lines between two `---` fences. Adding PyYAML for
four keys would be a dependency for a problem this project does not have, and
`protobuf` pins already make this environment one to leave alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["SkillLibrary", "Skill", "LOAD_TOOL", "LOAD_TOOL_NAME",
           "MAX_BODY_CHARS", "MAX_DESCRIPTION_CHARS", "NAME_PATTERN"]

LOAD_TOOL_NAME = "load_skill"

#: A skill body is a procedure, not a manual. The cap is generous enough for a
#: real playbook and small enough that loading one cannot swallow the transcript
#: budget (`AgentLimits.max_transcript_chars`, 20 000) in a single call.
MAX_BODY_CHARS = 6000

#: The index line is what makes this cheap; a description that runs long makes it
#: expensive on EVERY request, which is the thing this module exists to avoid.
MAX_DESCRIPTION_CHARS = 160

#: Lowercase, digits and hyphens. Also the anti-traversal guard — see point 5.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")

_FENCE = "---"


@dataclass
class Skill:
    """One playbook: what it is for, and the procedure itself."""

    name: str
    description: str
    body: str
    path: Path
    mtime: float

    @property
    def index_line(self) -> str:
        """The single line this skill costs while it is NOT loaded."""
        return f"  - {self.name}: {self.description}"


def parse_skill_file(text: str) -> tuple[dict, str]:
    """Split frontmatter from body. Returns ({}, whole_text) if there is none.

    Tolerant on purpose: a playbook with a broken header is still a playbook,
    and refusing to load it would be a worse failure than loading it unnamed.
    The CALLER decides what to do with a missing name — see `SkillLibrary.load`.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != _FENCE:
        return {}, text
    meta: dict = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == _FENCE:
            return meta, "\n".join(lines[i + 1:]).strip("\n")
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip().lower()] = value.strip()
    # Unterminated frontmatter: treat the whole file as body rather than
    # silently serving a header as a procedure.
    return {}, text


@dataclass
class SkillLibrary:
    """The playbooks on disk, their index, and the one tool that opens them."""

    root: Path
    #: name -> Skill, populated by `refresh()`.
    _skills: dict = field(default_factory=dict, init=False)
    #: Every load this run made, for the audit trail and the HUD.
    loads: list = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.refresh()

    # -- what is on disk --------------------------------------------------- #

    def refresh(self) -> list[str]:
        """Re-scan the directory. Returns the names it now holds.

        Cheap enough to call per run: a stat and a small read per file, over a
        directory that holds tens of files at most.
        """
        found: dict = {}
        if not self.root.is_dir():
            self._skills = found
            return []
        for path in sorted(self.root.glob("*.md")):
            skill = self._read(path)
            if skill is not None:
                found[skill.name] = skill
        self._skills = found
        return list(found)

    def _read(self, path: Path) -> Skill | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            return None
        meta, body = parse_skill_file(text)
        name = (meta.get("name") or path.stem).strip().lower()
        if not NAME_PATTERN.match(name):
            # An unnameable file is skipped rather than served under a name the
            # model cannot type back — and a name it cannot type is a name it
            # will keep guessing at.
            print(f"[SKILLS] ignoring {path.name}: bad skill name {name!r}",
                  flush=True)
            return None
        description = (meta.get("description") or "").strip()
        if not description:
            description = "(no description — this skill needs one)"
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS - 1].rstrip() + "…"
        return Skill(name=name, description=description, body=body.strip(),
                     path=path, mtime=mtime)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(str(name or "").strip().lower())

    # -- the index, which is the whole cost while nothing is loaded -------- #

    def index(self) -> str:
        """The lines that go into the system prompt. Empty when there are none."""
        if not self._skills:
            return ""
        lines = ["- Playbooks you can open with `load_skill` when a task matches "
                 "one. Open it BEFORE starting that kind of work, not after it "
                 "goes wrong:"]
        lines += [self._skills[n].index_line for n in sorted(self._skills)]
        return "\n".join(lines) + "\n"

    def tool_def(self) -> dict | None:
        """The `load_skill` definition, or None when there is nothing to load.

        Offering a loader with an empty library is a tool slot spent on a
        guaranteed dead end.
        """
        if not self._skills:
            return None
        definition = dict(LOAD_TOOL)
        definition["input_schema"] = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": self.names(),
                    "description": "Which playbook to open.",
                },
            },
            "required": ["name"],
        }
        return definition

    # -- loading ------------------------------------------------------------ #

    def load(self, name: str) -> str:
        """Return one skill's body, re-read from disk if it changed."""
        key = str(name or "").strip().lower()
        skill = self._skills.get(key)
        if skill is None:
            self.loads.append({"name": key, "ok": False})
            known = ", ".join(self.names()) or "(none)"
            return (f"There is no playbook called \"{name}\". The ones that "
                    f"exist are: {known}. Do not guess another name — carry on "
                    f"with what you know.")

        fresh = self._reread_if_changed(skill)
        self.loads.append({"name": fresh.name, "ok": True})
        body = fresh.body
        if len(body) > MAX_BODY_CHARS:
            dropped = len(body) - MAX_BODY_CHARS
            body = (body[:MAX_BODY_CHARS]
                    + f"\n\n… [playbook truncated — {dropped} more characters "
                      f"were not shown. What you have is the beginning of the "
                      f"procedure, not all of it.]")
        return f"PLAYBOOK: {fresh.name}\n{fresh.description}\n\n{body}"

    def _reread_if_changed(self, skill: Skill) -> Skill:
        """Pick up an edit made since the run started, without a restart."""
        try:
            if skill.path.stat().st_mtime == skill.mtime:
                return skill
        except OSError:
            return skill
        updated = self._read(skill.path)
        if updated is None or updated.name != skill.name:
            # A rename or a break mid-run: serve what was already loaded rather
            # than nothing, and leave the index alone until the next refresh.
            return skill
        self._skills[updated.name] = updated
        return updated

    def handle(self, arguments: dict) -> str:
        """Answer one `load_skill` call. Never dispatched, never authorised."""
        return self.load((arguments or {}).get("name", ""))


#: Rule 1 — when to call it, and when not. The "before, not after" line is the
#: one that matters: a procedure opened after the mistake has already been made
#: costs a step and fixes nothing.
LOAD_TOOL = {
    "name": LOAD_TOOL_NAME,
    "description": (
        "Open one of JARVIS's playbooks — a short procedure for a kind of work "
        "that has a right and a wrong way to do it. The list of playbooks and "
        "what each is for is in your instructions.\n\n"
        "Open one BEFORE starting that kind of task, not after something goes "
        "wrong. It costs one step and usually saves several.\n\n"
        "It gives you instructions only — it never grants a tool you do not "
        "already have, and never removes the need for the owner's confirmation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Which playbook to open."},
        },
        "required": ["name"],
    },
}
