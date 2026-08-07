"""Harness for modules/agent_runner.py + modules/agent_confirm.py — phase 4.

Fake engine, fake HUD, fake model, injected presence: no keys, no sockets, no
side effects. What matters here is the interactive contract:

  * the flag is OFF by default and the intent gate is narrow;
  * AT_DESK, a CONFIRM tool ASKS and continues in place on approval;
  * a prompt nobody answers is a refusal, never an approval and never a hang;
  * not at the desk (phase 5), CONFIRM is PARKED as a durable task and his phone
    is told the phrase that resumes it — and the loop is told it did NOT happen;
  * delegation hands the parent one sentence instead of a helper's tool output,
    and cannot deadlock on the engine lock it shares with the helper;
  * every step reaches the HUD as it happens.
"""

import asyncio
import sys

from modules import agent_confirm as acf
from modules import agent_core as ac
from modules import agent_runner as ar
from modules import agent_subagents as sa
from modules import agent_tools as at
from modules.tool_calls import ToolCall, ToolTurn
# One fake queue/notifier pair, shared with the yield harness so the two cannot
# drift apart.
from test_agent_yield import FakeNotifier, FakeQueue

TIERS = {"tavily_search": "AUTO", "web_browse": "AUTO", "search_documents": "AUTO",
         "memory_recall": "AUTO", "workspace_read": "AUTO", "list_directory": "AUTO",
         "find_file": "AUTO", "system_status": "AUTO", "read_screen": "AUTO",
         "workspace_write": "CONFIRM", "workspace_patch": "CONFIRM"}
# Wave 1 onward the shared fixture is the source; the literal above is kept for
# the handful of assertions written against exactly these names.
from agent_tier_fixture import TIERS as _SHARED
TIERS = {**_SHARED, **TIERS}


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


def test_the_write_intent_is_wired_so_confirm_is_reachable():
    """Without it, the desk-confirm and away-park paths are unexercisable — the
    read intent's `files` set is read-only by construction."""
    on = {"JARVIS_AGENT_LOOP": "1"}
    goal = "write a note called todo.md saying finish the agent loop"
    assert ar.should_use_agent(goal, on) is True
    assert ar.tool_set_for(goal) == "authoring"


def test_a_read_request_never_gets_the_writing_tool_set():
    assert ar.tool_set_for("what's in the latest file I was working on") == "files"
    assert ar.tool_set_for("") == "files"


def test_the_write_gate_needs_all_three_parts():
    """A verb alone is the whole risk: "save" turns up in ordinary chatter."""
    on = {"JARVIS_AGENT_LOOP": "1"}
    for text in ("save my work",                       # no file noun, no content
                 "write to my mother about the trip",   # no file noun
                 "create a new file",                   # no content
                 "did you save the file I asked about"):
        assert ar.should_use_agent(text, on) is False, text


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
            call_model=script(tool_turn("workspace_write", path=r"F:\work\a.py", content="x"),
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
            call_model=script(tool_turn("workspace_write", path=r"F:\work\a.py", content="x"),
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
    deny = tool_turn("workspace_write", path=r"F:\work\a.py", content="x")

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
        call_model=script(tool_turn("workspace_read", path=r"F:\work\a.py"), final("It says x."))))
    assert res.ok and hud.of_type(ar.CONFIRM_FRAME) == []
    assert engine.seen == [("workspace_read", False)], \
        "an AUTO tool must keep its normal governance check (no bypass)"


# ---- away: park it for his phone (phase 5) ------------------------------- #

def test_away_parks_the_confirm_and_tells_him_the_phrase():
    """Phase 5. Nobody to ask in the moment, so the action becomes a durable task
    he can authorise later — and the loop is told it did NOT happen."""
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    queue, notify = FakeQueue(), FakeNotifier()
    deny = tool_turn("workspace_write", path=r"F:\work\a.py", content="x")
    res = run(ar.run_agent_command(
        "write it", engine, registry=reg, tool_set="authoring", send=hud,
        presence="away", queue=queue, notify=notify,
        call_model=script(deny, deny, deny)))

    assert res.ok is False and res.stop_reason == ac.DENIED
    assert engine.seen == [], "a parked action must not have run"
    assert "NOT DONE" in res.tool_runs[0].error
    task = list(queue.tasks.values())[0]
    assert task["status"] == "needs_confirmation"
    assert task["actions"] == [{"action_type": "workspace_write",
                                "target": r"F:\work\a.py|x"}], \
        "the queued payload must be the model's actual call"
    assert notify.sent and f"approve task {task['id'][:8]}" in notify.sent[0][0]
    # The caller needs the sentence even though the run failed.
    assert res.notes and f"approve task {task['id'][:8]}" in res.notes[0]
    assert hud.of_type(ar.CONFIRM_FRAME) == [], "no HUD prompt when nobody is watching"
    assert hud.of_type(ar.PARKED_FRAME), "the HUD should still show what was parked"


