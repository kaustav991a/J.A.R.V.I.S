r"""run_evals.py — the eval set, in the two modes that answer different questions.

Roadmap §6.8.4 (Phase 4). ~40 real requests, each with the tool (or tools) that
should serve it. Run it after any change to a description, an alias or the
ranking, and the number moves or it does not.

    venv\Scripts\python.exe run_evals.py            # retrieval, offline
    venv\Scripts\python.exe run_evals.py --live     # the real model, real Groq
    venv\Scripts\python.exe run_evals.py --metrics  # what the live runs recorded

TWO MODES, AND THE DIFFERENCE IS THE POINT
------------------------------------------
**Offline (default)** measures the RETRIEVAL layer: given the request in plain
words, does `search_tools` surface the right tool at all? No model, no network,
no cost, deterministic — so it belongs in the suite and it fails a regression
the moment a rename or a reworded description breaks findability. It cannot tell
you whether the model then CHOOSES that tool.

**Live (`--live`)** measures the whole loop against real Groq: it runs each task
and records which tools were actually called. That is the honest end-to-end
number, and it costs rate limit and minutes, so it is not in the suite.

Neither is a substitute for the other, and quoting one as the other is how a
project ends up believing its agent is better than it is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = HERE / "evals" / "agent_tasks.jsonl"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def load_tasks(path: Path = TASKS) -> list:
    tasks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            tasks.append(json.loads(line))
    return tasks


# ── offline: does the catalogue SURFACE the right tool? ─────────────────────

def score_retrieval(tasks: list, top_n: int = 3) -> dict:
    """For each task, search the shelf and see where the expected tool lands.

    `top_n` is 3 because that is `MAX_RESULTS` — a tool ranked fourth is found
    by the search and then not loaded, which for the model is the same as not
    being found at all.
    """
    from agent_tier_fixture import tier_lookup
    from modules.agent_search import ToolShelf
    from modules.agent_tools import build_default_registry

    registry = build_default_registry(tier_lookup())
    results = []
    for task in tasks:
        # A task marked `confirm` needs an attended run to be offered at all —
        # scoring it against an unattended shelf would count a correct refusal
        # as a miss.
        # The base set is EXCLUDED from search candidates (a resident tool is
        # not something to find), so the placeholder must be one no task
        # expects — scoring against `system_status` as the base counted a
        # correct answer as a miss, which is the eval measuring itself.
        shelf = ToolShelf(registry, base=["read_screen"],
                          allow_confirm=bool(task.get("confirm")))
        hits = [h.name for h in shelf.search(task["prompt"])]
        expected = set(task["expect"])
        rank = next((i for i, name in enumerate(hits) if name in expected), None)
        results.append({
            "id": task["id"], "category": task.get("category", "?"),
            "hit": rank is not None, "loaded": rank is not None and rank < top_n,
            "rank": rank, "top": hits[:3], "expect": task["expect"],
        })
    return summarise(results, "retrieval")


# ── live: does the MODEL choose it, and does the run finish? ────────────────

def score_live(tasks: list, limit: int | None = None) -> dict:
    """Run each task through the real loop and record the tools it called.

    Needs the real provider chain and a real `ActionEngine`, so it is deliberately
    not importable from the suite.
    """
    import asyncio

    from action_engine import ActionEngine
    from modules import agent_runner

    engine = ActionEngine()
    results = []

    async def send(_payload):
        return None

    for task in tasks[:limit]:
        result = asyncio.run(agent_runner.run_agent_command(
            task["prompt"], engine, tool_set="files", send=send,
            presence="at_desk" if task.get("confirm") else "unknown"))

        # What the run ACTUALLY called, read from its own audit trail.
        #
        # This used to build a `called` list from a `watch(kind, data)` callback
        # that was defined and then never passed — `run_agent_command` takes no
        # event-callback parameter at all, so it could not have been. `called`
        # was therefore always empty and EVERY live task scored ✗, whatever the
        # model did. The 40/40 this project quotes is the RETRIEVAL eval; the
        # live number has never actually been measured.
        #
        # `tool_runs` is better than an event hook anyway: it is the loop's own
        # record of what executed, not a side channel that can drift from it.
        runs = list(getattr(result, "tool_runs", None) or [])
        called = [r.name for r in runs]
        # A DENIED call still means the model chose the right tool — governance
        # refusing it is a separate question from findability, and conflating the
        # two would score the loop down for working as designed.
        chosen = [r.name for r in runs if not getattr(r, "denied", False)]
        expected = set(task["expect"])
        hit = bool(expected & set(called))
        results.append({
            "id": task["id"], "category": task.get("category", "?"),
            "hit": hit, "loaded": hit,
            "rank": None, "top": called[:3], "expect": task["expect"],
            "ok": bool(result.ok), "stop": result.stop_reason,
            "denied": [r.name for r in runs if getattr(r, "denied", False)],
        })
        mark = "✓" if hit else "✗"
        denied = [r.name for r in runs if getattr(r, "denied", False)]
        note = f" (denied: {denied})" if denied else ""
        print(f"  {task['id']:12} {mark} called={called}{note}", flush=True)
        if not called and not chosen:
            print(f"    ↳ no tool ran at all — stop_reason={result.stop_reason}",
                  flush=True)
    return summarise(results, "live")


def summarise(results: list, mode: str) -> dict:
    by_category: dict = {}
    for row in results:
        agg = by_category.setdefault(row["category"], {"n": 0, "loaded": 0})
        agg["n"] += 1
        agg["loaded"] += 1 if row["loaded"] else 0
    return {
        "mode": mode,
        "tasks": len(results),
        "found": sum(1 for r in results if r["hit"]),
        "loaded": sum(1 for r in results if r["loaded"]),
        "accuracy": round(sum(1 for r in results if r["loaded"]) / len(results), 3)
        if results else None,
        "misses": [r for r in results if not r["loaded"]],
        "by_category": by_category,
    }


def report(summary: dict) -> int:
    print(f"\n{summary['mode']}: {summary['loaded']}/{summary['tasks']} "
          f"({summary['accuracy']:.0%})")
    for category, agg in sorted(summary["by_category"].items()):
        mark = "" if agg["loaded"] == agg["n"] else "   <-- "
        print(f"  {category:10} {agg['loaded']}/{agg['n']}{mark}")
    if summary["misses"]:
        print("\nMISSES — each one is a description, an alias or a ranking bug:")
        for miss in summary["misses"]:
            where = "not found at all" if miss["rank"] is None \
                else f"ranked {miss['rank'] + 1}"
            print(f"  {miss['id']:12} expected {miss['expect']}, {where}; "
                  f"got {miss['top']}")
    return 0 if not summary["misses"] else 1


def main(argv: list) -> int:
    tasks = load_tasks()
    if "--metrics" in argv:
        from modules import agent_metrics
        print(agent_metrics.format_summary(
            agent_metrics.summarise(agent_metrics.load_runs())))
        return 0
    if "--live" in argv:
        print(f"Running {len(tasks)} tasks against the REAL model — this costs "
              f"rate limit and minutes.\n")
        return report(score_live(tasks))
    return report(score_retrieval(tasks))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
