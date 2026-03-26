"""macOS-specific system metrics via subprocess calls."""

import re
import subprocess
import time

_battery_cache = {"data": None, "ts": 0}
_thermal_cache = {"data": None, "ts": 0}
_wifi_cache = {"data": None, "ts": 0}
CACHE_TTL = 300  # 5 minutes
WIFI_CACHE_TTL = 60  # 1 minute (signal changes faster)


def _run(cmd: list[str], timeout: int = 10) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def get_battery_health() -> dict | None:
    now = time.time()
    if _battery_cache["data"] and now - _battery_cache["ts"] < CACHE_TTL:
        return _battery_cache["data"]

    out = _run(["system_profiler", "SPPowerDataType"])
    if not out:
        return None

    result = {}
    m = re.search(r"Cycle Count:\s*(\d+)", out)
    if m:
        result["cycle_count"] = int(m.group(1))
    m = re.search(r"Maximum Capacity:\s*(\d+)%", out)
    if m:
        result["max_capacity"] = int(m.group(1))
    m = re.search(r"Condition:\s*(\w+)", out)
    if m:
        result["condition"] = m.group(1)

    # Battery temperature from ioreg (centidegrees Celsius)
    temp_out = _run(["ioreg", "-rc", "AppleSmartBattery"])
    if temp_out:
        tm = re.search(r'"Temperature"\s*=\s*(\d+)', temp_out)
        if tm:
            result["temperature"] = int(tm.group(1)) / 100  # Convert to °C

    if result:
        _battery_cache["data"] = result
        _battery_cache["ts"] = now
        return result
    return None


def get_process_power() -> dict[int, float]:
    out = _run(["top", "-l", "1", "-n", "20", "-stats", "pid,command,cpu,power"])
    if not out:
        return {}

    result = {}
    in_table = False
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("PID"):
            in_table = True
            continue
        if not in_table or not line:
            continue
        parts = line.split()
        if len(parts) >= 4:
            try:
                pid = int(parts[0])
                power = float(parts[-1])
                result[pid] = power
            except (ValueError, IndexError):
                continue
    return result


def get_thermal_status() -> str | None:
    now = time.time()
    if _thermal_cache["ts"] and now - _thermal_cache["ts"] < CACHE_TTL:
        return _thermal_cache["data"]

    out = _run(["pmset", "-g", "therm"])
    result = None
    if out:
        for line in out.splitlines():
            line = line.strip().lower()
            if "warning" in line and ("cpu" in line or "thermal" in line):
                result = line
                break

    _thermal_cache["data"] = result
    _thermal_cache["ts"] = now
    return result


def get_wifi_info() -> dict | None:
    """Get WiFi signal strength, noise, and connection info. Cached 1min."""
    now = time.time()
    if _wifi_cache["data"] and now - _wifi_cache["ts"] < WIFI_CACHE_TTL:
        return _wifi_cache["data"]

    out = _run(["system_profiler", "SPAirPortDataType"])
    if not out:
        return None

    result = {}
    # Find the first (current) Signal/Noise line
    m = re.search(r"Signal / Noise:\s*(-?\d+)\s*dBm\s*/\s*(-?\d+)\s*dBm", out)
    if m:
        result["rssi"] = int(m.group(1))
        result["noise"] = int(m.group(2))

    m = re.search(r"Transmit Rate:\s*(\d+)", out)
    if m:
        result["tx_rate"] = int(m.group(1))

    # Get channel from first occurrence after "Current Network"
    m = re.search(r"Channel:\s*(\d+)\s*\(([^)]+)\)", out)
    if m:
        result["channel"] = int(m.group(1))
        result["band"] = m.group(2)

    if result:
        _wifi_cache["data"] = result
        _wifi_cache["ts"] = now
        return result
    return None


def get_usb_devices() -> list[dict]:
    """Get connected USB devices with power draw."""
    out = _run(["system_profiler", "SPUSBDataType"])
    if not out:
        return []

    devices = []
    current_device = {}
    for line in out.splitlines():
        line = line.strip()
        if line.endswith(":") and not line.startswith(("USB", "Available", "Required")):
            if current_device.get("name"):
                devices.append(current_device)
            current_device = {"name": line.rstrip(":")}
        elif "Current Available" in line:
            m = re.search(r"(\d+)\s*mA", line)
            if m:
                current_device["available_ma"] = int(m.group(1))
        elif "Current Required" in line or "Extra Operating Current" in line:
            m = re.search(r"(\d+)\s*mA", line)
            if m:
                current_device["required_ma"] = current_device.get("required_ma", 0) + int(m.group(1))
        elif "Product ID" in line:
            current_device["product_id"] = line.split(":")[-1].strip()

    if current_device.get("name"):
        devices.append(current_device)

    # Filter to actual devices (have a product ID and power draw)
    return [d for d in devices if d.get("product_id") and d.get("required_ma", 0) > 0]


def get_wake_sleep_events(hours: int = 24) -> list[dict]:
    """Parse recent wake/sleep events from pmset log."""
    out = _run(["pmset", "-g", "log"], timeout=15)
    if not out:
        return []

    cutoff = time.time() - hours * 3600
    events = []

    for line in out.splitlines():
        if not re.match(r"^\d{4}-\d{2}-\d{2}", line):
            continue

        # Parse: "2026-03-26 08:04:32 +0530 Sleep  Entering Sleep state due to 'Maintenance Sleep'..."
        m = re.match(
            r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+[+-]\d{4}\s+(Sleep|DarkWake|Wake)\s+(.+)",
            line,
        )
        if not m:
            continue

        ts_str, event_type, detail = m.groups()
        try:
            from datetime import datetime
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue

        if ts < cutoff:
            continue

        # Extract reason
        reason = ""
        rm = re.search(r"due to '([^']+)'", detail)
        if rm:
            reason = rm.group(1)
        elif "DarkWake" in event_type:
            rm = re.search(r"due to (.+?)\s", detail)
            if rm:
                reason = rm.group(1)

        events.append({
            "ts": ts,
            "type": event_type,
            "reason": reason,
            "detail": detail[:100],
        })

    return events


def send_notification(title: str, message: str):
    title = title.replace("\\", "\\\\").replace('"', '\\"')
    message = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{message}" with title "{title}"'
    _run(["osascript", "-e", script])


def send_webhook(url: str, payload: dict):
    """POST JSON to a webhook URL (Slack, Discord, ntfy.sh, etc.)."""
    import json
    data = json.dumps(payload).encode()
    try:
        import urllib.request
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Best-effort, don't break collector
