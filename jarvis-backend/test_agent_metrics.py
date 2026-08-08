"""Harness for §6.8.4 — measurement, and the eval set as a standing gate.

Two halves.

**Metrics.** Counters filled from the loop's own event stream, so measurement
sees exactly what the HUD sees rather than a second version of the truth. The
rows below check the arithmetic (especially `first_call_valid`, which is the
one number that responds to a change in the tool layer without a human judging
anything) and the privacy rule: **no argument values, and not one character of
the goal.**

**The eval set.** 40 real requests, each with the tool that should serve it,
scored offline against the retrieval layer. It runs here — in the suite, on
every change — because a reworded description or a renamed tool can make a
capability unreachable without breaking a single other test. It cannot tell you
whether the MODEL then picks the tool; `run_evals.py --live` answers that and
costs rate limit, so it is deliberately not here.
"""

import asyncio
import json
import sys
from pathlib import Path

from modules import agent_metrics as am

HERE = Path(__file__).resolve().parent


def tmpfile(name="agent_runs.jsonl"):
    import tempfile
    return Path(tempfile.mkdtemp(prefix="jarvis-metrics-")) / name


def run(coro):
    return asyncio.run(coro)


def feed(events, metrics=None):
    """Push a scripted event stream through a collector, as the loop would."""
    metrics = metrics or am.RunMetrics(tool_set="files", presence="at_desk",
                                       goal_chars=42)
    handler = am.collector(metrics)

    async def drive():
        for kind, data in events:
            await handler(kind, data)

    run(drive())
    return metrics


# ── counting ────────────────────────────────────────────────────────────────

def test_a_clean_run_counts_calls_and_steps():
    metrics = feed([
        ("model_turn", {"step": 1}),
        ("tool_start", {"tool": "list_directory", "arguments": {"path": "F:/x"}}),
        ("tool_ok", {"tool": "list_directory", "output": "a.py\nb.py"}),
        ("model_turn", {"step": 2}),
        ("answer", {"text": "b.py is newest, Sir."}),
    ])
    assert metrics.steps == 2 and metrics.calls == 1
    assert metrics.tools["list_directory"].ok == 1
    assert metrics.answer_chars == len("b.py is newest, Sir.")


def test_first_call_valid_falls_when_a_repair_precedes_a_call():
    """The headline number. A repair means the model's FIRST attempt was
    malformed, so the call that follows it is not a first-call success."""
    clean = feed([("tool_start", {"tool": "find_file"}),
                  ("tool_ok", {"tool": "find_file", "output": "x"})])
    assert clean.first_call_valid_rate == 1.0

    repaired = feed([("repair", {"problem": "missing argument"}),
                     ("tool_start", {"tool": "find_file"}),
                     ("tool_ok", {"tool": "find_file", "output": "x"})])
    assert repaired.repairs == 1
    assert repaired.tools["find_file"].first_valid == 0
    assert repaired.first_call_valid_rate == 0.0


def test_a_repair_only_discounts_the_next_call_not_every_later_one():
    metrics = feed([("repair", {"problem": "bad"}),
                    ("tool_start", {"tool": "a"}), ("tool_ok", {"tool": "a"}),
                    ("tool_start", {"tool": "b"}), ("tool_ok", {"tool": "b"})])
    assert metrics.tools["a"].first_valid == 0
    assert metrics.tools["b"].first_valid == 1


def test_denials_and_errors_are_counted_separately():
    """A refused tool and a broken tool are different problems: one is
    governance working, the other is the tool layer failing."""
    metrics = feed([
        ("tool_start", {"tool": "gmail_send"}),
        ("denied", {"tool": "gmail_send", "reason": "unattended"}),
        ("tool_start", {"tool": "workspace_read"}),
        ("tool_error", {"tool": "workspace_read", "error": "File not found"}),
    ])
    assert metrics.denials == 1 and metrics.tools["gmail_send"].denied == 1
    assert metrics.tools["workspace_read"].errors == 1
    assert metrics.tools["workspace_read"].ok == 0


