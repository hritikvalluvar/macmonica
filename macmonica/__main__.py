"""CLI entry point for macmonica."""

import argparse
import logging
import sys


def _check_for_update():
    """Check PyPI for a newer version. Cached for 24h, never blocks on failure."""
    import json
    import time
    from pathlib import Path

    from . import __version__
    from .config import MACMONICA_DIR

    cache_file = MACMONICA_DIR / ".update_check"
    now = time.time()

    # Only check once per day
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if now - cached.get("ts", 0) < 86400:
                latest = cached.get("latest")
                if latest and latest != __version__:
                    return latest
                return None
        except (json.JSONDecodeError, OSError):
            pass

    try:
        import urllib.request
        req = urllib.request.Request(
            "https://pypi.org/pypi/macmonica/json",
            headers={"Accept": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        latest = data["info"]["version"]

        MACMONICA_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"ts": now, "latest": latest}))

        if latest != __version__:
            return latest
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(prog="macmonica", description="macOS System Health Monitor")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("dashboard", help="Live dashboard (default)")
    sub.add_parser("install", help="Set up collector + daily digest (launchd + cron)")
    sub.add_parser("uninstall", help="Remove collector and daily digest")
    sub.add_parser("collect", help="Run collector daemon")
    sub.add_parser("collect-once", help="Single collection snapshot")

    hist = sub.add_parser("history", help="Historical trends")
    hist.add_argument("--period", choices=["24h", "7d", "30d"], default="24h")

    sub.add_parser("alerts", help="Recent alerts")
    sub.add_parser("recommend", help="Current recommendations")
    sub.add_parser("doctor", help="Full system health check")
    sub.add_parser("why", help="Plain English system explanation")
    sub.add_parser("status", help="Collector and DB status")

    tp = sub.add_parser("top", help="Grouped process list with energy impact")
    tp.add_argument("--sort", choices=["cpu", "mem", "energy"], default="cpu")

    bl = sub.add_parser("blame", help="What drained my battery?")
    bl.add_argument("--hours", type=int, default=1, help="How far back to look (default: 1)")

    sub.add_parser("wake-log", help="Recent wake/sleep events")
    sub.add_parser("usb", help="Connected USB devices and power draw")

    cmp = sub.add_parser("compare", help="Compare two time periods")
    cmp.add_argument("period_a", choices=["24h", "7d", "30d"], help="Recent period")
    cmp.add_argument("period_b", choices=["24h", "7d", "30d"], help="Older period to compare against")

    exp = sub.add_parser("export", help="Export data to CSV")
    exp.add_argument("--period", choices=["24h", "7d", "30d", "all"], default="24h")
    exp.add_argument("--output", "-o", help="Output file (default: stdout)")

    dig = sub.add_parser("digest", help="Daily digest")
    dig.add_argument("--notify", action="store_true", help="Also send macOS notification")
    dig.add_argument("--today", action="store_true", help="Show today's data instead of yesterday")

    rpt = sub.add_parser("report", help="Weekly/monthly health report")
    rpt.add_argument("--period", choices=["week", "month"], default="week")
    rpt.add_argument("--output", "-o", help="Save to file")

    cfg = sub.add_parser("config", help="Configuration")
    cfg.add_argument("--init", action="store_true", help="Create default config file")

    args = parser.parse_args()
    cmd = args.command

    if cmd == "install":
        _install()

    elif cmd == "uninstall":
        _uninstall()

    elif cmd in ("collect", "collect-once"):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )
        from .collector import run_collector
        run_collector(once=(cmd == "collect-once"))

    elif cmd == "history":
        from .history import show_history
        show_history(args.period)

    elif cmd == "alerts":
        _show_alerts()

    elif cmd == "recommend":
        _show_recommendations()

    elif cmd == "doctor":
        from .doctor import run_doctor
        run_doctor()

    elif cmd == "why":
        from .why import run_why
        run_why()

    elif cmd == "top":
        from .top import run_top
        run_top(sort_by=args.sort)

    elif cmd == "blame":
        from .blame import run_blame
        run_blame(hours=args.hours)

    elif cmd == "wake-log":
        _show_wake_log()

    elif cmd == "usb":
        _show_usb()

    elif cmd == "report":
        from .report import run_report
        run_report(period=args.period, output=args.output)

    elif cmd == "compare":
        from .compare import run_compare
        run_compare(args.period_a, args.period_b)

    elif cmd == "export":
        from .export import run_export
        run_export(args.period, args.output)

    elif cmd == "digest":
        from .digest import run_digest
        run_digest(notify=args.notify, today=args.today)

    elif cmd == "status":
        _show_status()

    elif cmd == "config":
        _handle_config(args)

    else:
        from .dashboard import run_dashboard
        run_dashboard()

    # Show update notice (skip for background commands)
    if cmd not in ("collect", "collect-once"):
        try:
            latest = _check_for_update()
            if latest:
                from rich.console import Console
                from . import __version__
                Console().print(
                    f"\n[yellow]Update available:[/yellow] {__version__} → [green]{latest}[/green]"
                    f"  —  [dim]pip install --upgrade macmonica[/dim]"
                )
        except Exception:
            pass


