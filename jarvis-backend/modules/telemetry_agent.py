"""
Phase 2 – Invisible Fast-Lane: Telemetry Agent
Provides instant, comprehensive system metrics via psutil.

Supersedes the basic psutil call in os_agent.get_system_diagnostics().
No GUI is opened; all reads are in-process and complete in < 500 ms.
"""

import datetime
import platform
import time
from typing import Any, Optional

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False


class TelemetryAgent:
    """
    Reads CPU, RAM, Disk, Network, top-processes, battery, and uptime
    in a single blocking call and returns the data as a structured dict
    or a concise LLM-friendly summary string.
    """

    def get_full_snapshot(self) -> dict[str, Any]:
        """
        Returns a complete system snapshot as a structured dict.
        All keys are always present; individual sub-keys may contain an
        'error' key if that sensor is unavailable.
        """
        if not _PSUTIL_OK:
            return {"error": "psutil not installed — run: pip install psutil"}

        snap: dict[str, Any] = {}

        # ── CPU ──────────────────────────────────────────────────────────────
        try:
            freq = psutil.cpu_freq()
            snap["cpu"] = {
                "percent":        psutil.cpu_percent(interval=0.2),
                "cores_physical": psutil.cpu_count(logical=False),
                "cores_logical":  psutil.cpu_count(logical=True),
                "freq_mhz":       round(freq.current) if freq else None,
                "freq_max_mhz":   round(freq.max)     if freq else None,
            }
        except Exception as e:
            snap["cpu"] = {"error": str(e)}

        # ── RAM ──────────────────────────────────────────────────────────────
        try:
            ram  = psutil.virtual_memory()
            swap = psutil.swap_memory()
            snap["ram"] = {
                "total_gb":     round(ram.total     / (1024 ** 3), 1),
                "used_gb":      round(ram.used      / (1024 ** 3), 1),
                "available_gb": round(ram.available / (1024 ** 3), 1),
                "percent":      ram.percent,
                "swap_used_gb":  round(swap.used  / (1024 ** 3), 1),
                "swap_total_gb": round(swap.total / (1024 ** 3), 1),
            }
        except Exception as e:
            snap["ram"] = {"error": str(e)}

        # ── Disk ─────────────────────────────────────────────────────────────
        try:
            disks = []
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disks.append({
                        "drive":    part.mountpoint,
                        "fstype":   part.fstype,
                        "total_gb": round(usage.total / (1024 ** 3), 1),
                        "used_gb":  round(usage.used  / (1024 ** 3), 1),
                        "free_gb":  round(usage.free  / (1024 ** 3), 1),
                        "percent":  usage.percent,
                    })
                except PermissionError:
                    continue
            snap["disks"] = disks
        except Exception as e:
            snap["disks"] = [{"error": str(e)}]

        # ── Network (session totals) ─────────────────────────────────────────
        try:
            net = psutil.net_io_counters()
            snap["network"] = {
                "bytes_sent_mb": round(net.bytes_sent / (1024 ** 2), 1),
                "bytes_recv_mb": round(net.bytes_recv / (1024 ** 2), 1),
                "packets_sent":  net.packets_sent,
                "packets_recv":  net.packets_recv,
                "errin":         net.errin,
                "errout":        net.errout,
            }
        except Exception as e:
            snap["network"] = {"error": str(e)}

        # ── Top processes (by memory %) ──────────────────────────────────────
        try:
            procs = sorted(
                psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
                key=lambda p: p.info.get("memory_percent") or 0,
                reverse=True,
            )
            snap["top_processes"] = [
                {
                    "pid":  p.info["pid"],
                    "name": p.info["name"],
                    "cpu":  round(p.info.get("cpu_percent")    or 0, 1),
                    "mem":  round(p.info.get("memory_percent") or 0, 1),
                }
                for p in procs[:8]
            ]
        except Exception as e:
            snap["top_processes"] = [{"error": str(e)}]

        # ── Battery (laptops only) ───────────────────────────────────────────
        try:
            bat = psutil.sensors_battery()
            if bat:
                secs = bat.secsleft
                snap["battery"] = {
                    "percent":  bat.percent,
                    "charging": bat.power_plugged,
                    "secs_left": secs if secs not in (
                        psutil.POWER_TIME_UNLIMITED,
                        psutil.POWER_TIME_UNKNOWN,
                    ) else None,
                }
        except Exception:
            pass  # desktop — silently skip

        # ── System uptime ────────────────────────────────────────────────────
        try:
            boot_ts = psutil.boot_time()
            snap["uptime"] = {
                "boot_time":    datetime.datetime.fromtimestamp(boot_ts).strftime("%Y-%m-%d %H:%M"),
                "uptime_hours": round((time.time() - boot_ts) / 3600, 1),
            }
        except Exception as e:
            snap["uptime"] = {"error": str(e)}

        # ── Platform ─────────────────────────────────────────────────────────
        snap["platform"] = {
            "system":  platform.system(),
            "version": platform.version()[:80],
            "machine": platform.machine(),
            "node":    platform.node(),
        }

        return snap

    # ── LLM-facing output ────────────────────────────────────────────────────

    def get_summary_string(self) -> str:
        """
        Returns a concise, LLM/TTS-ready summary of the system state.
        Designed to be spoken by J.A.R.V.I.S. in 2-4 sentences.
        """
        snap = self.get_full_snapshot()
        if "error" in snap:
            return f"Telemetry offline: {snap['error']}"

        lines = ["SYSTEM TELEMETRY SNAPSHOT:"]

        cpu = snap.get("cpu", {})
        if "percent" in cpu:
            freq_str = f" @ {cpu['freq_mhz']} MHz" if cpu.get("freq_mhz") else ""
            lines.append(
                f"  CPU    : {cpu['percent']}% load"
                f" | {cpu.get('cores_physical','?')}P / {cpu.get('cores_logical','?')}L cores"
                f"{freq_str}"
            )

        ram = snap.get("ram", {})
        if "percent" in ram:
            lines.append(
                f"  RAM    : {ram['percent']}% used"
                f" ({ram.get('used_gb','?')} / {ram.get('total_gb','?')} GB)"
                + (
                    f"  |  Swap {ram['swap_used_gb']} / {ram['swap_total_gb']} GB"
                    if ram.get("swap_total_gb", 0) > 0 else ""
                )
            )

        for disk in snap.get("disks", []):
            if "percent" in disk:
                lines.append(
                    f"  Disk {disk['drive']:3}: {disk['percent']}% used"
                    f" ({disk.get('free_gb','?')} GB free / {disk.get('total_gb','?')} GB total)"
                )

        net = snap.get("network", {})
        if "bytes_recv_mb" in net:
            lines.append(
                f"  Network: ↑ {net.get('bytes_sent_mb','?')} MB sent,"
                f" ↓ {net.get('bytes_recv_mb','?')} MB received (session)"
            )

        uptime = snap.get("uptime", {})
        if "uptime_hours" in uptime:
            lines.append(
                f"  Uptime : {uptime['uptime_hours']}h"
                f" (booted {uptime.get('boot_time', 'unknown')})"
            )

        bat = snap.get("battery")
        if bat:
            status = "charging" if bat.get("charging") else "on battery"
            lines.append(f"  Battery: {bat['percent']}% ({status})")

        top = snap.get("top_processes", [])
        if top:
            top_str = ", ".join(
                f"{p['name']}({p['mem']}%)"
                for p in top[:5]
                if "name" in p
            )
            lines.append(f"  Top    : {top_str}")

        return "\n".join(lines)

    def get_quick_status(self) -> str:
        """
        Ultra-fast one-liner for conversational status responses.
        e.g. "CPU 12%, RAM 58% used (7.0/12 GB), C: 41% full."
        """
        if not _PSUTIL_OK:
            return "Telemetry offline — psutil not available."
        try:
            cpu  = psutil.cpu_percent(interval=0.1)
            ram  = psutil.virtual_memory()
            disk = psutil.disk_usage("C:\\")
            return (
                f"CPU {cpu}%, "
                f"RAM {ram.percent}% used "
                f"({round(ram.used/(1024**3),1)} / {round(ram.total/(1024**3),1)} GB), "
                f"C: drive {disk.percent}% full "
                f"({round(disk.free/(1024**3),1)} GB free)."
            )
        except Exception as e:
            return f"Quick telemetry read failed: {e}"
