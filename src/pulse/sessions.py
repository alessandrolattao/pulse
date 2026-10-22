# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""The background sessions that run the checks: launching, resuming, recognising, removing."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pulse.clock import parse
from pulse.config import BIN, CHECK_AGENT, CHECK_AGENT_REF, CHECK_MODEL, TITLE
from pulse.errors import PulseError
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

    A check that reuses its session continues the one it keeps, when that
    session still exists, and makes the new one its kept session otherwise.
    The browser is turned on (`--chrome`) only for a check the user allowed it
    to. A launch that fails becomes an `error` outcome, so the user sees it.
    """
    workdir = run.scope if run.scope != SYSTEM and Path(run.scope).is_dir() else cwd
    try:
        kept = kept_session(config, run)
        session = background(
            config, new_command(config, run) if kept is None else resume_command(config, run, kept), workdir
        )
    except PulseError as exc:
        finish(conn, config, run_id=run.run_id, outcome=Outcome.ERROR, summary=f"launch failed: {exc}", at=at)
        return
    conn.execute("UPDATE runs SET session_id = ? WHERE run_id = ?", (session, run.run_id))
    if run.reuse_session:
        conn.execute("UPDATE checks SET kept_session = ? WHERE check_id = ?", (session, run.check_id))
        # The earlier runs of the session hand it over: only the newest one stops or removes it.
        conn.execute("UPDATE runs SET session_removed = 1 WHERE session_id = ? AND run_id != ?", (session, run.run_id))


def new_command(config: Config, run: ClaimedRun) -> list[str]:
    """Return the arguments that start a new check session."""
    return [
        "--bg",
        "-n",
        f"{TITLE} · {run.name}",
        "--agent",
        CHECK_AGENT_REF,
        "--model",
        CHECK_MODEL,
        "--chrome" if run.browser else "--no-chrome",
        check_prompt(config, run),
    ]


def resume_command(config: Config, run: ClaimedRun, session: str) -> list[str]:
    """Return the arguments that continue a kept session.

    No other option: a session resumed with options becomes a copy, and
    without them it runs with the agent, model and browser it started with.
    """
    return ["--bg", "--resume", session, check_prompt(config, run)]


def kept_session(config: Config, run: ClaimedRun) -> str | None:
    """Return the full id of the session the check keeps, stopped and ready to resume.

    `claude --bg --resume` continues a session under its own id only when
    given the full id and when the session is not running, and an idle one
    still is: so it is stopped first, which keeps its conversation. None when
    the check keeps no session, or the user removed it.
    """
    if not run.reuse_session or run.kept_session is None:
        return None
    listed = call(config, ["agents", "--json", "--all"]).stdout
    try:
        sessions = json.loads(listed)
    except json.JSONDecodeError as exc:
        msg = f"`claude agents --json --all` printed no JSON: {listed.strip()[:OUTPUT_EXCERPT]}"
        raise PulseError(msg) from exc
    full = next(
        (
            session.get("sessionId")
            for session in sessions
            if isinstance(session, dict) and session.get("id") == run.kept_session
        ),
        None,
    )
    if not isinstance(full, str):
        return None
    call(config, ["stop", run.kept_session])
    return full


def background(config: Config, arguments: list[str], workdir: str) -> str:
    """Start a background session and return the short id `claude --bg` prints."""
    result = call(config, arguments, workdir)
    output = ANSI.sub("", result.stdout + result.stderr)
    match = BACKGROUNDED.search(output)
    if match is None:
        msg = f"exit 0: {output.strip()[:OUTPUT_EXCERPT]}"
        raise PulseError(msg)
    return match.group(1)


def call(config: Config, arguments: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run `claude` with these arguments; a failure to run, or an exit other than 0, raises."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv; the prompt is one argument
            [config.launcher, *arguments], cwd=cwd, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PulseError(str(exc)) from exc
    if result.returncode != 0:
        output = ANSI.sub("", result.stdout + result.stderr)
        msg = f"exit {result.returncode}: {output.strip()[:OUTPUT_EXCERPT]}"
        raise PulseError(msg)
    return result


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

    At once when it was ok, after `KEEP_SESSION` otherwise. The session a
    check keeps for its next run is stopped instead, which keeps its
    conversation; it is removed once the check no longer keeps it.
    """
    rows = conn.execute(
        "SELECT r.run_id, r.session_id, r.outcome, r.finished_at, r.session_stopped, "
        "c.active AND c.reuse_session AND c.kept_session = r.session_id AS kept "
        "FROM runs r JOIN checks c USING (check_id) "
        "WHERE r.session_id IS NOT NULL AND r.session_removed = 0 AND r.outcome != ? AND r.seen = 1",
        (Outcome.RUNNING,),
    ).fetchall()
    for row in rows:
        finished = parse(row["finished_at"]) if row["finished_at"] else at
        if row["outcome"] != Outcome.OK and at - finished < KEEP_SESSION:
            continue
        if row["kept"]:
            if not row["session_stopped"]:
                # A session the user already stopped or removed by hand counts as stopped too.
                subprocess.run(  # noqa: S603 - fixed argv
                    [config.launcher, "stop", row["session_id"]], capture_output=True, timeout=30, check=False
                )
                conn.execute("UPDATE runs SET session_stopped = 1 WHERE run_id = ?", (row["run_id"],))
            continue
        # A session the user already removed by hand counts as removed too.
        subprocess.run(  # noqa: S603 - fixed argv
            [config.launcher, "rm", row["session_id"]], capture_output=True, timeout=30, check=False
        )
        conn.execute("UPDATE runs SET session_removed = 1 WHERE run_id = ?", (row["run_id"],))
