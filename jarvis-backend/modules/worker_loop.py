"""
worker_loop.py — The Overnight Worker Loop (Roadmap §1.1: Continuous Autonomous Agency)
=======================================================================================

This is what turns J.A.R.V.I.S. from a *responder* into an *agent*. A background
daemon that continuously drains the durable task queue (modules/task_queue.py),
executes each queued goal's action plan with the engine's built-in self-correction,
and surfaces the outcome to the HUD/voice.

DESIGN GUARANTEES
-----------------
1. THE LOOP NEVER DIES. Every task runs inside nested try/except. A crash in one
   task is logged, recorded against that task, and the loop moves on — exactly the
   "traps its own tracebacks and self-corrects without crashing the event loop"
   requirement.
2. SAFE AUTONOMY. The worker pre-screens every action's governance tier (read-only,
   via governance_manager.get_tier) and ONLY auto-executes AUTO-tier actions.
   CONFIRM/BLOCK actions are never run unattended — they're recorded and surfaced so
   the user can authorise them interactively. This never touches the governance
   pending slot, so a background task can't hijack a voice/HUD confirmation.
3. NON-BLOCKING. All SQLite access is offloaded via asyncio.to_thread.
4. RESULT SURFACING. If the user is actively engaged (SYSTEM_ONLINE) when a task
   finishes, J.A.R.V.I.S. announces it immediately. Otherwise the result is left
   unreported and delivered on the next wake via report_pending().
"""

import asyncio
import traceback

from modules import task_queue
from governance_manager import governance_manager