def _show_alerts():
    import time
    from rich.console import Console
    from rich.table import Table
    from .db import get_connection, init_db, get_alerts

    with get_connection() as conn:
        init_db(conn)
        alerts = get_alerts(conn, time.time() - 7 * 86400)

    console = Console()
    if not alerts:
        console.print("[dim]No alerts in the last 7 days.[/dim]")
        return

    table = Table(title="Recent Alerts (7 days)")
    table.add_column("Time", style="dim")
    table.add_column("Type")
    table.add_column("Message")
    table.add_column("Value", justify="right")

    from datetime import datetime
    for a in alerts[:50]:
        ts = datetime.fromtimestamp(a["ts"]).strftime("%Y-%m-%d %H:%M")
        table.add_row(ts, a["alert_type"], a["message"], f"{a['value']:.1f}" if a["value"] else "")

    console.print(table)


def _show_recommendations():
    from rich.console import Console
    from .recommendations import get_current_recommendations

    console = Console()
    recs = get_current_recommendations()
    if not recs:
        console.print("[green]All clear — no recommendations right now.[/green]")
        return

    console.print("[bold]Recommendations:[/bold]")
    for r in recs:
        console.print(f"  [yellow]•[/yellow] {r}")


def _show_status():
    import os
    import time
    from datetime import datetime
    from rich.console import Console
    from rich.table import Table
    from .db import get_connection, init_db, get_db_stats
    from .collector import PID_FILE

    with get_connection() as conn:
        init_db(conn)
        stats = get_db_stats(conn)

    console = Console()
    table = Table(title="Macmonica Status")
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    collector_status = "[red]Not running[/red]"
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            collector_status = f"[green]Running[/green] (pid {pid})"
        except (ValueError, ProcessLookupError, PermissionError):
            collector_status = "[yellow]Stale PID file[/yellow]"

    table.add_row("Collector", collector_status)
    table.add_row("Snapshots", str(stats["snapshot_count"]))
    table.add_row("Alerts logged", str(stats["alert_count"]))
    table.add_row("DB size", f"{stats['db_size_mb']:.2f} MB")

    if stats["latest_ts"]:
        last = datetime.fromtimestamp(stats["latest_ts"])
        ago = int(time.time() - stats["latest_ts"])
        if ago < 120:
            ago_str = f"{ago}s ago"
        elif ago < 7200:
            ago_str = f"{ago // 60}m ago"
        else:
            ago_str = f"{ago // 3600}h ago"
        table.add_row("Last snapshot", f"{last.strftime('%Y-%m-%d %H:%M:%S')} ({ago_str})")
    else:
        table.add_row("Last snapshot", "[dim]None — run 'macmonica collect-once' first[/dim]")

    console.print(table)


def _show_wake_log():
    from datetime import datetime
    from rich.console import Console
    from rich.table import Table
    from .macos import get_wake_sleep_events

    console = Console()
    events = get_wake_sleep_events(hours=24)

    if not events:
        console.print("[dim]No wake/sleep events in the last 24h.[/dim]")
        return

    table = Table(title="Wake/Sleep Events (24h)", padding=(0, 1))
    table.add_column("Time", style="dim", width=10)
    table.add_column("Event", width=10)
    table.add_column("Reason", width=40)

    for e in events[-30:]:
        ts = datetime.fromtimestamp(e["ts"]).strftime("%H:%M:%S")
        etype = e["type"]
        color = "green" if "Wake" in etype else "yellow"
        table.add_row(ts, f"[{color}]{etype}[/{color}]", e["reason"])

    # Summary
    wakes = sum(1 for e in events if "Wake" in e["type"])
    sleeps = sum(1 for e in events if e["type"] == "Sleep")
    dark = sum(1 for e in events if e["type"] == "DarkWake")

    console.print(table)
    console.print(f"\n  [dim]Sleep: {sleeps}  |  DarkWake: {dark}  |  Wake: {wakes}[/dim]")
    if dark > 10:
        console.print(f"  [yellow]High DarkWake count ({dark}) — your Mac is waking frequently in sleep. Check for apps preventing sleep.[/yellow]")
    console.print()


