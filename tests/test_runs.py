# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from pulse.errors import PulseError
from pulse.runs import finish
from pulse.schedule import claim
from support import PROJECT, add_check, local

if TYPE_CHECKING:
    import sqlite3

    from pulse.config import Config


def test_reminder_deactivates_when_found(conn: sqlite3.Connection, config: Config) -> None:
    reminder = add_check(conn, kind="reminder")
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    result = finish(conn, config, run_id=run.run_id, outcome="found", summary="it happened", at=at)

    assert result["reminder_deactivated"] is True
    assert conn.execute("SELECT active FROM checks WHERE check_id = ?", (reminder,)).fetchone()[0] == 0
    assert claim(conn, PROJECT, local(2026, 10, 23, 9)) == []


def test_reminder_stays_active_when_ok(conn: sqlite3.Connection, config: Config) -> None:
    add_check(conn, kind="reminder")
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    assert (
        finish(conn, config, run_id=run.run_id, outcome="ok", summary="not yet", at=at)["reminder_deactivated"] is False
    )


def test_finishing_twice_is_refused(conn: sqlite3.Connection, config: Config) -> None:
    add_check(conn)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)
    finish(conn, config, run_id=run.run_id, outcome="ok", summary="fine", at=at)

    with pytest.raises(PulseError, match="already finished"):
        finish(conn, config, run_id=run.run_id, outcome="ok", summary="again", at=at)


def test_late_result_replaces_the_abandoned_marker(conn: sqlite3.Connection, config: Config) -> None:
    add_check(conn)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)
    claim(conn, PROJECT, at + timedelta(minutes=31))

    finish(conn, config, run_id=run.run_id, outcome="ok", summary="late but fine", at=at + timedelta(minutes=40))

    row = conn.execute("SELECT outcome FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
    assert row["outcome"] == "ok"


@pytest.mark.parametrize(("outcome", "summary", "message"), [("maybe", "x", "outcome must be"), ("ok", "", "empty")])
def test_invalid_outcome_or_empty_summary_is_refused(
    conn: sqlite3.Connection, config: Config, outcome: str, summary: str, message: str
) -> None:
    add_check(conn)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    with pytest.raises(PulseError, match=message):
        finish(conn, config, run_id=run.run_id, outcome=outcome, summary=summary, at=at)


def test_disabled_notifications_are_not_sent(conn: sqlite3.Connection, config: Config) -> None:
    add_check(conn)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    assert finish(conn, config, run_id=run.run_id, outcome="found", summary="something", at=at)["notified"] is False
