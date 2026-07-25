"""Harness for modules/agent_runner.py + modules/agent_confirm.py — phase 4.

Fake engine, fake HUD, fake model, injected presence: no keys, no sockets, no
side effects. What matters here is the interactive contract:

  * the flag is OFF by default and the intent gate is narrow;
  * AT_DESK, a CONFIRM tool ASKS and continues in place on approval;
  * a prompt nobody answers is a refusal, never an approval and never a hang;
  * not at the desk, CONFIRM is refused with a reason (the Telegram yield is the
    next phase, and pretending would be worse than saying so);
  * every step reaches the HUD as it happens.
"""

import asyncio
import sys

from modules import agent_confirm as acf
from modules import agent_core as ac
from modules import agent_runner as ar
from modules import agent_tools as at
from modules.tool_calls import ToolCall, ToolTurn

TIERS = {"tavily_search": "AUTO", "web_browse": "AUTO", "search_documents": "AUTO",
         "memory_recall": "AUTO", "workspace_read": "AUTO", "list_directory": "AUTO",
         "find_file": "AUTO", "system_status": "AUTO", "read_screen": "AUTO",
         "workspace_write": "CONFIRM"}


def registry():
    return at.build_default_registry(lambda a: TIERS.get(a, "BLOCK"))


def run(coro):
    return asyncio.run(coro)


class FakeEngine:
    def __init__(self, result="RESULT", state="COMPLETE"):
        self.result, self.state, self.seen = result, state, []

    async def execute_with_retry(self, payload, return_meta=False, trace_id=None, *,
                                 governance_bypass=False, permission_tier="admin"):
        self.seen.append((payload["action_type"], governance_bypass))
        return {"state": self.state, "result": self.result}


class FakeHud:
    def __init__(self):
        self.frames = []

    async def __call__(self, payload):
        self.frames.append(payload)

    def of_type(self, t):
        return [f for f in self.frames if f.get("type") == t]


def script(*turns):
    seq = list(turns)
    return lambda m, t, **k: seq.pop(0) if seq else ToolTurn(ok=True, text="done.",
                                                             provider="fake")


def tool_turn(_tool, _cid="t1", **args):
    # Leading underscores so a tool argument literally called `name` (find_file)
    # cannot collide with this helper's own parameters.
    return ToolTurn(ok=True, provider="fake",
                    tool_calls=[ToolCall(id=_cid, name=_tool, arguments=args)])


def final(text="Done, Sir."):
    return ToolTurn(ok=True, text=text, provider="fake")


# ---- the flag and the intent gate ---------------------------------------- #

def test_flag_is_off_by_default():
    assert ar.flag_enabled({}) is False
    assert ar.should_use_agent("find my most recent file and tell me what's in it",
                               {}) is False


def test_the_wired_intent_matches_when_the_flag_is_on():
    on = {"JARVIS_AGENT_LOOP": "1"}
    assert ar.should_use_agent(
        "find my most recent workspace file and tell me what's in it", on) is True
    assert ar.should_use_agent(
        "what's in the latest file I was working on", on) is True


def test_the_gate_stays_narrow():
    """A false positive routes a trivial command through a multi-step loop."""
    on = {"JARVIS_AGENT_LOOP": "1"}
    for text in ("open notepad",
                 "what's the weather",
                 "read my email",
                 "search the web for the score",
                 ""):
        assert ar.should_use_agent(text, on) is False, text


# ---- confirm registry ----------------------------------------------------- #

def test_a_prompt_resolves_when_answered():
    async def main():
        p = acf.confirms.open("workspace_write", "a.py", "approve?")
        asyncio.get_running_loop().call_soon(acf.confirms.resolve, p.id, True)
        return await acf.confirms.wait(p, 1.0)

    assert run(main()) == acf.APPROVED


def test_an_unanswered_prompt_expires_as_a_refusal():
    """Silence is not consent, and the run must not park forever."""
    async def main():
        p = acf.confirms.open("workspace_write", "a.py", "approve?")
        return await acf.confirms.wait(p, 0.05)

    assert run(main()) == acf.EXPIRED


def test_resolving_twice_is_a_no_op():
    """A double-clicked button must not approve a second, different action."""
    async def main():
        p = acf.confirms.open("workspace_write", "a.py", "approve?")
        first = acf.confirms.resolve(p.id, True)
        second = acf.confirms.resolve(p.id, False)
        await acf.confirms.wait(p, 1.0)
        return first, second

    first, second = run(main())
    assert first is True and second is False


def test_unknown_id_cannot_be_resolved():
    assert acf.confirms.resolve("nope", True) is False


