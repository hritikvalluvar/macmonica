"""Configuration loader with sensible defaults."""

import json
from pathlib import Path

SYSMON_DIR = Path.home() / ".sysmon"
DB_PATH = SYSMON_DIR / "sysmon.db"
CONFIG_PATH = SYSMON_DIR / "config.json"

DEFAULTS = {
    "collect_interval": 60,
    "retention_days": 30,
    "alerts": {
        "cpu_sustained": {"threshold": 90, "duration_minutes": 5, "enabled": True},
        "memory_high": {"threshold": 90, "enabled": True},
        "disk_high": {"threshold": 90, "enabled": True},
        "battery_health": {"threshold": 80, "enabled": True},
        "anomaly": {"enabled": True, "deviation_percent": 50},
        "disk_io_high": {"enabled": True, "write_mbps": 100},
        "wifi_weak": {"enabled": True, "threshold": -75},
    },
    "quiet_hours": {"enabled": True, "start": 23, "end": 7},
    "alert_cooldown_minutes": 15,
    "webhook_url": None,
    "auto_actions": [],
    "process_grouping": {
        "Google Chrome": ["Google Chrome Helper", "Google Chrome Helper (GPU)", "Google Chrome Helper (Renderer)"],
        "Safari": ["Safari Web Content", "Safari Networking", "Safari Web Content (Prewarmed)"],
        "Firefox": ["firefox", "plugin-container"],
        "VS Code": ["Code Helper", "Code Helper (GPU)", "Code Helper (Renderer)", "Code Helper (Plugin)"],
        "Slack": ["Slack Helper", "Slack Helper (GPU)", "Slack Helper (Renderer)"],
    },
}


def ensure_dir():
    SYSMON_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    config = DEFAULTS.copy()
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                user = json.load(f)
            _deep_merge(config, user)
        except (json.JSONDecodeError, OSError):
            import logging
            logging.getLogger("sysmon").warning("Failed to parse config at %s, using defaults", CONFIG_PATH)
    _validate(config)
    return config


def _validate(config: dict):
    config["collect_interval"] = max(10, min(3600, int(config.get("collect_interval", 60))))
    config["retention_days"] = max(1, min(365, int(config.get("retention_days", 30))))
    config["alert_cooldown_minutes"] = max(1, min(1440, int(config.get("alert_cooldown_minutes", 15))))


def save_default_config():
    ensure_dir()
    with open(CONFIG_PATH, "w") as f:
        json.dump(DEFAULTS, f, indent=2)


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
