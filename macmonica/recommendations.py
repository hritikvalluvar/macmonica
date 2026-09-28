"""Correlated recommendation engine — connects signals to actionable advice."""

import psutil

from .macos import get_battery_health, get_thermal_status

# Shared normalization — single source of truth
STRIP_SUFFIXES = (
    " Helper", " Renderer", " Worker", " (GPU)", " (Renderer)",
    " (Plugin)", " Web Content", " (Prewarmed)",
)


def normalize_process_name(name: str) -> str:
    """Strip helper/renderer suffixes to group by parent app."""
    for suffix in STRIP_SUFFIXES:
        if suffix in name:
            return name.split(suffix)[0].strip()
    return name


def get_current_recommendations() -> list[str]:
    recs = []

    # Single pass over all processes
    app_mem = {}
    app_cpu = {}
    app_count = {}
    spotlight_high = False
    kernel_task_cpu = 0.0
    windowserver_cpu = 0.0

    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            name = p.info["name"] or ""
            cpu = p.info["cpu_percent"] or 0
            mem_info = p.info["memory_info"]

            normalized = normalize_process_name(name)
            mem = mem_info.rss if mem_info else 0
            app_mem[normalized] = app_mem.get(normalized, 0) + mem
            app_cpu[normalized] = app_cpu.get(normalized, 0) + cpu
            app_count[normalized] = app_count.get(normalized, 0) + 1

            if name in ("mds", "mds_stores", "mdworker_shared") and cpu > 50:
                spotlight_high = True
            if name == "kernel_task":
                kernel_task_cpu = max(kernel_task_cpu, cpu)
            if name == "WindowServer":
                windowserver_cpu = max(windowserver_cpu, cpu)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Correlated: browser using high memory AND high CPU = likely too many tabs
    for name in app_mem:
        count = app_count[name]
        gb = app_mem[name] / (1024 ** 3)
        cpu = app_cpu.get(name, 0)

        if gb > 3 and count > 10 and cpu > 20:
            recs.append(
                f"{name} — {gb:.1f}GB RAM + {cpu:.0f}% CPU across {count} processes. "
                f"Closing unused tabs would free memory and reduce CPU/battery drain"
            )
        elif gb > 3 and count > 10:
            recs.append(f"{name} — {gb:.1f}GB across {count} processes. Consider closing unused tabs")
        elif gb > 4:
            recs.append(f"{name} — {gb:.1f}GB of memory. Consider restarting it")

    # Battery health with context
    health = get_battery_health()
    thermal = get_thermal_status()
    bat = psutil.sensors_battery()

    if health:
        cap = health.get("max_capacity", 100)
        cycles = health.get("cycle_count", 0)
        if cap <= 75:
            recs.append(f"Battery health at {cap}% ({cycles} cycles) — replacement recommended")
        elif cap <= 80:
            recs.append(f"Battery health at {cap}% ({cycles} cycles) — consider replacement soon")

    # Correlated: kernel_task high + thermal warning = throttling
    if kernel_task_cpu > 30 and thermal and thermal != "No warnings":
        recs.append(
            f"Thermal throttling active — kernel_task at {kernel_task_cpu:.0f}% CPU is the system "
            f"slowing down to cool the machine. Improve airflow or reduce workload"
        )
    elif kernel_task_cpu > 30:
        recs.append("kernel_task high CPU — possible thermal throttling, try improving airflow")

    # Correlated: unplugged + high CPU consumer = battery drain culprit
    if bat and not bat.power_plugged:
        top_cpu_apps = sorted(app_cpu.items(), key=lambda x: x[1], reverse=True)[:3]
        high_drain = [(name, cpu) for name, cpu in top_cpu_apps if cpu > 30]
        if high_drain:
            culprits = ", ".join(f"{name} ({cpu:.0f}%)" for name, cpu in high_drain)
            recs.append(f"On battery — {culprits} draining power. Quit or reduce activity to extend battery life")

    if spotlight_high:
        recs.append("Spotlight indexing — high CPU is temporary and will settle down")

    # Swap pressure
    swap = psutil.swap_memory()
    if swap.total > 0 and swap.percent > 50:
        # Find the biggest memory consumer to make it actionable
        top_mem = max(app_mem.items(), key=lambda x: x[1], default=None)
        if top_mem:
            gb = top_mem[1] / (1024 ** 3)
            recs.append(
                f"Swap at {swap.percent:.0f}% — {top_mem[0]} ({gb:.1f}GB) is the biggest consumer. "
                f"Close it or other apps to stop using swap"
            )
        else:
            recs.append(f"Swap at {swap.percent:.0f}% — close applications to free memory")

    if windowserver_cpu > 30:
        recs.append("WindowServer high CPU — try reducing transparency in System Settings > Accessibility")

    return recs
