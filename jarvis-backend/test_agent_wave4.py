"""Harness for §6.8.2 wave 4 — git.

Five tools, and the tier split is the design: reading a repository is free, and
the two actions that change history or publish it need a human. So three rows
here are about targets, and the rest are about the boundary between "look" and
"change" holding.

The sharp edge in this wave is the pipe. Three of the five handlers partition on
the FIRST pipe to separate an optional repo path from the rest — which means a
commit message containing a pipe silently becomes a directory name plus a
truncated message. That is refused in code, not in the description.
"""

import sys

from agent_tier_fixture import TIERS, tier_lookup
from modules import agent_tools as at
from modules.agent_search import ToolShelf
from modules.tool_calls import ToolCall

WAVE4_READ = ("github_status", "github_diff", "github_log")
WAVE4_WRITE = ("github_commit", "github_push")


def registry():
    return at.build_default_registry(tier_lookup())


def call(name, **args):
    return ToolCall(id="c1", name=name, arguments=args)


def target(name, **args):
    return registry().to_payload(call(name, **args))["target"]


# ── target composition ──────────────────────────────────────────────────────

def test_the_repo_path_is_omitted_rather_than_guessed():
    """Every handler treats an empty target as "the active workspace repo".
    Sending a placeholder path instead would point them somewhere real and
    wrong."""
    for name in ("github_status", "github_diff", "github_push"):
        assert target(name) == "", name


def test_a_given_repo_path_is_passed_through():
    assert target("github_status", repo_path=r"F:\work\JARVIS-Project") == \
        r"F:\work\JARVIS-Project"


def test_the_log_count_goes_where_the_handler_looks_for_it():
    """Handler: "", "N", or "repo|N" — it partitions on the pipe first and only
    then asks whether what is left is a digit."""
    assert target("github_log") == ""
    assert target("github_log", count=10) == "10"
    assert target("github_log", count=10, repo_path=r"F:\r") == r"F:\r|10"
    # A repo with no count still has to carry the separator, or the path would
    # be read as the count and ignored.
    assert target("github_log", repo_path=r"F:\r") == r"F:\r|"


def test_a_commit_message_travels_alone_when_no_repo_is_given():
    """Handler: "message" or "repo_path|message"."""
    assert target("github_commit", message="fix: the thing") == "fix: the thing"


def test_a_commit_with_a_repo_puts_the_path_first():
    got = target("github_commit", message="fix: the thing", repo_path=r"F:\r")
    repo, _, message = got.partition("|")
    assert repo == r"F:\r" and message == "fix: the thing"


def test_a_pipe_in_the_commit_message_is_refused():
    """Without a repo path the handler reads everything before the first pipe as
    a directory, so the commit lands in the wrong place — or nowhere — and the
    message is truncated either way."""
    problem = at._git_commit_precondition({"message": "fix: a | b"})
    assert problem and "pipe" in problem
    # With an explicit repo the pipe is unambiguous, so it is allowed.
    assert at._git_commit_precondition(
        {"message": "fix: a | b", "repo_path": r"F:\r"}) is None


def test_the_pipe_refusal_is_wired_to_the_tool():
    decision = registry().authorizer(allow_confirm=True)(
        call("github_commit", message="fix: a | b"))
    assert decision.allowed is False and "pipe" in decision.reason


# ── the tier split ──────────────────────────────────────────────────────────

def test_reading_a_repository_is_auto():
    reg = registry()
    for name in WAVE4_READ:
        assert reg.tier_of(name) == "AUTO", f"{name} is {reg.tier_of(name)}"


def test_changing_or_publishing_history_needs_a_human():
    reg = registry()
    for name in WAVE4_WRITE:
        assert reg.tier_of(name) == "CONFIRM", f"{name} is {reg.tier_of(name)}"


def test_neither_writer_is_findable_in_an_unattended_run():
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=False)
    for query in ("commit my changes", "push to github"):
        names = [h.name for h in s.search(query)]
        assert not set(names) & set(WAVE4_WRITE), f"{query!r} offered {names}"


def test_both_writers_are_findable_when_someone_can_approve():
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=True)
    assert "github_commit" in [h.name for h in s.search("commit my changes")]
    assert "github_push" in [h.name for h in s.search("push to the remote")]


# ── the descriptions carry what only the handler knows ──────────────────────

def test_commit_says_it_stages_everything():
    """`_github_commit` stages ALL changes. A model that thinks it can commit
    one file will describe a commit that did not happen."""
    description = registry().get("github_commit").description
    assert "EVERY change" in description
    assert "cannot commit a subset" in description


def test_commit_and_push_are_not_confused_with_each_other():
    reg = registry()
    assert "github_push" in reg.get("github_commit").description
    assert "publish" in reg.get("github_push").description.lower()


def test_the_two_status_tools_say_which_status_they_mean():
    """`github_status` and `system_status` share a word and mean nothing alike.
    The one a model is likelier to reach for by accident names the other."""
    assert "system_status" in registry().get("github_status").description


def test_the_repo_default_is_stated_everywhere_it_applies():
    reg = registry()
    for name in WAVE4_READ + WAVE4_WRITE:
        assert "active workspace repo" in reg.get(name).description \
            or "active workspace repo" in str(reg.get(name).input_schema), name


# ── what cannot be registered ───────────────────────────────────────────────

def test_the_three_undispatched_github_actions_are_absent():
    """`github_create_pr`, `github_create_repo` and `github_merge_pr` are in
    governance but have no `action ==` branch in the engine, so registering one
    would put a tool in front of the model that can never run."""
    names = registry().names()
    for absent in ("github_create_pr", "github_create_repo", "github_merge_pr"):
        assert absent not in names


# ── findability and the wired intents ───────────────────────────────────────

def test_the_reads_are_findable_by_plain_words():
    s = ToolShelf(registry(), base=["system_status"], allow_confirm=False)
    for query, expected in (
        ("what have i changed in the repo", "github_status"),
        ("show me the diff", "github_diff"),
        ("what did i commit recently", "github_log"),
    ):
        names = [h.name for h in s.search(query)]
        assert expected in names, f"{query!r} did not surface {expected}: {names}"


def test_wave_four_did_not_change_what_the_wired_intents_offer():
    reg = registry()
    assert len(reg.set_names("research")) == 6
    assert not set(reg.set_names("research")) & set(WAVE4_READ + WAVE4_WRITE)


def test_every_wave_four_action_is_in_the_shared_tier_fixture():
    reg = registry()
    for name in WAVE4_READ + WAVE4_WRITE:
        assert reg.get(name).action_type in TIERS, name


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
