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


def calls(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


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
    removed_now = [c["argv"] for c in calls(tmp_path / "claude.log") if c["argv"] == ["rm", "a1b2c3d4"]]
    remove_finished(conn, config, at + timedelta(hours=25))
    removed_later = [c["argv"] for c in calls(tmp_path / "claude.log") if c["argv"] == ["rm", "a1b2c3d4"]]

    assert len(removed_now) == 1
    assert len(removed_later) == 2
