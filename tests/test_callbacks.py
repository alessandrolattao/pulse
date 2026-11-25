# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from pulse import callbacks
from pulse.callbacks import run_callback
from pulse.errors import PulseError
from pulse.runs import finish
from pulse.schedule import claim
from pulse.sessions import launch, start
from support import PROJECT, add_check, local

if TYPE_CHECKING:
    import sqlite3

    from pulse.config import Config

ENVIRONMENT = 'printf "%s|%s|%s|%s|%s|%s\\n" "$PULSE_CHECK" "$PULSE_CHECK_ID" "$PULSE_OUTCOME" "$PULSE_SUMMARY" '
ENVIRONMENT += '"$PULSE_ENDED" "$(pwd)"'


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, int]]:
    """Stand in for the detached start of `pulse callback`: record the db and the run instead."""
    started: list[tuple[Path, int]] = []

    def record(_: sqlite3.Connection, config: Config, run_id: int) -> None:
        started.append((config.db_path, run_id))

    monkeypatch.setattr("pulse.runs.spawn", record)
    return started


def finished_run(  # noqa: PLR0913 - a builder: the check and the outcome of its run
    conn: sqlite3.Connection,
    config: Config,
    *,
    callback: str,
    scope: str = PROJECT,
    outcome: str = "found",
    summary: str = "v1.2 is out",
    until: str = "",
    ended: bool = False,
) -> int:
    """Add a check with this callback, run it and record the outcome; return the run's id."""
    add_check(conn, name="release", scope=scope, callback=callback, until=until)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, scope, at)
    finish(conn, config, run_id=run.run_id, outcome=outcome, summary=summary, at=at, ended=ended)
    return run.run_id


