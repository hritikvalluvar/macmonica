"""Grouped process list with energy impact — one-shot."""

from collections import defaultdict

import psutil
from rich.console import Console
from rich.table import Table

from .macos import get_process_power
from .recommendations import normalize_process_name


def _fmt(b):
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TB"


def run_top(sort_by: str = "cpu"):
    console = Console()
    power = get_process_power()

    raw = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "memory_info"]):
        try:
            info = p.info
            raw.append({
                "name": info["name"] or "?",
                "cpu": info["cpu_percent"] or 0,
                "mem_pct": info["memory_percent"] or 0,
                "mem_bytes": info["memory_info"].rss if info["memory_info"] else 0,
                "power": power.get(info["pid"], 0),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Group by normalized name
    groups = defaultdict(lambda: {"cpu": 0, "mem_pct": 0, "mem_bytes": 0, "power": 0, "count": 0})
    for p in raw:
        key = normalize_process_name(p["name"])
        groups[key]["cpu"] += p["cpu"]
        groups[key]["mem_pct"] += p["mem_pct"]
        groups[key]["mem_bytes"] += p["mem_bytes"]
        groups[key]["power"] += p["power"]
        groups[key]["count"] += 1

    sort_key = {"cpu": "cpu", "mem": "mem_bytes", "energy": "power"}.get(sort_by, "cpu")
    sorted_groups = sorted(groups.items(), key=lambda x: x[1][sort_key], reverse=True)

    table = Table(title=f"Processes (grouped, sorted by {sort_by})", padding=(0, 1))
    table.add_column("App", width=22)
    table.add_column("#", justify="right", width=4, style="dim")
    table.add_column("CPU %", justify="right", width=7)
    table.add_column("Memory", justify="right", width=8)
    table.add_column("Mem %", justify="right", width=6)
    table.add_column("Power", justify="right", width=6)

    from rich.markup import escape
    for name, g in sorted_groups[:25]:
        cpu_str = f"{g['cpu']:.1f}"
        if g["cpu"] > 50:
            cpu_str = f"[red]{cpu_str}[/red]"
        elif g["cpu"] > 20:
            cpu_str = f"[yellow]{cpu_str}[/yellow]"

        mem_str = _fmt(g["mem_bytes"])
        if g["mem_bytes"] > 3 * 1024 ** 3:
            mem_str = f"[red]{mem_str}[/red]"
        elif g["mem_bytes"] > 1 * 1024 ** 3:
            mem_str = f"[yellow]{mem_str}[/yellow]"

        power_str = f"{g['power']:.1f}" if g["power"] > 0 else "[dim]—[/dim]"
        table.add_row(
            escape(name[:22]),
            str(g["count"]),
            cpu_str,
            mem_str,
            f"{g['mem_pct']:.1f}",
            power_str,
        )

    console.print()
    console.print(table)
    console.print(f"\n  [dim]{len(raw)} processes → {len(groups)} app groups[/dim]\n")
