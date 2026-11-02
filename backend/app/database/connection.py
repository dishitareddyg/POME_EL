"""SQLite persistence helpers (stdlib sqlite3, no ORM)."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "simulator.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    config_json TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  REAL    NOT NULL,
    result_json TEXT    DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    role        TEXT    NOT NULL,
    origin      TEXT    NOT NULL DEFAULT 'initial',
    agent_json  TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);
"""

# Migrations for existing databases
_MIGRATIONS = [
    "ALTER TABLE experiments ADD COLUMN result_json TEXT DEFAULT NULL",
]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
        # Apply migrations idempotently (ignore errors if column already exists)
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
    finally:
        conn.close()


def check_db() -> bool:
    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False
