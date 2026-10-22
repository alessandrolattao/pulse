# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from pulse.clock import utcnow
from pulse.hook import tick
from pulse.runs import finish
from pulse.schedule import claim
from pulse.sessions import launch
from support import add_check

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from pulse.config import Config


@pytest.fixture
def hook_env(config: Config, monkeypatch: pytest.MonkeyPatch) -> Config:
    """The environment a plugin hook gets, pointed at the test db and fake claude."""
    monkeypatch.setenv("PULSE_DB", str(config.db_path))
    monkeypatch.setenv("PULSE_LAUNCHER", config.launcher)
    monkeypatch.setenv("PULSE_NOTIFY", "0")
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    return config


def payload(cwd: Path, **extra: str) -> str:
    return json.dumps({"cwd": str(cwd), "hook_event_name": "SessionStart", **extra})


def message(output: str) -> str:
    text = json.loads(output)["systemMessage"]
    assert isinstance(text, str)
    return text


@pytest.mark.usefixtures("hook_env")
def test_tick_launches_what_is_due_once(conn: sqlite3.Connection, tmp_path: Path) -> None:
    add_check(conn, scope=str(tmp_path))

    assert tick(payload(tmp_path)) == ""
    assert tick(payload(tmp_path)) == ""

    launches = [line for line in (tmp_path / "claude.log").read_text().splitlines() if '"--bg"' in line]
    assert len(launches) == 1


def test_tick_shows_each_outcome_once_and_hides_ok(hook_env: Config, conn: sqlite3.Connection, tmp_path: Path) -> None:
    found = add_check(conn, scope=str(tmp_path), name="release")
    fine = add_check(conn, scope=str(tmp_path), name="quiet")
    tick(payload(tmp_path))
    runs = {row["check_id"]: row["run_id"] for row in conn.execute("SELECT check_id, run_id FROM runs")}

    finish(conn, hook_env, run_id=runs[found], outcome="found", summary="`v1.56.0` is out", at=utcnow())
    finish(conn, hook_env, run_id=runs[fine], outcome="ok", summary="nothing new", at=utcnow())

    shown = message(tick(payload(tmp_path)))
    assert "release [found]: `v1.56.0` is out" in shown
    assert "quiet" not in shown
    assert tick(payload(tmp_path)) == ""


@pytest.mark.usefixtures("hook_env")
def test_tick_does_nothing_inside_a_check_session(conn: sqlite3.Connection, tmp_path: Path) -> None:
    add_check(conn, scope=str(tmp_path))

    assert tick(payload(tmp_path, agent_type="pulse-check")) == ""
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_tick_does_nothing_in_a_session_it_launched(hook_env: Config, conn: sqlite3.Connection, tmp_path: Path) -> None:
    add_check(conn, scope=str(tmp_path), cadence="every", interval_seconds=3600)

    [run] = claim(conn, str(tmp_path), utcnow())
    launch(conn, hook_env, run, str(tmp_path), utcnow())
    add_check(conn, scope=str(tmp_path), name="second")

    assert tick(payload(tmp_path, session_id="a1b2c3d4-1111-4000-8000-000000000000")) == ""
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


@pytest.mark.usefixtures("hook_env")
def test_a_failed_launch_reaches_the_user(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    add_check(conn, scope=str(tmp_path), name="release")

    shown = message(tick(payload(tmp_path)))

    assert "release [error]: launch failed" in shown


def test_a_broken_hook_reaches_the_user(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PULSE_DB", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)

    shown = message(tick(payload(tmp_path)))

    assert "the hook failed" in shown
    assert "no database" in shown
