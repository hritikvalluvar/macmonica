"""Historical trends with color-coded sparklines and predictions."""

import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .db import get_connection, init_db, get_snapshots, get_alerts

BLOCKS = " ▁▂▃▄▅▆▇█"

PERIODS = {
    "24h": 86400,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
}


def sparkline(values: list[float], width: int = 50, alert_threshold: float = None) -> str:
    """Render a sparkline. Values above alert_threshold are colored red."""
    if not values:
        return "[dim]no data[/dim]"
    mn, mx = min(values), max(values)
    rng = mx - mn or 1
    step = max(1, len(values) // width)
    sampled = []
    for i in range(0, len(values), step):
        chunk = values[i:i + step]
        sampled.append(sum(chunk) / len(chunk))
    sampled = sampled[:width]

    chars = []
    for v in sampled:
        block = BLOCKS[min(8, int((v - mn) / rng * 8))]
        if alert_threshold is not None and v >= alert_threshold:
            chars.append(f"[red]{block}[/red]")
        else:
            chars.append(block)
    return "".join(chars)


def show_history(period: str):
    console = Console()
    seconds = PERIODS.get(period, 86400)
    since = time.time() - seconds

    with get_connection() as conn:
        init_db(conn)
        rows = get_snapshots(conn, since)
        alerts = get_alerts(conn, since)

    if not rows:
        console.print(f"[dim]No data for the last {period}. Run 'macmonica collect' to start gathering data.[/dim]")
        return

    cpu_vals = [r["cpu_avg"] for r in rows if r["cpu_avg"] is not None]
    mem_vals = [r["mem_percent"] for r in rows if r["mem_percent"] is not None]
    bat_vals = [r["battery_percent"] for r in rows if r["battery_percent"] is not None]
    bat_cap = [r["battery_max_capacity"] for r in rows if r["battery_max_capacity"] is not None]
    disk_vals = [r["disk_percent"] for r in rows if r["disk_percent"] is not None]
    try:
        wifi_vals = [r["wifi_rssi"] for r in rows if r["wifi_rssi"] is not None]
    except (IndexError, KeyError):
        wifi_vals = []

    first_ts = datetime.fromtimestamp(rows[0]["ts"])
    last_ts = datetime.fromtimestamp(rows[-1]["ts"])

    console.print()
    console.print(Panel(
        f"[bold]{period}[/bold] — {first_ts.strftime('%Y-%m-%d %H:%M')} to {last_ts.strftime('%Y-%m-%d %H:%M')} — [dim]{len(rows)} snapshots[/dim]",
        border_style="bright_blue",
    ))

    stats = Table(title="Summary", padding=(0, 1), expand=False)
    stats.add_column("Metric", style="bold", width=8, no_wrap=True)
    stats.add_column("Avg", justify="right", width=5, no_wrap=True)
    stats.add_column("Min", justify="right", width=5, no_wrap=True)
    stats.add_column("Max", justify="right", width=5, no_wrap=True)
    stats.add_column("Trend", min_width=20, max_width=45)

    if cpu_vals:
        stats.add_row("CPU", f"{_avg(cpu_vals):.1f}", f"{min(cpu_vals):.1f}", f"{max(cpu_vals):.1f}",
                       sparkline(cpu_vals, width=40, alert_threshold=90))
    if mem_vals:
        stats.add_row("Memory", f"{_avg(mem_vals):.1f}", f"{min(mem_vals):.1f}", f"{max(mem_vals):.1f}",
                       sparkline(mem_vals, width=40, alert_threshold=90))
    if disk_vals:
        stats.add_row("Disk", f"{_avg(disk_vals):.1f}", f"{min(disk_vals):.1f}", f"{max(disk_vals):.1f}",
                       sparkline(disk_vals, width=40, alert_threshold=90))
    if bat_vals:
        stats.add_row("Battery", f"{_avg(bat_vals):.1f}", f"{min(bat_vals):.1f}", f"{max(bat_vals):.1f}",
                       sparkline(bat_vals, width=40))
    if wifi_vals:
        stats.add_row("WiFi", f"{_avg(wifi_vals):.0f}", f"{min(wifi_vals)}", f"{max(wifi_vals)}",
                       sparkline(wifi_vals, width=40, alert_threshold=-75))

    console.print(stats)

    # Battery degradation + prediction
    if bat_cap and len(bat_cap) > 1:
        console.print()
        first_cap = bat_cap[0]
        last_cap = bat_cap[-1]
        delta = last_cap - first_cap
        if delta != 0:
            console.print(f"[bold]Battery Health:[/bold] {first_cap}% → {last_cap}% ({delta:+d}% over {period})")
        else:
            console.print(f"[bold]Battery Health:[/bold] Stable at {last_cap}%")
        console.print(f"  [dim]{sparkline(bat_cap, width=40)}[/dim]")

    # Battery prediction (needs 7d+ data)
    if bat_cap and len(bat_cap) > 100:
        prediction = _predict_battery(bat_cap, rows, seconds)
        if prediction:
            console.print(f"  {prediction}")

    # Peak usage
    if cpu_vals and len(rows) > 1:
        console.print()
        peak_row = max(rows, key=lambda r: r["cpu_avg"] or 0)
        peak_time = datetime.fromtimestamp(peak_row["ts"]).strftime("%Y-%m-%d %H:%M")
        console.print(f"[bold]Peak CPU:[/bold] {peak_row['cpu_avg']:.1f}% at {peak_time}")

    if mem_vals and len(rows) > 1:
        peak_row = max(rows, key=lambda r: r["mem_percent"] or 0)
        peak_time = datetime.fromtimestamp(peak_row["ts"]).strftime("%Y-%m-%d %H:%M")
        console.print(f"[bold]Peak Memory:[/bold] {peak_row['mem_percent']:.1f}% at {peak_time}")

    # Alert summary
    console.print()
    if alerts:
        alert_counts = {}
        for a in alerts:
            t = a["alert_type"]
            alert_counts[t] = alert_counts.get(t, 0) + 1
        parts = [f"{k}: {v}" for k, v in alert_counts.items()]
        console.print(f"[bold]Alerts fired:[/bold] {', '.join(parts)}")
    else:
        console.print("[dim]No alerts in this period.[/dim]")
    console.print()


def _predict_battery(capacities, rows, period_seconds):
    """Predict when battery health will hit 75% based on observed degradation rate."""
    if not capacities or len(capacities) < 2:
        return None

    first = capacities[0]
    last = capacities[-1]
    if first <= last:
        return None  # No degradation observed

    # Rate: % lost per day
    days = period_seconds / 86400
    rate_per_day = (first - last) / days
    if rate_per_day <= 0:
        return None

    current = last
    targets = [75, 70, 65]
    predictions = []
    for target in targets:
        if current <= target:
            continue
        days_to_target = (current - target) / rate_per_day
        months = days_to_target / 30
        if months < 36:
            predictions.append(f"{target}% in ~{months:.0f} months")

    if predictions:
        return f"[dim]Prediction: {', '.join(predictions)}[/dim]"
    return None


def _avg(vals):
    return sum(vals) / len(vals) if vals else 0
