"""Alert system — thresholds, anomaly detection, auto-actions, webhooks, quiet hours."""

import logging
import os
import signal
import time
from datetime import datetime

from .db import get_recent_snapshots, get_last_alert_of_type, insert_alert, get_snapshots
from .macos import send_notification, send_webhook

logger = logging.getLogger("sysmon.alerts")


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
    cooldown = config.get("alert_cooldown_minutes", 15) * 60
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

    # Anomaly detection
    anomaly_cfg = alerts_cfg.get("anomaly", {})
    if anomaly_cfg.get("enabled", True):
        _check_anomalies(conn, config, snapshot, anomaly_cfg, cooldown, quiet)

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
    threshold = cfg.get("deviation_percent", 50)
    week_ago = time.time() - 7 * 86400
    baseline = get_snapshots(conn, week_ago)

    if len(baseline) < 60:
        return

    for key, label in [("cpu_avg", "CPU"), ("mem_percent", "Memory")]:
        vals = [r[key] for r in baseline if r[key] is not None]
        if not vals:
            continue

        avg = sum(vals) / len(vals)
        current = snapshot.get(key)
        if current is None or avg < 10:
            continue

        deviation = ((current - avg) / avg) * 100
        if deviation > threshold:
            _fire(
                conn, config, f"anomaly_{key}",
                f"{label} at {current:.0f}% — {deviation:.0f}% above your 7-day avg ({avg:.0f}%)",
                current, cooldown, quiet,
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
                send_notification("Sysmon Auto", action.get("message", f"{metric} triggered"))


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
        send_notification("Sysmon", message)

    # Webhook
    webhook_url = config.get("webhook_url")
    if webhook_url:
        send_webhook(webhook_url, {
            "alert_type": alert_type,
            "message": message,
            "value": value,
            "ts": time.time(),
        })
