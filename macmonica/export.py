"""Export snapshots to CSV."""

import csv
import sys
import time

from .db import get_connection, init_db, get_snapshots

PERIODS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": 365 * 86400}

COLUMNS = [
    "ts", "cpu_avg", "cpu_max", "load_1", "load_5", "load_15",
    "mem_percent", "mem_used", "mem_total", "swap_percent",
    "disk_percent", "disk_read_bytes", "disk_write_bytes",
    "net_sent_bytes", "net_recv_bytes",
    "battery_percent", "battery_plugged", "battery_cycle_count",
    "battery_max_capacity", "battery_condition", "thermal_warning",
]


def run_export(period: str, output: str | None):
    seconds = PERIODS.get(period, 86400)
    since = time.time() - seconds

    with get_connection() as conn:
        init_db(conn)
        rows = get_snapshots(conn, since)

    if not rows:
        print(f"No data for the last {period}.", file=sys.stderr)
        return

    f = open(output, "w", newline="") if output else sys.stdout
    try:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for r in rows:
            writer.writerow([r[c] for c in COLUMNS])
    finally:
        if output:
            f.close()
            print(f"Exported {len(rows)} snapshots to {output}", file=sys.stderr)