def test_ids_are_unguessable_and_unique():
    async def main():
        a = acf.confirms.open("t", "x", "q")
        b = acf.confirms.open("t", "x", "q")
        assert a.id != b.id and len(a.id) >= 16
        acf.confirms.cancel_all()

    run(main())


def test_cancel_all_resolves_outstanding_prompts_as_refusals():
    async def main():
        p = acf.confirms.open("workspace_write", "a.py", "approve?")
        n = acf.confirms.cancel_all()
        return n, await acf.confirms.wait(p, 1.0)

    n, outcome = run(main())
    assert n == 1 and outcome == acf.CANCELLED


def test_outstanding_lists_what_the_hud_would_redraw():
    async def main():
        acf.confirms.open("workspace_write", "a.py", "approve?")
        out = acf.confirms.outstanding()
        acf.confirms.cancel_all()
        return out

    out = run(main())
    assert out[0]["tool"] == "workspace_write" and out[0]["target"] == "a.py"


def test_the_question_names_the_real_target():
    q = acf.question_for("workspace_write", "notes.py")
    assert "workspace_write" in q and "notes.py" in q
    assert acf.question_for("read_screen", "").endswith("— approve?")


# ---- AT_DESK: ask, then continue in place -------------------------------- #

def test_at_desk_confirm_approves_and_continues_in_place():
    """The primary path: he is watching, he says yes, the run carries on — no
    serialisation, no Telegram, no exit."""
    hud, engine, reg = FakeHud(), FakeEngine("written"), registry()
    confirms = acf.ConfirmRegistry()

    async def main():
        task = asyncio.create_task(ar.run_agent_command(
            "write it", engine, registry=reg, tool_set="authoring",
            send=hud, presence="at_desk", confirms=confirms,
            call_model=script(tool_turn("workspace_write", path="a.py", content="x"),
                              final("Written, Sir."))))
        # Wait for the prompt, then approve it the way the POST endpoint does.
        for _ in range(200):
            await asyncio.sleep(0.005)
            outstanding = confirms.outstanding()
            if outstanding:
                confirms.resolve(outstanding[0]["confirmation_id"], True)
                break
        return await asyncio.wait_for(task, timeout=2.0)

    res = run(main())
    assert res.ok and res.answer == "Written, Sir."
    assert engine.seen == [("workspace_write", True)], \
        "an approved CONFIRM tool must execute with governance_bypass=True"
    prompts = hud.of_type(ar.CONFIRM_FRAME)
    assert prompts[0]["question"].startswith("JARVIS wants to run workspace_write")
    assert prompts[-1]["resolved"] == acf.APPROVED


def test_at_desk_denial_is_fed_back_so_the_model_can_reroute():
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    confirms = acf.ConfirmRegistry()

    async def main():
        task = asyncio.create_task(ar.run_agent_command(
            "write it", engine, registry=reg, tool_set="authoring",
            send=hud, presence="at_desk", confirms=confirms,
            call_model=script(tool_turn("workspace_write", path="a.py", content="x"),
                              final("Understood — I won't write it."))))
        for _ in range(200):
            await asyncio.sleep(0.005)
            if confirms.outstanding():
                confirms.resolve(confirms.outstanding()[0]["confirmation_id"], False)
                break
        return await asyncio.wait_for(task, timeout=2.0)

    res = run(main())
    assert res.ok and "won't write it" in res.answer
    assert engine.seen == [], "a declined tool must never reach the engine"
    assert res.tool_runs[0].denied and "declined" in res.tool_runs[0].error


def test_an_ignored_prompt_ends_the_run_honestly():
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    confirms = acf.ConfirmRegistry()
    deny = tool_turn("workspace_write", path="a.py", content="x")

    res = run(ar.run_agent_command(
        "write it", engine, registry=reg, tool_set="authoring", send=hud,
        presence="at_desk", confirms=confirms, confirm_timeout=0.05,
        call_model=script(deny, deny, deny)))
    assert res.ok is False and res.stop_reason == ac.DENIED
    assert "timed out" in res.error
    assert engine.seen == []


def test_auto_tools_are_never_prompted_for():
    hud, engine, reg = FakeHud(), FakeEngine("contents"), registry()
    confirms = acf.ConfirmRegistry()
    res = run(ar.run_agent_command(
        "read it", engine, registry=reg, tool_set="files", send=hud,
        presence="at_desk", confirms=confirms,
        call_model=script(tool_turn("workspace_read", path="a.py"), final("It says x."))))
    assert res.ok and hud.of_type(ar.CONFIRM_FRAME) == []
    assert engine.seen == [("workspace_read", False)], \
        "an AUTO tool must keep its normal governance check (no bypass)"


# ---- away: honest refusal, not a fake yield ------------------------------ #

