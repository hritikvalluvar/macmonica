"""Daily digest — summarize yesterday's stats."""

import time
from datetime import datetime, timedelta

from rich.console import Console
from rich.panel import Panel

from .db import get_connection, init_db, get_snapshots, get_alerts
from .macos import send_notification


def run_digest(notify: bool = False, today: bool = False):
    """Generate and display digest. Use today=True to show today's data instead of yesterday."""
    console = Console()

    now = time.time()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    if today:
        period_start = today_start
        period_end = now
        label = datetime.now().strftime("%Y-%m-%d") + " (so far)"
    else:
        period_start = today_start - 86400
        period_end = today_start
        label = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    with get_connection() as conn:
        init_db(conn)
        rows = get_snapshots(conn, period_start)
        rows = [r for r in rows if r["ts"] < period_end]
        alerts = get_alerts(conn, period_start)
        alerts = [a for a in alerts if a["ts"] < period_end]

    if not rows:
        console.print(f"[dim]No data for {'today' if today else 'yesterday'}.[/dim]")
        return

    def _avg(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else None

    def _minmax(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return (min(vals), max(vals)) if vals else (None, None)

    cpu_avg = _avg("cpu_avg")
    mem_avg = _avg("mem_percent")
    bat_start = next((r["battery_percent"] for r in rows if r["battery_percent"] is not None), None)
    bat_end = next((r["battery_percent"] for r in reversed(rows) if r["battery_percent"] is not None), None)
    bat_cap = next((r["battery_max_capacity"] for r in reversed(rows) if r["battery_max_capacity"] is not None), None)
    cpu_min, cpu_max = _minmax("cpu_avg")
    mem_min, mem_max = _minmax("mem_percent")

    lines = [f"[bold]{label}[/bold] — {len(rows)} snapshots"]
    lines.append("")

    if cpu_avg is not None:
        lines.append(f"CPU:     avg {cpu_avg:.0f}%  (min {cpu_min:.0f}%, max {cpu_max:.0f}%)")
    if mem_avg is not None:
        lines.append(f"Memory:  avg {mem_avg:.0f}%  (min {mem_min:.0f}%, max {mem_max:.0f}%)")
    if bat_start is not None and bat_end is not None:
        lines.append(f"Battery: {bat_start:.0f}% → {bat_end:.0f}%")
    if bat_cap is not None:
        lines.append(f"Health:  {bat_cap}%")
    if alerts:
        types = {}
        for a in alerts:
            types[a["alert_type"]] = types.get(a["alert_type"], 0) + 1
        alert_str = ", ".join(f"{k}: {v}" for k, v in types.items())
        lines.append(f"Alerts:  {alert_str}")
    else:
        lines.append("Alerts:  None")

    body = "\n".join(lines)
    console.print()
    console.print(Panel(body, title="[bold]Daily Digest[/bold]", border_style="bright_blue"))
    console.print()

    # Send as notification if requested
    if notify:
        short = f"CPU {cpu_avg:.0f}%, Mem {mem_avg:.0f}%"
        if bat_cap is not None:
            short += f", Health {bat_cap}%"
        if alerts:
            short += f", {len(alerts)} alerts"
        send_notification(f"Macmonica — {label}", short)
