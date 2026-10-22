# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers shared by the tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pulse.clock import stamp

if TYPE_CHECKING:
    import sqlite3

PROJECT = "/repo/project"
CREATED = datetime(2026, 10, 1, tzinfo=UTC)


def add_check(  # noqa: PLR0913 - a builder: every column of a check, all optional
    conn: sqlite3.Connection,
    *,
    cadence: str = "daily",
    interval_seconds: int | None = None,
    due_at: datetime | None = None,
    cron: str | None = None,
    kind: str = "check",
    scope: str = PROJECT,
    name: str = "test check",
    text: str = "check something",
    browser: bool = False,
    reuse_session: bool = False,
    created_at: datetime = CREATED,
) -> int:
    """Insert a check and return its id."""
    cursor = conn.execute(
        "INSERT INTO checks (scope, name, text, cadence, interval_seconds, due_at, cron, kind, browser, reuse_session, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            scope,
            name,
            text,
            cadence,
            interval_seconds,
            None if due_at is None else stamp(due_at),
            cron,
            kind,
            int(browser),
            int(reuse_session),
            stamp(created_at),
        ),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Return a wall-clock time in the machine's zone, as UTC.

    Daily and cron schedules follow local time, so the tests speak in local hours.
    """
    naive = datetime(year, month, day, hour, minute)  # noqa: DTZ001 - local on purpose
    return naive.astimezone().astimezone(UTC)
