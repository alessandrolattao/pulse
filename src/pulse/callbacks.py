# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Callbacks: the command a check runs after every run, with its outcome, and no model involved.

`runs.finish` starts `pulse callback <run_id>` as a detached process once the
outcome is recorded, so that neither a hook nor a check session waits for it.
That process runs the command with `/bin/sh -c` and records its exit status
and the first lines of its output on the run. A callback never changes the
run's outcome, never notifies and never stops the check.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pulse.config import BIN
from pulse.errors import PulseError
from pulse.model import ABANDONED, SYSTEM, Outcome

if TYPE_CHECKING:
    import sqlite3

    from pulse.config import Config

SHELL = "/bin/sh"
TIMEOUT = timedelta(minutes=5)
"""How long a callback may run before its process group is killed."""
TIMED_OUT = 124
"""The exit status recorded for a callback killed at the timeout, as `timeout(1)` reports it."""
NOT_STARTED = 127
"""The exit status recorded for a callback that could not start, as the shell reports a missing command."""
OUTPUT_LINES = 20
OUTPUT_CHARS = 2000
"""What the run keeps of the output: its first lines, at most this many characters."""
READ_BYTES = 64 * 1024
"""What is read of the output: far more than the excerpt needs, whatever its characters."""


def spawn(conn: sqlite3.Connection, config: Config, run_id: int) -> None:
    """Start `pulse callback <run_id>` detached: its own session, no terminal, nothing to wait for.

    Its stdout and stderr go to /dev/null too: inherited, they would hold the
    pipe of the hook or of the `pulse finish` that started it open. A start
    that fails is recorded on the run, as a callback that could not start.
    """
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv
            [str(BIN), "--db", str(config.db_path), "callback", str(run_id)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        record(conn, run_id, NOT_STARTED, not_started(str(exc)))


def run_callback(conn: sqlite3.Connection, run_id: int) -> dict[str, object]:
    """Run the callback of a finished run, once, and record its exit status and output on the run.

    Refused for a run with no outcome yet (running, or only marked abandoned),
    for one whose callback already ran, and for a check that has no callback
    any more.
    """
    row = conn.execute(
        "SELECT r.*, c.name, c.scope, c.callback FROM runs r JOIN checks c USING (check_id) WHERE r.run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        msg = f"run {run_id} does not exist"
        raise PulseError(msg)
    if row["outcome"] == Outcome.RUNNING or row["summary"] == ABANDONED:
        msg = f"run {run_id} is not finished"
        raise PulseError(msg)
    if row["callback_status"] is not None:
        msg = f"the callback of run {run_id} already ran"
        raise PulseError(msg)
    if not row["callback"]:
        msg = f"check {row['check_id']} has no callback"
        raise PulseError(msg)
    status, output = execute(row["callback"], workdir(row["scope"]), environment(row))
    record(conn, run_id, status, output)
    return {"run_id": run_id, "callback_status": status, "callback_output": output}


def record(conn: sqlite3.Connection, run_id: int, status: int, output: str) -> None:
    """Store a callback's exit status and output on its run."""
    conn.execute("UPDATE runs SET callback_status = ?, callback_output = ? WHERE run_id = ?", (status, output, run_id))


def not_started(reason: str) -> str:
    """The output recorded for a callback that could not start."""
    return f"callback not started: {reason}"


def workdir(scope: str) -> str:
    """Where a check's callback runs: its project directory, or the home directory for a system check."""
    return str(Path.home()) if scope == SYSTEM else scope


def environment(row: sqlite3.Row) -> dict[str, str]:
    """The user's environment plus the run's outcome."""
    return {
        **os.environ,
        "PULSE_CHECK": row["name"] or f"check {row['check_id']}",
        "PULSE_CHECK_ID": str(row["check_id"]),
        "PULSE_OUTCOME": row["outcome"],
        "PULSE_SUMMARY": row["summary"] or "",
        "PULSE_ENDED": "1" if row["ended"] else "",
    }


def execute(command: str, cwd: str, env: dict[str, str]) -> tuple[int, str]:
    """Run a command with the shell and return its exit status and the excerpt of its output.

    stdout and stderr go together into a temporary file rather than a pipe,
    so that a process the command leaves behind cannot hold Pulse waiting.
    The command gets its own process group, so that at the timeout it is
    killed with everything it started. It never runs anywhere but `cwd`: a
    relative path in it would point at the wrong files.
    """
    if not Path(cwd).is_dir():
        return NOT_STARTED, not_started(f"working directory {cwd} does not exist")
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(  # noqa: S603 - the user's own command, saved with their yes
                [SHELL, "-c", command],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            return NOT_STARTED, not_started(str(exc))
        try:
            status = exit_status(process.wait(timeout=TIMEOUT.total_seconds()))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            status = TIMED_OUT
        output.seek(0)
        return status, excerpt(output.read(READ_BYTES))


def exit_status(returncode: int) -> int:
    """An exit status as the shell's `$?` shows it: a command killed by signal N is 128 + N."""
    return 128 - returncode if returncode < 0 else returncode


def excerpt(raw: bytes) -> str:
    """The first lines of an output, at most `OUTPUT_CHARS` characters."""
    lines = raw.decode(errors="replace").splitlines()[:OUTPUT_LINES]
    return "\n".join(lines)[:OUTPUT_CHARS]
