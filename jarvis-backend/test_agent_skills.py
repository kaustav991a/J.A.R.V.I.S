"""Harness for §6.8.2 rule 18 — playbooks, and progressive disclosure of them.

The claim this makes is an economic one: ~6 procedures stay available at the
cost of ~6 lines, because Groq has no prompt caching and every token of the
system prompt is bought on every request of every run. So the rows below check
the ARITHMETIC as well as the wiring — that the index really is a fraction of
the bodies, and that a body only ever arrives when the model asks for one.

The other half is a boundary: a skill is INSTRUCTIONS and never CAPABILITY.
Loading one must not add a tool, raise a tier, or reach the engine. A playbook
that names a blocked action is still a playbook.
"""

import asyncio
import sys

from agent_tier_fixture import TIERS, tier_lookup
from modules import agent_core as ac
from modules import agent_runner as ar
from modules import agent_skills as asx
from modules import agent_tools as at
from modules.agent_core import AgentLimits
from modules.agent_search import ToolShelf
from modules.tool_calls import ToolCall, ToolTurn

SKILL = """---
name: {name}
description: {description}
---

# {name}

{body}
"""


def library(tmp, files=None):
    """A library over a temp directory, so nothing here depends on the shipped
    playbooks staying the way they are today."""
    files = files or {"alpha": ("Do alpha things.", "Step one.\nStep two.")}
    for name, (description, body) in files.items():
        (tmp / f"{name}.md").write_text(
            SKILL.format(name=name, description=description, body=body),
            encoding="utf-8")
    return asx.SkillLibrary(tmp)


def tmpdir():
    import tempfile
    from pathlib import Path
    return Path(tempfile.mkdtemp(prefix="jarvis-skills-"))


def registry():
    return at.build_default_registry(tier_lookup())


def run(coro):
    return asyncio.run(coro)


class FakeEngine:
    def __init__(self, result="RESULT"):
        self.result, self.seen = result, []

    async def execute_with_retry(self, payload, return_meta=False, trace_id=None, *,
                                 governance_bypass=False, permission_tier="admin"):
        self.seen.append(payload["action_type"])
        return {"state": "COMPLETE", "result": self.result}


def tool_turn(_tool, _cid="t1", **args):
    return ToolTurn(ok=True, provider="fake",
                    tool_calls=[ToolCall(id=_cid, name=_tool, arguments=args)])


def final(text="Done, Sir."):
    return ToolTurn(ok=True, text=text, provider="fake")


# ── the file format ─────────────────────────────────────────────────────────

def test_frontmatter_is_split_from_the_body():
    meta, body = asx.parse_skill_file(
        "---\nname: x\ndescription: y\n---\n\n# Heading\n\ntext\n")
    assert meta == {"name": "x", "description": "y"}
    assert body.startswith("# Heading") and "text" in body


def test_a_file_with_no_frontmatter_is_still_a_playbook():
    """A broken header is a worse reason to lose a procedure than to serve it
    unnamed — the caller falls back to the filename."""
    meta, body = asx.parse_skill_file("# Just a heading\n\ntext")
    assert meta == {} and body.startswith("# Just a heading")


def test_unterminated_frontmatter_is_not_served_as_a_procedure():
    meta, body = asx.parse_skill_file("---\nname: x\ndescription: y\n\n# no fence")
    assert meta == {}
    assert body.startswith("---"), "the header leaked into the body silently"


def test_the_filename_names_the_skill_when_the_header_does_not():
    tmp = tmpdir()
    (tmp / "orphan.md").write_text("no header here", encoding="utf-8")
    lib = asx.SkillLibrary(tmp)
    assert lib.names() == ["orphan"]
    assert "(no description" in lib.get("orphan").description


def test_a_name_that_cannot_be_typed_back_is_skipped():
    """A model that cannot reproduce the name will keep guessing at it, and each
    guess is a step."""
    tmp = tmpdir()
    (tmp / "bad.md").write_text(
        "---\nname: Not A Valid Name!\ndescription: d\n---\nbody\n",
        encoding="utf-8")
    assert asx.SkillLibrary(tmp).names() == []


