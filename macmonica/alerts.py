"""Alert system — thresholds, anomaly detection, trend alerts, auto-actions, webhooks."""

import logging
import os
import signal
import time
from datetime import datetime

from .db import get_recent_snapshots, get_last_alert_of_type, insert_alert, get_snapshots
from .macos import send_notification, send_webhook

logger = logging.getLogger("macmonica.alerts")


def _in_quiet_hours(config: dict) -> bool:
    quiet = config.get("quiet_hours")
    if not quiet or not quiet.get("enabled"):
        return False
    now = datetime.now().hour
    start = quiet.get("start", 23)
    end = quiet.get("end", 7)
    if start <= end:
        return start <= now < end
    else:
        return now >= start or now < end


def check_and_fire_alerts(conn, snapshot: dict, config: dict):
    alerts_cfg = config.get("alerts", {})
    cooldown = config.get("alert_cooldown_minutes", 30) * 60
    quiet = _in_quiet_hours(config)

    # CPU sustained
    cpu_cfg = alerts_cfg.get("cpu_sustained", {})
    if cpu_cfg.get("enabled"):
        duration = cpu_cfg.get("duration_minutes", 5)
        threshold = cpu_cfg.get("threshold", 90)
        recent = get_recent_snapshots(conn, duration)
        if len(recent) >= max(1, duration - 1):
            if all(r["cpu_avg"] >= threshold for r in recent):
                _fire(
                    conn, config, "cpu_sustained",
                    f"CPU above {threshold}% for {duration}min (now {snapshot['cpu_avg']:.0f}%)",
                    snapshot["cpu_avg"], cooldown, quiet,
                )

    # Memory high
    mem_cfg = alerts_cfg.get("memory_high", {})
    if mem_cfg.get("enabled") and snapshot.get("mem_percent"):
        threshold = mem_cfg.get("threshold", 90)
        if snapshot["mem_percent"] >= threshold:
            _fire(
                conn, config, "memory_high",
                f"Memory at {snapshot['mem_percent']:.0f}%",
                snapshot["mem_percent"], cooldown, quiet,
            )

    # Disk high
    disk_cfg = alerts_cfg.get("disk_high", {})
    if disk_cfg.get("enabled") and snapshot.get("disk_percent"):
        threshold = disk_cfg.get("threshold", 90)
        if snapshot["disk_percent"] >= threshold:
            _fire(
                conn, config, "disk_high",
                f"Disk at {snapshot['disk_percent']:.0f}%",
                snapshot["disk_percent"], cooldown, quiet,
            )

    # Disk I/O rate — detect runaway writes
    disk_io_cfg = alerts_cfg.get("disk_io_high", {})
    if disk_io_cfg.get("enabled", True):
        _check_disk_io(conn, config, snapshot, disk_io_cfg, cooldown, quiet)

    # Battery health
    bat_cfg = alerts_cfg.get("battery_health", {})
    if bat_cfg.get("enabled") and snapshot.get("battery_max_capacity"):
        threshold = bat_cfg.get("threshold", 80)
        if snapshot["battery_max_capacity"] <= threshold:
            cycles = snapshot.get("battery_cycle_count", "?")
            _fire(
                conn, config, "battery_health",
                f"Battery health {snapshot['battery_max_capacity']}% ({cycles} cycles)",
                snapshot["battery_max_capacity"], 86400, quiet,
            )

    # Battery temperature
    bat_temp_cfg = alerts_cfg.get("battery_temp", {})
    if bat_temp_cfg.get("enabled", True) and snapshot.get("battery_temp"):
        threshold = bat_temp_cfg.get("threshold", 40)
        if snapshot["battery_temp"] >= threshold:
            _fire(
                conn, config, "battery_temp",
                f"Battery temperature {snapshot['battery_temp']:.1f}°C — reduce load or move to cooler area",
                snapshot["battery_temp"], cooldown, quiet,
            )

    # WiFi signal weak
    wifi_cfg = alerts_cfg.get("wifi_weak", {})
    if wifi_cfg.get("enabled", True) and snapshot.get("wifi_rssi"):
        threshold = wifi_cfg.get("threshold", -75)
        if snapshot["wifi_rssi"] < threshold:
            _fire(
                conn, config, "wifi_weak",
                f"WiFi signal weak ({snapshot['wifi_rssi']} dBm)",
                snapshot["wifi_rssi"], cooldown, quiet,
            )

    # Anomaly detection (σ-based with process attribution)
    anomaly_cfg = alerts_cfg.get("anomaly", {})
    if anomaly_cfg.get("enabled", True):
        _check_anomalies(conn, config, snapshot, anomaly_cfg, cooldown, quiet)

    # Trend alerts (gradual shifts over days)
    trend_cfg = alerts_cfg.get("trends", {})
    if trend_cfg.get("enabled", True):
        _check_trends(conn, config, snapshot, trend_cfg, quiet)

    # Auto-actions
    _run_auto_actions(config, snapshot)


