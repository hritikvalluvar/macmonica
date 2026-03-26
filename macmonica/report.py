"""Weekly/monthly system health report."""

import time
from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .db import get_connection, init_db, get_snapshots, get_alerts
from .history import sparkline

PERIODS = {"week": 7, "month": 30}


def _safe(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def run_report(period: str = "week", output: str = None):
    console = Console(file=open(output, "w") if output else None, force_terminal=output is None)
    days = PERIODS.get(period, 7)
    since = time.time() - days * 86400

    with get_connection() as conn:
        init_db(conn)
        rows = get_snapshots(conn, since)
        alerts = get_alerts(conn, since)

    if not rows:
        console.print(f"[dim]No data for the last {period}.[/dim]")
        return

    start_date = datetime.fromtimestamp(rows[0]["ts"]).strftime("%Y-%m-%d")
    end_date = datetime.fromtimestamp(rows[-1]["ts"]).strftime("%Y-%m-%d")

    console.print()
    console.print(Panel(
        f"[bold]System Health Report[/bold]\n{start_date} to {end_date} ({len(rows)} snapshots)",
        border_style="bright_blue",
    ))

    # Overview stats
    def _series(key):
        return [r[key] for r in rows if r[key] is not None]

    cpu = _series("cpu_avg")
    mem = _series("mem_percent")
    disk = _series("disk_percent")
    bat = _series("battery_percent")
    bat_cap = _series("battery_max_capacity")
    wifi = [r["wifi_rssi"] for r in rows if _safe(r, "wifi_rssi") is not None]

    table = Table(title="Resource Summary", padding=(0, 1))
    table.add_column("Metric", style="bold", width=10)
    table.add_column("Avg", justify="right", width=6)
    table.add_column("Min", justify="right", width=6)
    table.add_column("Max", justify="right", width=6)
    table.add_column("Trend", width=30)

    for vals, label, thresh in [
        (cpu, "CPU %", 90), (mem, "Memory %", 90), (disk, "Disk %", 90),
        (bat, "Battery %", None), (wifi, "WiFi dBm", -75),
    ]:
        if vals:
            avg = sum(vals) / len(vals)
            table.add_row(label, f"{avg:.1f}", f"{min(vals):.0f}", f"{max(vals):.0f}",
                          sparkline(vals, width=25, alert_threshold=thresh))

    console.print(table)

    # Battery health section
    if bat_cap:
        console.print()
        first_cap = bat_cap[0]
        last_cap = bat_cap[-1]
        delta = last_cap - first_cap
        cycles = next((r["battery_cycle_count"] for r in reversed(rows) if _safe(r, "battery_cycle_count")), "?")
        console.print(f"[bold]Battery Health:[/bold] {last_cap}% (cycles: {cycles})")
        if delta != 0:
            console.print(f"  Change: {delta:+d}% over this {period}")

    # Daily breakdown
    console.print()
    daily = Table(title="Daily Averages", padding=(0, 1))
    daily.add_column("Date", width=12)
    daily.add_column("CPU", justify="right", width=6)
    daily.add_column("Mem", justify="right", width=6)
    daily.add_column("Bat", justify="right", width=8)

    days_data = {}
    for r in rows:
        day = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")
        if day not in days_data:
            days_data[day] = {"cpu": [], "mem": [], "bat_start": None, "bat_end": None}
        if r["cpu_avg"] is not None:
            days_data[day]["cpu"].append(r["cpu_avg"])
        if r["mem_percent"] is not None:
            days_data[day]["mem"].append(r["mem_percent"])
        if r["battery_percent"] is not None:
            if days_data[day]["bat_start"] is None:
                days_data[day]["bat_start"] = r["battery_percent"]
            days_data[day]["bat_end"] = r["battery_percent"]

    for day in sorted(days_data.keys()):
        d = days_data[day]
        cpu_avg = f"{sum(d['cpu']) / len(d['cpu']):.0f}%" if d["cpu"] else "—"
        mem_avg = f"{sum(d['mem']) / len(d['mem']):.0f}%" if d["mem"] else "—"
        if d["bat_start"] is not None and d["bat_end"] is not None:
            bat_str = f"{d['bat_start']:.0f}→{d['bat_end']:.0f}%"
        else:
            bat_str = "—"
        daily.add_row(day, cpu_avg, mem_avg, bat_str)

    console.print(daily)

    # Alerts section
    console.print()
    if alerts:
        alert_counts = {}
        for a in alerts:
            alert_counts[a["alert_type"]] = alert_counts.get(a["alert_type"], 0) + 1
        console.print(f"[bold]Alerts:[/bold] {sum(alert_counts.values())} total")
        for k, v in sorted(alert_counts.items(), key=lambda x: -x[1]):
            console.print(f"  {k}: {v}")
    else:
        console.print(f"[green]No alerts this {period}.[/green]")

    console.print()

    if output:
        console.file.close()
        import sys
        print(f"Report saved to {output}", file=sys.stderr)