def recorded(conn: sqlite3.Connection, run_id: int) -> tuple[int | None, str]:
    row = conn.execute("SELECT callback_status, callback_output FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return row["callback_status"], row["callback_output"]


def test_finish_starts_the_callback_of_a_check_that_has_one(
    conn: sqlite3.Connection, config: Config, spawned: list[tuple[Path, int]]
) -> None:
    run_id = finished_run(conn, config, callback="notify-inbox")

    assert spawned == [(config.db_path, run_id)]


def test_finish_starts_nothing_for_a_check_without_a_callback(
    conn: sqlite3.Connection, config: Config, spawned: list[tuple[Path, int]]
) -> None:
    run_id = finished_run(conn, config, callback="")

    assert spawned == []
    assert recorded(conn, run_id) == (None, "")


def test_the_abandoned_marker_starts_no_callback_and_the_late_result_does(
    conn: sqlite3.Connection, config: Config, spawned: list[tuple[Path, int]]
) -> None:
    add_check(conn, callback="notify-inbox")
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    claim(conn, PROJECT, at + timedelta(minutes=31))
    abandoned = list(spawned)
    finish(conn, config, run_id=run.run_id, outcome="ok", summary="late but fine", at=at + timedelta(minutes=40))

    assert abandoned == []
    assert spawned == [(config.db_path, run.run_id)]


def test_a_note_and_a_failed_launch_start_the_callback(
    conn: sqlite3.Connection, config: Config, spawned: list[tuple[Path, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    note = add_check(conn, kind="note", text="Call Marco", callback="notify-inbox")
    check = add_check(conn, callback="notify-inbox")
    at = local(2026, 10, 22, 9)
    runs = {run.check_id: run for run in claim(conn, PROJECT, at)}

    start(conn, config, runs[note], "/", at)
    launch(conn, config, runs[check], "/", at)

    assert spawned == [(config.db_path, runs[note].run_id), (config.db_path, runs[check].run_id)]


@pytest.mark.usefixtures("spawned")
def test_the_callback_gets_the_outcome_and_runs_in_the_project(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    run_id = finished_run(conn, config, callback=ENVIRONMENT, scope=str(tmp_path), summary="`v1.2` is out")

    result = run_callback(conn, run_id)

    output = f"release|1|found|`v1.2` is out||{tmp_path}"
    assert result == {"run_id": run_id, "callback_status": 0, "callback_output": output}
    assert recorded(conn, run_id) == (0, output)


@pytest.mark.usefixtures("spawned")
def test_the_callback_of_a_run_that_ended_its_check_gets_pulse_ended(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    run_id = finished_run(
        conn, config, callback=ENVIRONMENT, scope=str(tmp_path), outcome="ok", until="it is out", ended=True
    )

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (0, f"release|1|ok|v1.2 is out|1|{tmp_path}")


@pytest.mark.usefixtures("spawned")
def test_the_callback_gets_auth_as_recorded(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    run_id = finished_run(
        conn, config, callback=ENVIRONMENT, scope=str(tmp_path), outcome="auth", summary="run gh auth login"
    )

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (0, f"release|1|auth|run gh auth login||{tmp_path}")


@pytest.mark.usefixtures("spawned")
def test_a_check_without_a_name_is_called_by_its_id(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    add_check(conn, name="", scope=str(tmp_path), callback='printf "%s" "$PULSE_CHECK"')
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, str(tmp_path), at)
    finish(conn, config, run_id=run.run_id, outcome="ok", summary="fine", at=at)

    run_callback(conn, run.run_id)

    assert recorded(conn, run.run_id) == (0, "check 1")


@pytest.mark.usefixtures("spawned")
def test_the_callback_of_a_system_check_runs_in_the_home_directory(
    conn: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    run_id = finished_run(conn, config, callback="pwd", scope="system")

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (0, str(home))


@pytest.mark.usefixtures("spawned")
def test_the_callback_keeps_the_users_environment(
    conn: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INBOX_TOKEN", "s3cret")
    run_id = finished_run(conn, config, callback='printf "%s" "$INBOX_TOKEN"', scope=str(tmp_path))

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (0, "s3cret")


@pytest.mark.usefixtures("spawned")
def test_a_failing_callback_changes_nothing_of_the_run_or_the_check(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    run_id = finished_run(conn, config, callback="echo inbox down >&2; exit 3", scope=str(tmp_path), outcome="ok")

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (3, "inbox down")
    row = conn.execute("SELECT outcome, summary FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert tuple(row) == ("ok", "v1.2 is out")
    assert conn.execute("SELECT active FROM checks").fetchone()[0] == 1
    assert len(claim(conn, str(tmp_path), local(2026, 10, 23, 9))) == 1


@pytest.mark.usefixtures("spawned")
def test_a_command_that_does_not_exist_is_recorded_as_the_shell_reports_it(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    run_id = finished_run(conn, config, callback="pulse-no-such-command", scope=str(tmp_path))

    run_callback(conn, run_id)

    status, output = recorded(conn, run_id)
    assert status == 127
    assert "pulse-no-such-command" in output


@pytest.mark.usefixtures("spawned")
def test_a_command_killed_by_a_signal_is_recorded_as_the_shell_reports_it(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    run_id = finished_run(conn, config, callback="echo dying; kill -KILL $$", scope=str(tmp_path))

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (137, "dying")


@pytest.mark.usefixtures("spawned")
def test_a_callback_whose_project_directory_is_gone_does_not_run(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    gone = tmp_path / "moved-away"
    run_id = finished_run(conn, config, callback=f"touch {tmp_path}/ran", scope=str(gone))

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (127, f"callback not started: working directory {gone} does not exist")
    assert not (tmp_path / "ran").exists()


@pytest.mark.usefixtures("spawned")
def test_a_shell_that_cannot_start_is_recorded(
    conn: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(callbacks, "SHELL", str(tmp_path / "no-shell"))
    run_id = finished_run(conn, config, callback="echo sent", scope=str(tmp_path))

    run_callback(conn, run_id)

    status, output = recorded(conn, run_id)
    assert status == 127
    assert output.startswith("callback not started: ")
    assert str(tmp_path / "no-shell") in output


def test_a_detached_start_that_fails_leaves_the_outcome_and_is_recorded(
    conn: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(callbacks, "BIN", tmp_path / "no-pulse")

    run_id = finished_run(conn, config, callback="echo sent", scope=str(tmp_path))

    row = conn.execute("SELECT outcome, summary FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    assert tuple(row) == ("found", "v1.2 is out")
    status, output = recorded(conn, run_id)
    assert status == 127
    assert output.startswith("callback not started: ")
    assert str(tmp_path / "no-pulse") in output


@pytest.mark.usefixtures("spawned")
def test_only_the_first_lines_of_the_output_are_kept(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    run_id = finished_run(conn, config, callback="seq 1 50; echo done >&2", scope=str(tmp_path))

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (0, "\n".join(str(number) for number in range(1, 21)))


@pytest.mark.usefixtures("spawned")
def test_the_output_kept_is_at_most_2000_characters(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    run_id = finished_run(conn, config, callback="yes é | head -n 3000 | tr -d '\\n'", scope=str(tmp_path))

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (0, "é" * 2000)


def alive(pid: int) -> bool:
    """Whether a process still runs: gone and zombie both count as dead."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.mark.usefixtures("spawned")
def test_a_callback_that_runs_too_long_is_killed_with_its_children(
    conn: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(callbacks, "TIMEOUT", timedelta(seconds=1))
    run_id = finished_run(
        conn, config, callback="echo waiting; sleep 30 & echo $! > child.pid; wait", scope=str(tmp_path)
    )

    run_callback(conn, run_id)

    assert recorded(conn, run_id) == (124, "waiting")
    child = int((tmp_path / "child.pid").read_text())
    deadline = time.monotonic() + 5
    while alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(child)


@pytest.mark.usefixtures("spawned")
def test_the_callback_runs_at_most_once_per_run(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    run_id = finished_run(conn, config, callback="echo sent >> sent.log", scope=str(tmp_path))
    run_callback(conn, run_id)

    with pytest.raises(PulseError, match="already ran"):
        run_callback(conn, run_id)

    assert (tmp_path / "sent.log").read_text() == "sent\n"


def test_a_run_without_an_outcome_runs_no_callback(conn: sqlite3.Connection, tmp_path: Path) -> None:
    add_check(conn, scope=str(tmp_path), callback="echo sent >> sent.log")
    at = local(2026, 10, 22, 9)
    [running] = claim(conn, str(tmp_path), at)

    with pytest.raises(PulseError, match="not finished"):
        run_callback(conn, running.run_id)
    claim(conn, str(tmp_path), at + timedelta(minutes=31))
    with pytest.raises(PulseError, match="not finished"):
        run_callback(conn, running.run_id)

    assert not (tmp_path / "sent.log").exists()
    assert recorded(conn, running.run_id) == (None, "")


@pytest.mark.usefixtures("spawned")
def test_a_check_whose_callback_was_removed_runs_nothing(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    run_id = finished_run(conn, config, callback="echo sent >> sent.log", scope=str(tmp_path))
    conn.execute("UPDATE checks SET callback = ''")

    with pytest.raises(PulseError, match="no callback"):
        run_callback(conn, run_id)

    assert not (tmp_path / "sent.log").exists()


def test_an_unknown_run_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(PulseError, match="does not exist"):
        run_callback(conn, 42)
