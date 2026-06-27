"""
Phase 8.7 — The Overwatch Daemon
=================================
Proactive background intelligence loop that monitors system health thresholds
and pushes LLM-synthesized spoken alerts to the user via TTS + WebSocket HUD.

Loop interval : asyncio.sleep(300)  — every 5 minutes
Cooldown      : per-alert registry prevents repeat spam within each window
Speech quality: synthesize_info_gen() — same LLM pipeline as user responses
"""

import asyncio
import time
import threading
import datetime

try:
    import psutil
except ImportError:
    psutil = None  # Graceful degradation if psutil isn't installed

# ---------------------------------------------------------------------------
# Per-alert cooldown windows (seconds)
# ---------------------------------------------------------------------------
_COOLDOWN_SECS: dict = {
    "cpu_high":       600,   # 10 min — CPU spikes can be transient
    "ram_high":       600,   # 10 min
    "battery_low":    900,   # 15 min — battery drains slowly
    "midnight_nudge": 3600,  # 1 hour  — once per late-night session
}


class OverwatchDaemon:
    """
    J.A.R.V.I.S. Overwatch Daemon — Phase 8.7.

    Monitors hardware thresholds and clock, fires a dynamically synthesised
    voice alert + HUD broadcast when a threshold is breached.  Each alert
    has an independent cooldown entry so only one alert fires per cycle and
    repeated identical alerts are suppressed.

    Thresholds:
        CPU  > 90%          → thermal throttle warning
        RAM  > 85%          → memory pressure warning
        Battery < 20% (discharging) → charge immediately
        Hour in [0, 4)      → midnight wellness nudge
    """

    def __init__(
        self,
        broadcast_callback,        # async fn(payload: dict) → None  (WebSocket HUD)
        speak_callback,            # async fn(text: str) → None       (TTS)
        is_system_online_fn=None,  # sync  fn() → bool  — skip when user is offline
        active_user_fn=None,       # sync  fn() → str   — returns "KAUSTAV" etc.
    ):
        self.broadcast_callback = broadcast_callback
        self.speak_callback = speak_callback
        self.is_system_online_fn = is_system_online_fn
        self.active_user_fn = active_user_fn
        self.is_running = False
        self._cooldown_registry: dict = {}

    # ── Public entrypoint ──────────────────────────────────────────────────
    async def start(self):
        self.is_running = True
        print(
            "[OVERWATCH] Daemon activated. First check in 90 seconds.",
            flush=True,
        )
        # Stagger startup so the system fully boots before the first poll.
        await asyncio.sleep(90)

        while self.is_running:
            try:
                await self._check_cycle()
            except Exception as exc:
                print(f"[OVERWATCH] Check cycle error (non-fatal): {exc}", flush=True)
            await asyncio.sleep(300)  # 5-minute intervals

    # ── Cooldown helpers ───────────────────────────────────────────────────
    def _is_on_cooldown(self, key: str) -> bool:
        last = self._cooldown_registry.get(key, 0.0)
        return (time.time() - last) < _COOLDOWN_SECS.get(key, 600)

    def _mark_fired(self, key: str):
        self._cooldown_registry[key] = time.time()

    def _active_user(self) -> str:
        if callable(self.active_user_fn):
            try:
                return self.active_user_fn()
            except Exception:
                pass
        return "KAUSTAV"

    # ── Core alert dispatcher ──────────────────────────────────────────────
    async def _fire_alert(self, key: str, situation_brief: str, log_label: str):
        """
        Generate a spoken alert via synthesize_info_gen() (same LLM pipeline
        as regular JARVIS responses), then broadcast audio + HUD update.

        sass_index is hard-wired to 0 for all Overwatch alerts — these are
        system health events and must always be strictly professional.
        """
        # Late import to avoid circular imports at module load time.
        import speaker as _speaker
        from brain import synthesize_info_gen

        # Never interrupt an active TTS stream.
        if getattr(_speaker, "is_system_speaking", False):
            print(
                f"[OVERWATCH] Suppressed '{key}' — system already speaking.",
                flush=True,
            )
            return

        active_user = self._active_user()
        print(f"\n[OVERWATCH] ⚡ Alert: {log_label}", flush=True)

        # 1. Visual HUD broadcast — fires immediately (no audio dependency)
        await self.broadcast_callback({
            "status": "speaking",
            "message": f"⚡ OVERWATCH ALERT: {log_label}",
            "is_proactive": True,
            "source": "overwatch",
        })

        # 2. LLM synthesis — run blocking generator in background thread,
        #    stream sentences back via queue (mirrors _stream_synthesize_speak).
        spoken_parts: list = []
        try:
            loop = asyncio.get_event_loop()
            sentence_queue: asyncio.Queue = asyncio.Queue()

            def _producer():
                try:
                    for sentence in synthesize_info_gen(
                        original_query="proactive system health alert",
                        raw_data=situation_brief,
                        active_user=active_user,
                        sass_index=0,  # Always tactical — no sarcasm on health alerts
                    ):
                        loop.call_soon_threadsafe(sentence_queue.put_nowait, sentence)
                except Exception as exc:
                    fallback = f"I have detected a system anomaly, Sir. {situation_brief[:100]}"
                    loop.call_soon_threadsafe(sentence_queue.put_nowait, fallback)
                finally:
                    loop.call_soon_threadsafe(sentence_queue.put_nowait, None)  # sentinel

            t = threading.Thread(target=_producer, daemon=True)
            t.start()

            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    break
                spoken_parts.append(sentence)
                # Update HUD progressively as speech arrives
                assembled = " ".join(spoken_parts).strip()
                await self.broadcast_callback({
                    "status": "complete",
                    "result": assembled,
                    "is_proactive": True,
                    "source": "overwatch",
                })
                await self.speak_callback(sentence)

            t.join(timeout=5.0)

        except Exception as exc:
            print(f"[OVERWATCH] Synthesis failed — using fallback: {exc}", flush=True)
            fallback = f"Excuse me, Sir. {situation_brief[:120]}"
            await self.broadcast_callback({
                "status": "complete",
                "result": fallback,
                "is_proactive": True,
                "source": "overwatch",
            })
            await self.speak_callback(fallback)

        # 3. Restore standby state on HUD
        await asyncio.sleep(4)
        await self.broadcast_callback({
            "status": "online",
            "message": "SYSTEM ONLINE // STANDBY",
            "is_proactive": True,
        })

        # 4. Stamp cooldown AFTER the alert fully fires
        self._mark_fired(key)

    # ── Check cycle ────────────────────────────────────────────────────────
    async def _check_cycle(self):
        """
        Run all threshold checks in priority order.
        Returns immediately after the first alert fires to prevent
        audio overlap and to preserve the 5-minute cadence cleanly.
        """
        # Only alert when the system is awake and a user is active.
        if callable(self.is_system_online_fn) and not self.is_system_online_fn():
            return

        if psutil is None:
            print("[OVERWATCH] psutil unavailable — hardware checks skipped.", flush=True)
            return

        now_hour = datetime.datetime.now().hour

        # ── Priority 1: CPU thermal alert ─────────────────────────────────
        if not self._is_on_cooldown("cpu_high"):
            try:
                # interval=1 gives a 1-second blocking average — far more
                # accurate than a snapshot (cpu_percent(interval=0)).
                cpu = psutil.cpu_percent(interval=1)
                if cpu > 90:
                    brief = (
                        f"SYSTEM HEALTH ALERT: CPU utilisation is currently at {cpu:.0f}%. "
                        f"Sustained usage at this level indicates thermal throttling or a "
                        f"runaway process. The user should investigate and consider closing "
                        f"resource-heavy applications such as VS Code, browsers, or "
                        f"compilation tasks."
                    )
                    await self._fire_alert(
                        "cpu_high",
                        brief,
                        f"CPU at {cpu:.0f}% — thermal throttle warning",
                    )
                    return
            except Exception as exc:
                print(f"[OVERWATCH] CPU check failed: {exc}", flush=True)

        # ── Priority 2: RAM pressure alert ────────────────────────────────
        if not self._is_on_cooldown("ram_high"):
            try:
                vm = psutil.virtual_memory()
                if vm.percent > 85:
                    used_gb = vm.used / (1024 ** 3)
                    total_gb = vm.total / (1024 ** 3)
                    brief = (
                        f"SYSTEM HEALTH ALERT: RAM usage is at {vm.percent:.0f}% "
                        f"({used_gb:.1f} GB of {total_gb:.1f} GB consumed). "
                        f"The system is under significant memory pressure. "
                        f"The user should close unnecessary applications to prevent "
                        f"performance degradation or system instability."
                    )
                    await self._fire_alert(
                        "ram_high",
                        brief,
                        f"RAM at {vm.percent:.0f}% — memory pressure",
                    )
                    return
            except Exception as exc:
                print(f"[OVERWATCH] RAM check failed: {exc}", flush=True)

        # ── Priority 3: Battery critical alert ────────────────────────────
        if not self._is_on_cooldown("battery_low"):
            try:
                battery = psutil.sensors_battery()
                # Only alert if: battery present + discharging + below threshold
                if (
                    battery is not None
                    and not battery.power_plugged
                    and battery.percent < 20
                ):
                    brief = (
                        f"POWER ALERT: Battery is at {battery.percent:.0f}% and the "
                        f"system is running on reserve power with no charger connected. "
                        f"The user should plug in the charger immediately to avoid "
                        f"unexpected data loss or system shutdown."
                    )
                    await self._fire_alert(
                        "battery_low",
                        brief,
                        f"Battery at {battery.percent:.0f}% — charger required",
                    )
                    return
            except Exception as exc:
                print(f"[OVERWATCH] Battery check failed: {exc}", flush=True)

        # ── Priority 4: Midnight wellness nudge ───────────────────────────
        if not self._is_on_cooldown("midnight_nudge"):
            if 0 <= now_hour < 4:
                time_str = datetime.datetime.now().strftime("%I:%M %p")
                brief = (
                    f"WELLNESS ALERT: The current time is {time_str}. "
                    f"The user has been active past midnight. "
                    f"Medical research consistently links sleep deprivation to reduced "
                    f"cognitive performance, impaired decision-making, and long-term "
                    f"health risks. It would be advisable to wrap up the current session "
                    f"and rest. The work will still be here tomorrow."
                )
                await self._fire_alert(
                    "midnight_nudge",
                    brief,
                    f"Midnight wellness check — {time_str}",
                )
                return