def test_a_runaway_description_cannot_make_the_index_expensive():
    """The index is bought on every request. One overlong description in one
    file would quietly undo the reason this module exists."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("x" * 400, "body")})
    assert len(lib.get("alpha").description) <= asx.MAX_DESCRIPTION_CHARS
    assert lib.get("alpha").description.endswith("…")


# ── the economics, which are the claim ──────────────────────────────────────

def test_the_index_is_a_line_per_skill_and_nothing_else():
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "A" * 4000),
                        "beta": ("Do beta.", "B" * 4000)})
    index = lib.index()
    assert "alpha: Do alpha." in index and "beta: Do beta." in index
    assert "AAAA" not in index and "BBBB" not in index


def test_the_index_costs_a_fraction_of_the_bodies():
    """The whole rule in one assertion: what sits in context permanently must be
    small against what it stands in for."""
    tmp = tmpdir()
    lib = library(tmp, {n: (f"Do {n}.", f"{n} body\n" * 200)
                        for n in ("alpha", "beta", "gamma")})
    bodies = sum(len(lib.get(n).body) for n in lib.names())
    assert len(lib.index()) * 10 < bodies, \
        f"index {len(lib.index())} vs bodies {bodies} — disclosure is not paying"


def test_a_body_is_capped_and_the_cap_is_announced():
    """Rule 8. A truncated procedure that ends mid-sentence reads as a complete
    one that happens to stop."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "x" * (asx.MAX_BODY_CHARS + 500))})
    out = lib.load("alpha")
    assert "truncated" in out and "not all of it" in out
    assert len(out) < asx.MAX_BODY_CHARS + 500


# ── loading ─────────────────────────────────────────────────────────────────

def test_loading_returns_the_body_with_its_name_and_purpose():
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha things.", "Step one.\nStep two.")})
    out = lib.load("alpha")
    assert out.startswith("PLAYBOOK: alpha")
    assert "Do alpha things." in out and "Step two." in out


def test_an_unknown_name_lists_the_real_ones_and_says_not_to_guess():
    """A bare "not found" sends a model into a second and third guess — the same
    failure `search_tools` was written to avoid."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "body")})
    out = lib.load("alfa")
    assert "alpha" in out and "Do not guess" in out


def test_an_edit_during_a_run_is_picked_up():
    """A playbook you cannot iterate on mid-session is one that stays wrong."""
    import os
    import time
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "OLD TEXT")})
    assert "OLD TEXT" in lib.load("alpha")
    path = tmp / "alpha.md"
    path.write_text(SKILL.format(name="alpha", description="Do alpha.",
                                 body="NEW TEXT"), encoding="utf-8")
    os.utime(path, (time.time() + 2, time.time() + 2))
    out = lib.load("alpha")
    assert "NEW TEXT" in out and "OLD TEXT" not in out


def test_a_name_cannot_escape_the_skills_directory():
    """The model picks this string, and a traversal attempt is exactly what a
    confused model produces after two failed loads."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "body")})
    for hostile in ("../../.env", "..\\..\\.env", "/etc/passwd",
                    "alpha/../../secret"):
        out = lib.load(hostile)
        assert "no playbook called" in out, hostile


def test_every_load_is_recorded_for_the_trail():
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "body")})
    lib.load("alpha")
    lib.load("ghost")
    assert lib.loads == [{"name": "alpha", "ok": True},
                         {"name": "ghost", "ok": False}]


# ── the tool, and the boundary it must not cross ────────────────────────────

def test_the_loader_offers_only_names_that_exist():
    """An enum, so a hallucinated name is refused by validation before it costs
    a step."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "b"), "beta": ("Do beta.", "b")})
    assert lib.tool_def()["input_schema"]["properties"]["name"]["enum"] == \
        ["alpha", "beta"]


def test_an_empty_library_offers_no_loader_at_all():
    """A loader with nothing to load is a tool slot spent on a dead end."""
    assert asx.SkillLibrary(tmpdir()).tool_def() is None


def test_the_loader_says_it_grants_no_capability():
    description = asx.LOAD_TOOL["description"]
    assert "never grants a tool" in description
    assert "confirmation" in description


def test_loading_a_skill_never_reaches_the_engine_or_the_authorizer():
    """The boundary. A skill is instructions; if it could be dispatched or could
    pass through governance it would be something else entirely."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "Step one.")})
    engine = FakeEngine()
    authorised = []
    turns = [tool_turn(asx.LOAD_TOOL_NAME, name="alpha"), final("Done.")]

    def model(messages, tools, **k):
        return turns.pop(0)

    async def authorize(call):
        authorised.append(call.name)
        return ac.Decision(True)

    result = run(ac.run_agent_loop(
        "do the thing", registry().defs("files"),
        registry().executor(engine), call_model=model, authorize=authorize,
        skills=lib))
    assert result.ok
    assert engine.seen == [], "a playbook was dispatched as an action"
    assert asx.LOAD_TOOL_NAME not in authorised
    body = [m for m in result.messages if m.get("role") == "tool"][0]["content"]
    assert "Step one." in body