class OvernightWorker:
    def __init__(
        self,
        execute_fn,                 # ActionEngine.execute_with_retry (async)
        broadcast_fn,               # async: send a dict payload to the HUD
        speak_fn,                   # async: speak a line of text
        is_system_online_fn,        # () -> bool
        active_user_fn,             # () -> str
        poll_interval: float = 8.0,
        replan_fn=None,             # async (goal, failed_step, error) -> list[action]
        max_heal_attempts: int = 3, # §1.1b: bounded LLM self-correction retries
    ) -> None:
        self.execute_fn = execute_fn
        self.broadcast = broadcast_fn
        self.speak = speak_fn
        self.is_online = is_system_online_fn
        self.active_user = active_user_fn
        self.poll_interval = poll_interval
        self.replan_fn = replan_fn
        self.max_heal_attempts = max_heal_attempts
        self.is_running = True

    # -----------------------------------------------------------------------
    # Main daemon loop
    # -----------------------------------------------------------------------
    async def start(self) -> None:
        # Recover any task left mid-flight by a previous crash/restart.
        try:
            recovered = await asyncio.to_thread(task_queue.requeue_stuck_running)
            if recovered:
                print(f"[WORKER] Recovered {recovered} interrupted task(s) → re-queued.", flush=True)
        except Exception as e:
            print(f"[WORKER] Startup recovery skipped: {e}", flush=True)

        print("[WORKER] Overnight Worker Loop online. Standing by for queued goals.", flush=True)
        while self.is_running:
            try:
                task = await asyncio.to_thread(task_queue.claim_next_pending)
                if not task:
                    await asyncio.sleep(self.poll_interval)
                    continue
                await self._run_task(task)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # The loop itself must NEVER die, even on an unexpected claim/IO error.
                print(f"[WORKER] Loop-level error (continuing): {e}", flush=True)
                traceback.print_exc()
                await asyncio.sleep(self.poll_interval)
        print("[WORKER] Overnight Worker Loop stopped.", flush=True)

    # -----------------------------------------------------------------------
    # Execute one task's action plan (fully sandboxed)
    # -----------------------------------------------------------------------
    async def _run_task(self, task: dict) -> None:
        tid = task["id"]
        title = task["title"]
        actions = task.get("actions") or []
        print(f"[WORKER] >> Task {tid}: '{title}' ({len(actions)} action(s))", flush=True)

        try:
            await self._notify({"status": "task_started", "task_id": tid, "title": title})

            results: list[str] = []
            needs_confirm = False
            blocked = False

            for i, action in enumerate(actions):
                atype = str(action.get("action_type", "")).strip() or "unknown"
                tier = governance_manager.get_tier(atype)  # read-only; no pending-slot mutation

                if tier == "AUTO":
                    meta = None
                    try:
                        meta = await self.execute_fn(action, True, None)
                        res = meta.get("result", meta) if isinstance(meta, dict) else meta
                    except Exception as ae:
                        # execute_with_retry already traps most failures, but be defensive.
                        traceback.print_exc()
                        res = f"action error: {type(ae).__name__}: {ae}"
                    results.append(f"step {i + 1} [{atype}]: {str(res)[:500]}")

                    # ── §1.1b: dynamic LLM self-correction ───────────────────
                    # If the step failed and a replanner is wired, feed
                    # {goal, failed_step, error} back to the brain for a NEW
                    # plan and retry — bounded, governance-gated, never crashes.
                    if self._is_step_failure(meta, res) and self.replan_fn is not None:
                        healed = await self._self_heal(title, action, res, results)
                        if healed is not None:
                            res = healed  # latest (recovered) result feeds the summary
                elif tier == "CONFIRM":
                    needs_confirm = True
                    results.append(f"step {i + 1} [{atype}]: deferred — needs your authorisation (CONFIRM tier)")
                else:  # BLOCK or unknown → fail-safe skip
                    blocked = True
                    results.append(f"step {i + 1} [{atype}]: skipped — blocked by governance policy")

            summary = "\n".join(results) if results else "No actions were attached to this task."

            if needs_confirm:
                await asyncio.to_thread(task_queue.mark_needs_confirmation, tid, summary)
                await self._announce(
                    tid,
                    f"The task you queued needs your authorisation, Sir: {title}.",
                    {"status": "task_needs_confirmation", "task_id": tid, "title": title, "result": summary},
                )
            elif blocked and not any("step" in r and "blocked" not in r for r in results):
                # Every step was blocked → treat as failed-by-policy.
                await asyncio.to_thread(task_queue.mark_failed, tid, summary)
                await self._announce(
                    tid,
                    f"I couldn't action the queued task '{title}', Sir — it was blocked by policy.",
                    {"status": "task_failed", "task_id": tid, "title": title, "result": summary},
                )
            else:
                await asyncio.to_thread(task_queue.mark_done, tid, summary)
                await self._announce(
                    tid,
                    f"I've completed the task you queued, Sir: {title}.",
                    {"status": "task_done", "task_id": tid, "title": title, "result": summary},
                )
            print(f"[WORKER] [DONE] Task {tid} finished.", flush=True)

        except Exception as e:
            # Absolute backstop — a task crash is recorded, never propagated.
            tb = traceback.format_exc()
            print(f"[WORKER] [FAIL] Task {tid} crashed: {e}\n{tb}", flush=True)
            try:
                await asyncio.to_thread(task_queue.mark_failed, tid, f"{type(e).__name__}: {e}")
                await self._notify({"status": "task_failed", "task_id": tid, "title": title})
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # §1.1b — LLM self-correction
    # -----------------------------------------------------------------------
    @staticmethod
    def _is_step_failure(meta, res) -> bool:
        """A step counts as failed on a FAILED engine state or a failure string."""
        rs = str(res)
        if rs.startswith(("GOVERNANCE_BLOCKED:", "GOVERNANCE_CONFIRM:")):
            return True
        if isinstance(meta, dict) and meta.get("state") == "FAILED":
            return True
        low = rs.lower()
        return low.startswith(("action error", "action failed", "error:", "failed:"))

    async def _self_heal(self, goal: str, failed_action: dict, error, results: list) -> str | None:
        """Ask the brain for a new plan to overcome a failed step; retry it.

        Bounded by `max_heal_attempts`. Only AUTO-tier recovery actions are run
        unattended (governance preserved). Returns the last recovered result on
        success, or None if recovery couldn't be achieved.
        """
        step_desc = failed_action
        err = error
        for attempt in range(1, self.max_heal_attempts + 1):
            try:
                new_actions = await self.replan_fn(goal, step_desc, err)
            except Exception as e:
                print(f"[WORKER] self-heal replan error: {e}", flush=True)
                break
            if not new_actions:
                break
            results.append(f"  ↻ self-heal attempt {attempt}: replanned {len(new_actions)} action(s)")

            all_ok = True
            last_res = None
            for na in new_actions:
                natype = str(na.get("action_type", "")).strip() or "unknown"
                tier = governance_manager.get_tier(natype)
                if tier != "AUTO":
                    results.append(f"    [{natype}]: skipped during heal (tier {tier})")
                    all_ok = False
                    continue
                m = None
                try:
                    m = await self.execute_fn(na, True, None)
                    r = m.get("result", m) if isinstance(m, dict) else m
                except Exception as e:
                    r = f"action error: {type(e).__name__}: {e}"
                results.append(f"    [{natype}]: {str(r)[:300]}")
                if self._is_step_failure(m, r):
                    all_ok = False
                    step_desc, err = na, r   # carry the new failure forward for the next attempt
                else:
                    last_res = r

            if all_ok and last_res is not None:
                results.append(f"  ✅ self-heal succeeded on attempt {attempt}")
                return last_res

        results.append(f"  ✗ self-heal exhausted — step could not be recovered")
        return None

    # -----------------------------------------------------------------------
    # Report results queued while the user was away (called on wake)
    # -----------------------------------------------------------------------
    async def report_pending(self, user: str | None = None) -> None:
        """Surface any finished-but-unreported tasks. Safe to call on every wake."""
        try:
            items = await asyncio.to_thread(task_queue.get_unreported_finished, user)
        except Exception as e:
            print(f"[WORKER] report_pending lookup failed: {e}", flush=True)
            return
        if not items:
            return

        lines: list[str] = []
        for it in items[:5]:
            status = it.get("status")
            t = it.get("title", "a task")
            if status == task_queue.DONE:
                lines.append(f"{t} — done")
            elif status == task_queue.NEEDS_CONFIRMATION:
                lines.append(f"{t} — awaiting your authorisation")
            else:
                lines.append(f"{t} — couldn't be completed")
            try:
                await asyncio.to_thread(task_queue.mark_reported, it["id"])
            except Exception:
                pass

        msg = "While you were away, Sir, I worked through the queue. " + "; ".join(lines) + "."
        await self._notify({"status": "task_report", "count": len(items), "items": items[:5]})
        try:
            await self.speak(msg)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------
    async def _notify(self, payload: dict) -> None:
        try:
            await self.broadcast(payload)
        except Exception:
            pass

    async def _announce(self, tid: str, line: str, payload: dict) -> None:
        """HUD always; voice + mark-reported only if the user is actively engaged."""
        await self._notify(payload)
        try:
            if self.is_online():
                await self.speak(line)
                await asyncio.to_thread(task_queue.mark_reported, tid)
        except Exception:
            pass
