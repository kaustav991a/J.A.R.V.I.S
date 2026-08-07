"""Harness for modules/agent_files.py — §6.8.1 gaps C, D, E, F, G.

Real files in a temp directory, and the REAL `ToolRegistry` for the wiring
checks — because the point of every one of these is that the guarantee lives in
code the loop actually runs, not in a prompt (rule 3). A precondition nothing
consults is the `f84f644` failure with a different name.

The five properties, and the concrete bug each one closes:

  C  a read comes back with line numbers, so a cited `path:line` is real
  D  a long file is readable PAST the first window — the old path announced
     truncation and offered no way to continue
  E  an existing file cannot be overwritten by an agent that never read it,
     and not at all if it changed on disk since
  F  an `old_string` matching more than once is REFUSED — `patch_file`
     defaults to replacing every occurrence, so this used to happen silently
  G  a relative path is refused, because different tools resolve one against
     different roots (the live 2026-07-26 failure)
"""

import os
import shutil
import sys
import tempfile
import time

from modules import agent_files as af
from modules import agent_tools as at
from modules.tool_calls import ToolCall

from agent_tier_fixture import TIERS

HEADER_RULE = "─" * 60


def registry():
    return at.build_default_registry(lambda a: TIERS.get(a, "BLOCK"))


def call(name, **args):
    return ToolCall(id="c1", name=name, arguments=args)


