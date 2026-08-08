"""
test_voice_loop_owner.py — one microphone, one wake-word loop (F-11)
====================================================================

The live gate on 2026-08-08 caught the HUD starting a SECOND wake-word loop
when the page was reloaded. The loop lives inside `websocket_endpoint`, so it
started once per connection: two threads sitting in `wait_for_wake_word`, every
`[VAD]`/`[STT]` line printed twice, one spoken "wake up" running the boot
sequence twice, and the orphaned loop writing to a closed socket
("Cannot call send once a close message has been sent").

Why this harness compiles main.py instead of importing it: importing `main`
drags in pygame, TensorFlow, MediaPipe and the whole agent stack — far too
heavy for a harness, and it would start daemons. So the two ownership helpers
are lifted out of main.py's SOURCE with `ast` and executed here. That runs the
REAL functions, not a copy of them, which is the f84f644 lesson: a test that
matches source text cannot tell "refused" from "nothing happened".

The wiring itself can't be driven without a live WebSocket, so it is asserted
structurally — if someone deletes the claim or the release, these fail.
"""

import ast
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
MAIN = HERE / "main.py"

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


# ── lift the real helpers out of main.py ─────────────────────────────────────

_SOURCE = MAIN.read_text(encoding="utf-8", errors="replace")
_TREE = ast.parse(_SOURCE)

_WANTED = {"_claim_voice_loop", "_release_voice_loop"}
_funcs = [n for n in _TREE.body
          if isinstance(n, ast.FunctionDef) and n.name in _WANTED]
_globals_assign = [
    n for n in _TREE.body
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "_VOICE_LOOP_OWNER"
            for t in n.targets)
]

_NS: dict = {}
if len(_funcs) == len(_WANTED) and _globals_assign:
    mod = ast.Module(body=_globals_assign + _funcs, type_ignores=[])
    exec(compile(ast.fix_missing_locations(mod), str(MAIN), "exec"), _NS)


class _FakeSocket:
    """Stands in for a WebSocket — identity is all the helpers use."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<sock {self.name}>"


def _reset():
    _NS["_VOICE_LOOP_OWNER"] = None


# ── the behaviour ────────────────────────────────────────────────────────────

def test_the_helpers_were_found_in_main():
    check(len(_funcs) == 2,
          f"_claim_voice_loop and _release_voice_loop exist in main.py, found {len(_funcs)}")
    check(bool(_globals_assign), "_VOICE_LOOP_OWNER is a module-level global")


def test_first_connection_owns_the_loop():
    _reset()
    a = _FakeSocket("a")
    check(_NS["_claim_voice_loop"](a) is True, "first connection claims the loop")


def test_second_connection_does_not_get_a_rival_loop():
    _reset()
    a, b = _FakeSocket("a"), _FakeSocket("b")
    _NS["_claim_voice_loop"](a)
    check(_NS["_claim_voice_loop"](b) is False,
          "second connection is REFUSED the loop — this is the whole bug")


def test_a_reload_storm_still_yields_exactly_one_owner():
    _reset()
    claim = _NS["_claim_voice_loop"]
    owner = _FakeSocket("owner")
    claim(owner)
    granted = sum(1 for i in range(20) if claim(_FakeSocket(f"reload{i}")))
    check(granted == 0,
          f"20 further connections started 0 rival loops, got {granted}")


def test_a_non_owner_cannot_release_someone_elses_loop():
    _reset()
    a, b = _FakeSocket("a"), _FakeSocket("b")
    _NS["_claim_voice_loop"](a)
    _NS["_release_voice_loop"](b)          # b never owned it
    check(_NS["_claim_voice_loop"](b) is False,
          "a stale socket disconnecting does not hand the mic away from the live one")


def test_the_owner_disconnecting_frees_the_loop():
    _reset()
    a, b = _FakeSocket("a"), _FakeSocket("b")
    _NS["_claim_voice_loop"](a)
    _NS["_release_voice_loop"](a)
    check(_NS["_claim_voice_loop"](b) is True,
          "next HUD to connect gets a working microphone after the owner leaves")


def test_reconnect_cycles_never_leak_ownership():
    _reset()
    claim, release = _NS["_claim_voice_loop"], _NS["_release_voice_loop"]
    for i in range(10):
        s = _FakeSocket(f"cycle{i}")
        got = claim(s)
        check(got is True, f"cycle {i}: reconnect claims the loop")
        release(s)
    check(_NS["_VOICE_LOOP_OWNER"] is None,
          "no ownership left stranded after 10 connect/disconnect cycles")


# ── the wiring, asserted structurally ────────────────────────────────────────

def _endpoint_node():
    for n in ast.walk(_TREE):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "websocket_endpoint":
            return n
    return None


def _calls_named(node, name):
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
               and c.func.id == name for c in ast.walk(node))


def test_the_endpoint_actually_claims_before_listening():
    ep = _endpoint_node()
    check(ep is not None, "websocket_endpoint found in main.py")
    check(ep is not None and _calls_named(ep, "_claim_voice_loop"),
          "the endpoint calls _claim_voice_loop — without this the guard is dead code")


def test_the_endpoint_releases_in_a_finally():
    ep = _endpoint_node()
    released_in_finally = False
    for n in ast.walk(ep) if ep else []:
        if isinstance(n, ast.Try) and n.finalbody:
            if any(_calls_named(stmt, "_release_voice_loop") for stmt in n.finalbody):
                released_in_finally = True
    check(released_in_finally,
          "release happens in a finally — a crashing socket must not keep the mic forever")


TESTS = [
    test_the_helpers_were_found_in_main,
    test_first_connection_owns_the_loop,
    test_second_connection_does_not_get_a_rival_loop,
    test_a_reload_storm_still_yields_exactly_one_owner,
    test_a_non_owner_cannot_release_someone_elses_loop,
    test_the_owner_disconnecting_frees_the_loop,
    test_reconnect_cycles_never_leak_ownership,
    test_the_endpoint_actually_claims_before_listening,
    test_the_endpoint_releases_in_a_finally,
]


def main():
    print("=" * 60)
    print("voice-loop ownership harness (F-11)")
    print("=" * 60)
    for t in TESTS:
        t()
    print("-" * 60)
    print(f"{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
