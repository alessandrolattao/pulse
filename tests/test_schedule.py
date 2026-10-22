from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pulse.model import ABANDONED, SYSTEM
from pulse.runs import finish
from pulse.schedule import claim
from support import PROJECT, add_check, local

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from pulse.config import Config


def ids(conn: sqlite3.Connection, at: datetime, *, failed_only: bool = False) -> list[int]:
    """Claim in the test project and return the claimed check ids."""
    return [run.check_id for run in claim(conn, PROJECT, at, failed_only=failed_only)]


def run_and_finish(conn: sqlite3.Connection, config: Config, at: datetime, outcome: str = "ok") -> None:
    """Claim the one due check and close its run."""
    [run] = claim(conn, PROJECT, at)
    finish(conn, config, run_id=run.run_id, outcome=outcome, summary="done", at=at)


def test_daily_runs_once_per_local_day(conn: sqlite3.Connection, config: Config) -> None:
    check = add_check(conn, cadence="daily")
    run_and_finish(conn, config, local(2026, 10, 22, 9))

    assert ids(conn, local(2026, 10, 22, 23)) == []
    assert ids(conn, local(2026, 10, 23, 0, 5)) == [check]


def test_every_runs_again_after_its_interval(conn: sqlite3.Connection, config: Config) -> None:
    check = add_check(conn, cadence="every", interval_seconds=2 * 3600)
    start = local(2026, 10, 22, 9)
    run_and_finish(conn, config, start, "found")

    assert ids(conn, start + timedelta(minutes=119)) == []
    assert ids(conn, start + timedelta(minutes=121)) == [check]


def test_cron_runs_at_each_occurrence_after_it_was_added(conn: sqlite3.Connection, config: Config) -> None:
    check = add_check(conn, cadence="cron", cron="0 9 * * *", created_at=local(2026, 10, 22, 10))

    # The 09:00 of the day it was added came before it existed.
    assert ids(conn, local(2026, 10, 22, 12)) == []
    assert ids(conn, local(2026, 10, 23, 8, 59)) == []
    run_and_finish(conn, config, local(2026, 10, 23, 9, 30))
    assert ids(conn, local(2026, 10, 23, 18)) == []
    assert ids(conn, local(2026, 10, 24, 9)) == [check]


def test_once_runs_after_its_time_then_deactivates(conn: sqlite3.Connection, config: Config) -> None:
    check = add_check(conn, cadence="once", due_at=local(2026, 10, 23, 9))

    assert ids(conn, local(2026, 10, 23, 8)) == []
    run_and_finish(conn, config, local(2026, 10, 23, 11))

    assert ids(conn, local(2026, 10, 30, 9)) == []
    assert conn.execute("SELECT active FROM checks WHERE check_id = ?", (check,)).fetchone()[0] == 0


def test_two_sessions_never_take_the_same_check(conn: sqlite3.Connection) -> None:
    add_check(conn)
    at = local(2026, 10, 22, 9)

    assert len(ids(conn, at)) == 1
    assert ids(conn, at + timedelta(seconds=1)) == []


def test_a_failed_run_waits_for_the_next_period_or_a_retry(conn: sqlite3.Connection, config: Config) -> None:
    check = add_check(conn)
    at = local(2026, 10, 22, 9)
    run_and_finish(conn, config, at, "auth")

    assert ids(conn, at + timedelta(minutes=5)) == []
    assert ids(conn, at + timedelta(minutes=6), failed_only=True) == [check]


def test_a_failed_daily_check_runs_again_the_next_day(conn: sqlite3.Connection, config: Config) -> None:
    check = add_check(conn)
    run_and_finish(conn, config, local(2026, 10, 22, 9), "error")

    assert ids(conn, local(2026, 10, 23, 9)) == [check]


def test_a_stale_run_is_abandoned_and_offered_to_retry(conn: sqlite3.Connection) -> None:
    check = add_check(conn)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    assert ids(conn, at + timedelta(minutes=29), failed_only=True) == []
    assert ids(conn, at + timedelta(minutes=31)) == []
    old = conn.execute("SELECT outcome, summary FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
    assert (old["outcome"], old["summary"]) == ("error", ABANDONED)
    assert ids(conn, at + timedelta(minutes=32), failed_only=True) == [check]


def test_retry_skips_checks_that_did_not_fail(conn: sqlite3.Connection, config: Config) -> None:
    add_check(conn, cadence="every", interval_seconds=3600, name="fine")
    add_check(conn, name="never run")
    at = local(2026, 10, 22, 9)
    [fine_run, _] = claim(conn, PROJECT, at)
    finish(conn, config, run_id=fine_run.run_id, outcome="ok", summary="fine", at=at)
    conn.execute("DELETE FROM runs WHERE outcome = 'running'")

    assert ids(conn, at + timedelta(minutes=5), failed_only=True) == []


def test_scope_is_project_plus_system(conn: sqlite3.Connection) -> None:
    mine = add_check(conn)
    system = add_check(conn, scope=SYSTEM)
    add_check(conn, scope="/repo/other")

    assert sorted(ids(conn, local(2026, 10, 22, 9))) == sorted([mine, system])


def test_inactive_checks_are_never_claimed(conn: sqlite3.Connection) -> None:
    check = add_check(conn)
    conn.execute("UPDATE checks SET active = 0 WHERE check_id = ?", (check,))

    assert ids(conn, local(2026, 10, 22, 9)) == []


def test_claimed_runs_carry_schedule_and_browser(conn: sqlite3.Connection) -> None:
    add_check(conn, cadence="every", interval_seconds=1800, browser=True)

    [run] = claim(conn, PROJECT, local(2026, 10, 22, 9))

    assert (run.schedule, run.browser) == ("every 30m", True)
