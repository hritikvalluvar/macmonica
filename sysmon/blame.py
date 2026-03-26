"""Blame — correlate battery drain with resource hogs."""

import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .db import get_connection, init_db, get_snapshots, get_top_processes_for_snapshot


def run_blame(hours: int = 1):
    console = Console()
    since = time.time() - hours * 3600

    with get_connection() as conn:
        init_db(conn)
        rows = get_snapshots(conn, since)

        if len(rows) < 2:
            console.print(f"[dim]Not enough data for the last {hours}h. Need at least 2 snapshots.[/dim]")
            return

        # Battery drain
        bat_start = next((r["battery_percent"] for r in rows if r["battery_percent"] is not None), None)
        bat_end = next((r["battery_percent"] for r in reversed(rows) if r["battery_percent"] is not None), None)

        # Aggregate process CPU across all snapshots
        proc_cpu = {}
        proc_appearances = {}
        for r in rows:
            procs = get_top_processes_for_snapshot(conn, r["id"])
            for p in procs:
                name = p["name"]
                proc_cpu[name] = proc_cpu.get(name, 0) + (p["cpu_percent"] or 0)
                proc_appearances[name] = proc_appearances.get(name, 0) + 1

    # Average CPU per process
    proc_avg_cpu = {name: proc_cpu[name] / proc_appearances[name] for name in proc_cpu}
    sorted_procs = sorted(proc_avg_cpu.items(), key=lambda x: x[1], reverse=True)

    # Time range
    t_start = datetime.fromtimestamp(rows[0]["ts"]).strftime("%H:%M")
    t_end = datetime.fromtimestamp(rows[-1]["ts"]).strftime("%H:%M")

    # CPU average
    cpu_vals = [r["cpu_avg"] for r in rows if r["cpu_avg"] is not None]
    cpu_avg = sum(cpu_vals) / len(cpu_vals) if cpu_vals else 0

    # Memory average
    mem_vals = [r["mem_percent"] for r in rows if r["mem_percent"] is not None]
    mem_avg = sum(mem_vals) / len(mem_vals) if mem_vals else 0

    # Header
    console.print()
    header = f"[bold]{t_start} → {t_end}[/bold] ({hours}h, {len(rows)} snapshots)"
    if bat_start is not None and bat_end is not None:
        drain = bat_start - bat_end
        drain_color = "red" if drain > 20 else "yellow" if drain > 10 else "green"
        header += f"\nBattery: {bat_start:.0f}% → {bat_end:.0f}% ([{drain_color}]-{drain:.0f}%[/{drain_color}])"
        if drain > 0:
            rate = drain / hours
            header += f" ({rate:.1f}%/hr)"
    header += f"\nCPU avg: {cpu_avg:.0f}%  |  Memory avg: {mem_avg:.0f}%"

    console.print(Panel(header, title="[bold]Battery Blame[/bold]", border_style="yellow"))

    # Top CPU consumers
    if sorted_procs:
        table = Table(title="Top CPU Consumers (avg)", padding=(0, 1))
        table.add_column("Process", width=25)
        table.add_column("Avg CPU %", justify="right", width=10)
        table.add_column("Seen", justify="right", width=5, style="dim")

        from rich.markup import escape
        for name, avg_cpu in sorted_procs[:10]:
            cpu_str = f"{avg_cpu:.1f}"
            if avg_cpu > 30:
                cpu_str = f"[red]{cpu_str}[/red]"
            elif avg_cpu > 10:
                cpu_str = f"[yellow]{cpu_str}[/yellow]"
            table.add_row(escape(name[:25]), cpu_str, f"{proc_appearances[name]}x")

        console.print(table)

    # Verdict
    console.print()
    if bat_start is not None and bat_end is not None and bat_start > bat_end:
        drain = bat_start - bat_end
        if sorted_procs:
            top_name, top_cpu = sorted_procs[0]
            if top_cpu > 20:
                console.print(f"  [bold]Verdict:[/bold] [yellow]{top_name}[/yellow] was the likely drain — averaged {top_cpu:.0f}% CPU")
            elif drain > 15:
                console.print(f"  [bold]Verdict:[/bold] Heavy overall usage ({cpu_avg:.0f}% avg CPU) caused the {drain:.0f}% drain")
            else:
                console.print(f"  [bold]Verdict:[/bold] Normal drain ({drain:.0f}% over {hours}h) — no single process dominated")
    else:
        console.print("  [green]Battery was charging or stable.[/green]")
    console.print()
