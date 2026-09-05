"""Harness: the dry engine must record everything and pretend nothing.

WHY THIS EXISTS
---------------
Row `2.1` asks one question — given a request in plain words, does the model
reach for the right tool? Until now the only way to measure it was
`run_evals.py --live`, which drives a real `ActionEngine`.

2026-09-05 is what that costs. Run while he was out, it opened Notepad, ran the
`deep_work` macro, spent search-API quota and fired seven TV commands. None of
that was the measurement: **which tool the model names is decided before
anything executes.**

`DryEngine` records the call and returns a reply, so the same number comes out of
an untouched desk. That puts two obligations on it, and they pull against each
other:

1. **It must never read as a real result.** A transcript that cannot tell a dry
   run from a completed action is worse than no measurement — it is a stored
   lie, which is the defect this whole project has spent a week removing.
2. **It must read as FINISHED.** The first version said *"{action} would have
   run. Nothing was executed."* — true, and to a model that is not an answer, so
   it called the same tool again: `tv_power` three times, `gmail_read_unread`
   five, until the step limit. Every task's tool list was inflated and the later
   ones starved.

WHAT THIS PINS
--------------
Both obligations, and that the recorder records.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from modules.dry_engine import DryEngine  # noqa: E402

_checks = 0
_fails: list[str] = []


def check(cond: bool, label: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        _fails.append(label)
        print(f"FAIL  {label}")


def _run(engine, payload, meta=True):
    return asyncio.run(engine.execute_with_retry(payload, meta, None))


def test_nothing_is_executed():
    """The whole point. There is no path from here to an ActionEngine."""
    src = (HERE / "modules" / "dry_engine.py").read_text(encoding="utf-8")
    for forbidden in ("import action_engine", "from action_engine",
                      "ActionEngine(", "subprocess", "os.system"):
        check(forbidden not in src,
              f"dry_engine does not reach for {forbidden!r}")


def test_the_call_is_recorded():
    e = DryEngine()
    _run(e, {"action_type": "tv_power", "target": ""})
    _run(e, {"action_type": "gmail_read", "target": "is:unread|5"})
    check(e.tools_called() == ["tv_power", "gmail_read"],
          f"both calls recorded in order: {e.tools_called()}")
    check(e.calls[1]["target"] == "is:unread|5", "with the target kept")


def test_the_reply_can_never_pass_for_a_real_result():
    e = DryEngine()
    out = _run(e, {"action_type": "gmail_read_unread", "target": "5"}, meta=False)
    check("[dry run]" in out,
          f"every reply is marked as a dry run: {out[:50]!r}")
    check("no work" in out.lower() or "returns no data" in out.lower(),
          "and says plainly that nothing was done")


def test_the_reply_reads_as_finished_so_the_model_stops():
    """Obligation 2, and the one that was got wrong first."""
    e = DryEngine()
    out = _run(e, {"action_type": "tv_power", "target": ""}, meta=False)
    check("completed" in out.lower(),
          f"the reply states completion: {out[:60]!r}")
    check("would have run" not in out.lower(),
          "not the conditional phrasing that made the model retry")
    check("do not call this tool again" in out.lower(),
          "and tells it explicitly not to repeat the call")


def test_the_meta_shape_matches_what_the_runner_expects():
    """`registry.executor` calls `execute_with_retry(payload, True, None)` and
    reads `result`. A shape mismatch would fail as a tool error and be scored as
    a selection miss."""
    e = DryEngine()
    meta = _run(e, {"action_type": "check_calendar", "target": ""})
    check(isinstance(meta, dict), "meta mode returns a dict")
    for key in ("trace_id", "state", "result", "used_fallback"):
        check(key in meta, f"...carrying {key!r}, as ActionEngine does")
    check(meta["state"] == "COMPLETED",
          "and a state the runner reads as success, so the loop advances")


def test_a_caller_can_override_a_reply():
    """Some tasks need a shaped answer to progress. It stays opt-in, and the
    override is the caller's claim, not the engine's."""
    e = DryEngine(replies={"system_status": "CPU 4%, RAM 61%."})
    check(_run(e, {"action_type": "system_status"}, meta=False)
          == "CPU 4%, RAM 61%.", "an explicit reply is used verbatim")


def test_reset_clears_between_tasks():
    """The scorer reuses one engine across forty tasks; leakage would attribute
    one task's tools to the next."""
    e = DryEngine()
    _run(e, {"action_type": "tv_power"})
    e.reset()
    check(e.tools_called() == [], "reset empties the recording")


def test_the_dry_results_carry_what_the_reporter_reads():
    """The whole run was measured and then thrown away on a KeyError.

    `score_dry` built its result rows without `category`, `loaded` or
    `needs_context` - the three keys `summarise` reads. All thirty-seven tasks
    scored, then the reporting crashed and the numbers were lost. A measurement
    that cannot be printed was not taken.
    """
    import run_evals
    rows = [
        {"id": "a", "hit": True, "loaded": True, "no_call": False,
         "category": "mail", "needs_context": False, "called": ["gmail_read"]},
        {"id": "b", "hit": False, "loaded": False, "no_call": True,
         "category": "mail", "needs_context": False, "called": []},
        {"id": "c", "hit": False, "loaded": False, "no_call": False,
         "category": "git", "needs_context": True, "called": ["find_file"]},
    ]
    out = run_evals.summarise(rows, "dry")
    check(out["mode"] == "dry", "the mode is carried through")
    check(out["tasks"] == 2,
          f"a follow-up with no antecedent is excluded from the score "
          f"({out['tasks']})")
    check(out["found"] == 1, f"hits are counted ({out['found']})")
    check(out["accuracy"] == 0.5, f"and the accuracy computed ({out['accuracy']})")


def test_score_dry_emits_those_keys():
    """Pinned against the source, because the crash was three missing keys."""
    src = (HERE / "run_evals.py").read_text(encoding="utf-8")
    body = src.split("def score_dry")[1].split("def score_live")[0]
    for key in ('"category"', '"loaded"', '"needs_context"'):
        check(key in body, f"score_dry emits {key}")


if __name__ == "__main__":
    tests = sorted(((n, f) for n, f in globals().items()
                    if n.startswith("test_") and callable(f)),
                   key=lambda nf: nf[1].__code__.co_firstlineno)
    for name, fn in tests:
        try:
            fn()
        except Exception:
            _fails.append(name)
            print(f"FAIL  {name} raised")
            traceback.print_exc()
    print(f"\n{_checks - len(_fails)}/{_checks} passed.")
    sys.exit(1 if _fails else 0)