def test_the_shelf_and_the_playbooks_are_counted():
    metrics = feed([("tool_search", {"query": "send an email"}),
                    ("skill_loaded", {"name": "handle-email", "chars": 900}),
                    ("compacted", {"groups": 2})])
    assert metrics.searches == 1
    assert metrics.skills_loaded == 1
    assert metrics.compactions == 1


def test_a_helpers_calls_count_but_its_steps_do_not():
    """A sub-agent's call is still a call the system made; adding its steps to
    the parent's would make every delegation look like a runaway."""
    metrics = feed([("model_turn", {"step": 1}),
                    ("sub:model_turn", {"step": 7}),
                    ("sub:tool_start", {"tool": "workspace_read"}),
                    ("sub:tool_ok", {"tool": "workspace_read", "output": "x"})])
    assert metrics.steps == 1
    assert metrics.tools["workspace_read"].calls == 1


def test_durations_are_recorded_per_tool():
    metrics = feed([("tool_start", {"tool": "tavily_search"}),
                    ("tool_ok", {"tool": "tavily_search", "output": "sunny"})])
    assert metrics.tools["tavily_search"].ms_total >= 0
    assert metrics.tools["tavily_search"].out_chars == len("sunny")


def test_the_collector_still_narrates():
    """It WRAPS the narrator rather than replacing it — one event stream, two
    readers, so the HUD and the metrics can never disagree about a run."""
    seen = []

    async def inner(kind, data):
        seen.append(kind)

    handler = am.collector(am.RunMetrics(), inner)
    run(handler("tool_start", {"tool": "x"}))
    assert seen == ["tool_start"]


def test_a_broken_collector_never_breaks_a_run():
    metrics = am.RunMetrics()
    handler = am.collector(metrics)
    run(handler("tool_start", None))          # malformed event
    run(handler("tool_ok", {"tool": None}))
    assert metrics.calls >= 0                 # got here at all


# ── privacy: the rule that makes this file safe to keep ─────────────────────

def test_no_argument_value_is_ever_recorded():
    """Argument NAMES answer "which argument does it forget". The values answer
    nothing, and a file of them is a transcript under another name — beside a
    project that went to real trouble to encrypt the other one."""
    metrics = feed([
        ("tool_start", {"tool": "gmail_send",
                        "arguments": {"to": "her@example.com",
                                      "subject": "the divorce",
                                      "body": "SECRET SENTENCE"}}),
        ("tool_ok", {"tool": "gmail_send", "output": "Sent, Sir."}),
    ])
    blob = json.dumps(metrics.as_dict())
    for secret in ("her@example.com", "the divorce", "SECRET SENTENCE"):
        assert secret not in blob, secret
    assert set(metrics.as_dict()["tools"]["gmail_send"]["args"]) == \
        {"to", "subject", "body"}


def test_the_goal_is_stored_as_a_length_and_nothing_else():
    goal = "write a note about her birthday"
    metrics = am.RunMetrics(goal_chars=len(goal))
    blob = json.dumps(metrics.as_dict())
    assert "birthday" not in blob
    assert metrics.as_dict()["goal_chars"] == len(goal)


def test_a_tool_output_is_measured_not_kept():
    metrics = feed([("tool_start", {"tool": "workspace_read"}),
                    ("tool_ok", {"tool": "workspace_read",
                                 "output": "PASSWORD=hunter2"})])
    blob = json.dumps(metrics.as_dict())
    assert "hunter2" not in blob
    assert metrics.tools["workspace_read"].out_chars == len("PASSWORD=hunter2")


# ── the file ────────────────────────────────────────────────────────────────

def test_a_run_is_appended_and_read_back():
    path = tmpfile()
    metrics = feed([("tool_start", {"tool": "find_file"}),
                    ("tool_ok", {"tool": "find_file", "output": "x"})])
    metrics.ok, metrics.stop_reason = True, "answered"
    assert am.record_run(metrics, path) is True
    runs = am.load_runs(path)
    assert len(runs) == 1 and runs[0]["stop_reason"] == "answered"


