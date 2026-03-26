"""Enhanced live terminal dashboard with process grouping, energy impact, and recommendations."""

import time
from collections import defaultdict

import psutil
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .macos import get_battery_health, get_process_power, get_thermal_status
from .recommendations import get_current_recommendations, normalize_process_name

# Track previous net counters for rate calculation
_prev_net = {"ts": 0, "sent": 0, "recv": 0}


def _bar(pct, width=20):
    filled = int(pct / 100 * width)
    empty = width - filled
    color = "red" if pct >= 90 else "yellow" if pct >= 70 else "green"
    return f"[{color}]{'█' * filled}[/{color}][dim]{'░' * empty}[/dim] {pct:.1f}%"


def _fmt(b):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _fmt_rate(bps):
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1048576:
        return f"{bps / 1024:.1f} KB/s"
    else:
        return f"{bps / 1048576:.1f} MB/s"


def cpu_panel():
    per_cpu = psutil.cpu_percent(percpu=True)
    avg = sum(per_cpu) / len(per_cpu)
    load1, load5, load15 = psutil.getloadavg()
    freq = psutil.cpu_freq()

    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right", width=8)
    t.add_column(width=35)

    for i, pct in enumerate(per_cpu):
        t.add_row(f"Core {i}", _bar(pct))

    t.add_row("", "")
    t.add_row("Average", _bar(avg))
    if freq:
        t.add_row("Freq", f"{freq.current:.0f} MHz")
    t.add_row("Load", f"{load1:.2f}  {load5:.2f}  {load15:.2f}  [dim](1m 5m 15m)[/dim]")

    return Panel(t, title="[bold cyan]CPU[/bold cyan]", border_style="cyan")


def memory_panel():
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()

    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right", width=10)
    t.add_column(width=35)

    t.add_row("RAM", _bar(vm.percent))
    t.add_row("Used", f"{_fmt(vm.used)} / {_fmt(vm.total)}")
    t.add_row("Available", _fmt(vm.available))
    t.add_row("Wired", _fmt(vm.wired))
    t.add_row("", "")
    if sw.total > 0:
        t.add_row("Swap", _bar(sw.percent))
        t.add_row("Swap Used", f"{_fmt(sw.used)} / {_fmt(sw.total)}")
    else:
        t.add_row("Swap", "[dim]None[/dim]")

    return Panel(t, title="[bold magenta]Memory[/bold magenta]", border_style="magenta")


def disk_panel():
    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right", width=12)
    t.add_column(width=35)

    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        if usage.total < 1_000_000_000:
            continue
        name = part.mountpoint if len(part.mountpoint) <= 12 else "..." + part.mountpoint[-9:]
        t.add_row(escape(name), _bar(usage.percent))
        t.add_row("", f"{_fmt(usage.used)} / {_fmt(usage.total)}  free: {_fmt(usage.free)}")

    io = psutil.disk_io_counters()
    if io:
        t.add_row("", "")
        t.add_row("Read", _fmt(io.read_bytes))
        t.add_row("Written", _fmt(io.write_bytes))

    return Panel(t, title="[bold blue]Disk[/bold blue]", border_style="blue")


def network_panel():
    global _prev_net
    net = psutil.net_io_counters()
    now = time.time()

    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right", width=10)
    t.add_column(width=35)

    # Calculate rates
    dt = now - _prev_net["ts"] if _prev_net["ts"] else 0
    if dt > 0:
        send_rate = (net.bytes_sent - _prev_net["sent"]) / dt
        recv_rate = (net.bytes_recv - _prev_net["recv"]) / dt
    else:
        send_rate = recv_rate = 0

    _prev_net = {"ts": now, "sent": net.bytes_sent, "recv": net.bytes_recv}

    t.add_row("↑ Rate", _fmt_rate(send_rate))
    t.add_row("↓ Rate", _fmt_rate(recv_rate))
    t.add_row("", "")
    t.add_row("Total ↑", _fmt(net.bytes_sent))
    t.add_row("Total ↓", _fmt(net.bytes_recv))
    t.add_row("Errors", f"↑ {net.errout}  ↓ {net.errin}")

    return Panel(t, title="[bold yellow]Network[/bold yellow]", border_style="yellow")


