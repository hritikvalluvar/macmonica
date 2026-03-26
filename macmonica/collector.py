"""Background data collector — gathers metrics and stores in SQLite."""

import logging
import os
import signal
import time

import psutil

from .config import load_config, MACMONICA_DIR
from .db import get_connection, init_db, insert_snapshot_with_processes, cleanup
from .macos import get_battery_health, get_thermal_status, get_wifi_info

logger = logging.getLogger("macmonica.collector")

PID_FILE = MACMONICA_DIR / "collector.pid"
_shutdown = False

# Non-blocking CPU: call cpu_percent() without interval, use delta from previous call
_cpu_primed = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown = True


def collect_snapshot() -> dict:
    """Gather a single snapshot of all system metrics.

    Uses non-blocking cpu_percent() — relies on the time gap between
    successive calls (the sleep interval) for accurate deltas.
    ~0.05s per cycle instead of 1.0s with blocking.
    """
    global _cpu_primed
    per_cpu = psutil.cpu_percent(percpu=True)

    # First call always returns 0 — skip it
    if not _cpu_primed:
        _cpu_primed = True
        per_cpu = [0.0] * len(per_cpu)

    cpu_avg = sum(per_cpu) / len(per_cpu) if per_cpu else 0
    cpu_max = max(per_cpu) if per_cpu else 0

    load1, load5, load15 = psutil.getloadavg()

    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    dio = psutil.disk_io_counters()
    net = psutil.net_io_counters()
    bat = psutil.sensors_battery()
    bat_health = get_battery_health()   # cached 5min
    thermal = get_thermal_status()      # cached 5min
    wifi = get_wifi_info()              # cached 1min

    return {
        "ts": time.time(),
        "cpu_avg": cpu_avg,
        "cpu_max": cpu_max,
        "load_1": load1, "load_5": load5, "load_15": load15,
        "mem_percent": vm.percent,
        "mem_used": vm.used,
        "mem_total": vm.total,
        "swap_percent": sw.percent,
        "disk_percent": disk.percent,
        "disk_read_bytes": dio.read_bytes if dio else None,
        "disk_write_bytes": dio.write_bytes if dio else None,
        "net_sent_bytes": net.bytes_sent,
        "net_recv_bytes": net.bytes_recv,
        "battery_percent": bat.percent if bat else None,
        "battery_plugged": int(bat.power_plugged) if bat else None,
        "battery_cycle_count": bat_health.get("cycle_count") if bat_health else None,
        "battery_max_capacity": bat_health.get("max_capacity") if bat_health else None,
        "battery_condition": bat_health.get("condition") if bat_health else None,
        "thermal_warning": thermal,
        "wifi_rssi": wifi.get("rssi") if wifi else None,
        "wifi_noise": wifi.get("noise") if wifi else None,
        "wifi_tx_rate": wifi.get("tx_rate") if wifi else None,
    }


def collect_top_processes(n: int = 5) -> list[dict]:
    """Get top N processes by CPU usage. No subprocess calls — pure psutil."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "cpu_percent": info["cpu_percent"] or 0,
                "mem_percent": info["memory_percent"] or 0,
                "energy_impact": None,  # Only computed in dashboard (avoids spawning `top`)
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["cpu_percent"], reverse=True)
    return procs[:n]


def run_collector(once: bool = False):
    """Main collection loop."""
    global _shutdown, _cpu_primed

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    config = load_config()
    conn = get_connection()

    try:
        init_db(conn)

        if not once:
            PID_FILE.write_text(str(os.getpid()))

        # Prime CPU counter so first real snapshot has data
        psutil.cpu_percent(percpu=True)
        _cpu_primed = False  # Will be set True on first collect_snapshot call

        cycle = 0
        logger.info("Collector started (pid=%d, interval=%ds, once=%s)",
                     os.getpid(), config["collect_interval"], once)

        while not _shutdown:
            try:
                snapshot = collect_snapshot()
                procs = collect_top_processes()
                sid = insert_snapshot_with_processes(conn, snapshot, procs)

                logger.info("Snapshot #%d (cpu=%.1f%%, mem=%.1f%%)", sid, snapshot["cpu_avg"], snapshot["mem_percent"])

                from .alerts import check_and_fire_alerts
                check_and_fire_alerts(conn, snapshot, config)

                cycle += 1
                if cycle % 100 == 0:
                    cleanup(conn, config["retention_days"])
                    logger.info("DB cleanup (retention=%dd)", config["retention_days"])

            except Exception:
                logger.exception("Collection cycle failed")

            if once:
                break

            time.sleep(config["collect_interval"])

    finally:
        conn.close()
        if PID_FILE.exists() and not once:
            PID_FILE.unlink(missing_ok=True)
        logger.info("Collector stopped")