def test_away_parks_at_most_one_action_per_run():
    """A model that keeps reaching for a writing tool must not turn into five
    phone notifications."""
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    queue, notify = FakeQueue(), FakeNotifier()
    res = run(ar.run_agent_command(
        "write them", engine, registry=reg, tool_set="authoring", send=hud,
        presence="away", queue=queue, notify=notify,
        limits=ac.AgentLimits(max_repairs=3),
        call_model=script(tool_turn("workspace_write", path=r"F:\work\a.py", content="x"),
                          tool_turn("workspace_write", "t2", path="b.py", content="y"),
                          tool_turn("workspace_write", "t3", path=r"F:\work\c.py", content="z"),
                          final("I've parked the first one, Sir."))))
    assert len(queue.tasks) == 1 and len(notify.sent) == 1
    assert res.ok, "the model may still finish the turn with an honest sentence"


def test_away_does_not_park_a_call_that_is_missing_arguments():
    """That is the model's mistake, not something to wake the owner about."""
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    queue, notify = FakeQueue(), FakeNotifier()
    res = run(ar.run_agent_command(
        "write it", engine, registry=reg, tool_set="authoring", send=hud,
        presence="away", queue=queue, notify=notify,
        call_model=script(tool_turn("workspace_write", path=r"F:\work\a.py"),
                          final("I need the contents, Sir."))))
    assert queue.tasks == {} and notify.sent == []
    assert res.tool_runs[0].denied and "content" in res.tool_runs[0].error


def test_a_parked_run_never_claims_the_write_happened():
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    queue, notify = FakeQueue(), FakeNotifier()
    res = run(ar.run_agent_command(
        "write it", engine, registry=reg, tool_set="authoring", send=hud,
        presence="away", queue=queue, notify=notify,
        call_model=script(tool_turn("workspace_write", path=r"F:\work\a.py", content="x"),
                          final("Saved it, Sir."))))
    # The model's own words are its business, but the run must carry the parked
    # note, the engine must be untouched, and the transcript must have told the
    # model in plain terms that nothing was written.
    assert engine.seen == [] and res.notes
    told = [m for m in res.messages
            if "do not claim it happened" in str(m.get("content", "")).lower()]
    assert told, "the loop must feed the refusal back, not swallow it"


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
                          tool_turn("workspace_read", "t2", path=r"F:\work\notes.py"),
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
        call_model=script(tool_turn("workspace_read", path=r"F:\work\a.py"), final("Done."))))
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
                          tool_turn("workspace_read", "t2", path=r"F:\work\real.py"),
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
                          tool_turn("workspace_read", "t2", path=r"F:\work\notes.py"),
                          final("notes.py is the most recent; it says: "
                                "TODO: finish the agent loop."))))
    assert res.ok and "notes.py" in res.answer
    assert [a for a, _ in engine.seen] == ["list_directory", "workspace_read"]
    assert len(hud.of_type(ar.FRAME)) == 8


# ---- delegation (phase 5) ------------------------------------------------- #

def test_the_delegate_tool_is_absent_unless_asked_for():
    hud, engine, reg = FakeHud(), FakeEngine("x"), registry()
    offered = []

    def spy(messages, tools, **k):
        offered.append([t["name"] for t in tools])
        return final("Done.")

    run(ar.run_agent_command("read it", engine, registry=reg, tool_set="files",
                             send=hud, presence="at_desk", call_model=spy))
    assert sa.DELEGATE_TOOL not in offered[0]


def test_delegation_adds_one_tool_and_returns_one_sentence():
    hud, reg = FakeHud(), registry()

    class Files(FakeEngine):
        async def execute_with_retry(self, payload, *a, **k):
            self.seen.append((payload["action_type"], k.get("governance_bypass", False)))
            return {"state": "COMPLETE", "result": "a.py  b.py  c.py"}

    engine = Files()
    offered = []
    # The parent delegates once, the helper does the looking, the parent answers.
    turns = [tool_turn(sa.DELEGATE_TOOL, question="which file is newest?"),
             tool_turn("workspace_read", "s1", path=r"F:\work\c.py"),   # the HELPER's call
             final("c.py is the newest."),                     # the helper's answer
             final("The newest is c.py, Sir.")]                # the parent's answer

    def model(messages, tools, **k):
        offered.append([t["name"] for t in tools])
        return turns.pop(0)

    res = run(ar.run_agent_command(
        "read it", engine, registry=reg, tool_set="files", send=hud,
        presence="at_desk", delegate=True, delegate_set="research",
        call_model=model))
    assert res.ok and res.answer == "The newest is c.py, Sir."
    assert sa.DELEGATE_TOOL in offered[0]
    # The helper's turn is offered the research set, never the delegate itself.
    assert sa.DELEGATE_TOOL not in offered[1]
    # What came back to the parent is the sentence, not the directory listing.
    parent_tool_results = [m for m in res.messages if m.get("role") == "tool"]
    assert parent_tool_results[0]["content"] == "c.py is the newest."


