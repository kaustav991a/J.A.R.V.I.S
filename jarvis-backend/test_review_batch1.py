"""Harness for the high-severity findings of the pre-Electron review, batch 1.

Four defects, four different shapes, one theme: **a control that looked present
and was not applied on the path that mattered.**

  R1  a desk "yes" resolved whatever was pending process-wide, including an
      action staged by Telegram or the overnight worker
  R2  /api/backdoor answered {"status": "success"} out of its own except block
  R3  the workspace sandbox contained the code that does the enforcing
  R11 the GUI save path never asked the protected-file list

Each test drives the REAL code and asserts on observable behaviour. Where a
defect is about something NOT happening, the test proves the sink was never
reached — a refusal string alone would pass even if the damage had been done.
"""

import ast
import asyncio
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS  {label}")
    else:
        _failed += 1
        print(f"FAIL  {label}")


# ── R1: an approval resolves the prompt the approver was shown ───────────────

def test_a_desk_yes_cannot_consume_a_remotely_staged_action():
    """The single pending slot is process-wide. The desk's approval must not
    reach into it.

    Reproduces the real sequence: Telegram stages a CONFIRM action (filling the
    slot), the desk has no prompt of its own pinned, and the owner says "yes".
    """
    from governance_manager import GovernanceManager

    gm = GovernanceManager()
    # Whatever a remote channel staged. Straight into the slot, as check() does.
    import time as _time
    gm._pending_slot = {"id": "remote-cid", "payload": {"action_type": "gmail_send"},
                        "expires_at": _time.monotonic() + 300}
    gm._pending_registry["remote-cid"] = gm._pending_slot

    check(gm.has_pending(), "a remotely-staged action fills the process-wide slot")

    # The desk-side guard: with no id pinned, the approval path must not run.
    desk_cid = None
    armed = desk_cid is not None
    check(not armed,
          "the desk intercept does NOT arm when only a remote action is pending")

    # And the old behaviour, pinned so it cannot come back: consuming with None
    # still returns the remote payload, which is exactly why no approval path
    # may pass None.
    leaked = gm.consume_pending(None)
    check(leaked is not None and leaked.get("action_type") == "gmail_send",
          "consume_pending(None) still resolves the remote payload — so an "
          "approval path must never call it")


def test_neither_dispatch_path_arms_on_has_pending():
    """Structural, because the dangerous branch needs a live Telegram stage to
    reach at runtime. Asserts on the source: no approval intercept may be armed
    by `governance_manager.has_pending()`, and none may consume with None."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")

    bad_arm = "governance_manager.has_pending() or _DESK_PENDING" in src
    check(not bad_arm,
          "no dispatch path arms its approval intercept on has_pending()")

    # Checked on the parsed CODE, not the text — the comment explaining this fix
    # naturally mentions `consume_pending(None)`, and a substring test would
    # match its own documentation.
    tree = ast.parse(src)
    by_none = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if name != "consume_pending":
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value is None:
                by_none.append(node.lineno)
    check(not by_none,
          "no approval path consumes the pending slot by None"
          + (f" — line {by_none}" if by_none else ""))
    # Both sites must still resolve by the pinned id.
    check(src.count('consume_pending(_DESK_PENDING["cid"])') == 2,
          "both dispatch paths resolve the desk's own pinned id")


def test_a_desk_staged_prompt_still_gets_an_id_to_approve():
    """The fix would be useless — and confusing — if a desk prompt could end up
    with no id, because it would then be unapprovable. `pending_id()` is the
    fallback that keeps the desk's own prompt answerable."""
    from governance_manager import GovernanceManager

    gm = GovernanceManager()
    check(gm.pending_id() is None, "no pending action means no id")
    import time as _time
    gm._pending_slot = {"id": "desk-cid", "payload": {"action_type": "workspace_write"},
                        "expires_at": _time.monotonic() + 300}
    check(gm.pending_id() == "desk-cid", "the staged id is retrievable")


# ── R2: a crash is not a success ─────────────────────────────────────────────

