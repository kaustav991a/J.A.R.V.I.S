"""Harness: the agent loop is never accidentally ungoverned.

`run_agent_loop(authorize=None)` treats every call as allowed. That default is
fail-OPEN by construction, and this project has already paid for one gate that
was never wired — "the shelf had never been wired in production" (§6.8), where
every catalogue tool was registered and unreachable and nothing said so.

Two properties, and they are different in kind:

1. **Behavioural** — an ungoverned run announces itself. It stays permissive, so
   the harnesses that rely on it keep working, but it can no longer happen in
   silence.
2. **Structural** — the one production entry point supplies an authorizer on
   EVERY branch. `run_agent_command` picks a desk or an away authorizer from an
   if/else; a third branch added later that forgot to is the failure this
   catches, and it cannot be caught by running the code because that branch
   would need the conditions that reach it.
"""

import asyncio
import ast
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


from modules import agent_core as ac  # noqa: E402
from modules.tool_calls import ToolCall, ToolTurn  # noqa: E402

TOOLS = [{"name": "peek", "description": "look",
          "input_schema": {"type": "object", "properties": {}}}]


def _turns(*turns):
    seq = list(turns)

    def call_model(messages, tools, **kwargs):
        return seq.pop(0)
    return call_model


def _tool_turn(name):
    return ToolTurn(ok=True, text=None,
                    tool_calls=[ToolCall(id="c1", name=name, arguments={})],
                    provider="fake", model="fake")


def _final(text):
    return ToolTurn(ok=True, text=text, tool_calls=[], provider="fake",
                    model="fake")


def test_a_run_with_no_authorizer_says_so():
    """It still runs — but it emits `ungoverned`, so a run that lost its
    governance is visible in the trace instead of looking ordinary."""
    events = []

    async def execute(_call):
        return "peeked"

    async def on_event(kind, data):
        events.append((kind, data))

    result = asyncio.run(ac.run_agent_loop(
        "look", TOOLS, execute, authorize=None, on_event=on_event,
        call_model=_turns(_tool_turn("peek"), _final("Done, Sir."))))

    check(result.ok, "an ungoverned run still completes (the contract is unchanged)")
    kinds = [k for k, _ in events]
    check("ungoverned" in kinds,
          f"...and it announced itself; events were {kinds}")
    check(sum(1 for k in kinds if k == "ungoverned") == 1,
          "warned once per run, not once per call")


def test_a_governed_run_emits_no_such_warning():
    async def execute(_call):
        return "peeked"

    events = []

    async def on_event(kind, data):
        events.append(kind)

    result = asyncio.run(ac.run_agent_loop(
        "look", TOOLS, execute,
        authorize=lambda call: ac.Decision(True, "fine"),
        on_event=on_event,
        call_model=_turns(_tool_turn("peek"), _final("Done, Sir."))))

    check(result.ok, "a governed run completes")
    check("ungoverned" not in events,
          "an authorized run must not cry wolf")


def test_the_production_entry_point_authorizes_on_every_branch():
    """`run_agent_command` must reach `run_agent_loop` with an authorizer no
    matter which branch it took.

    Read structurally: the point is the branch nobody has written yet. Asserts
    that `authorize` is assigned in every branch of the presence if/else AND
    that the call passes it.
    """
    src = (HERE / "modules" / "agent_runner.py").read_text(encoding="utf-8",
                                                           errors="replace")
    tree = ast.parse(src)

    passes_authorize = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "run_agent_loop":
            continue
        passes_authorize = any(kw.arg == "authorize" and kw.value is not None
                               for kw in node.keywords)
    check(passes_authorize,
          "agent_runner passes authorize= to run_agent_loop")

    # Every `if/else` that assigns `authorize` must assign it on BOTH sides — an
    # `if` with no `else` would leave it unbound or stale.
    lopsided = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue

        def assigns(body):
            return any(
                isinstance(s, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "authorize"
                        for t in s.targets)
                for s in body)

        if assigns(node.body) and not assigns(node.orelse):
            lopsided.append(node.lineno)
    check(not lopsided,
          "no branch assigns `authorize` without its counterpart"
          + (f" — lines {lopsided}" if lopsided else ""))


def test_the_default_is_still_permissive_on_purpose():
    """If this ever flips to deny-by-default, it must be a deliberate change with
    every harness updated — not a surprise. Pinned so the flip cannot be quiet."""
    import inspect
    sig = inspect.signature(ac.run_agent_loop)
    check(sig.parameters["authorize"].default is None,
          "authorize still defaults to None")


TESTS = sorted(
    (fn for name, fn in list(globals().items())
     if name.startswith("test_") and callable(fn)),
    key=lambda fn: fn.__code__.co_firstlineno,
)


def main():
    print("=" * 60)
    print("Agent governance wiring")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
