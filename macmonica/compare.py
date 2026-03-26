"""Compare two time periods side-by-side."""

import time
from rich.console import Console
from rich.table import Table

from .db import get_connection, init_db, get_snapshots, get_alerts
from .history import sparkline

PERIODS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def run_compare(period_a: str, period_b: str):
    console = Console()
    sec_a = PERIODS.get(period_a)
    sec_b = PERIODS.get(period_b)
    if not sec_a or not sec_b:
        console.print("[red]Invalid period. Use 24h, 7d, or 30d.[/red]")
        return

    now = time.time()

    with get_connection() as conn:
        init_db(conn)
        rows_a = get_snapshots(conn, now - sec_a)
        rows_b = get_snapshots(conn, now - sec_b, limit=len(rows_a) * 2 if rows_a else 1000)
        # Period B = older period, excluding period A overlap
        rows_b = [r for r in rows_b if r["ts"] < now - sec_a]
        alerts_a = get_alerts(conn, now - sec_a)
        alerts_b = [a for a in get_alerts(conn, now - sec_b) if a["ts"] < now - sec_a]

    if not rows_a and not rows_b:
        console.print("[dim]No data. Run 'macmonica collect' first.[/dim]")
        return

    def _stats(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            return None, None, None, []
        return sum(vals) / len(vals), min(vals), max(vals), vals

    table = Table(title=f"Compare: {period_a} vs prior {period_b}", padding=(0, 1))
    table.add_column("Metric", style="bold", width=8, no_wrap=True)
    table.add_column(f"{period_a} Avg", justify="right", width=6)
    table.add_column(f"Prior Avg", justify="right", width=6)
    table.add_column("Delta", justify="right", width=7)
    table.add_column(f"{period_a}", width=18)
    table.add_column(f"Prior", width=18)

    for key, label in [("cpu_avg", "CPU"), ("mem_percent", "Memory"), ("disk_percent", "Disk"), ("battery_percent", "Battery")]:
        avg_a, min_a, max_a, vals_a = _stats(rows_a, key)
        avg_b, min_b, max_b, vals_b = _stats(rows_b, key)

        str_a = f"{avg_a:.1f}" if avg_a is not None else "—"
        str_b = f"{avg_b:.1f}" if avg_b is not None else "—"

        if avg_a is not None and avg_b is not None:
            delta = avg_a - avg_b
            if key == "battery_percent":
                # Lower battery is expected (discharge), flag capacity drops
                delta_str = f"{delta:+.1f}"
            else:
                color = "red" if delta > 5 else "green" if delta < -5 else ""
                delta_str = f"[{color}]{delta:+.1f}[/{color}]" if color else f"{delta:+.1f}"
        else:
            delta_str = "—"

        spark_a = sparkline(vals_a, width=15) if vals_a else "[dim]—[/dim]"
        spark_b = sparkline(vals_b, width=15) if vals_b else "[dim]—[/dim]"

        table.add_row(label, str_a, str_b, delta_str, spark_a, spark_b)

    # Battery health comparison
    cap_a = [r["battery_max_capacity"] for r in rows_a if r["battery_max_capacity"] is not None]
    cap_b = [r["battery_max_capacity"] for r in rows_b if r["battery_max_capacity"] is not None]
    if cap_a and cap_b:
        latest_a = cap_a[-1]
        latest_b = cap_b[-1]
        delta = latest_a - latest_b
        color = "red" if delta < 0 else "green" if delta > 0 else ""
        delta_str = f"[{color}]{delta:+d}%[/{color}]" if color else f"{delta:+d}%"
        table.add_row("Health", f"{latest_a}%", f"{latest_b}%", delta_str, "", "")

    console.print()
    console.print(table)

    # Data counts
    console.print()
    console.print(f"  [dim]{period_a}: {len(rows_a)} snapshots, {len(alerts_a)} alerts  |  Prior: {len(rows_b)} snapshots, {len(alerts_b)} alerts[/dim]")
    console.print()
