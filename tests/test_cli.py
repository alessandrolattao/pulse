# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""The `pulse` command as Claude and the check sessions run it: bin/pulse in a subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import time
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
REMINDER = ["--kind", "reminder", "--method", "gh pr view 652"]
NOTE = ["--kind", "note"]


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
        (["--kind", "note", "--daily", "--reuse-session"], "a note runs no session"),
        ([*CHECK, "--every", "1m"], "shortest interval"),
        ([*CHECK, "--cron", "0 9 * *"], "5 fields"),
        ([*CHECK, "--at", "tomorrow"], "a time is written like"),
        ([*CHECK, "--daily", "--until", "  "], "--no-until"),
        ([*REMINDER, "--daily", "--until", "PR 652 is merged"], "only a check takes an end condition"),
        ([*NOTE, "--daily", "--until", "PR 652 is merged"], "only a check takes an end condition"),
        ([*CHECK, "--daily", "--callback", " "], "--no-callback"),
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


def test_add_stores_the_session_reuse(tmp_path: Path) -> None:
    added = add(tmp_path, tmp_path / "pulse.db", *CHECK, "--daily", "--reuse-session")

    assert added.returncode == 0, added.stdout
    assert output(added)["reuse_session"] is True


@pytest.mark.parametrize(
    ("added", "options", "kept"),
    [
        ([], ["--browser"], None),
        (["--browser"], ["--no-browser"], None),
        ([], ["--no-reuse-session"], None),
        ([], ["--no-browser"], "a1b2c3d4"),
        ([], ["--method", "gh api repos/x/y/releases"], "a1b2c3d4"),
    ],
)
def test_the_kept_session_goes_with_a_new_browser_or_without_reuse(
    tmp_path: Path, added: list[str], options: list[str], kept: str | None
) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, *added, "--daily", "--reuse-session")
    conn = connect(db)
    conn.execute("UPDATE checks SET kept_session = 'a1b2c3d4'")

    updated = run(["update", "1", *options], tmp_path, db)

    assert updated.returncode == 0, updated.stdout
    assert conn.execute("SELECT kept_session FROM checks").fetchone()[0] == kept
    conn.close()


def test_add_stores_the_end_condition_and_list_shows_it(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"

    added = add(tmp_path, db, *CHECK, "--every", "1h", "--until", " PR 652 is merged ")
    checks = output(run(["list"], tmp_path, db))["checks"]

    assert added.returncode == 0, added.stdout
    assert output(added)["until"] == "PR 652 is merged"
    assert isinstance(checks, list)
    assert [check["until"] for check in checks] == ["PR 652 is merged"]


def test_a_check_saved_without_until_has_no_end(tmp_path: Path) -> None:
    added = add(tmp_path, tmp_path / "pulse.db", *CHECK, "--daily")

    assert output(added)["until"] == ""


@pytest.mark.parametrize(
    ("options", "until"), [(["--until", "the batch is over"], "the batch is over"), (["--no-until"], "")]
)
def test_update_sets_or_removes_the_end_condition(tmp_path: Path, options: list[str], until: str) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily", "--until", "PR 652 is merged")

    updated = run(["update", "1", *options], tmp_path, db)

    assert updated.returncode == 0, updated.stdout
    assert output(updated)["until"] == until


@pytest.mark.parametrize(
    ("added", "options", "error"),
    [
        (CHECK, ["--until", " "], "--no-until"),
        (REMINDER, ["--until", "PR 652 is merged"], "only a check takes an end condition"),
        (NOTE, ["--until", "PR 652 is merged"], "only a check takes an end condition"),
    ],
)
def test_update_refuses_an_empty_end_or_an_end_on_another_kind(
    tmp_path: Path, added: list[str], options: list[str], error: str
) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *added, "--daily")

    updated = run(["update", "1", *options], tmp_path, db)

    assert updated.returncode == 1
    assert error in str(output(updated)["error"])


@pytest.mark.parametrize("kind", [CHECK, REMINDER, NOTE])
def test_every_kind_takes_a_callback_and_list_shows_it(tmp_path: Path, kind: list[str]) -> None:
    db = tmp_path / "pulse.db"

    added = add(tmp_path, db, *kind, "--daily", "--callback", " ./hand-on.sh ")
    checks = output(run(["list"], tmp_path, db))["checks"]

    assert added.returncode == 0, added.stdout
    assert output(added)["callback"] == "./hand-on.sh"
    assert isinstance(checks, list)
    assert [check["callback"] for check in checks] == ["./hand-on.sh"]


def test_a_check_saved_without_callback_has_none(tmp_path: Path) -> None:
    added = add(tmp_path, tmp_path / "pulse.db", *CHECK, "--daily")

    assert output(added)["callback"] == ""


@pytest.mark.parametrize(
    ("options", "callback"), [(["--callback", "./other.sh"], "./other.sh"), (["--no-callback"], "")]
)
def test_update_sets_or_removes_the_callback(tmp_path: Path, options: list[str], callback: str) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily", "--callback", "./hand-on.sh")

    updated = run(["update", "1", *options], tmp_path, db)

    assert updated.returncode == 0, updated.stdout
    assert output(updated)["callback"] == callback


