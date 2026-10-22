"""The `pulse` command as Claude and the check sessions run it: bin/pulse in a subprocess."""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from pulse.clock import utcnow
from pulse.config import BIN
from pulse.db import connect
from pulse.project import project_of
from pulse.schedule import claim

if TYPE_CHECKING:
    from pathlib import Path

CHECK = ["--kind", "check", "--method", "gh release list", "--requires", "gh, logged in (gh auth status)"]


def clean_env() -> dict[str, str]:
    """Return the environment without any Pulse or plugin variable, notifications off."""
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PULSE_", "CLAUDE_PLUGIN_"))}
    env["PULSE_NOTIFY"] = "0"
    return env


def run(args: list[str], cwd: Path, db: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run `bin/pulse --db <db> <args>` from `cwd`."""
    return subprocess.run(
        [str(BIN), "--db", str(db), *args],
        cwd=cwd,
        env=clean_env(),
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def add(cwd: Path, db: Path, *options: str, name: str = "release") -> subprocess.CompletedProcess[str]:
    """Add a project check with the given options."""
    return run(["add", "--scope", "project", "--name", name, *options, "Is the release out?"], cwd, db)


def output(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Parse the JSON a command printed."""
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def claim_one(cwd: Path, db: Path) -> int:
    """Claim the one due check the way a tick would and return its run id."""
    conn = connect(db)
    [claimed] = claim(conn, project_of(str(cwd)), utcnow())
    conn.close()
    return claimed.run_id


@pytest.mark.parametrize(
    ("options", "schedule"),
    [
        (["--daily"], "daily"),
        (["--every", "2h"], "every 2h"),
        (["--cron", "0  9 * *  mon"], "cron 0 9 * * mon"),
        (["--at", "2026-10-23 09:00"], "once at 2026-10-23 09:00"),
    ],
)
def test_add_stores_the_schedule(tmp_path: Path, options: list[str], schedule: str) -> None:
    db = tmp_path / "pulse.db"

    added = add(tmp_path, db, *CHECK, *options)

    assert added.returncode == 0, added.stdout
    assert output(added)["schedule"] == schedule


@pytest.mark.parametrize(
    ("options", "error"),
    [
        (["--kind", "check", "--daily"], "trial run worked"),
        (["--kind", "note", "--daily", "--browser"], "a note runs no session"),
        ([*CHECK, "--every", "1m"], "shortest interval"),
        ([*CHECK, "--cron", "0 9 * *"], "5 fields"),
        ([*CHECK, "--at", "tomorrow"], "a time is written like"),
    ],
)
def test_add_refuses_what_cannot_run(tmp_path: Path, options: list[str], error: str) -> None:
    result = add(tmp_path, tmp_path / "pulse.db", *options)

    assert result.returncode == 1
    message = output(result)["error"]
    assert isinstance(message, str)
    assert error in message


def test_a_note_needs_no_method(tmp_path: Path) -> None:
    result = add(tmp_path, tmp_path / "pulse.db", "--kind", "note", "--at", "2026-10-23 09:00")

    assert result.returncode == 0, result.stdout


def test_update_changes_the_configuration_only(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily")

    updated = run(["update", "1", "--method", "gh api repos/x/y/releases", "--browser"], tmp_path, db)

    assert updated.returncode == 0, updated.stdout
    view = output(updated)
    assert (view["method"], view["browser"], view["schedule"]) == ("gh api repos/x/y/releases", True, "daily")


def test_list_shows_the_checks_in_scope(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily")

    checks = output(run(["list"], tmp_path, db))["checks"]

    assert isinstance(checks, list)
    assert [check["name"] for check in checks] == ["release"]


def test_finish_reads_the_summary_verbatim_from_stdin(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily")
    run_id = claim_one(tmp_path, db)
    summary = "`go.mod` present, declares `go 1.27` and $(this never runs)"

    done = run(["finish", str(run_id), "--outcome", "ok"], tmp_path, db, summary)
    runs = output(run(["log"], tmp_path, db))["runs"]

    assert done.returncode == 0, done.stdout
    assert isinstance(runs, list)
    assert runs[0]["summary"] == summary


def test_an_empty_summary_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily")
    run_id = claim_one(tmp_path, db)

    done = run(["finish", str(run_id), "--outcome", "ok"], tmp_path, db, "  \n")

    assert done.returncode == 1
    assert "empty" in str(output(done)["error"])


def test_removing_an_unknown_check_is_an_error(tmp_path: Path) -> None:
    result = run(["remove", "42"], tmp_path, tmp_path / "pulse.db")

    assert result.returncode == 1
    assert "does not exist" in str(output(result)["error"])


def test_without_a_db_pulse_refuses(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(BIN), "list"], cwd=tmp_path, env=clean_env(), capture_output=True, text=True, check=False
    )

    assert result.returncode == 1
    assert "no database" in str(output(result)["error"])
