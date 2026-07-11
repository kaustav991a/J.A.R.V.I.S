"""
daemon_supervisor.py — In-Process Daemon Health-Restart (Roadmap §3.1)
======================================================================

The standalone watchdog.py keeps the whole *server* alive. This supervisor is the
finer-grained complement: it keeps the long-running *background daemons* (proactive
agent, overwatch, routine scheduler, overnight worker) alive WITHOUT a full server
bounce. If one of them throws and its asyncio task dies, the supervisor logs the
traceback and re-spawns it from its factory.

Design:
- **Additive / low-risk.** Daemons are still created exactly as before in lifespan;
  the supervisor `adopt()`s each task plus a factory to recreate it. It only acts
  when a task is found `done()` (crashed or returned) — initial startup is unchanged.
- A task that ends via cancellation (graceful shutdown) is NOT restarted.
- A per-daemon restart cap prevents a hard-failing daemon from spinning forever.
"""

from __future__ import annotations

import asyncio
import time


class DaemonSupervisor:
    # Phase 4 item 8: a daemon that stays healthy this long earns one restart
    # credit back, so transient crash storms (network flap, provider outage)
    # can't permanently exhaust the cap and disable a daemon forever.
    RESTART_DECAY_SECS = 600.0

    def __init__(self, check_interval: float = 20.0, max_restarts: int = 10,
                 should_continue=None) -> None:
        self.check_interval = check_interval
        self.max_restarts = max_restarts
        # Optional () -> bool guard; when it returns False (e.g. during shutdown)
        # the supervisor stops and never restarts a cleanly-exited daemon.
        self.should_continue = should_continue or (lambda: True)
        self.is_running = True
        # name -> {"factory": callable->coro, "task": Task, "restarts": int,
        #          "last_restart": float|None}
        self._daemons: dict[str, dict] = {}

    def adopt(self, name: str, factory, task: asyncio.Task) -> None:
        """Track an already-spawned daemon task plus a factory to recreate it.

        `factory` is a zero-arg callable returning the daemon's coroutine
        (e.g. `lambda: proactive_agent.start()`).
        """
        self._daemons[name] = {"factory": factory, "task": task, "restarts": 0,
                               "last_restart": None}

    def _respawn(self, name: str) -> None:
        d = self._daemons[name]
        d["task"] = asyncio.create_task(d["factory"](), name=f"daemon:{name}")
        d["last_restart"] = time.monotonic()

    async def _alert_owner(self, name: str) -> None:
        """Phase 4 item 8: a daemon left down is a silent capability loss —
        tell the owner wherever he is instead of only logging it."""
        try:
            from modules.owner_notify import notify_owner
            await notify_owner(
                f"Sir, my '{name}' background daemon kept crashing and has hit "
                f"its restart cap — I've left it offline. Its duties are "
                f"suspended until the next system restart.",
            )
        except Exception as e:  # noqa: BLE001
            print(f"[SUPERVISOR] owner alert failed: {e}", flush=True)

    async def start(self) -> None:
        print(f"[SUPERVISOR] Daemon health-monitor online — watching {len(self._daemons)} daemon(s).", flush=True)
        while self.is_running:
            try:
                await asyncio.sleep(self.check_interval)
                if not self.should_continue():
                    break  # shutting down — stop monitoring, restart nothing
                for name, d in self._daemons.items():
                    task = d["task"]
                    if task is None or not task.done():
                        # Healthy (or already given up). Phase 4 item 8: decay
                        # one restart credit per healthy stretch so a past crash
                        # storm doesn't permanently count against the daemon.
                        if (task is not None and d["restarts"] > 0
                                and d.get("last_restart") is not None
                                and time.monotonic() - d["last_restart"] > self.RESTART_DECAY_SECS):
                            d["restarts"] -= 1
                            d["last_restart"] = time.monotonic()
                            print(f"[SUPERVISOR] Daemon '{name}' healthy for "
                                  f"{int(self.RESTART_DECAY_SECS)}s — restart credit "
                                  f"restored ({d['restarts']}/{self.max_restarts} used).", flush=True)
                        continue
                    if task.cancelled():
                        continue  # graceful shutdown — leave it down

                    # The task ended on its own. Surface why.
                    exc = None
                    try:
                        exc = task.exception()
                    except Exception:
                        exc = None

                    if d["restarts"] >= self.max_restarts:
                        print(f"[SUPERVISOR] Daemon '{name}' hit the restart cap "
                              f"({self.max_restarts}) — leaving it down.", flush=True)
                        d["task"] = None
                        await self._alert_owner(name)
                        continue

                    d["restarts"] += 1
                    reason = f"crashed: {type(exc).__name__}: {exc}" if exc else "exited unexpectedly"
                    print(f"[SUPERVISOR] ⚠️ Daemon '{name}' {reason}. "
                          f"Restarting (#{d['restarts']}).", flush=True)
                    try:
                        self._respawn(name)
                    except Exception as e:
                        print(f"[SUPERVISOR] Failed to restart '{name}': {e}", flush=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # The supervisor itself must never die.
                print(f"[SUPERVISOR] monitor-loop error (continuing): {e}", flush=True)
        print("[SUPERVISOR] Daemon health-monitor stopped.", flush=True)
