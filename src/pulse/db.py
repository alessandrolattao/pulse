# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""The SQLite file: schema, and the upgrades of a db created by an older version."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    check_id         INTEGER PRIMARY KEY,
    scope            TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    text             TEXT NOT NULL,
    cadence          TEXT NOT NULL,
    interval_seconds INTEGER,
    due_at           TEXT,
    cron             TEXT,
    kind             TEXT NOT NULL,
    method           TEXT NOT NULL DEFAULT '',
    requires         TEXT NOT NULL DEFAULT '',
    browser          INTEGER NOT NULL DEFAULT 0,
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY,
    check_id        INTEGER NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    outcome         TEXT NOT NULL,
    summary         TEXT,
    session_id      TEXT,
    seen            INTEGER NOT NULL DEFAULT 0,
    session_removed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_by_check ON runs (check_id, started_at);
"""

MIGRATIONS: dict[str, dict[str, str]] = {
    "checks": {
        "name": "TEXT NOT NULL DEFAULT ''",
        "interval_seconds": "INTEGER",
        "due_at": "TEXT",
        "cron": "TEXT",
        "browser": "INTEGER NOT NULL DEFAULT 0",
        "method": "TEXT NOT NULL DEFAULT ''",
        "requires": "TEXT NOT NULL DEFAULT ''",
    },
    "runs": {
        "session_id": "TEXT",
        "seen": "INTEGER NOT NULL DEFAULT 0",
        "session_removed": "INTEGER NOT NULL DEFAULT 0",
    },
}
"""Columns added after the first version, for a db created before them."""

DATA_MIGRATIONS = (
    # The first version knew `hourly`, which is now `every` 1h.
    "UPDATE checks SET cadence = 'every', interval_seconds = 3600 WHERE cadence = 'hourly'",
)


def connect(path: Path) -> sqlite3.Connection:
    """Open the db, creating or upgrading it.

    Autocommit: the writes that must be atomic open their own transaction.
    WAL lets several sessions read while one writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        # Table and column names come from MIGRATIONS above, never from input.
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    for statement in DATA_MIGRATIONS:
        conn.execute(statement)
    return conn