def test_away_refuses_confirm_with_a_reason():
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    deny = tool_turn("workspace_write", path="a.py", content="x")
    res = run(ar.run_agent_command(
        "write it", engine, registry=reg, tool_set="authoring", send=hud,
        presence="away", call_model=script(deny, deny, deny)))
    assert res.ok is False and res.stop_reason == ac.DENIED
    assert "unattended" in res.error and engine.seen == []
    assert hud.of_type(ar.CONFIRM_FRAME) == [], "no prompt when nobody is watching"


def test_away_still_runs_auto_tools():
    hud, engine, reg = FakeHud(), FakeEngine("telemetry"), registry()
    res = run(ar.run_agent_command(
        "status", engine, registry=reg, tool_set="research", send=hud,
        presence="home", call_model=script(tool_turn("system_status"), final("All well."))))
    assert res.ok and engine.seen == [("system_status", False)]


# ---- narration ------------------------------------------------------------ #

def test_every_step_reaches_the_hud_as_it_happens():
    hud, engine, reg = FakeHud(), FakeEngine("file contents here"), registry()
    res = run(ar.run_agent_command(
        "read it", engine, registry=reg, tool_set="files", send=hud,
        presence="at_desk",
        call_model=script(tool_turn("find_file", name="notes"),
                          tool_turn("workspace_read", "t2", path="notes.py"),
                          final("notes.py contains your reading list."))))
    events = [f["event"] for f in hud.of_type(ar.FRAME)]
    assert events == ["model_turn", "tool_start", "tool_ok",
                      "model_turn", "tool_start", "tool_ok",
                      "model_turn", "answer"]
    assert res.ok


def test_narration_frames_also_drive_todays_hud_fields():
    """Additive: the existing status/message pair is populated too, so the trace
    is visible without any frontend change."""
    hud, engine, reg = FakeHud(), FakeEngine("x"), registry()
    run(ar.run_agent_command(
        "read it", engine, registry=reg, tool_set="files", send=hud,
        presence="at_desk",
        call_model=script(tool_turn("workspace_read", path="a"), final("Done."))))
    frames = hud.of_type(ar.FRAME)
    assert all(f.get("status") and f.get("message") for f in frames)
    assert frames[-1]["status"] == "complete" and frames[-1]["message"] == "Done."


def test_a_tool_failure_is_narrated_and_the_run_continues():
    hud, reg = FakeHud(), registry()

    class Flaky(FakeEngine):
        async def execute_with_retry(self, payload, *a, **k):
            self.seen.append((payload["action_type"], False))
            if payload["action_type"] == "find_file":
                return {"state": "FAILED", "result": "No such file, Sir."}
            return {"state": "COMPLETE", "result": "contents"}

    engine = Flaky()
    res = run(ar.run_agent_command(
        "read it", engine, registry=reg, tool_set="files", send=hud,
        presence="at_desk",
        call_model=script(tool_turn("find_file", name="ghost"),
                          tool_turn("workspace_read", "t2", path="real.py"),
                          final("Found it in real.py."))))
    assert res.ok
    errors = [f for f in hud.of_type(ar.FRAME) if f["event"] == "tool_error"]
    assert errors and "No such file, Sir." in errors[0]["message"]


# ---- the demo intent, end to end ----------------------------------------- #

def test_the_demo_intent_runs_end_to_end():
    """'find my most recent workspace file and tell me what's in it' — two tools,
    watched, no confirmation needed because the files set is read-only."""
    hud, reg = FakeHud(), registry()

    class Files(FakeEngine):
        async def execute_with_retry(self, payload, *a, **k):
            self.seen.append((payload["action_type"], k.get("governance_bypass", False)))
            if payload["action_type"] == "list_directory":
                return {"state": "COMPLETE", "result": "notes.py (modified today)"}
            return {"state": "COMPLETE", "result": "TODO: finish the agent loop"}

    engine = Files()
    goal = "find my most recent workspace file and tell me what's in it"
    assert ar.should_use_agent(goal, {"JARVIS_AGENT_LOOP": "1"})
    res = run(ar.run_agent_command(
        goal, engine, registry=reg, tool_set="files", send=hud, presence="at_desk",
        call_model=script(tool_turn("list_directory", path="~/workspace"),
                          tool_turn("workspace_read", "t2", path="notes.py"),
                          final("notes.py is the most recent; it says: "
                                "TODO: finish the agent loop."))))
    assert res.ok and "notes.py" in res.answer
    assert [a for a, _ in engine.seen] == ["list_directory", "workspace_read"]
    assert len(hud.of_type(ar.FRAME)) == 8


def test_the_system_prompt_forbids_inventing_file_contents():
    assert "Never invent" in ar.SYSTEM_PROMPT
    assert "no tool call" in ar.SYSTEM_PROMPT


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
