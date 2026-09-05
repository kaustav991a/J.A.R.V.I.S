"""An ActionEngine stand-in that records what would run, and runs nothing.

WHY THIS EXISTS
---------------
Row `2.1` wants one number: **given a request in plain words, does the model
reach for the right tool?** The only way to measure it was `run_evals.py --live`,
which drives a real `ActionEngine` — so getting the number costs real actions on
whatever desk it runs from.

2026-09-05 is what that costs. Run while he was out of the house, it opened
Notepad, ran the `deep_work` macro, spent search-API quota, and fired seven TV
commands including Netflix and YouTube. Its own docstring had warned about
locking the screen and closing applications; three tasks were excluded for that
and the other thirty-seven were not read.

**None of those side effects were the measurement.** The number comes from which
tool the model NAMED, which is decided before anything executes. So this records
the call and hands back a plausible success, and the same number falls out with
an untouched desk.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not judge whether the action would have SUCCEEDED. A dry run cannot know
that the file exists or the TV is reachable, and pretending otherwise would be
the failure this project keeps finding — a layer reporting an outcome it never
observed. Every reply says `[dry run]` in as many words, so nothing downstream
can mistake it for a result.

`--live` therefore stays, and stays honest: it is the end-to-end number, it needs
a person at the desk, and it is not what you run to check tool selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DryEngine:
    """Same surface as `ActionEngine` for the one method the agent loop uses.

    `registry.executor()` calls exactly `await engine.execute_with_retry(payload,
    True, None, ...)`, so that is the whole contract.
    """

    calls: list = field(default_factory=list)
    #: Replies the model reads. Keyed by action_type; the default is generic.
    replies: dict = field(default_factory=dict)

    async def execute_with_retry(self, payload: dict, return_meta: bool = False,
                                 trace_id: str | None = None,
                                 **kwargs: Any) -> Any:
        action = str((payload or {}).get("action_type") or "unknown")
        target = str((payload or {}).get("target") or "")
        self.calls.append({"action_type": action, "target": target})

        # It must read as FINISHED, or the model calls the same tool again.
        #
        # The first version said "{action} would have run. Nothing was
        # executed." - true, and to a model that is not an answer, so it
        # retried: tv_power three times, gmail_read_unread five, until the step
        # limit. Every task's tool list was inflated and later tasks starved.
        #
        # So the reply states completion, says plainly that a dry run has no
        # data, and tells the model not to repeat the call. Still unmistakably
        # marked - the transcript must never be able to pass this off as a real
        # result - but shaped so the loop can end.
        body = self.replies.get(
            action,
            f"[dry run] {action} completed"
            + (f" for target {target!r}" if target else "")
            + ". A dry run carries out no work and returns no data, so there "
              "is nothing to read back. Do not call this tool again; move on, "
              "and tell the owner this was a dry run.")

        if return_meta:
            return {"trace_id": trace_id, "state": "COMPLETED",
                    "result": body, "used_fallback": False}
        return body

    # Some call sites reach for `execute`; keep the surface honest.
    async def execute(self, payload: dict, *a: Any, **k: Any) -> Any:
        return await self.execute_with_retry(payload, False, None)

    def tools_called(self) -> list:
        return [c["action_type"] for c in self.calls]

    def reset(self) -> None:
        self.calls.clear()
