# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from pulse.db import connect

if TYPE_CHECKING:
    from pathlib import Path

FIRST_SCHEMA = """
CREATE TABLE checks (
    check_id   INTEGER PRIMARY KEY,
    scope      TEXT NOT NULL,
    text       TEXT NOT NULL,
    cadence    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE runs (
    run_id      INTEGER PRIMARY KEY,
    check_id    INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    outcome     TEXT NOT NULL,
    summary     TEXT
);
INSERT INTO checks (scope, text, cadence, kind, created_at)
    VALUES ('system', 'Is the release out?', 'daily', 'check', '2026-10-01T00:00:00+00:00');
INSERT INTO runs (check_id, started_at, outcome, summary)
    VALUES (1, '2026-10-02T09:00:00+00:00', 'ok', 'not yet');
"""


def test_an_older_db_gets_no_end_and_no_ended_run(tmp_path: Path) -> None:
    path = tmp_path / "pulse.db"
    with sqlite3.connect(path) as old:
        old.executescript(FIRST_SCHEMA)
    old.close()

    conn = connect(path)

    assert conn.execute("SELECT until FROM checks").fetchone()[0] == ""
    assert conn.execute("SELECT ended FROM runs").fetchone()[0] == 0
    conn.close()