class Temp:
    """A real directory with real files — the preconditions stat the disk."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="jarvis_files_")
        af.ledger.clear()
        return self

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def path(self, name):
        return os.path.join(self.dir, name)

    def read(self, name):
        with open(self.path(name), encoding="utf-8") as handle:
            return handle.read()

    def __exit__(self, *exc):
        af.ledger.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


def engine_read(body):
    """What `workspace_agent.read_file` actually returns: header, then body."""
    return f"FILE: x\nLINES: n | SIZE: n bytes\n{HEADER_RULE}\n{body}"


# ── C: anchors (rule 5) ──────────────────────────────────────────────────────

def test_a_read_comes_back_with_line_numbers():
    out = af.paginate_read(engine_read("alpha\nbravo\ncharlie"), {})
    assert "\talpha" in out and "\tbravo" in out, out
    assert "   1\talpha" in out, f"numbering does not start at 1: {out!r}"


def test_numbering_counts_the_file_not_the_header():
    """Off-by-three is the failure mode here: `workspace_agent` prints three
    header lines, and numbering them would make every citation wrong."""
    out = af.paginate_read(engine_read("first line"), {})
    assert "   1\tfirst line" in out, out
    assert "1\tFILE:" not in out, "the header was numbered as file content"


def test_a_result_that_is_not_a_file_read_passes_through_untouched():
    """Refusals and not-founds are already instructions; numbering them is
    nonsense, and it would also hide that the read failed."""
    for text in ("Access denied: 'F:/x' is outside the permitted workspace roots.",
                 "File not found: F:/x.md", "Read error: boom"):
        assert af.paginate_read(text, {}) == text


# ── D: paging (rule 8) ───────────────────────────────────────────────────────

def test_a_long_file_is_windowed_and_says_how_to_continue():
    body = "\n".join(f"line {i}" for i in range(1, 501))
    out = af.paginate_read(engine_read(body), {})
    assert "   1\tline 1" in out
    assert f"line {af.DEFAULT_READ_LIMIT}" in out
    assert f"line {af.DEFAULT_READ_LIMIT + 1}" not in out, "the window was not applied"
    assert "offset=200 to continue" in out, \
        "truncation was announced without a way to continue — only half of rule 8"


def test_the_second_window_starts_where_the_first_ended():
    body = "\n".join(f"line {i}" for i in range(1, 501))
    out = af.paginate_read(engine_read(body), {"offset": 200, "limit": 50})
    assert " 201\tline 201" in out, out
    assert "line 200" not in out and "line 251" not in out
    assert "offset=250 to continue" in out


def test_the_last_window_says_end_of_file_rather_than_offering_more():
    body = "\n".join(f"line {i}" for i in range(1, 11))
    out = af.paginate_read(engine_read(body), {"offset": 5, "limit": 50})
    assert "end of file" in out, out
    assert "to continue" not in out


def test_an_offset_past_the_end_says_so_instead_of_returning_nothing():
    """An empty window reads as "the file ends here", which is a different and
    false claim."""
    out = af.paginate_read(engine_read("a\nb\nc"), {"offset": 99})
    assert "past the end" in out and "3 lines" in out, out


def test_a_nonsense_offset_or_limit_is_clamped_not_crashed():
    body = "\n".join(f"line {i}" for i in range(1, 21))
    for args in ({"offset": -5}, {"limit": 0}, {"limit": "ten"},
                 {"offset": None}, {"limit": 10 ** 9}):
        out = af.paginate_read(engine_read(body), args)
        assert "   1\tline 1" in out, f"{args} -> {out[:120]!r}"


def test_the_limit_is_capped_so_one_call_cannot_flood_the_transcript():
    body = "\n".join(f"line {i}" for i in range(1, 3001))
    out = af.paginate_read(engine_read(body), {"limit": 99999})
    assert f"line {af.MAX_READ_LIMIT}" in out
    assert f"line {af.MAX_READ_LIMIT + 1}" not in out


# ── G: absolute paths (rule 7) ───────────────────────────────────────────────

def test_a_relative_path_is_refused_with_the_reason_and_the_fix():
    problem = af.absolute_path_problem("notes.md")
    assert problem is not None
    assert "relative path" in problem, problem
    assert "find_file" in problem or "list_directory" in problem, \
        "refused without saying how to get an absolute path"


def test_an_absolute_path_passes_on_either_slash_style():
    for path in (r"F:\work\a.py", "F:/work/a.py", "/home/k/a.py"):
        assert af.absolute_path_problem(path) is None, path


def test_an_empty_or_missing_path_is_refused():
    for value in ("", "   ", None, 42):
        assert af.absolute_path_problem(value) is not None, value


def test_the_real_read_tool_refuses_a_relative_path():
    """Wiring, not availability. This drives the shipping authorizer."""
    decision = registry().authorizer()(call("workspace_read", path="notes.md"))
    assert decision.allowed is False
    assert "relative path" in decision.reason, decision.reason


# ── E: read before write (rule 3) ────────────────────────────────────────────

def test_a_new_file_needs_no_prior_read():
    """Creating a file destroys nothing, so the check must not block it."""
    with Temp() as t:
        assert af.write_precondition({"path": t.path("brand_new.txt"),
                                      "content": "x"}) is None


def test_overwriting_an_unread_file_is_refused():
    with Temp() as t:
        path = t.write("existing.txt", "important\n")
        problem = af.write_precondition({"path": path, "content": "new"})
        assert problem is not None, "a blind overwrite was allowed"
        assert "have not read it" in problem, problem
        assert "edit tool" in problem, \
            "the refusal does not point at the cheaper, safer alternative"


def test_overwriting_after_reading_is_allowed():
    with Temp() as t:
        path = t.write("existing.txt", "important\n")
        af.ledger.mark_read(path)
        assert af.write_precondition({"path": path, "content": "new"}) is None


def test_a_file_that_changed_since_the_read_is_refused():
    """The ledger records mtime, not merely the fact of a read — otherwise the
    agent happily clobbers an edit made in between."""
    with Temp() as t:
        path = t.write("existing.txt", "v1\n")
        af.ledger.mark_read(path)
        time.sleep(0.01)
        os.utime(path, (time.time() + 60, time.time() + 60))
        problem = af.write_precondition({"path": path, "content": "v2"})
        assert problem is not None and "changed on disk" in problem, problem


def test_the_ledger_is_case_and_separator_insensitive_on_this_platform():
    """`F:\\work\\X.py` and `f:/work/x.py` are one file on Windows; demanding a
    second read of a file already in context is advice that is simply wrong."""
    with Temp() as t:
        path = t.write("Existing.txt", "x")
        af.ledger.mark_read(path)
        assert af.ledger.has_read(path.replace("\\", "/")), "separator style broke it"
        if os.name == "nt":
            assert af.ledger.has_read(path.upper()), "case broke it"


def test_the_ledger_only_records_reads_that_delivered_content():
    """A ledger entry for a refused read would satisfy read-before-write without
    the model having seen a byte."""
    with Temp() as t:
        path = t.write("x.txt", "hello")
        af.note_read({"path": path}, "Access denied: outside the workspace roots.")
        assert not af.ledger.has_read(path), "a refusal was recorded as a read"
        af.note_read({"path": path}, engine_read("hello"))
        assert af.ledger.has_read(path)


def test_the_real_write_tool_refuses_a_blind_overwrite():
    with Temp() as t:
        path = t.write("existing.txt", "important\n")
        decision = registry().authorizer(allow_confirm=True)(
            call("workspace_write", path=path, content="new"))
        assert decision.allowed is False, "a blind overwrite reached approval"
        assert "have not read it" in decision.reason, decision.reason


# ── F: uniqueness (rule 4) ───────────────────────────────────────────────────

def test_an_ambiguous_edit_is_refused_and_says_how_many():
    """The defect this closes: `patch_file` defaults to count=0 (replace ALL)
    and `_workspace_patch` never passes a count, so an ambiguous edit silently
    rewrote every match."""
    with Temp() as t:
        path = t.write("a.py", "x = 1\ny = 1\nz = 1\n")
        af.ledger.mark_read(path)
        problem = af.edit_precondition({"path": path, "old_string": "= 1",
                                        "new_string": "= 2"})
        assert problem is not None, "an ambiguous edit was allowed"
        assert "matches 3 places" in problem, problem
        assert "replace_all" in problem, \
            "no escape hatch offered for a deliberate rename"


def test_an_explicit_replace_all_is_permitted():
    with Temp() as t:
        path = t.write("a.py", "x = 1\ny = 1\n")
        af.ledger.mark_read(path)
        assert af.edit_precondition({"path": path, "old_string": "= 1",
                                     "new_string": "= 2",
                                     "replace_all": True}) is None


def test_a_unique_edit_is_allowed():
    with Temp() as t:
        path = t.write("a.py", "alpha = 1\nbravo = 2\n")
        af.ledger.mark_read(path)
        assert af.edit_precondition({"path": path, "old_string": "alpha = 1",
                                     "new_string": "alpha = 9"}) is None


def test_a_string_that_is_not_in_the_file_names_the_commonest_cause():
    with Temp() as t:
        path = t.write("a.py", "alpha = 1\n")
        af.ledger.mark_read(path)
        problem = af.edit_precondition({"path": path, "old_string": "   1\talpha = 1",
                                        "new_string": "x"})
        assert problem is not None
        assert "line-number prefix" in problem, \
            "the likeliest cause — pasting read output back verbatim — is not named"


def test_editing_a_file_you_never_read_is_refused():
    with Temp() as t:
        path = t.write("a.py", "alpha = 1\n")
        problem = af.edit_precondition({"path": path, "old_string": "alpha",
                                        "new_string": "beta"})
        assert problem is not None and "have not read" in problem, problem


def test_editing_a_missing_file_says_write_it_instead():
    with Temp() as t:
        problem = af.edit_precondition({"path": t.path("nope.py"),
                                        "old_string": "a", "new_string": "b"})
        assert problem is not None and "does not exist" in problem, problem


def test_an_empty_or_unchanged_old_string_is_refused():
    with Temp() as t:
        path = t.write("a.py", "alpha\n")
        af.ledger.mark_read(path)
        assert af.edit_precondition({"path": path, "old_string": "",
                                     "new_string": "x"}) is not None
        assert af.edit_precondition({"path": path, "old_string": "alpha",
                                     "new_string": "alpha"}) is not None


def test_the_real_edit_tool_refuses_an_ambiguous_edit():
    with Temp() as t:
        path = t.write("a.py", "v = 1\nw = 1\n")
        af.ledger.mark_read(path)
        decision = registry().authorizer(allow_confirm=True)(
            call("edit_file", path=path, old_string="= 1", new_string="= 2"))
        assert decision.allowed is False, "an ambiguous edit reached approval"
        assert "must be unique" in decision.reason, decision.reason


# ── F, at the ROOT: workspace_agent itself, and every caller ─────────────────
#
# The agent-loop precondition above only protects the agent loop. `patch_file`
# is also reached by the ordinary voice/HUD command path (brain.py routes any
# filename with an extension to workspace_patch) and by self_improve.py, and
# neither passes a count. Those callers are why the fix had to land HERE.

def _workspace(temp_dir):
    """A real WorkspaceAgent with its sandbox pointed at the temp directory."""
    from pathlib import Path

    from modules import workspace_agent as wa
    saved = list(wa.WORKSPACE_ROOTS)
    wa.WORKSPACE_ROOTS[:] = [Path(temp_dir).resolve()]
    return wa.WorkspaceAgent(), (wa, saved)


def _restore(handle):
    module, saved = handle
    module.WORKSPACE_ROOTS[:] = saved


def test_patch_file_refuses_an_ambiguous_patch():
    """The defect, at its root. Before 2026-08-08 this rewrote all three."""
    with Temp() as t:
        agent, handle = _workspace(t.dir)
        try:
            path = t.write("cfg.py", "timeout = 30\nretry = 30\nwait = 30\n")
            result = agent.patch_file(path, "= 30", "= 60")
            assert "refused" in result.lower(), result
            assert "matches 3 places" in result, result
            assert t.read("cfg.py") == "timeout = 30\nretry = 30\nwait = 30\n", \
                "the file was modified by a patch that should have been refused"
        finally:
            _restore(handle)


def test_patch_file_still_applies_a_unique_patch():
    with Temp() as t:
        agent, handle = _workspace(t.dir)
        try:
            path = t.write("cfg.py", "timeout = 30\nretry = 5\n")
            result = agent.patch_file(path, "timeout = 30", "timeout = 60")
            assert "Patched" in result, result
            assert t.read("cfg.py") == "timeout = 60\nretry = 5\n"
        finally:
            _restore(handle)


def test_patch_file_replaces_everything_when_told_to_explicitly():
    """The old behaviour is still reachable — it just has to be asked for."""
    with Temp() as t:
        agent, handle = _workspace(t.dir)
        try:
            path = t.write("cfg.py", "a = 30\nb = 30\n")
            result = agent.patch_file(path, "= 30", "= 60", replace_all=True)
            assert "Patched" in result, result
            assert t.read("cfg.py") == "a = 60\nb = 60\n"
        finally:
            _restore(handle)


def test_an_explicit_count_is_still_honoured():
    """`count > 0` was always a deliberate statement of intent, so only the
    SILENT default changed."""
    with Temp() as t:
        agent, handle = _workspace(t.dir)
        try:
            path = t.write("cfg.py", "a = 30\nb = 30\n")
            agent.patch_file(path, "= 30", "= 60", count=1)
            assert t.read("cfg.py") == "a = 60\nb = 30\n"
        finally:
            _restore(handle)


class _StubEngine:
    """Just enough of ActionEngine to drive its real `_workspace_patch` body."""

    def __init__(self, workspace_agent, prefix):
        self.workspace_agent = workspace_agent
        self.PATCH_ALL_PREFIX = prefix


def test_the_voice_command_path_refuses_an_ambiguous_patch():
    """The path that mattered most and was NOT covered by the agent-loop fix:
    say "change timeout = 30 to 60" out loud and brain.py routes it here.

    Drives `ActionEngine._workspace_patch`'s real body against a stub self, so
    this is the actual shipped parsing rather than a source-text check.
    """
    import action_engine as ae

    with Temp() as t:
        agent, handle = _workspace(t.dir)
        try:
            path = t.write("cfg.py", "a = 30\nb = 30\n")
            engine = _StubEngine(agent, ae.ActionEngine.PATCH_ALL_PREFIX)
            out = ae.ActionEngine._workspace_patch(engine, f"{path}|= 30|= 60")
            assert "refused" in out.lower(), out
            assert t.read("cfg.py") == "a = 30\nb = 30\n", "the file was changed"
        finally:
            _restore(handle)


def test_the_all_prefix_survives_the_trip_through_the_engine():
    """`replace_all` has to reach `patch_file`, or a deliberate rename made
    through the agent would be refused at the far end."""
    import action_engine as ae

    with Temp() as t:
        agent, handle = _workspace(t.dir)
        try:
            path = t.write("cfg.py", "a = 30\nb = 30\n")
            target = af.build_patch_target({"path": path, "old_string": "= 30",
                                            "new_string": "= 60",
                                            "replace_all": True})
            engine = _StubEngine(agent, ae.ActionEngine.PATCH_ALL_PREFIX)
            out = ae.ActionEngine._workspace_patch(engine, target)
            assert "Patched" in out, out
            assert t.read("cfg.py") == "a = 60\nb = 60\n"
        finally:
            _restore(handle)


def test_the_all_prefix_constant_has_not_drifted_from_the_engine():
    """`agent_files` duplicates the literal so it stays importable without the
    action stack. Duplicated constants drift; this is the pin."""
    import action_engine as ae

    assert af.PATCH_ALL_PREFIX == ae.ActionEngine.PATCH_ALL_PREFIX


def test_the_edit_target_is_composed_for_the_engine():
    target = af.build_patch_target({"path": r"F:\a.py", "old_string": "x",
                                    "new_string": "y"})
    assert target == r"F:\a.py|x|y", target


def test_a_pipe_in_the_replacement_survives_the_engines_split():
    """`_workspace_patch` splits with maxsplit=2, so a pipe in the REPLACEMENT
    lands in the third field intact. Pinning it because the target format is a
    string protocol and this is its one sharp edge."""
    target = af.build_patch_target({"path": "F:/a.py", "old_string": "x",
                                    "new_string": "a|b"})
    assert target.split("|", 2) == ["F:/a.py", "x", "a|b"]


# ── the precondition hook itself ─────────────────────────────────────────────

def test_a_precondition_that_blows_up_fails_closed():
    """It exists to stop a destructive call. If it cannot answer, the call must
    not proceed on the strength of its silence."""
    reg = registry()
    reg.register("boom_tool", "t", {"type": "object", "properties": {}},
                 action_type="system_status",
                 precondition=lambda a: (_ for _ in ()).throw(RuntimeError("nope")))
    decision = reg.authorizer()(call("boom_tool"))
    assert decision.allowed is False, "a broken precondition allowed the call"
    assert "not proceeding" in decision.reason, decision.reason


def test_the_edit_tool_is_offered_before_the_whole_file_writer():
    """Ordering inside the authoring set is a nudge, and the more damaging
    default is rewriting a whole file to change one line."""
    names = registry().set_names("authoring")
    assert "edit_file" in names and "workspace_write" in names
    assert names.index("edit_file") < names.index("workspace_write"), names


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
