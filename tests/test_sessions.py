# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from pulse.config import BIN, CHECK_AGENT, CHECK_AGENT_REF, Config
from pulse.runs import finish
from pulse.schedule import claim
from pulse.sessions import check_prompt, is_check_session, launch, remove_finished, start
from support import PROJECT, add_check, local

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


FULL_ID = "a1b2c3d4-0000-4000-8000-000000000000"
KEPT = json.dumps([{"id": "a1b2c3d4", "sessionId": FULL_ID, "state": "done"}])


def calls(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def argvs(log: Path) -> list[list[str]]:
    found: list[list[str]] = []
    for call in calls(log):
        argv = call["argv"]
        assert isinstance(argv, list)
        found.append([str(part) for part in argv])
    return found


def cleanups(log: Path) -> list[list[str]]:
    return [argv for argv in argvs(log) if argv[0] in {"stop", "rm"}]


def removed_runs(conn: sqlite3.Connection) -> set[int]:
    return {row["run_id"] for row in conn.execute("SELECT run_id FROM runs WHERE session_removed = 1")}


def kept_session(conn: sqlite3.Connection, check_id: int) -> str | None:
    row = conn.execute("SELECT kept_session FROM checks WHERE check_id = ?", (check_id,)).fetchone()
    kept: str | None = row[0]
    return kept


def test_launch_starts_a_named_check_session_and_keeps_its_id(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    add_check(conn, name="PR 652 released", scope=str(tmp_path))
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, str(tmp_path), at)

    launch(conn, config, run, "/elsewhere", at)

    [call] = calls(tmp_path / "claude.log")
    argv = call["argv"]
    assert isinstance(argv, list)
    assert argv[:8] == [
        "--bg",
        "-n",
        "Pulse · PR 652 released",
        "--agent",
        CHECK_AGENT_REF,
        "--model",
        "sonnet",
        "--no-chrome",
    ]
    assert call["cwd"] == str(tmp_path)
    session = conn.execute("SELECT session_id FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()[0]
    assert session == "a1b2c3d4"


def test_a_browser_check_gets_chrome(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    add_check(conn, browser=True)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    launch(conn, config, run, str(tmp_path), at)

    [call] = calls(tmp_path / "claude.log")
    assert isinstance(call["argv"], list)
    assert "--chrome" in call["argv"]


def test_a_note_is_told_without_a_session(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    add_check(conn, kind="note", text="Call Marco about the contract")
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    start(conn, config, run, str(tmp_path), at)

    row = conn.execute("SELECT outcome, summary FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
    assert (row["outcome"], row["summary"]) == ("found", "Call Marco about the contract")
    assert calls(tmp_path / "claude.log") == []


def test_prompt_carries_the_db_and_the_finish_command(conn: sqlite3.Connection, config: Config) -> None:
    conn.execute("DELETE FROM checks")
    check = add_check(conn, text="Is the release out?")
    conn.execute(
        "UPDATE checks SET method = ?, requires = ? WHERE check_id = ?",
        ("gh release list --repo aws/aws-lambda-go", "gh, logged in (gh auth status)", check),
    )
    [run] = claim(conn, PROJECT, local(2026, 10, 22, 9))

    prompt = check_prompt(config, run)

    assert "Is the release out?" in prompt
    assert "gh release list --repo aws/aws-lambda-go" in prompt
    assert "gh auth status" in prompt
    assert f"{BIN} --db {config.db_path} finish {run.run_id} --outcome" in prompt
    assert "<<'PULSE_SUMMARY'" in prompt


def test_a_failed_launch_becomes_an_error_outcome(
    conn: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    add_check(conn)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    launch(conn, config, run, "/", at)

    row = conn.execute("SELECT outcome, summary FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
    assert row["outcome"] == "error"
    assert "launch failed" in row["summary"]
    assert "agent view is off" in row["summary"]


@pytest.mark.parametrize("agent", [CHECK_AGENT, CHECK_AGENT_REF])
def test_check_sessions_are_recognised_by_agent(conn: sqlite3.Connection, agent: str) -> None:
    assert is_check_session(conn, {"agent_type": agent})


def test_check_sessions_are_recognised_by_session_id(conn: sqlite3.Connection, config: Config) -> None:
    add_check(conn)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)
    launch(conn, config, run, "/", at)

    assert is_check_session(conn, {"session_id": "a1b2c3d4-0000-4000-8000-000000000000"})
    assert not is_check_session(conn, {"session_id": "ffffffff-0000-4000-8000-000000000000"})
    assert not is_check_session(conn, {})


def test_sessions_are_removed_once_seen_ok_or_old(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    ok_check = add_check(conn, name="ok")
    found_check = add_check(conn, name="found")
    at = local(2026, 10, 22, 9)
    runs = {run.check_id: run for run in claim(conn, PROJECT, at)}
    for run in runs.values():
        launch(conn, config, run, "/", at)
    finish(conn, config, run_id=runs[ok_check].run_id, outcome="ok", summary="fine", at=at)
    finish(conn, config, run_id=runs[found_check].run_id, outcome="found", summary="look", at=at)
    conn.execute("UPDATE runs SET seen = 1")

    remove_finished(conn, config, at)
    removed_now = removed_runs(conn)
    remove_finished(conn, config, at + timedelta(hours=25))
    removed_later = removed_runs(conn)

    assert removed_now == {runs[ok_check].run_id}
    assert removed_later == {runs[ok_check].run_id, runs[found_check].run_id}
    assert cleanups(tmp_path / "claude.log") == [["rm", "a1b2c3d4"], ["rm", "a1b2c3d4"]]


def test_a_reused_check_keeps_the_session_of_its_first_run(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    check = add_check(conn, reuse_session=True)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    launch(conn, config, run, str(tmp_path), at)

    [argv] = argvs(tmp_path / "claude.log")
    assert argv[:2] == ["--bg", "-n"]
    assert kept_session(conn, check) == "a1b2c3d4"


def test_the_next_run_resumes_the_kept_session_as_it_is(
    conn: sqlite3.Connection, config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_AGENTS", KEPT)
    add_check(conn, reuse_session=True)
    first_at = local(2026, 10, 22, 9)
    [first] = claim(conn, PROJECT, first_at)
    launch(conn, config, first, str(tmp_path), first_at)
    finish(conn, config, run_id=first.run_id, outcome="ok", summary="fine", at=first_at)
    next_at = local(2026, 10, 23, 9)
    [second] = claim(conn, PROJECT, next_at)

    launch(conn, config, second, str(tmp_path), next_at)

    assert argvs(tmp_path / "claude.log")[1:] == [
        ["agents", "--json", "--all"],
        ["stop", "a1b2c3d4"],
        ["--bg", "--resume", FULL_ID, check_prompt(config, second)],
    ]
    rows = conn.execute("SELECT run_id, session_id, session_removed FROM runs ORDER BY run_id").fetchall()
    assert [tuple(row) for row in rows] == [(first.run_id, "a1b2c3d4", 1), (second.run_id, "a1b2c3d4", 0)]


def test_a_kept_session_that_is_gone_is_replaced(conn: sqlite3.Connection, config: Config, tmp_path: Path) -> None:
    check = add_check(conn, reuse_session=True)
    conn.execute("UPDATE checks SET kept_session = 'deadbeef' WHERE check_id = ?", (check,))
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    launch(conn, config, run, str(tmp_path), at)

    [listing, started] = argvs(tmp_path / "claude.log")
    assert listing == ["agents", "--json", "--all"]
    assert started[:2] == ["--bg", "-n"]
    assert kept_session(conn, check) == "a1b2c3d4"


@pytest.mark.parametrize(
    ("listed", "error"), [("agent view is off", "printed no JSON"), ("null", "printed no list of sessions")]
)
def test_an_unreadable_session_list_becomes_an_error_outcome(
    conn: sqlite3.Connection, config: Config, monkeypatch: pytest.MonkeyPatch, listed: str, error: str
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_AGENTS", listed)
    check = add_check(conn, reuse_session=True)
    conn.execute("UPDATE checks SET kept_session = 'a1b2c3d4' WHERE check_id = ?", (check,))
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)

    launch(conn, config, run, "/", at)

    row = conn.execute("SELECT outcome, summary FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
    assert row["outcome"] == "error"
    assert error in row["summary"]


def test_a_kept_session_is_stopped_once_and_not_removed(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    add_check(conn, reuse_session=True)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)
    launch(conn, config, run, "/", at)
    finish(conn, config, run_id=run.run_id, outcome="ok", summary="fine", at=at)
    conn.execute("UPDATE runs SET seen = 1")

    remove_finished(conn, config, at)
    remove_finished(conn, config, at)

    assert cleanups(tmp_path / "claude.log") == [["stop", "a1b2c3d4"]]


def test_a_session_the_check_no_longer_keeps_is_removed(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    check = add_check(conn, reuse_session=True)
    at = local(2026, 10, 22, 9)
    [run] = claim(conn, PROJECT, at)
    launch(conn, config, run, "/", at)
    finish(conn, config, run_id=run.run_id, outcome="ok", summary="fine", at=at)
    conn.execute("UPDATE runs SET seen = 1")
    remove_finished(conn, config, at)

    conn.execute("UPDATE checks SET active = 0 WHERE check_id = ?", (check,))
    remove_finished(conn, config, at)

    assert cleanups(tmp_path / "claude.log") == [["stop", "a1b2c3d4"], ["rm", "a1b2c3d4"]]


def test_a_kept_session_is_left_to_a_newer_run_in_flight(
    conn: sqlite3.Connection, config: Config, tmp_path: Path
) -> None:
    add_check(conn, reuse_session=True)
    at = local(2026, 10, 22, 9)
    [first] = claim(conn, PROJECT, at)
    launch(conn, config, first, "/", at)
    finish(conn, config, run_id=first.run_id, outcome="ok", summary="fine", at=at)
    conn.execute("UPDATE runs SET seen = 1")
    next_at = local(2026, 10, 23, 9)
    claim(conn, PROJECT, next_at)

    remove_finished(conn, config, next_at)

    assert cleanups(tmp_path / "claude.log") == []