def test_update_refuses_an_empty_callback(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily", "--callback", "./hand-on.sh")

    updated = run(["update", "1", "--callback", "  "], tmp_path, db)

    assert updated.returncode == 1
    assert "--no-callback" in str(output(updated)["error"])
    checks = output(run(["list"], tmp_path, db))["checks"]
    assert isinstance(checks, list)
    assert [check["callback"] for check in checks] == ["./hand-on.sh"]


def test_update_refuses_a_callback_and_no_callback_together(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily")

    updated = run(["update", "1", "--callback", "./hand-on.sh", "--no-callback"], tmp_path, db)

    assert updated.returncode == 2
    assert "not allowed with" in updated.stderr


def callback_of(db: Path, run_id: int, timeout: float = 15) -> tuple[int, str]:
    """Wait for the detached callback of a run to record its exit status, and return it with the output."""
    conn = connect(db)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT callback_status, callback_output FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row["callback_status"] is not None:
                return row["callback_status"], row["callback_output"]
            time.sleep(0.1)
    finally:
        conn.close()
    msg = f"the callback of run {run_id} recorded nothing within {timeout} s"
    raise AssertionError(msg)


def test_finish_hands_the_outcome_to_the_callback_without_waiting_for_it(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    callback = 'sleep 3; printf "%s|%s|%s" "$PULSE_CHECK" "$PULSE_OUTCOME" "$PULSE_SUMMARY" > got.txt; echo handed on'
    add(tmp_path, db, *CHECK, "--daily", "--callback", callback)
    run_id = claim_one(tmp_path, db)

    done = run(["finish", str(run_id), "--outcome", "found"], tmp_path, db, "v1.2 is out")
    pending = output(run(["log"], tmp_path, db))["runs"]

    assert done.returncode == 0, done.stdout
    assert isinstance(pending, list)
    assert (pending[0]["outcome"], pending[0]["callback_status"]) == ("found", None)
    assert callback_of(db, run_id) == (0, "handed on")
    assert (tmp_path / "got.txt").read_text() == "release|found|v1.2 is out"
    runs = output(run(["log"], tmp_path, db))["runs"]
    assert isinstance(runs, list)
    assert [(row["outcome"], row["callback_status"], row["callback_output"]) for row in runs] == [
        ("found", 0, "handed on")
    ]


def test_the_hook_starts_a_notes_callback_without_waiting_for_it(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *NOTE, "--daily", "--callback", 'sleep 3; printf "%s" "$PULSE_SUMMARY"', name="call")

    ticked = run(["tick"], tmp_path, db, json.dumps({"cwd": str(tmp_path), "hook_event_name": "UserPromptSubmit"}))
    pending = output(run(["log"], tmp_path, db))["runs"]

    assert ticked.returncode == 0, ticked.stdout
    assert "call [found]: Is the release out?" in json.loads(ticked.stdout)["systemMessage"]
    assert isinstance(pending, list)
    assert [(row["outcome"], row["callback_status"]) for row in pending] == [("found", None)]
    assert callback_of(db, pending[0]["run_id"]) == (0, "Is the release out?")


def test_a_failing_callback_leaves_the_outcome_and_the_check_alone(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily", "--callback", "echo inbox down; exit 7")
    run_id = claim_one(tmp_path, db)

    run(["finish", str(run_id), "--outcome", "ok"], tmp_path, db, "nothing new")

    assert callback_of(db, run_id) == (7, "inbox down")
    checks = output(run(["list"], tmp_path, db))["checks"]
    assert isinstance(checks, list)
    assert [(check["active"], check["last_run"]["outcome"]) for check in checks] == [(True, "ok")]


def test_the_callback_command_refuses_a_run_that_is_not_finished(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--daily", "--callback", "./hand-on.sh")
    run_id = claim_one(tmp_path, db)

    result = run(["callback", str(run_id)], tmp_path, db)

    assert result.returncode == 1
    assert "not finished" in str(output(result)["error"])


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


def test_finish_ended_switches_the_check_off_and_keeps_its_log(tmp_path: Path) -> None:
    db = tmp_path / "pulse.db"
    add(tmp_path, db, *CHECK, "--every", "1h", "--until", "PR 652 is merged")
    run_id = claim_one(tmp_path, db)

    done = run(["finish", str(run_id), "--outcome", "ok", "--ended"], tmp_path, db, "merged, nothing new")
    checks = output(run(["list", "--all"], tmp_path, db))["checks"]
    runs = output(run(["log"], tmp_path, db))["runs"]

    assert done.returncode == 0, done.stdout
    assert output(done)["ended"] is True
    assert isinstance(checks, list)
    assert [check["active"] for check in checks] == [False]
    assert isinstance(runs, list)
    assert [(row["outcome"], row["ended"], row["summary"]) for row in runs] == [("ok", 1, "merged, nothing new")]
    assert output(run(["list"], tmp_path, db))["checks"] == []


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
