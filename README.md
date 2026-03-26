# macmonica

A lightweight macOS system health monitor that tracks metrics over time, sends smart alerts, and tells you exactly what's draining your resources.

**What makes it different from htop/btop/glances:** Those are live-only dashboards. macmonica tracks history, predicts battery degradation, groups processes by app, sends macOS notifications, and tells you *why* your Mac is slow — not just *that* it's slow.

## Install

```bash
pip install macmonica
```

After install, the CLI command is `macmonica`:

```bash
macmonica doctor
```

## Quick Start

```bash
# Set up background collector + daily digest (one command)
macmonica install

# See what's happening right now
macmonica why

# Live dashboard
macmonica

# What drained my battery?
macmonica blame

# To remove
macmonica uninstall
```

## Commands

### Real-time

| Command | Description |
|---------|-------------|
| `macmonica` | Live dashboard with process grouping, energy impact, battery health |
| `macmonica doctor` | Full system health check with pass/fail/warn verdicts |
| `macmonica why` | Plain English explanation of what's wrong |
| `macmonica top [--sort cpu\|mem\|energy]` | Grouped process list (like top, but useful) |
| `macmonica recommend` | Actionable recommendations based on current state |

### Battery & Power

| Command | Description |
|---------|-------------|
| `macmonica blame [--hours N]` | Correlates battery drain with top CPU consumers |
| `macmonica wake-log` | Shows wake/sleep events — catches phantom drain |
| `macmonica usb` | Connected USB devices and their power draw |

### Historical

| Command | Description |
|---------|-------------|
| `macmonica history [--period 24h\|7d\|30d]` | Sparkline trends with color-coded alert zones |
| `macmonica compare 24h 7d` | Side-by-side period comparison |
| `macmonica report [--period week\|month]` | Full health report with daily breakdown |
| `macmonica digest [--today] [--notify]` | Daily summary with optional macOS notification |
| `macmonica alerts` | Alert history log |

### Data & Config

| Command | Description |
|---------|-------------|
| `macmonica export [--period 24h\|7d\|30d\|all] [-o file.csv]` | Export to CSV |
| `macmonica status` | Collector status, DB size, last snapshot time |
| `macmonica config [--init]` | View or create configuration |
| `macmonica collect` | Run collector daemon (for launchd) |
| `macmonica collect-once` | Single snapshot (for testing) |

## Features

### Smart Alerts (macOS notifications)
- CPU sustained above threshold
- Memory/disk usage critical
- Battery health degraded
- WiFi signal weak
- Disk I/O rate abnormal
- **Anomaly detection** — alerts when metrics deviate from your 7-day baseline
- **Quiet hours** — no notifications between 11pm-7am (configurable)

### Battery Intelligence
- Tracks cycle count, max capacity, and condition over time
- Predicts when battery will hit 75% health
- `blame` command tells you exactly what drained your battery
- Wake/sleep log catches phantom drain from DarkWake events

### Process Grouping
Chrome's 30 helper processes show as one "Chrome" row with aggregated CPU/memory. Same for Safari, VS Code, Slack, Firefox, and any Electron app.

### Webhooks
Send alerts to Slack, Discord, or ntfy.sh:
```json
{
  "webhook_url": "https://ntfy.sh/your-topic"
}
```

### Auto-Actions
Configure automatic responses to conditions:
```json
{
  "auto_actions": [
    {
      "condition": {"metric": "cpu_avg", "op": ">", "value": 95},
      "action": "notify",
      "message": "CPU critical"
    }
  ]
}
```

## Configuration

Config lives at `~/.macmonica/config.json`. Create defaults with:

```bash
macmonica config --init
```

Key settings:
- `collect_interval`: Seconds between snapshots (default: 60, min: 10)
- `retention_days`: How long to keep data (default: 30)
- `quiet_hours`: `{"enabled": true, "start": 23, "end": 7}`
- `webhook_url`: URL for alert webhooks
- `auto_actions`: List of automated response rules
- `alerts`: Per-alert-type thresholds and enable/disable

## Resource Usage

The background collector is designed to be invisible:
- **CPU**: ~0.26s per collection cycle, then sleeps 60s
- **Memory**: ~28MB (Python + psutil baseline)
- **Disk**: ~1MB/month of SQLite data
- **No subprocess calls** in the hot path (battery/thermal cached 5min, WiFi cached 1min)

## Data Storage

All data is stored locally in `~/.macmonica/macmonica.db` (SQLite). Nothing is sent anywhere unless you configure a webhook.

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.10+

## License

MIT
