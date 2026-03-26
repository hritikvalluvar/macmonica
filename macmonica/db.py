"""SQLite storage for system snapshots, processes, and alerts."""

import sqlite3
import time
from pathlib import Path

from .config import DB_PATH, ensure_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cpu_avg REAL,
    cpu_max REAL,
    load_1 REAL,
    load_5 REAL,
    load_15 REAL,
    mem_percent REAL,
    mem_used INTEGER,
    mem_total INTEGER,
    swap_percent REAL,
    disk_percent REAL,
    disk_read_bytes INTEGER,
    disk_write_bytes INTEGER,
    net_sent_bytes INTEGER,
    net_recv_bytes INTEGER,
    battery_percent REAL,
    battery_plugged INTEGER,
    battery_cycle_count INTEGER,
    battery_max_capacity INTEGER,
    battery_condition TEXT,
    thermal_warning TEXT,
    wifi_rssi INTEGER,
    wifi_noise INTEGER,
    wifi_tx_rate INTEGER,
    battery_temp REAL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS top_processes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    pid INTEGER,
    cpu_percent REAL,
    mem_percent REAL,
    energy_impact REAL,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alerts_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL
);

CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts_log(ts);
"""


def get_connection() -> sqlite3.Connection:
    ensure_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


MIGRATIONS = [
    "ALTER TABLE snapshots ADD COLUMN wifi_rssi INTEGER",
    "ALTER TABLE snapshots ADD COLUMN wifi_noise INTEGER",
    "ALTER TABLE snapshots ADD COLUMN wifi_tx_rate INTEGER",
    "ALTER TABLE snapshots ADD COLUMN battery_temp REAL",
]


def init_db(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    # Run migrations for existing DBs
    for sql in MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def insert_snapshot_with_processes(conn: sqlite3.Connection, data: dict, procs: list[dict]) -> int:
    """Atomically insert a snapshot and its top processes in one transaction."""
    cols = [
        "ts", "cpu_avg", "cpu_max", "load_1", "load_5", "load_15",
        "mem_percent", "mem_used", "mem_total", "swap_percent",
        "disk_percent", "disk_read_bytes", "disk_write_bytes",
        "net_sent_bytes", "net_recv_bytes",
        "battery_percent", "battery_plugged", "battery_cycle_count",
        "battery_max_capacity", "battery_condition", "thermal_warning",
        "wifi_rssi", "wifi_noise", "wifi_tx_rate", "battery_temp",
    ]
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    values = [data.get(c) for c in cols]

    cur = conn.execute(
        f"INSERT INTO snapshots ({col_names}) VALUES ({placeholders})", values
    )
    snapshot_id = cur.lastrowid

    for p in procs:
        conn.execute(
            "INSERT INTO top_processes (snapshot_id, name, pid, cpu_percent, mem_percent, energy_impact) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (snapshot_id, p["name"], p["pid"], p["cpu_percent"], p["mem_percent"], p.get("energy_impact")),
        )

    conn.commit()
    return snapshot_id


# Keep backwards-compatible aliases
def insert_snapshot(conn: sqlite3.Connection, data: dict) -> int:
    return insert_snapshot_with_processes(conn, data, [])


def insert_top_processes(conn: sqlite3.Connection, snapshot_id: int, procs: list[dict]):
    for p in procs:
        conn.execute(
            "INSERT INTO top_processes (snapshot_id, name, pid, cpu_percent, mem_percent, energy_impact) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (snapshot_id, p["name"], p["pid"], p["cpu_percent"], p["mem_percent"], p.get("energy_impact")),
        )
    conn.commit()


def insert_alert(conn: sqlite3.Connection, alert_type: str, message: str, value: float = None):
    conn.execute(
        "INSERT INTO alerts_log (ts, alert_type, message, value) VALUES (?, ?, ?, ?)",
        (time.time(), alert_type, message, value),
    )
    conn.commit()


def get_snapshots(conn: sqlite3.Connection, since: float, limit: int = None) -> list[sqlite3.Row]:
    if limit:
        return conn.execute(
            "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts ASC LIMIT ?",
            (since, int(limit)),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts ASC", (since,)
    ).fetchall()


def get_latest_snapshot(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()


def get_recent_snapshots(conn: sqlite3.Connection, minutes: int) -> list[sqlite3.Row]:
    since = time.time() - minutes * 60
    return conn.execute(
        "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts ASC", (since,)
    ).fetchall()


def get_top_processes_for_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM top_processes WHERE snapshot_id = ? ORDER BY cpu_percent DESC", (snapshot_id,)
    ).fetchall()


def get_alerts(conn: sqlite3.Connection, since: float) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM alerts_log WHERE ts >= ? ORDER BY ts DESC", (since,)
    ).fetchall()


def get_last_alert_of_type(conn: sqlite3.Connection, alert_type: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM alerts_log WHERE alert_type = ? ORDER BY ts DESC LIMIT 1",
        (alert_type,),
    ).fetchone()


def cleanup(conn: sqlite3.Connection, retention_days: int = 30, vacuum: bool = False):
    cutoff = time.time() - max(1, retention_days) * 86400
    conn.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM alerts_log WHERE ts < ?", (cutoff,))
    conn.commit()
    if vacuum:
        conn.execute("VACUUM")



def get_db_stats(conn: sqlite3.Connection) -> dict:
    snapshot_count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    alert_count = conn.execute("SELECT COUNT(*) FROM alerts_log").fetchone()[0]
    latest = get_latest_snapshot(conn)
    return {
        "snapshot_count": snapshot_count,
        "alert_count": alert_count,
        "latest_ts": latest["ts"] if latest else None,
        "db_size_mb": DB_PATH.stat().st_size / 1048576 if DB_PATH.exists() else 0,
    }