def _check_disk_io(conn, config, snapshot, cfg, cooldown, quiet):
    """Alert if disk write rate exceeds threshold (MB/s sustained)."""
    threshold_mbps = cfg.get("write_mbps", 100)  # 100 MB/s sustained
    recent = get_recent_snapshots(conn, 3)  # last 3 minutes

    if len(recent) < 2:
        return

    # Compute write rate from consecutive snapshots
    rates = []
    for i in range(1, len(recent)):
        prev, curr = recent[i - 1], recent[i]
        if prev["disk_write_bytes"] and curr["disk_write_bytes"]:
            dt = curr["ts"] - prev["ts"]
            if dt > 0:
                rate_mbps = (curr["disk_write_bytes"] - prev["disk_write_bytes"]) / dt / 1048576
                rates.append(rate_mbps)

    if rates and all(r > threshold_mbps for r in rates):
        avg_rate = sum(rates) / len(rates)
        _fire(
            conn, config, "disk_io_high",
            f"Disk writes at {avg_rate:.0f} MB/s — possible runaway process",
            avg_rate, cooldown, quiet,
        )


def _check_anomalies(conn, config, snapshot, cfg, cooldown, quiet):
    """Detect anomalies using standard deviation bands + time-of-day baseline.

    Instead of comparing against a flat 7-day average (which makes 23% CPU
    "anomalous" when the average is 15%), this uses:
    1. Time-of-day baseline: compare against the same 2-hour window over the past 7 days
    2. Standard deviation: alert only when >3σ above the time-of-day mean
    3. Minimum absolute thresholds: ignore low values regardless of deviation
    4. Process attribution: include the top process when alerting
    """
    min_cpu = cfg.get("min_cpu", 40)
    min_mem = cfg.get("min_mem", 85)
    sigma_threshold = cfg.get("sigma", 3.0)

    week_ago = time.time() - 7 * 86400
    baseline = get_snapshots(conn, week_ago)

    if len(baseline) < 120:  # need decent data before alerting
        return

    # Filter baseline to same time-of-day window (±1 hour)
    current_hour = datetime.now().hour
    tod_baseline = []
    for r in baseline:
        h = datetime.fromtimestamp(r["ts"]).hour
        # Within ±1 hour (wraps around midnight)
        if abs(h - current_hour) <= 1 or abs(h - current_hour) >= 23:
            tod_baseline.append(r)

    # Fall back to full baseline if time-of-day window has too few samples
    if len(tod_baseline) < 30:
        tod_baseline = baseline

    metrics = [
        ("cpu_avg", "CPU", min_cpu),
        ("mem_percent", "Memory", min_mem),
    ]

    for key, label, min_val in metrics:
        vals = [r[key] for r in tod_baseline if r[key] is not None]
        if len(vals) < 20:
            continue

        avg = sum(vals) / len(vals)
        variance = sum((v - avg) ** 2 for v in vals) / len(vals)
        std = variance ** 0.5

        current = snapshot.get(key)
        if current is None or std < 1:  # skip if no variance
            continue

        # Must exceed both: absolute minimum AND statistical threshold
        sigma_above = (current - avg) / std
        if current >= min_val and sigma_above >= sigma_threshold:
            # Attribute to top process
            blame = _get_top_process_name(conn, snapshot)
            blame_str = f" (top: {blame})" if blame else ""
            _fire(
                conn, config, f"anomaly_{key}",
                f"{label} at {current:.0f}% — {sigma_above:.1f}σ above normal for this time of day{blame_str}",
                current, cooldown, quiet,
            )


def _get_top_process_name(conn, snapshot) -> str | None:
    """Get the top CPU process from the current snapshot's top_processes."""
    try:
        row = conn.execute(
            "SELECT name, cpu_percent FROM top_processes "
            "WHERE snapshot_id = (SELECT id FROM snapshots WHERE ts = ? LIMIT 1) "
            "ORDER BY cpu_percent DESC LIMIT 1",
            (snapshot["ts"],)
        ).fetchone()
        if row and row["cpu_percent"] > 5:
            from .recommendations import normalize_process_name
            return f"{normalize_process_name(row['name'])} {row['cpu_percent']:.0f}%"
    except Exception:
        pass
    return None