def battery_panel():
    bat = psutil.sensors_battery()
    if not bat:
        return Panel("[dim]No battery[/dim]", title="[bold green]Battery[/bold green]", border_style="green")

    health = get_battery_health()

    t = Table.grid(padding=(0, 2))
    t.add_column(justify="right", width=10)
    t.add_column(width=35)

    t.add_row("Charge", _bar(bat.percent))
    status = "Charging" if bat.power_plugged else "Discharging"
    color = "green" if bat.power_plugged else "yellow"
    t.add_row("Status", f"[{color}]{status}[/{color}]")

    if bat.secsleft > 0 and not bat.power_plugged:
        hrs = bat.secsleft // 3600
        mins = (bat.secsleft % 3600) // 60
        t.add_row("Remaining", f"{hrs}h {mins}m")

    if health:
        cap = health.get("max_capacity", "?")
        cap_color = "red" if isinstance(cap, int) and cap < 80 else "yellow" if isinstance(cap, int) and cap < 90 else "green"
        t.add_row("Health", f"[{cap_color}]{cap}%[/{cap_color}]")
        t.add_row("Cycles", str(health.get("cycle_count", "?")))
        t.add_row("Condition", str(health.get("condition", "?")))
        temp = health.get("temperature")
        if temp is not None:
            temp_color = "red" if temp >= 40 else "yellow" if temp >= 35 else "green"
            t.add_row("Temp", f"[{temp_color}]{temp:.1f}°C[/{temp_color}]")

    return Panel(t, title="[bold green]Battery[/bold green]", border_style="green")


def processes_panel():
    """Top processes grouped by app with energy impact."""
    power = get_process_power()

    # Collect all processes
    raw = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            raw.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "cpu": info["cpu_percent"] or 0,
                "mem": info["memory_percent"] or 0,
                "power": power.get(info["pid"], 0),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Group by normalized name
    groups = defaultdict(lambda: {"cpu": 0, "mem": 0, "power": 0, "count": 0})
    for p in raw:
        key = normalize_process_name(p["name"])
        groups[key]["cpu"] += p["cpu"]
        groups[key]["mem"] += p["mem"]
        groups[key]["power"] += p["power"]
        groups[key]["count"] += 1

    sorted_groups = sorted(groups.items(), key=lambda x: x[1]["cpu"], reverse=True)

    t = Table(padding=(0, 1), expand=True)
    t.add_column("Process", width=18)
    t.add_column("#", justify="right", width=3, style="dim")
    t.add_column("CPU %", justify="right", width=7)
    t.add_column("MEM %", justify="right", width=7)
    t.add_column("Power", justify="right", width=6)

    for name, g in sorted_groups[:10]:
        cpu_str = f"{g['cpu']:.1f}"
        if g["cpu"] > 50:
            cpu_str = f"[red]{cpu_str}[/red]"
        elif g["cpu"] > 20:
            cpu_str = f"[yellow]{cpu_str}[/yellow]"

        count_str = str(g["count"]) if g["count"] > 1 else ""
        power_str = f"{g['power']:.1f}" if g["power"] > 0 else "[dim]—[/dim]"

        t.add_row(escape(name[:18]), count_str, cpu_str, f"{g['mem']:.1f}", power_str)

    return Panel(t, title="[bold red]Top Processes[/bold red]", border_style="red")


def recommendations_panel():
    recs = get_current_recommendations()
    if not recs:
        return Panel("[green]All clear[/green]", title="[bold]Recommendations[/bold]", border_style="dim", height=4)

    text = "\n".join(f"[yellow]•[/yellow] {r}" for r in recs[:3])
    return Panel(text, title="[bold]Recommendations[/bold]", border_style="yellow", height=4)


def build_layout():
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=6),
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(name="cpu"),
        Layout(name="memory"),
        Layout(name="processes"),
    )
    layout["right"].split_column(
        Layout(name="disk"),
        Layout(name="network"),
        Layout(name="battery"),
    )
    return layout


def update_layout(layout):
    layout["header"].update(
        Panel(
            Text("System Monitor  [Ctrl+C to quit]", justify="center", style="bold white"),
            style="on dark_blue",
            border_style="bright_blue",
        )
    )
    layout["cpu"].update(cpu_panel())
    layout["memory"].update(memory_panel())
    layout["processes"].update(processes_panel())
    layout["disk"].update(disk_panel())
    layout["network"].update(network_panel())
    layout["battery"].update(battery_panel())
    layout["footer"].update(recommendations_panel())


def run_dashboard():
    console = Console()
    layout = build_layout()

    # Prime CPU readings
    psutil.cpu_percent(percpu=True)
    time.sleep(0.5)

    update_layout(layout)

    with Live(layout, console=console, refresh_per_second=1, screen=True):
        while True:
            time.sleep(1)
            update_layout(layout)
