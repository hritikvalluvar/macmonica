"""Rule-based recommendation engine."""

import psutil

from .macos import get_battery_health

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
    app_count = {}
    spotlight_high = False
    kernel_task_high = False
    windowserver_high = False

    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            name = p.info["name"] or ""
            cpu = p.info["cpu_percent"] or 0
            mem_info = p.info["memory_info"]

            # Memory aggregation
            normalized = normalize_process_name(name)
            mem = mem_info.rss if mem_info else 0
            app_mem[normalized] = app_mem.get(normalized, 0) + mem
            app_count[normalized] = app_count.get(normalized, 0) + 1

            # Spotlight
            if name in ("mds", "mds_stores", "mdworker_shared") and cpu > 50:
                spotlight_high = True

            # kernel_task
            if name == "kernel_task" and cpu > 30:
                kernel_task_high = True

            # WindowServer
            if name == "WindowServer" and cpu > 30:
                windowserver_high = True

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Memory hog recommendations
    for name, mem in app_mem.items():
        count = app_count[name]
        gb = mem / (1024 ** 3)
        if gb > 3 and count > 10:
            recs.append(f"{name} is using {gb:.1f}GB across {count} processes — consider closing unused tabs/windows")
        elif gb > 4:
            recs.append(f"{name} is using {gb:.1f}GB of memory — consider restarting it")

    # Battery health
    health = get_battery_health()
    if health:
        cap = health.get("max_capacity", 100)
        cycles = health.get("cycle_count", 0)
        if cap <= 75:
            recs.append(f"Battery health is at {cap}% ({cycles} cycles) — replacement recommended")
        elif cap <= 80:
            recs.append(f"Battery health is at {cap}% ({cycles} cycles) — consider battery replacement soon")

    if spotlight_high:
        recs.append("Spotlight is indexing — high CPU is temporary and will settle down")

    # Swap pressure
    swap = psutil.swap_memory()
    if swap.total > 0 and swap.percent > 50:
        recs.append(f"Swap usage is at {swap.percent:.0f}% — close some applications to free memory")

    if kernel_task_high:
        recs.append("kernel_task is using high CPU — possible thermal throttling, try improving airflow")

    if windowserver_high:
        recs.append("WindowServer is using high CPU — try reducing transparency in System Settings > Accessibility")

    return recs