def _check_trends(conn, config, snapshot, cfg, quiet):
    """Detect gradual shifts by comparing this week vs last week.

    Fires at most once per day per trend type. Looks for:
    - Memory baseline creeping up (possible leak or accumulation)
    - Battery health declining faster than expected
    - Disk usage growing steadily
    """
    cooldown = 86400  # once per day max

    now = time.time()
    this_week = get_snapshots(conn, now - 7 * 86400)
    last_week_start = now - 14 * 86400
    last_week_end = now - 7 * 86400

    # Need two weeks of data
    last_week = [r for r in get_snapshots(conn, last_week_start) if r["ts"] < last_week_end]
    if len(this_week) < 200 or len(last_week) < 200:
        return

    # Memory trend: average memory this week vs last week
    mem_this = [r["mem_percent"] for r in this_week if r["mem_percent"] is not None]
    mem_last = [r["mem_percent"] for r in last_week if r["mem_percent"] is not None]
    if mem_this and mem_last:
        avg_this = sum(mem_this) / len(mem_this)
        avg_last = sum(mem_last) / len(mem_last)
        increase = avg_this - avg_last
        min_increase = cfg.get("memory_increase_pct", 10)
        if increase >= min_increase:
            _fire(
                conn, config, "trend_memory",
                f"Memory usage up {increase:.0f}% this week vs last (avg {avg_last:.0f}% → {avg_this:.0f}%)",
                avg_this, cooldown, quiet,
            )

    # Disk trend: steady growth
    disk_this = [r["disk_percent"] for r in this_week if r["disk_percent"] is not None]
    disk_last = [r["disk_percent"] for r in last_week if r["disk_percent"] is not None]
    if disk_this and disk_last:
        avg_this = sum(disk_this) / len(disk_this)
        avg_last = sum(disk_last) / len(disk_last)
        increase = avg_this - avg_last
        min_increase = cfg.get("disk_increase_pct", 5)
        if increase >= min_increase:
            _fire(
                conn, config, "trend_disk",
                f"Disk usage up {increase:.1f}% this week vs last (avg {avg_last:.0f}% → {avg_this:.0f}%)",
                avg_this, cooldown, quiet,
            )

    # Battery health trend: compare first and last readings over available data
    caps_this = [r["battery_max_capacity"] for r in this_week if r["battery_max_capacity"] is not None]
    caps_last = [r["battery_max_capacity"] for r in last_week if r["battery_max_capacity"] is not None]
    if caps_this and caps_last:
        health_now = caps_this[-1]
        health_before = caps_last[0]
        if health_before > health_now:
            drop = health_before - health_now
            if drop >= cfg.get("battery_health_drop_pct", 1):
                _fire(
                    conn, config, "trend_battery_health",
                    f"Battery health dropped {drop}% over 2 weeks ({health_before}% → {health_now}%)",
                    health_now, cooldown, quiet,
                )


def _run_auto_actions(config: dict, snapshot: dict):
    """Execute auto-actions based on config rules."""
    actions = config.get("auto_actions", [])
    for action in actions:
        if not action.get("enabled", True):
            continue

        condition = action.get("condition", {})
        metric = condition.get("metric")
        op = condition.get("op", ">")
        threshold = condition.get("value")

        if not metric or threshold is None:
            continue

        current = snapshot.get(metric)
        if current is None:
            continue

        triggered = False
        if op == ">" and current > threshold:
            triggered = True
        elif op == "<" and current < threshold:
            triggered = True
        elif op == ">=" and current >= threshold:
            triggered = True

        if triggered:
            cmd = action.get("action")
            if cmd == "kill" and action.get("process"):
                _kill_process(action["process"])
            elif cmd == "notify":
                send_notification("Macmonica Auto", action.get("message", f"{metric} triggered"))


def _kill_process(name: str):
    """Kill processes matching name. Only kills user processes, never system ones."""
    import psutil
    protected = {"kernel_task", "WindowServer", "launchd", "loginwindow", "Finder", "Dock", "SystemUIServer"}
    if name in protected:
        logger.warning("Refusing to kill protected process: %s", name)
        return

    for p in psutil.process_iter(["name", "pid"]):
        try:
            if p.info["name"] == name:
                os.kill(p.info["pid"], signal.SIGTERM)
                logger.info("Auto-killed process %s (pid %d)", name, p.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
            continue


def _fire(conn, config, alert_type, message, value, cooldown, quiet=False):
    last = get_last_alert_of_type(conn, alert_type)
    if last and time.time() - last["ts"] < cooldown:
        return

    insert_alert(conn, alert_type, message, value)

    if not quiet:
        send_notification("Macmonica", message)

    # Webhook
    webhook_url = config.get("webhook_url")
    if webhook_url:
        send_webhook(webhook_url, {
            "alert_type": alert_type,
            "message": message,
            "value": value,
            "ts": time.time(),
        })
