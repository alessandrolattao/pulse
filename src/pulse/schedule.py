# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""What is due, and taking it: the only place that decides when a check runs."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pulse.clock import parse, stamp
from pulse.cron import Cron
from pulse.model import ABANDONED, STALE_AFTER, SYSTEM, Cadence, ClaimedRun, Kind, Outcome, format_interval

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime


def describe(check: sqlite3.Row) -> str:
    """Write a check's schedule the way the user would say it."""
    cadence = Cadence(check["cadence"])
    if cadence is Cadence.EVERY:
        return f"every {format_interval(check['interval_seconds'])}"
    if cadence is Cadence.CRON:
        return f"cron {check['cron']}"
    if cadence is Cadence.ONCE:
        return f"once at {parse(check['due_at']).astimezone():%Y-%m-%d %H:%M}"
    return "daily"


def in_scope(conn: sqlite3.Connection, project: str) -> list[sqlite3.Row]:
    """Return the active checks of this project plus the system ones."""
    return conn.execute(
        "SELECT * FROM checks WHERE active = 1 AND scope IN (?, ?) ORDER BY check_id",
        (SYSTEM, project),
    ).fetchall()


def abandon_stale(conn: sqlite3.Connection, at: datetime) -> None:
    """Close the runs whose session never reported, as errors the user will see."""
    conn.execute(
        "UPDATE runs SET outcome = ?, summary = ?, finished_at = ?, seen = 0 WHERE outcome = ? AND started_at < ?",
        (Outcome.ERROR, ABANDONED, stamp(at), Outcome.RUNNING, stamp(at - STALE_AFTER)),
    )


def in_flight(conn: sqlite3.Connection, check_id: int) -> bool:
    """Whether a run of the check is still working."""
    row = conn.execute(
        "SELECT 1 FROM runs WHERE check_id = ? AND outcome = ? LIMIT 1", (check_id, Outcome.RUNNING)
    ).fetchone()
    return row is not None


def last_finished(conn: sqlite3.Connection, check_id: int) -> sqlite3.Row | None:
    """Return the most recent run of a check that is not running any more."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM runs WHERE check_id = ? AND outcome != ? ORDER BY started_at DESC, run_id DESC LIMIT 1",
        (check_id, Outcome.RUNNING),
    ).fetchone()
    return row


def period_start(check: sqlite3.Row, at: datetime) -> datetime | None:
    """Return when the current period of a check began, or None when it has no current period.

    A run that finished after this moment, however it ended, used up the period:
    a failed one comes back only through a retry, so that a check whose login
    expired does not relaunch on every hook event.
    """
    cadence = Cadence(check["cadence"])
    if cadence is Cadence.DAILY:
        return at.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    if cadence is Cadence.EVERY:
        return at - timedelta(seconds=check["interval_seconds"])
    if cadence is Cadence.CRON:
        fired = Cron.parse(check["cron"]).previous(at)
        # An occurrence from before the check existed is not owed to it.
        return fired if fired is not None and fired >= parse(check["created_at"]) else None
    due = parse(check["due_at"])
    return parse(check["created_at"]) if due <= at else None


def is_due(conn: sqlite3.Connection, check: sqlite3.Row, at: datetime) -> bool:
    """Whether a check should run now, on its own schedule."""
    if in_flight(conn, check["check_id"]):
        return False
    start = period_start(check, at)
    if start is None:
        return False
    last = last_finished(conn, check["check_id"])
    return last is None or parse(last["started_at"]) < start


def is_retryable(conn: sqlite3.Connection, check: sqlite3.Row) -> bool:
    """Whether a retry should run the check now: its last run failed and none is working."""
    if in_flight(conn, check["check_id"]):
        return False
    last = last_finished(conn, check["check_id"])
    return last is not None and Outcome(last["outcome"]).failed


def insert_run(conn: sqlite3.Connection, check_id: int, at: datetime) -> int:
    """Open a `running` run for a check and return its id."""
    cursor = conn.execute(
        "INSERT INTO runs (check_id, started_at, outcome) VALUES (?, ?, ?)",
        (check_id, stamp(at), Outcome.RUNNING),
    )
    if cursor.lastrowid is None:  # pragma: no cover - sqlite always sets it after an INSERT
        msg = "sqlite returned no run id"
        raise RuntimeError(msg)
    return cursor.lastrowid


def claim(conn: sqlite3.Connection, project: str, at: datetime, *, failed_only: bool = False) -> list[ClaimedRun]:
    """Take every check in scope that is due, or with `failed_only` every one to retry.

    One transaction, so that two sessions opening together never take the
    same check.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        abandon_stale(conn, at)
        claimed: list[ClaimedRun] = []
        for check in in_scope(conn, project):
            wanted = is_retryable(conn, check) if failed_only else is_due(conn, check, at)
            if not wanted:
                continue
            claimed.append(
                ClaimedRun(
                    run_id=insert_run(conn, check["check_id"], at),
                    check_id=check["check_id"],
                    name=check["name"] or f"check {check['check_id']}",
                    kind=Kind(check["kind"]),
                    schedule=describe(check),
                    scope=check["scope"],
                    browser=bool(check["browser"]),
                    reuse_session=bool(check["reuse_session"]),
                    kept_session=check["kept_session"],
                    text=check["text"],
                    method=check["method"],
                    requires=check["requires"],
                )
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return claimed
