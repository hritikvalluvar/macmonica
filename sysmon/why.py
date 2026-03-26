"""Plain English system explanation — 'why is my Mac slow/hot/draining?'"""

import psutil
from rich.console import Console
from rich.panel import Panel

from .macos import get_battery_health, get_thermal_status, get_wifi_info
from .recommendations import normalize_process_name


def run_why():
    console = Console()
    findings = []

    # Gather everything in one pass
    load1, load5, load15 = psutil.getloadavg()
    cores = psutil.cpu_count()
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    bat = psutil.sensors_battery()
    thermal = get_thermal_status()
    wifi = get_wifi_info()
    health = get_battery_health()

    # Process scan — single pass
    app_cpu = {}
    app_mem = {}
    app_count = {}
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            name = normalize_process_name(p.info["name"] or "")
            cpu = p.info["cpu_percent"] or 0
            mem = p.info["memory_info"].rss if p.info["memory_info"] else 0
            app_cpu[name] = app_cpu.get(name, 0) + cpu
            app_mem[name] = app_mem.get(name, 0) + mem
            app_count[name] = app_count.get(name, 0) + 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    top_cpu = sorted(app_cpu.items(), key=lambda x: x[1], reverse=True)
    top_mem = sorted(app_mem.items(), key=lambda x: x[1], reverse=True)

    # --- Analyze ---

    # CPU pressure
    load_ratio = load5 / cores if cores else 0
    if load_ratio > 2:
        findings.append(f"Your Mac is [red]overloaded[/red] — load ({load5:.1f}) is {load_ratio:.1f}x your {cores} cores.")
        if top_cpu and top_cpu[0][1] > 30:
            findings.append(f"  → [yellow]{top_cpu[0][0]}[/yellow] is the biggest CPU consumer at {top_cpu[0][1]:.0f}%.")
    elif load_ratio > 1:
        findings.append(f"CPU is under [yellow]moderate pressure[/yellow] — load {load5:.1f} across {cores} cores.")
        if top_cpu and top_cpu[0][1] > 20:
            findings.append(f"  → {top_cpu[0][0]} is using {top_cpu[0][1]:.0f}% CPU.")

    # Memory pressure
    if vm.percent > 90:
        findings.append(f"Memory is [red]critically low[/red] at {vm.percent:.0f}% — only {vm.available / 1024**3:.1f}GB free.")
    elif vm.percent > 80:
        findings.append(f"Memory is getting [yellow]tight[/yellow] at {vm.percent:.0f}%.")

    if top_mem and top_mem[0][1] > 3 * 1024 ** 3:
        gb = top_mem[0][1] / 1024 ** 3
        count = app_count.get(top_mem[0][0], 1)
        msg = f"  → {top_mem[0][0]} is using {gb:.1f}GB"
        if count > 5:
            msg += f" across {count} processes (close unused tabs?)"
        findings.append(msg)

    # Swap
    if sw.total > 0 and sw.percent > 30:
        findings.append(f"Your Mac is [yellow]swapping[/yellow] ({sw.percent:.0f}% used) — this slows everything down. Close some apps.")

    # Thermal
    if thermal and "no thermal" not in thermal:
        findings.append(f"[red]Thermal throttling[/red] detected — your Mac is too hot and slowing itself down.")
        # Check kernel_task
        kt_cpu = app_cpu.get("kernel_task", 0)
        if kt_cpu > 20:
            findings.append(f"  → kernel_task is using {kt_cpu:.0f}% CPU to manage heat.")

    # Battery
    if bat and not bat.power_plugged:
        if bat.percent < 15:
            findings.append(f"Battery is [red]critically low[/red] at {bat.percent}% — plug in soon.")
        elif bat.percent < 30:
            findings.append(f"Battery is [yellow]low[/yellow] at {bat.percent}%.")

        if health:
            cap = health.get("max_capacity", 100)
            cycles = health.get("cycle_count", 0)
            if cap < 80:
                findings.append(f"Battery health is [red]degraded[/red] at {cap}% ({cycles} cycles) — shorter life is expected.")

    # WiFi
    if wifi:
        rssi = wifi.get("rssi", 0)
        if rssi > -50:
            pass  # Excellent
        elif rssi > -60:
            pass  # Good
        elif rssi > -70:
            findings.append(f"WiFi signal is [yellow]fair[/yellow] ({rssi} dBm) — may cause slower speeds.")
        else:
            findings.append(f"WiFi signal is [red]weak[/red] ({rssi} dBm) — move closer to your router or check for interference.")

    # Spotlight
    mds_cpu = app_cpu.get("mds_stores", 0) + app_cpu.get("mds", 0) + app_cpu.get("mdworker_shared", 0)
    if mds_cpu > 30:
        findings.append(f"Spotlight is indexing ({mds_cpu:.0f}% CPU) — this is temporary and will settle down.")

    # All good?
    if not findings:
        findings.append("[green]Everything looks good![/green] No resource issues detected.")
        findings.append(f"  CPU load: {load5:.1f}/{cores} cores  |  Memory: {vm.percent:.0f}%  |  Battery: {bat.percent if bat else 'N/A'}%")

    # Render
    console.print()
    body = "\n".join(findings)
    console.print(Panel(body, title="[bold]Why is my Mac...[/bold]", border_style="bright_blue"))
    console.print()