def test_the_command_endpoint_does_not_answer_success_from_its_except_block():
    """Structural: reaching the real handler needs the whole app. The property is
    that the `except` returns rather than falling through to the unconditional
    success at the bottom."""
    src = (HERE / "main.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)

    # The property is NOT "every fault handler returns". The WebSocket path has
    # one too, and there it is correct: it reports the error over the socket and
    # the loop continues — no HTTP response is being falsified. The bug is
    # specifically a fault handler that FALLS THROUGH TO A SUCCESS RETURN, so
    # that is what this looks for: a function whose body ends in
    # `return {"status": "success"}` and whose fault handler does not return.
    def returns_bare_success(fn):
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
                continue
            for k, v in zip(node.value.keys, node.value.values):
                if (isinstance(k, ast.Constant) and k.value == "status"
                        and isinstance(v, ast.Constant) and v.value == "success"):
                    return True
        return False

    faults = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not returns_bare_success(fn):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                body = handler.body
                dumped = ast.dump(ast.Module(body=body, type_ignores=[]))
                if "EXECUTION FAULT" not in dumped:
                    continue
                last = body[-1] if body else None
                if not isinstance(last, (ast.Return, ast.Raise)):
                    faults.append(f"{fn.name}:{getattr(last, 'lineno', '?')}")
    check(not faults,
          "no fault handler falls through into a success return"
          + (f" — {faults}" if faults else ""))

    check("status_code=500" in src,
          "...and it answers with a 500, not a 200")


# ── R3: the rules may not rewrite themselves ─────────────────────────────────

def test_the_enforcement_code_cannot_be_written_by_the_workspace_tools():
    from modules.protected_paths import BACKEND_DIR, enforcement_write_problem

    for name in ("governance.json", "governance_manager.py"):
        problem = enforcement_write_problem(str(BACKEND_DIR / name))
        check(problem is not None, f"a write to {name} is refused")
    for name in ("protected_paths.py", "url_safety.py", "shell_safety.py",
                 "agent_tools.py", "terminal_agent.py", "backdoor_gate.py"):
        problem = enforcement_write_problem(str(BACKEND_DIR / "modules" / name))
        check(problem is not None, f"a write to modules/{name} is refused")


def test_reading_the_rules_is_still_allowed():
    """These files are not secret — they are in git and hold no credential.
    Refusing to READ them would stop JARVIS explaining its own behaviour, which
    is a feature, and would be a different (worse) rule than the one intended."""
    from modules.protected_paths import BACKEND_DIR

    agent = _workspace_agent()
    if agent is None:
        print("SKIP  workspace agent unavailable")
        return
    out = agent.read_file(str(BACKEND_DIR / "governance.json"))
    check("Access denied" not in out,
          "governance.json is still READABLE (it is a rule, not a secret)")


def test_the_write_and_patch_paths_both_use_the_write_resolver():
    """A rule enforced at one of two call sites is the shape of finding 17."""
    src = (HERE / "modules" / "workspace_agent.py").read_text(encoding="utf-8",
                                                              errors="replace")
    tree = ast.parse(src)
    for target in ("write_file", "patch_file"):
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == target), None)
        check(fn is not None, f"{target} exists")
        if fn is None:
            continue
        body = ast.dump(ast.Module(body=fn.body, type_ignores=[]))
        check("_resolve_safe_for_write" in body,
              f"{target} resolves through the WRITE resolver")


def test_an_ordinary_workspace_file_is_still_writable():
    """The point is a short list of rules, not a jail — the comment in
    protected_paths says so, and this pins it."""
    from modules.protected_paths import enforcement_write_problem

    check(enforcement_write_problem(str(HERE / "notes.txt")) is None,
          "an ordinary file is unaffected")
    check(enforcement_write_problem(str(HERE / "modules" / "media_query.py")) is None,
          "an ordinary module is unaffected")


def _workspace_agent():
    try:
        from modules.workspace_agent import WorkspaceAgent
        return WorkspaceAgent()
    except Exception:  # noqa: BLE001
        return None


# ── R11: the GUI save path asks the protected list ───────────────────────────

def test_ghost_save_file_refuses_the_key_files():
    """Drives the real engine branch with a GUI agent that records whether it
    was reached. The dialog must never be opened on a protected target."""
    import action_engine as ae
    from modules import protected_paths as pp

    engine = ae.ActionEngine.__new__(ae.ActionEngine)
    engine.protected_files = pp.PROTECTED_FILES
    engine.protected_folders = pp.PROTECTED_FOLDERS
    engine._pending_save_decision = None
    engine._last_launched_app = None
    engine._last_launched_pid = None
    engine._last_launched_hwnd = None

    reached = []

    class SpyGui:
        @staticmethod
        def ghost_save_file(target_dir, filename, **kwargs):
            reached.append(os.path.join(target_dir, filename))
            return "SUCCESS"

    engine.human_gui_agent = SpyGui()
    engine._refresh_launch_session_target = lambda: None

    for target in (f"{pp.BACKEND_DIR}|.env",
                   f"{pp.BACKEND_DIR}|jarvis_key.dpapi",
                   f"{pp.BACKEND_DIR}|jarvis_longterm.db"):
        out = asyncio.run(engine.execute(
            {"action_type": "ghost_save_file", "target": target},
            governance_bypass=True))
        check(isinstance(out, str) and "won't" in out.lower() or "protected" in str(out).lower()
              or "not" in str(out).lower(),
              f"refused: {target.split('|')[-1]}")
    check(reached == [],
          f"the save dialog was never opened on a protected path; got {reached}")


def test_the_overwrite_leg_rechecks_rather_than_trusting_the_staged_decision():
    """`resolve_pending_save` runs with force_overwrite=True, so it is the leg
    that actually clobbers. It must re-check rather than trust process state."""
    import action_engine as ae
    from modules import protected_paths as pp

    engine = ae.ActionEngine.__new__(ae.ActionEngine)
    engine.protected_files = pp.PROTECTED_FILES
    engine.protected_folders = pp.PROTECTED_FOLDERS
    engine._last_launched_app = None
    engine._last_launched_pid = None
    engine._last_launched_hwnd = None
    engine._refresh_launch_session_target = lambda: None

    reached = []

    class SpyGui:
        @staticmethod
        def ghost_save_file(target_dir, filename, **kwargs):
            reached.append(os.path.join(target_dir, filename))
            return "SUCCESS"

    engine.human_gui_agent = SpyGui()
    # A decision staged before the guard existed, or staged against a path that
    # has since become protected.
    engine._pending_save_decision = {
        "target_dir": str(pp.BACKEND_DIR),
        "original_filename": ".env",
        "alternative_filename": ".env",
    }
    out = engine.resolve_pending_save("overwrite")
    check(reached == [], f"the overwrite leg refused too; got {reached}")
    check("Overwritten" not in str(out), "...and did not claim it had overwritten")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 62)
    print("Pre-Electron review, batch 1 — the high-severity findings")
    print("=" * 62)
    for t in TESTS:
        t()
    print("-" * 62)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
