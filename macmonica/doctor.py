"""System health checklist with pass/fail/warn verdicts."""

import psutil
from rich.console import Console
from rich.table import Table

from .macos import get_battery_health, get_thermal_status


def _verdict(ok, warn_msg=None):
    if ok is True:
        return "[green]PASS[/green]"
    elif ok is False:
        return "[red]FAIL[/red]"
    else:
        return f"[yellow]WARN[/yellow]"


def run_doctor():
    console = Console()
    table = Table(title="System Health Check", padding=(0, 1))
    table.add_column("Check", width=22, style="bold")
    table.add_column("Status", width=6, justify="center")
    table.add_column("Detail", min_width=30)

    checks = []

    # CPU load
    load1, load5, load15 = psutil.getloadavg()
    cores = psutil.cpu_count()
    load_ratio = load5 / cores if cores else load5
    if load_ratio > 2:
        checks.append(("CPU Load", False, f"Load {load5:.1f} is {load_ratio:.1f}x core count ({cores} cores) — system is overloaded"))
    elif load_ratio > 1:
        checks.append(("CPU Load", None, f"Load {load5:.1f} is above core count ({cores}) — moderate pressure"))
    else:
        checks.append(("CPU Load", True, f"Load {load5:.1f} / {cores} cores ({load_ratio:.1f}x)"))

    # Memory
    vm = psutil.virtual_memory()
    if vm.percent > 90:
        checks.append(("Memory", False, f"{vm.percent:.0f}% used — critically low ({_fmt(vm.available)} free)"))
    elif vm.percent > 80:
        checks.append(("Memory", None, f"{vm.percent:.0f}% used ({_fmt(vm.available)} free)"))
    else:
        checks.append(("Memory", True, f"{vm.percent:.0f}% used ({_fmt(vm.available)} free)"))

    # Swap
    sw = psutil.swap_memory()
    if sw.total > 0 and sw.percent > 50:
        checks.append(("Swap", False, f"{sw.percent:.0f}% used ({_fmt(sw.used)}) — heavy swapping"))
    elif sw.total > 0 and sw.percent > 20:
        checks.append(("Swap", None, f"{sw.percent:.0f}% used ({_fmt(sw.used)})"))
    else:
        checks.append(("Swap", True, f"{'Not in use' if sw.total == 0 else f'{sw.percent:.0f}% used'}"))

    # Disk space
    disk = psutil.disk_usage("/")
    free_gb = disk.free / (1024 ** 3)
    if disk.percent > 95:
        checks.append(("Disk Space", False, f"{disk.percent:.0f}% full — only {free_gb:.1f}GB free"))
    elif disk.percent > 85:
        checks.append(("Disk Space", None, f"{disk.percent:.0f}% full ({free_gb:.1f}GB free)"))
    else:
        checks.append(("Disk Space", True, f"{disk.percent:.0f}% full ({free_gb:.1f}GB free)"))

    # Battery
    bat = psutil.sensors_battery()
    if bat:
        health = get_battery_health()
        if health:
            cap = health.get("max_capacity", 100)
            cycles = health.get("cycle_count", 0)
            condition = health.get("condition", "Unknown")

            if cap < 75:
                checks.append(("Battery Health", False, f"{cap}% capacity, {cycles} cycles — replacement recommended"))
            elif cap < 80:
                checks.append(("Battery Health", None, f"{cap}% capacity, {cycles} cycles — degraded"))
            else:
                checks.append(("Battery Health", True, f"{cap}% capacity, {cycles} cycles, {condition}"))
        else:
            checks.append(("Battery Health", True, f"{bat.percent}% charge"))

        if bat.percent < 10 and not bat.power_plugged:
            checks.append(("Battery Level", False, f"{bat.percent}% — plug in now"))
        elif bat.percent < 20 and not bat.power_plugged:
            checks.append(("Battery Level", None, f"{bat.percent}% — low"))
        else:
            status = "charging" if bat.power_plugged else "on battery"
            checks.append(("Battery Level", True, f"{bat.percent}% ({status})"))
    else:
        checks.append(("Battery", True, "No battery (desktop)"))

    # Thermal
    thermal = get_thermal_status()
    if thermal and "no thermal" not in thermal:
        checks.append(("Thermal", False, thermal))
    else:
        checks.append(("Thermal", True, "No warnings"))

    # Process count
    proc_count = len(psutil.pids())
    if proc_count > 500:
        checks.append(("Processes", None, f"{proc_count} running — higher than typical"))
    else:
        checks.append(("Processes", True, f"{proc_count} running"))

    # Memory hogs (any single app group > 4GB)
    from .recommendations import normalize_process_name
    app_mem = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            name = normalize_process_name(p.info["name"] or "")
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            app_mem[name] = app_mem.get(name, 0) + mem
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    hogs = [(n, m) for n, m in app_mem.items() if m > 3 * 1024 ** 3]
    if hogs:
        hog_list = ", ".join(f"{n} ({m / 1024**3:.1f}GB)" for n, m in sorted(hogs, key=lambda x: -x[1]))
        checks.append(("Memory Hogs", None, hog_list))
    else:
        checks.append(("Memory Hogs", True, "No app using >3GB"))

    # Render
    for name, ok, detail in checks:
        table.add_row(name, _verdict(ok), detail)

    console.print()
    console.print(table)

    # Summary
    fails = sum(1 for _, ok, _ in checks if ok is False)
    warns = sum(1 for _, ok, _ in checks if ok is None)
    passes = sum(1 for _, ok, _ in checks if ok is True)
    console.print()
    if fails:
        console.print(f"  [red]{fails} FAIL[/red]  [yellow]{warns} WARN[/yellow]  [green]{passes} PASS[/green]")
    elif warns:
        console.print(f"  [yellow]{warns} WARN[/yellow]  [green]{passes} PASS[/green]")
    else:
        console.print(f"  [green]All {passes} checks passed![/green]")
    console.print()


def _fmt(b):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"
