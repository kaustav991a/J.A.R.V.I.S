r"""agent_metrics.py — what the loop actually did, so tuning stops being a guess.

Roadmap §6.8.4 (Phase 4). Everything before this was built on reasoning about
failure modes; this is the part that produces NUMBERS. Without it, "is the
70B model good enough", "did the playbooks help", "which tool does it get wrong"
are all opinions — and the answer to each decides real work.

WHAT IS RECORDED, AND WHAT DELIBERATELY IS NOT
----------------------------------------------
Recorded: which tools were called, how often, how many succeeded, how many were
refused, how long each took, how big the results were, how many steps a run
took, why it stopped, and whether the FIRST attempt at each tool was valid.

**Not recorded: any argument VALUE, and not one character of the goal.** The
same rule the sealed-queue dead-letter file follows — names, counts, types and
lengths only. A metrics file that quietly accumulates what he asked JARVIS to do
is a transcript with a different name, and it would sit unencrypted beside a
project that went to some trouble to encrypt the other one. Argument NAMES are
kept because "which argument does it forget" is a real question; their contents
are not, because the answer never needs them.

THE ONE NUMBER THAT MATTERS MOST
--------------------------------
`first_call_valid` — the share of tool calls that were well-formed on the first
attempt, before any repair. It is the cleanest single signal of whether the tool
LAYER is doing its job: descriptions, schemas and error messages all move it,
and it responds to a change without needing a human to judge the output.

HOW IT ATTACHES
---------------
Through the existing `on_event` stream — the same events the HUD narrator reads.
No new hooks in `agent_core`, and a collector that throws cannot break a run
(the loop already swallows telemetry errors). That also means metrics observe
exactly what the user sees, rather than a second, parallel truth.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["RunMetrics", "collector", "record_run", "load_runs", "summarise",
           "METRICS_ENV", "metrics_path", "enabled"]

#: Off is a supported state, but this defaults ON: it records no content, and a
#: measurement you have to remember to switch on is one you do not have when the
#: interesting run happens.
METRICS_ENV = "JARVIS_AGENT_METRICS"

#: Keep the file bounded. At ~400 bytes a run this is a few megabytes, which is
#: months of desk use, and the oldest runs are the least interesting.
MAX_RUNS_KEPT = 5000


def enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(METRICS_ENV, "1")).strip().lower() not in \
        ("0", "false", "no", "off")


def metrics_path() -> Path:
    return Path(__file__).resolve().parent.parent / "metrics" / "agent_runs.jsonl"


@dataclass
class ToolStat:
    """One tool's record within one run."""

    calls: int = 0
    ok: int = 0
    errors: int = 0
    denied: int = 0
    #: Calls whose FIRST attempt was well-formed — no repair, no schema refusal.
    first_valid: int = 0
    ms_total: float = 0.0
    out_chars: int = 0
    #: Argument names seen. Names only — see the module docstring.
    args_seen: set = field(default_factory=set)

    def as_dict(self) -> dict:
        return {"calls": self.calls, "ok": self.ok, "errors": self.errors,
                "denied": self.denied, "first_valid": self.first_valid,
                "ms": round(self.ms_total, 1), "out_chars": self.out_chars,
                "args": sorted(self.args_seen)}


@dataclass
class RunMetrics:
    """One agent run, as counters. Built by `collector`, written by `record_run`."""

    tool_set: str = ""
    presence: str = ""
    #: Length only. The goal itself is never stored.
    goal_chars: int = 0
    started: float = field(default_factory=time.time)
    steps: int = 0
    repairs: int = 0
    denials: int = 0
    searches: int = 0
    skills_loaded: int = 0
    compactions: int = 0
    parked: int = 0
    provider_failures: int = 0
    stop_reason: str = ""
    ok: bool = False
    answer_chars: int = 0
    tools: dict = field(default_factory=dict)
    _open: dict = field(default_factory=dict, init=False)
    #: A repair applies to the NEXT call attempt, which is how "first call
    #: valid" is measured without the loop having to tell us.
    _pending_repair: bool = field(default=False, init=False)

    def tool(self, name: str) -> ToolStat:
        return self.tools.setdefault(name, ToolStat())

    @property
    def duration_s(self) -> float:
        return round(time.time() - self.started, 2)

    @property
    def calls(self) -> int:
        return sum(t.calls for t in self.tools.values())

    @property
    def first_call_valid_rate(self) -> float | None:
        attempts = self.calls + self.repairs
        if not attempts:
            return None
        return round(sum(t.first_valid for t in self.tools.values()) / attempts, 3)

    def as_dict(self) -> dict:
        return {
            "ts": round(self.started, 1),
            "duration_s": self.duration_s,
            "tool_set": self.tool_set,
            "presence": self.presence,
            "goal_chars": self.goal_chars,
            "ok": self.ok,
            "stop_reason": self.stop_reason,
            "steps": self.steps,
            "calls": self.calls,
            "repairs": self.repairs,
            "denials": self.denials,
            "searches": self.searches,
            "skills_loaded": self.skills_loaded,
            "compactions": self.compactions,
            "parked": self.parked,
            "provider_failures": self.provider_failures,
            "answer_chars": self.answer_chars,
            "first_call_valid": self.first_call_valid_rate,
            "tools": {name: stat.as_dict() for name, stat in sorted(self.tools.items())},
        }