def _show_usb():
    from rich.console import Console
    from rich.table import Table
    from .macos import get_usb_devices

    console = Console()
    devices = get_usb_devices()

    if not devices:
        console.print("[dim]No USB devices with power draw detected.[/dim]")
        return

    table = Table(title="USB Devices (Power Draw)", padding=(0, 1))
    table.add_column("Device", width=30)
    table.add_column("Required mA", justify="right", width=12)
    table.add_column("Available mA", justify="right", width=12)

    total_ma = 0
    from rich.markup import escape
    for d in devices:
        req = d.get("required_ma", 0)
        avail = d.get("available_ma", 0)
        total_ma += req
        table.add_row(escape(d["name"]), str(req), str(avail) if avail else "—")

    console.print(table)
    watts = total_ma * 5 / 1000  # Rough estimate at 5V
    console.print(f"\n  [dim]Total USB power draw: ~{total_ma}mA (~{watts:.1f}W)[/dim]\n")


def _install():
    import os
    import shutil
    import subprocess
    from pathlib import Path
    from rich.console import Console
    from .config import ensure_dir, save_default_config, CONFIG_PATH, MACMONICA_DIR

    console = Console()
    python = sys.executable
    macmonica_bin = shutil.which("macmonica") or f"{python} -m macmonica"
    home = Path.home()
    plist_name = "com.macmonica.collector.plist"
    plist_dst = home / "Library" / "LaunchAgents" / plist_name

    console.print("[bold]Installing macmonica...[/bold]\n")

    # 1. Create data directory + default config
    ensure_dir()
    if not CONFIG_PATH.exists():
        save_default_config()
        console.print(f"  [green]Created config[/green] at {CONFIG_PATH}")
    else:
        console.print(f"  [dim]Config already exists at {CONFIG_PATH}[/dim]")

    # 2. Create and load launchd plist for collector
    # Unload existing if present
    if plist_dst.exists():
        subprocess.run(["launchctl", "unload", str(plist_dst)], capture_output=True)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.macmonica.collector</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>macmonica</string>
        <string>collect</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{MACMONICA_DIR}/collector.log</string>
    <key>StandardErrorPath</key>
    <string>{MACMONICA_DIR}/collector-err.log</string>
</dict>
</plist>"""

    plist_dst.write_text(plist_content)
    subprocess.run(["launchctl", "load", str(plist_dst)], capture_output=True)
    console.print(f"  [green]Collector installed[/green] — runs on login, collecting every 60s")

    # 3. Add daily digest cron job (8am)
    cron_line = f"0 8 * * * {python} -m macmonica digest --notify"
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""

    if "macmonica digest" not in existing and "macmonica" not in existing:
        new_cron = existing.rstrip("\n") + "\n" + cron_line + "\n" if existing.strip() else cron_line + "\n"
        subprocess.run(["crontab", "-"], input=new_cron, text=True, capture_output=True)
        console.print(f"  [green]Daily digest cron[/green] — 8am notification with yesterday's summary")
    else:
        console.print(f"  [dim]Daily digest cron already exists[/dim]")

    console.print()
    console.print("[bold green]Done![/bold green] macmonica is running.\n")
    console.print("  macmonica status    — check collector")
    console.print("  macmonica doctor    — full health check")
    console.print("  macmonica why       — what's going on?")
    console.print("  macmonica           — live dashboard")
    console.print("  macmonica uninstall — remove everything")
    console.print()


def _uninstall():
    import subprocess
    from pathlib import Path
    from rich.console import Console

    console = Console()
    home = Path.home()
    plist_name = "com.macmonica.collector.plist"
    plist_dst = home / "Library" / "LaunchAgents" / plist_name

    console.print("[bold]Uninstalling macmonica...[/bold]\n")

    # 1. Stop and remove launchd agent
    if plist_dst.exists():
        subprocess.run(["launchctl", "unload", str(plist_dst)], capture_output=True)
        plist_dst.unlink()
        console.print("  [green]Removed collector launchd agent[/green]")
    else:
        console.print("  [dim]No collector launchd agent found[/dim]")

    # 2. Remove digest cron
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode == 0 and "macmonica digest" in result.stdout:
        new_cron = "\n".join(
            line for line in result.stdout.splitlines()
            if "macmonica digest" not in line
        ).strip() + "\n"
        if new_cron.strip():
            subprocess.run(["crontab", "-"], input=new_cron, text=True, capture_output=True)
        else:
            subprocess.run(["crontab", "-r"], capture_output=True)
        console.print("  [green]Removed daily digest cron job[/green]")
    else:
        console.print("  [dim]No digest cron job found[/dim]")

    console.print()
    console.print("[dim]Data at ~/.macmonica/ was kept. Delete it manually if you want: rm -rf ~/.macmonica[/dim]")
    console.print()


def _handle_config(args):
    from rich.console import Console
    from .config import load_config, save_default_config, CONFIG_PATH
    import json

    console = Console()
    if args.init:
        save_default_config()
        console.print(f"[green]Config created at {CONFIG_PATH}[/green]")
    else:
        config = load_config()
        console.print_json(json.dumps(config, indent=2))


if __name__ == "__main__":
    main()