def test_a_delegate_run_does_not_deadlock_on_the_engine_lock():
    """The helper takes the same COMMAND_LOCK around its own tool calls; wrapping
    the delegate in it too would hang the run forever."""
    hud, engine, reg = FakeHud(), FakeEngine("listing"), registry()
    turns = [tool_turn(sa.DELEGATE_TOOL, question="which file is newest?"),
             tool_turn("workspace_read", "s1", path=r"F:\work\c.py"),
             final("c.py."),
             final("c.py, Sir.")]

    async def main():
        lock = asyncio.Lock()
        return await asyncio.wait_for(ar.run_agent_command(
            "read it", engine, registry=reg, tool_set="files", send=hud,
            lock=lock, presence="at_desk", delegate=True,
            call_model=lambda m, t, **k: turns.pop(0)), timeout=3.0)

    res = run(main())
    assert res.ok and engine.seen == [("workspace_read", False)]


def test_a_delegation_with_no_question_is_refused_not_run():
    hud, engine, reg = FakeHud(), FakeEngine(), registry()
    res = run(ar.run_agent_command(
        "read it", engine, registry=reg, tool_set="files", send=hud,
        presence="at_desk", delegate=True,
        call_model=script(tool_turn(sa.DELEGATE_TOOL), final("I need a question."))))
    assert res.tool_runs[0].denied and "question" in res.tool_runs[0].error
    assert engine.seen == []


def test_helper_steps_are_narrated_under_their_own_label():
    hud, engine, reg = FakeHud(), FakeEngine("listing"), registry()
    turns = [tool_turn(sa.DELEGATE_TOOL, question="which file is newest?"),
             tool_turn("workspace_read", "s1", path=r"F:\work\c.py"),
             final("c.py."),
             final("c.py, Sir.")]
    run(ar.run_agent_command(
        "read it", engine, registry=reg, tool_set="files", send=hud,
        presence="at_desk", delegate=True,
        call_model=lambda m, t, **k: turns.pop(0)))
    frames = hud.of_type(ar.FRAME)
    sub = [f for f in frames if str(f["event"]).startswith("sub:")]
    assert sub, "a long delegation must not look like a hang"
    assert all(f["message"].startswith("Helper: ") for f in sub)
    # Only the PARENT's answer may tell the HUD the whole task is complete.
    assert [f["event"] for f in frames if f["status"] == "complete"] == ["answer"]


def test_limits_default_when_nothing_is_set():
    lim = ar.limits_from_env({})
    assert lim.max_transcript_chars == ac.AgentLimits().max_transcript_chars
    assert lim.max_steps == ac.AgentLimits().max_steps


def test_the_transcript_budget_and_step_cap_are_tunable():
    """The right value depends on the provider's tokens-per-minute on the day."""
    lim = ar.limits_from_env({"JARVIS_AGENT_TRANSCRIPT_CHARS": "3000",
                              "JARVIS_AGENT_MAX_STEPS": "4"})
    assert lim.max_transcript_chars == 3000 and lim.max_steps == 4


def test_a_junk_limit_is_ignored_not_fatal():
    lim = ar.limits_from_env({"JARVIS_AGENT_TRANSCRIPT_CHARS": "lots",
                              "JARVIS_AGENT_MAX_STEPS": "-3"})
    assert lim.max_transcript_chars == ac.AgentLimits().max_transcript_chars
    assert lim.max_steps == ac.AgentLimits().max_steps


def test_zero_disables_compaction_deliberately():
    assert ar.limits_from_env({"JARVIS_AGENT_TRANSCRIPT_CHARS": "0"}
                              ).max_transcript_chars == 0


def test_the_system_prompt_names_the_real_workspace_roots():
    """Live 2026-07-26: the loop listed the user's HOME (list_directory's sandbox),
    then handed the newest name to workspace_read, whose roots are different — it
    could name a file it could not open. The model cannot infer either sandbox."""
    prompt = ar.system_prompt()
    note = ar.workspace_note()
    assert note, "workspace roots must resolve in this repo"
    assert note in prompt and prompt.startswith("You are JARVIS")
    assert "FULL paths" in prompt


def test_the_note_separates_what_is_listable_from_what_is_readable():
    """The two file tools do not share a sandbox. Told only about the readable
    roots, the model tried to LIST F:\\work, was refused, and thrashed until the
    step cap — so the note must name the browsable intersection."""
    from pathlib import Path
    note = ar.workspace_note()
    assert "READ" in note and "LIST" in note
    assert str(Path.home()) in note
    assert "do not retry" in note.lower()


def test_the_prompt_survives_an_unresolvable_workspace(monkeypatch=None):
    """No roots (or an import fault) must degrade to the plain prompt, not crash."""
    import sys as _sys
    real = _sys.modules.get("modules.workspace_agent")
    _sys.modules["modules.workspace_agent"] = None      # forces an import fault
    try:
        assert ar.workspace_note() == ""
        assert ar.system_prompt() == ar.SYSTEM_PROMPT
    finally:
        if real is not None:
            _sys.modules["modules.workspace_agent"] = real
        else:
            del _sys.modules["modules.workspace_agent"]


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