def collector(run: RunMetrics, inner=None):
    """An `on_event` handler that fills `run`, then calls `inner`.

    Wrapping rather than replacing keeps the HUD narration and the measurement
    reading the same event stream — two sources of truth about one run is how
    you end up debugging the metrics instead of the agent.
    """
    async def on_event(kind: str, data: dict):
        try:
            _apply(run, kind, data or {})
        except Exception:  # noqa: BLE001 — measurement must never break a run
            pass
        if inner is not None:
            result = inner(kind, data)
            if hasattr(result, "__await__"):
                await result

    return on_event


def _apply(run: RunMetrics, kind: str, data: dict) -> None:
    # A sub-agent's events arrive prefixed. They are counted under the same
    # tools — a helper's call is still a call the system made — but its steps
    # are not added to the parent's, or a delegation would look like a runaway.
    nested = kind.startswith("sub:")
    kind = kind[4:] if nested else kind

    if kind == "model_turn":
        if not nested:
            run.steps = max(run.steps, int(data.get("step") or 0))
    elif kind == "tool_start":
        name = str(data.get("tool") or "?")
        stat = run.tool(name)
        stat.calls += 1
        if not run._pending_repair:
            stat.first_valid += 1
        run._pending_repair = False
        for arg in (data.get("arguments") or {}):
            stat.args_seen.add(str(arg))
        run._open[name] = time.monotonic()
    elif kind == "tool_ok":
        name = str(data.get("tool") or "?")
        stat = run.tool(name)
        stat.ok += 1
        stat.out_chars += len(str(data.get("output") or ""))
        _close(run, name, stat)
    elif kind == "tool_error":
        name = str(data.get("tool") or "?")
        stat = run.tool(name)
        stat.errors += 1
        _close(run, name, stat)
    elif kind == "denied":
        name = str(data.get("tool") or "?")
        run.tool(name).denied += 1
        run.denials += 1
        _close(run, name, run.tool(name))
    elif kind == "repair":
        run.repairs += 1
        # The next `tool_start` is a retry, so it does not count as first-valid.
        run._pending_repair = True
    elif kind == "tool_search":
        run.searches += 1
    elif kind == "skill_loaded":
        run.skills_loaded += 1
    elif kind == "compacted":
        run.compactions += 1
    elif kind == "parked":
        run.parked += 1
    elif kind == "provider_failed":
        run.provider_failures += 1
    elif kind == "answer":
        run.answer_chars = len(str(data.get("text") or ""))


def _close(run: RunMetrics, name: str, stat: ToolStat) -> None:
    started = run._open.pop(name, None)
    if started is not None:
        stat.ms_total += (time.monotonic() - started) * 1000.0