def test_a_bad_skill_name_does_not_count_against_the_error_streak():
    """Three unlucky guesses must not kill a healthy run — the same rule the
    tool search follows."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "body")})
    turns = [tool_turn(asx.LOAD_TOOL_NAME, "c1", name="ghost"),
             tool_turn(asx.LOAD_TOOL_NAME, "c2", name="phantom"),
             tool_turn(asx.LOAD_TOOL_NAME, "c3", name="wraith"),
             final("Carried on anyway, Sir.")]
    result = run(ac.run_agent_loop(
        "do the thing", registry().defs("files"),
        registry().executor(FakeEngine()),
        call_model=lambda m, t, **k: turns.pop(0), skills=lib,
        limits=AgentLimits(max_consecutive_errors=2)))
    assert result.ok and result.answer == "Carried on anyway, Sir."


def test_the_loader_is_offered_every_turn_even_as_the_shelf_rebuilds():
    """With a shelf the tool list is regenerated before each model turn, so a
    loader added once would vanish after the first."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "body")})
    reg = registry()
    shelf = ToolShelf(reg, base=reg.set_names("files"), max_tools=8,
                      extra=[lib.tool_def()])
    offered = []
    turns = [tool_turn("list_directory", "c1", path=r"F:\work"), final("Done.")]

    def model(messages, tools, **k):
        offered.append([t["name"] for t in tools])
        return turns.pop(0)

    run(ac.run_agent_loop("do the thing", [], registry().executor(FakeEngine()),
                          call_model=model, skills=lib, shelf=shelf))
    assert all(asx.LOAD_TOOL_NAME in names for names in offered), offered
    assert [names.count(asx.LOAD_TOOL_NAME) for names in offered] == \
        [1] * len(offered), "the loader was offered twice"


def test_the_loader_counts_against_the_tool_cap():
    """It occupies a slot for the whole run. A shelf that did not know would
    promote one tool too many and the loop would refuse the list it just
    built."""
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "body")})
    reg = registry()
    plain = ToolShelf(reg, base=["system_status"], max_tools=8)
    with_loader = ToolShelf(reg, base=["system_status"], max_tools=8,
                            extra=[lib.tool_def()])
    assert with_loader.room() == plain.room() - 1


# ── the runner wiring, and the shipped playbooks ────────────────────────────

def test_skills_are_on_by_default_and_can_be_switched_off():
    assert ar.skills_enabled({}) is True
    for off in ("0", "false", "no", "off", "OFF"):
        assert ar.skills_enabled({ar.SKILLS_ENV: off}) is False, off


def test_the_index_reaches_the_system_prompt_and_the_bodies_do_not():
    tmp = tmpdir()
    lib = library(tmp, {"alpha": ("Do alpha.", "SECRET BODY TEXT")})
    prompt = ar.system_prompt(lib)
    assert "alpha: Do alpha." in prompt
    assert "SECRET BODY TEXT" not in prompt
    assert ar.workspace_note() in prompt, "the workspace note was displaced"


def test_a_prompt_without_skills_is_unchanged():
    assert ar.system_prompt() == ar.system_prompt(None)


def test_the_shipped_playbooks_load_and_are_described():
    """The real directory, not a fixture: a playbook that fails to parse is
    invisible, and an invisible playbook is one nobody notices is missing."""
    lib = ar.skill_library()
    assert len(lib.names()) >= 5, lib.names()
    for name in lib.names():
        skill = lib.get(name)
        assert skill.description and "(no description" not in skill.description, name
        assert len(skill.body) > 200, f"{name} is too short to be a procedure"
        assert lib.load(name).startswith(f"PLAYBOOK: {name}")


def test_the_shipped_playbooks_stay_within_the_body_cap():
    """A capped playbook is served truncated — which is honest, but it means the
    end of a procedure the author wrote is not being read."""
    lib = ar.skill_library()
    for name in lib.names():
        assert len(lib.get(name).body) <= asx.MAX_BODY_CHARS, \
            f"{name} is {len(lib.get(name).body)} chars — split it"


def test_the_shipped_playbooks_name_real_tools_and_real_arguments():
    """A procedure that names a tool or an argument that does not exist costs a
    step and a repair every time it is followed — and it reads as authoritative,
    so the model trusts it over the schema in front of it.

    The check is deliberately over ARGUMENTS too: `replace_all` and `old_string`
    are the kind of detail a playbook exists to carry, and the kind that goes
    stale silently when a schema is renamed.
    """
    import re
    reg = registry()
    known = set(reg.names()) | {asx.LOAD_TOOL_NAME, "search_tools"}
    for tool in reg.names():
        known |= set((reg.get(tool).input_schema.get("properties") or {}))
    lib = ar.skill_library()
    for name in lib.names():
        for match in re.findall(r"`([a-z][a-z0-9_]{3,})`", lib.get(name).body):
            if "_" not in match:
                continue          # prose in backticks, not an identifier
            assert match in known, \
                f"{name} names something that does not exist: {match}"


if __name__ == "__main__":
    import traceback

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    sys.exit(1 if failed else 0)