def test_a_half_written_line_does_not_poison_the_file():
    """A kill mid-write leaves a partial line. One bad line must cost one run,
    not the history."""
    path = tmpfile()
    am.record_run(feed([("tool_start", {"tool": "a"})]), path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"broken": ')
    am.record_run(feed([("tool_start", {"tool": "b"})]), path)
    assert len(am.load_runs(path)) == 2


def test_recording_failure_costs_a_measurement_not_a_run():
    """A read-only checkout or a full disk must not raise into the loop."""
    assert am.record_run(am.RunMetrics(),
                         Path("Z:/definitely/not/a/path/runs.jsonl")) is False


def test_metrics_can_be_switched_off():
    assert am.enabled({}) is True
    for off in ("0", "false", "no", "off"):
        assert am.enabled({am.METRICS_ENV: off}) is False


# ── the summary, which is what a decision actually reads ────────────────────

def test_the_summary_aggregates_across_runs():
    path = tmpfile()
    for _ in range(3):
        metrics = feed([("model_turn", {"step": 2}),
                        ("tool_start", {"tool": "find_file"}),
                        ("tool_ok", {"tool": "find_file", "output": "x"})])
        metrics.ok, metrics.stop_reason = True, "answered"
        am.record_run(metrics, path)
    failed = feed([("repair", {"problem": "bad"}),
                   ("tool_start", {"tool": "find_file"}),
                   ("tool_error", {"tool": "find_file", "error": "boom"})])
    failed.ok, failed.stop_reason = False, "step_cap"
    am.record_run(failed, path)

    summary = am.summarise(am.load_runs(path))
    assert summary["runs"] == 4 and summary["completed"] == 3
    assert summary["completion_rate"] == 0.75
    assert summary["stop_reasons"] == {"answered": 3, "step_cap": 1}
    assert summary["tools"]["find_file"]["calls"] == 4
    assert summary["tools"]["find_file"]["errors"] == 1
    assert summary["tools"]["find_file"]["error_rate"] == 0.25
    # 4 calls, 1 repair = 5 attempts, 3 of which were first-call valid.
    assert summary["first_call_valid"] == 0.6


def test_an_empty_history_summarises_to_nothing_rather_than_crashing():
    assert am.summarise([]) == {"runs": 0}
    assert "No agent runs recorded" in am.format_summary(am.summarise([]))


def test_the_summary_renders_as_readable_lines():
    metrics = feed([("tool_start", {"tool": "find_file"}),
                    ("tool_ok", {"tool": "find_file", "output": "x"})])
    metrics.ok, metrics.stop_reason = True, "answered"
    text = am.format_summary(am.summarise([metrics.as_dict()]))
    assert "find_file" in text and "first-call-valid" in text


# ── the eval set, run as a gate ─────────────────────────────────────────────

def test_the_eval_set_is_well_formed():
    import run_evals
    tasks = run_evals.load_tasks()
    assert len(tasks) >= 30, f"only {len(tasks)} tasks"
    seen = set()
    from agent_tier_fixture import tier_lookup
    from modules.agent_tools import build_default_registry
    registry = build_default_registry(tier_lookup())
    for task in tasks:
        assert task["id"] not in seen, f"duplicate id {task['id']}"
        seen.add(task["id"])
        assert task["prompt"].strip()
        assert task["expect"], task["id"]
        for name in task["expect"]:
            assert registry.get(name) is not None, \
                f"{task['id']} expects a tool that does not exist: {name}"


def test_every_expected_tool_is_reachable_from_plain_words():
    """The standing gate. A reworded description or a renamed tool can make a
    capability unreachable without breaking any other test — this is where that
    shows up, and it is why the eval lives in the suite rather than in a
    document."""
    import run_evals
    summary = run_evals.score_retrieval(run_evals.load_tasks())
    misses = [f"{m['id']} (expected {m['expect']}, got {m['top']})"
              for m in summary["misses"]]
    assert not misses, "unreachable: " + "; ".join(misses)


def test_the_eval_covers_every_domain_the_catalogue_serves():
    """A 100% score over three categories would be a comfortable lie."""
    import run_evals
    categories = {t.get("category") for t in run_evals.load_tasks()}
    for required in ("mail", "tv", "files", "git", "web", "apps", "partner",
                     "memory", "system"):
        assert required in categories, f"nothing in the eval covers {required}"


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