def record_run(run: RunMetrics, path: Path | None = None) -> bool:
    """Append one run. Returns whether it was written.

    Failure to record is never allowed to matter: a full disk, a locked file or
    a read-only checkout must cost a measurement, not a run.
    """
    if not enabled():
        return False
    target = Path(path) if path else metrics_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # A process killed mid-write leaves a line with no newline on the end.
        # Appending straight onto it would splice two records into one and cost
        # BOTH — so a torn tail is closed first, and the damage stays at one.
        if target.exists() and target.stat().st_size:
            with target.open("rb") as check:
                check.seek(-1, 2)
                torn = check.read(1) not in (b"\n", b"\r")
            if torn:
                with target.open("a", encoding="utf-8") as handle:
                    handle.write("\n")
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run.as_dict(), default=str) + "\n")
    except OSError as exc:
        print(f"[METRICS] not recorded: {exc}", flush=True)
        return False
    _trim(target)
    return True


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size < 4_000_000:
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= MAX_RUNS_KEPT:
            return
        path.write_text("\n".join(lines[-MAX_RUNS_KEPT:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load_runs(path: Path | None = None) -> list:
    target = Path(path) if path else metrics_path()
    if not target.is_file():
        return []
    runs = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue        # a half-written line from a kill; skip it
    return runs


def summarise(runs: list) -> dict:
    """Turn a pile of runs into the handful of numbers a decision needs."""
    if not runs:
        return {"runs": 0}
    total_calls = sum(r.get("calls", 0) for r in runs)
    total_repairs = sum(r.get("repairs", 0) for r in runs)
    per_tool: dict = {}
    for run in runs:
        for name, stat in (run.get("tools") or {}).items():
            agg = per_tool.setdefault(name, {"calls": 0, "ok": 0, "errors": 0,
                                             "denied": 0, "first_valid": 0,
                                             "ms": 0.0})
            for key in ("calls", "ok", "errors", "denied", "first_valid"):
                agg[key] += stat.get(key, 0)
            agg["ms"] += stat.get("ms", 0.0)
    for name, agg in per_tool.items():
        agg["error_rate"] = round(agg["errors"] / agg["calls"], 3) if agg["calls"] else None
        agg["first_call_valid"] = round(agg["first_valid"] / agg["calls"], 3) \
            if agg["calls"] else None
        agg["avg_ms"] = round(agg["ms"] / agg["calls"], 1) if agg["calls"] else None

    stop_reasons: dict = {}
    for run in runs:
        reason = run.get("stop_reason") or "?"
        stop_reasons[reason] = stop_reasons.get(reason, 0) + 1

    steps = sorted(r.get("steps", 0) for r in runs)
    return {
        "runs": len(runs),
        "completed": sum(1 for r in runs if r.get("ok")),
        "completion_rate": round(sum(1 for r in runs if r.get("ok")) / len(runs), 3),
        "calls": total_calls,
        "repairs": total_repairs,
        # The headline number. Attempts, not calls, is the denominator: a repair
        # IS an attempt, and hiding it would make a bad schema look harmless.
        "first_call_valid": (round(
            sum(sum(s.get("first_valid", 0) for s in (r.get("tools") or {}).values())
                for r in runs) / (total_calls + total_repairs), 3)
            if (total_calls + total_repairs) else None),
        "denials": sum(r.get("denials", 0) for r in runs),
        "searches": sum(r.get("searches", 0) for r in runs),
        "skills_loaded": sum(r.get("skills_loaded", 0) for r in runs),
        "compactions": sum(r.get("compactions", 0) for r in runs),
        "median_steps": steps[len(steps) // 2] if steps else None,
        "avg_duration_s": round(sum(r.get("duration_s", 0) for r in runs) / len(runs), 2),
        "stop_reasons": dict(sorted(stop_reasons.items(),
                                    key=lambda kv: -kv[1])),
        "tools": dict(sorted(per_tool.items(), key=lambda kv: -kv[1]["calls"])),
    }


def format_summary(summary: dict) -> str:
    """The same numbers as a few lines a person can read at the terminal."""
    if not summary.get("runs"):
        return "No agent runs recorded yet."
    lines = [
        f"{summary['runs']} run(s) — {summary['completed']} completed "
        f"({summary['completion_rate']:.0%}), median {summary['median_steps']} step(s), "
        f"{summary['avg_duration_s']}s average",
        f"{summary['calls']} tool call(s), {summary['repairs']} repair(s), "
        f"first-call-valid {summary['first_call_valid']}",
        f"{summary['denials']} denial(s), {summary['searches']} search(es), "
        f"{summary['skills_loaded']} playbook(s) opened, "
        f"{summary['compactions']} compaction(s)",
        "stop reasons: " + ", ".join(f"{k} x{v}" for k, v in
                                     summary["stop_reasons"].items()),
        "",
        f"{'tool':28} {'calls':>6} {'err':>5} {'deny':>5} {'1st-ok':>7} {'avg ms':>8}",
    ]
    for name, agg in summary["tools"].items():
        valid = "-" if agg["first_call_valid"] is None \
            else f"{agg['first_call_valid']:.2f}"
        avg_ms = "-" if agg["avg_ms"] is None else f"{agg['avg_ms']:.0f}"
        lines.append(f"{name[:28]:28} {agg['calls']:>6} {agg['errors']:>5} "
                     f"{agg['denied']:>5} {valid:>7} {avg_ms:>8}")
    return "\n".join(lines)
