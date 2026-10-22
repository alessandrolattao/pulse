# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""The background sessions that run the checks: launching, recognising, removing."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pulse.clock import parse
from pulse.config import BIN, CHECK_AGENT, CHECK_MODEL, TITLE
from pulse.model import FINAL_OUTCOMES, KEEP_SESSION, SYSTEM, Kind, Outcome
from pulse.runs import finish

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from pulse.config import Config
    from pulse.model import ClaimedRun

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
BACKGROUNDED = re.compile(r"backgrounded\s*·\s*([0-9a-f]{6,})")
OUTPUT_EXCERPT = 300


def check_prompt(config: Config, run: ClaimedRun) -> str:
    """Build what a pulse-check session receives.

    The run, and the exact command that records its outcome, db included,
    since the session inherits no environment.
    """
    outcomes = "|".join(FINAL_OUTCOMES)
    browser = (
        "browser: allowed, read only, through Claude in Chrome (see your rules)\n"
        if run.browser
        else "browser: not allowed\n"
    )
    return (
        f"Pulse run {run.run_id}\n"
        f"check_id: {run.check_id}\n"
        f"name: {run.name}\n"
        f"kind: {run.kind}\n"
        f"schedule: {run.schedule}\n"
        f"scope: {run.scope}\n"
        f"{browser}\n"
        f"Check:\n{run.text}\n\n"
        f"Verified method (follow it):\n{run.method or '(none recorded)'}\n\n"
        f"Requires:\n{run.requires or '(nothing recorded)'}\n\n"
        "Record the outcome with exactly this command, the summary on stdin:\n"
        f"{BIN} --db {config.db_path} finish {run.run_id} --outcome <{outcomes}> <<'PULSE_SUMMARY'\n"
        "<summary>\n"
        "PULSE_SUMMARY\n"
    )


def start(conn: sqlite3.Connection, config: Config, run: ClaimedRun, cwd: str, at: datetime) -> None:
    """Carry out one claimed run: a note is told at once, anything else gets its session."""
    if run.kind is Kind.NOTE:
        finish(conn, config, run_id=run.run_id, outcome=Outcome.FOUND, summary=run.text, at=at)
        return
    launch(conn, config, run, cwd, at)


def launch(conn: sqlite3.Connection, config: Config, run: ClaimedRun, cwd: str, at: datetime) -> None:
    """Start the background session of one claimed run, in its project when it has one.

    The browser is turned on (`--chrome`) only for a check the user allowed it
    to. A launch that fails becomes an `error` outcome, so the user sees it.
    """
    workdir = run.scope if run.scope != SYSTEM and Path(run.scope).is_dir() else cwd
    command = [
        config.launcher,
        "--bg",
        "-n",
        f"{TITLE} · {run.name}",
        "--agent",
        CHECK_AGENT,
        "--model",
        CHECK_MODEL,
        "--chrome" if run.browser else "--no-chrome",
        check_prompt(config, run),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv; the prompt is one argument
            command, cwd=workdir, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure = str(exc)
    else:
        output = ANSI.sub("", result.stdout + result.stderr)
        match = BACKGROUNDED.search(output)
        if result.returncode == 0 and match is not None:
            conn.execute("UPDATE runs SET session_id = ? WHERE run_id = ?", (match.group(1), run.run_id))
            return
        failure = f"exit {result.returncode}: {output.strip()[:OUTPUT_EXCERPT]}"
    finish(conn, config, run_id=run.run_id, outcome=Outcome.ERROR, summary=f"launch failed: {failure}", at=at)


def is_check_session(conn: sqlite3.Connection, payload: dict[str, object]) -> bool:
    """Tell whether the hook runs inside a check session, which must never launch checks.

    Two guards: the agent it runs as (bare or plugin-scoped), and its session
    id among the ones Pulse launched (the short id `claude --bg` prints is the
    prefix of the full one).
    """
    agent = payload.get("agent_type")
    if isinstance(agent, str) and agent.split(":")[-1] == CHECK_AGENT:
        return True
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM runs WHERE session_id IS NOT NULL AND ? LIKE session_id || '%' LIMIT 1",
        (session_id,),
    ).fetchone()
    return row is not None


def remove_finished(conn: sqlite3.Connection, config: Config, at: datetime) -> None:
    """Take finished check sessions out of `claude agents` once the user has seen their outcome.

    At once when it was ok, after `KEEP_SESSION` otherwise.
    """
    rows = conn.execute(
        "SELECT run_id, session_id, outcome, finished_at FROM runs "
        "WHERE session_id IS NOT NULL AND session_removed = 0 AND outcome != ? AND seen = 1",
        (Outcome.RUNNING,),
    ).fetchall()
    for row in rows:
        finished = parse(row["finished_at"]) if row["finished_at"] else at
        if row["outcome"] != Outcome.OK and at - finished < KEEP_SESSION:
            continue
        # A session the user already removed by hand counts as removed too.
        subprocess.run(  # noqa: S603 - fixed argv
            [config.launcher, "rm", row["session_id"]], capture_output=True, timeout=30, check=False
        )
        conn.execute("UPDATE runs SET session_removed = 1 WHERE run_id = ?", (row["run_id"],))
