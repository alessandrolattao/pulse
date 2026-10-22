"""The `pulse` command: manage checks and record outcomes. Every subcommand but `tick` prints JSON."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from pulse.clock import stamp, utcnow
from pulse.config import Config
from pulse.cron import Cron
from pulse.db import connect
from pulse.errors import PulseError
from pulse.hook import tick
from pulse.model import FINAL_OUTCOMES, SYSTEM, Cadence, Kind, parse_interval
from pulse.project import project_of
from pulse.runs import finish
from pulse.schedule import claim, describe, in_scope, last_finished
from pulse.sessions import start

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

Result: TypeAlias = dict[str, object]


@dataclass(frozen=True, slots=True, kw_only=True)
class Context:
    """What every subcommand works with."""

    args: argparse.Namespace
    config: Config
    conn: sqlite3.Connection
    cwd: str
    project: str
    at: datetime


def build_parser() -> argparse.ArgumentParser:
    """Build the command line of `pulse`."""
    parser = argparse.ArgumentParser(prog="pulse", description=__doc__)
    parser.add_argument("--db", type=Path, help="the database (default: $PULSE_DB, then $CLAUDE_PLUGIN_DATA/pulse.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("tick", help="hook entry point: reads the hook JSON on stdin")

    add = sub.add_parser("add", help="add a check, a reminder or a note")
    add.add_argument("--scope", choices=("project", SYSTEM), required=True)
    add.add_argument("--kind", choices=[kind.value for kind in Kind], required=True)
    add.add_argument("--name", required=True, help="a few words: the title of the session and of the notification")
    when = add.add_mutually_exclusive_group(required=True)
    when.add_argument("--daily", action="store_true", help="once per local calendar day")
    when.add_argument("--every", metavar="INTERVAL", help="once per interval since the last run: 30m, 2h, 3d")
    when.add_argument("--cron", metavar="EXPRESSION", help='at fixed local times: "0 9 * * mon"')
    when.add_argument("--at", metavar="TIME", help='once, at the first chance after a local time: "2026-10-23 09:00"')
    add.add_argument("--browser", action="store_true", help="let the check read through Claude in Chrome")
    add_configuration(add)
    add.add_argument("text", help="the request, complete enough for an agent with no other context")

    update = sub.add_parser("update", help="change the name, text, method, requirements or browser of a check")
    update.add_argument("check_id", type=int)
    update.add_argument("--name")
    update.add_argument("--text")
    add_configuration(update)
    update.add_argument("--browser", action=argparse.BooleanOptionalAction, default=None)

    listing = sub.add_parser("list", help="the checks of this project and the system ones")
    listing.add_argument("--all", action="store_true", help="every scope, inactive checks included")

    remove = sub.add_parser("remove", help="deactivate a check; its log stays")
    remove.add_argument("check_id", type=int)

    sub.add_parser("retry", help="relaunch the checks in scope whose last run ended in auth or error")

    # The summary comes on stdin, never as an argument: it is an agent's text,
    # and in a shell argument its backticks would run as commands.
    done = sub.add_parser("finish", help="record a run's outcome; the summary is read from stdin")
    done.add_argument("run_id", type=int)
    done.add_argument("--outcome", choices=[outcome.value for outcome in FINAL_OUTCOMES], required=True)

    log = sub.add_parser("log", help="the runs, most recent first")
    log.add_argument("--check-id", type=int)
    log.add_argument("--limit", type=int, default=20)
    log.add_argument("--all", action="store_true", help="every scope")

    sub.add_parser("project", help="the project of the current directory")
    return parser


def add_configuration(parser: argparse.ArgumentParser) -> None:
    """Add the options that hold what a trial run proved: how to do the check and what it needs."""
    parser.add_argument("--method", help="the verified way to run the check: commands, endpoints, pages, in order")
    parser.add_argument("--requires", help="tools and logins it needs, each with the command that checks or renews it")


def check_view(conn: sqlite3.Connection, check: sqlite3.Row) -> Result:
    """A check as JSON, with its last finished run."""
    last = last_finished(conn, check["check_id"])
    return {
        "check_id": check["check_id"],
        "name": check["name"],
        "scope": check["scope"],
        "schedule": describe(check),
        "kind": check["kind"],
        "browser": bool(check["browser"]),
        "active": bool(check["active"]),
        "text": check["text"],
        "method": check["method"],
        "requires": check["requires"],
        "created_at": check["created_at"],
        "last_run": None
        if last is None
        else {"started_at": last["started_at"], "outcome": last["outcome"], "summary": last["summary"]},
    }


def parse_local_time(text: str) -> datetime:
    """Read a local wall-clock time such as `2026-10-23 09:00`."""
    try:
        naive = datetime.fromisoformat(text.strip())
    except ValueError as exc:
        msg = f"a time is written like 2026-10-23 09:00, not {text!r}"
        raise PulseError(msg) from exc
    return naive.astimezone() if naive.tzinfo is None else naive


def schedule_columns(args: argparse.Namespace) -> tuple[Cadence, int | None, str | None, str | None]:
    """Turn the schedule options into the cadence, interval, due time and cron columns."""
    if args.every is not None:
        return Cadence.EVERY, parse_interval(args.every), None, None
    if args.cron is not None:
        Cron.parse(args.cron)
        return Cadence.CRON, None, None, " ".join(args.cron.split())
    if args.at is not None:
        return Cadence.ONCE, None, stamp(parse_local_time(args.at)), None
    return Cadence.DAILY, None, None, None


def cmd_add(ctx: Context) -> Result:
    """Store a new check in this project or in the system scope."""
    text, name = ctx.args.text.strip(), ctx.args.name.strip()
    if not text or not name:
        msg = "the request text and its name must not be empty"
        raise PulseError(msg)
    method = (ctx.args.method or "").strip()
    if ctx.args.kind == Kind.NOTE:
        if ctx.args.browser or method:
            msg = "a note runs no session: it takes no browser and no method"
            raise PulseError(msg)
    elif not method:
        msg = "a check is saved only once a trial run worked: pass --method with what worked"
        raise PulseError(msg)
    scope = ctx.project if ctx.args.scope == "project" else SYSTEM
    cadence, interval, due_at, cron = schedule_columns(ctx.args)
    cursor = ctx.conn.execute(
        "INSERT INTO checks (scope, name, text, cadence, interval_seconds, due_at, cron, kind, method, requires, "
        "browser, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            scope,
            name,
            text,
            cadence,
            interval,
            due_at,
            cron,
            ctx.args.kind,
            method,
            (ctx.args.requires or "").strip(),
            int(ctx.args.browser),
            stamp(ctx.at),
        ),
    )
    row = ctx.conn.execute("SELECT * FROM checks WHERE check_id = ?", (cursor.lastrowid,)).fetchone()
    return check_view(ctx.conn, row)


def cmd_update(ctx: Context) -> Result:
    """Change what a check does or needs, keeping its schedule and its log."""
    row = ctx.conn.execute("SELECT * FROM checks WHERE check_id = ?", (ctx.args.check_id,)).fetchone()
    if row is None:
        msg = f"check {ctx.args.check_id} does not exist"
        raise PulseError(msg)
    changes: dict[str, object] = {
        column: value.strip()
        for column, value in (
            ("name", ctx.args.name),
            ("text", ctx.args.text),
            ("method", ctx.args.method),
            ("requires", ctx.args.requires),
        )
        if value is not None
    }
    if ctx.args.browser is not None:
        changes["browser"] = int(ctx.args.browser)
    if not changes:
        msg = "nothing to change: pass at least one of --name, --text, --method, --requires, --browser"
        raise PulseError(msg)
    if any(changes.get(column) == "" for column in ("name", "text")):
        msg = "the name and the text of a check cannot be empty"
        raise PulseError(msg)
    if row["kind"] == Kind.NOTE and (changes.get("browser") or changes.get("method")):
        msg = "a note runs no session: it takes no browser and no method"
        raise PulseError(msg)
    # Column names come from the fixed tuple above, never from input.
    assignments = ", ".join(f"{column} = ?" for column in changes)
    ctx.conn.execute(
        f"UPDATE checks SET {assignments} WHERE check_id = ?",  # noqa: S608 - fixed column names
        (*changes.values(), ctx.args.check_id),
    )
    updated = ctx.conn.execute("SELECT * FROM checks WHERE check_id = ?", (ctx.args.check_id,)).fetchone()
    return check_view(ctx.conn, updated)


def cmd_list(ctx: Context) -> Result:
    """List the checks in scope, or every check with `--all`."""
    rows = (
        ctx.conn.execute("SELECT * FROM checks ORDER BY check_id").fetchall()
        if ctx.args.all
        else in_scope(ctx.conn, ctx.project)
    )
    return {"project": ctx.project, "checks": [check_view(ctx.conn, row) for row in rows]}


def cmd_remove(ctx: Context) -> Result:
    """Deactivate a check, keeping its log."""
    updated = ctx.conn.execute("UPDATE checks SET active = 0 WHERE check_id = ?", (ctx.args.check_id,))
    if updated.rowcount == 0:
        msg = f"check {ctx.args.check_id} does not exist"
        raise PulseError(msg)
    return {"check_id": ctx.args.check_id, "active": False}


def cmd_retry(ctx: Context) -> Result:
    """Relaunch the checks in scope whose last run ended in `auth` or `error`."""
    runs = claim(ctx.conn, ctx.project, ctx.at, failed_only=True)
    for run in runs:
        start(ctx.conn, ctx.config, run, ctx.cwd, ctx.at)
    return {"project": ctx.project, "relaunched": [run.name for run in runs]}


def cmd_finish(ctx: Context) -> Result:
    """Record the outcome of a run; the summary comes on stdin."""
    return finish(
        ctx.conn,
        ctx.config,
        run_id=ctx.args.run_id,
        outcome=ctx.args.outcome,
        summary=sys.stdin.read().strip(),
        at=ctx.at,
    )


def cmd_log(ctx: Context) -> Result:
    """List the runs in scope, most recent first."""
    rows = ctx.conn.execute(
        "SELECT r.*, c.name, c.text, c.scope, c.kind, c.cadence FROM runs r JOIN checks c USING (check_id) "
        "WHERE (? OR c.scope IN (?, ?)) AND (? IS NULL OR r.check_id = ?) "
        "ORDER BY r.started_at DESC, r.run_id DESC LIMIT ?",
        (ctx.args.all, SYSTEM, ctx.project, ctx.args.check_id, ctx.args.check_id, ctx.args.limit),
    ).fetchall()
    return {"project": ctx.project, "runs": [dict(row) for row in rows]}


def cmd_project(ctx: Context) -> Result:
    """Show the project the current directory belongs to."""
    return {"project": ctx.project}


COMMANDS: dict[str, Callable[[Context], Result]] = {
    "add": cmd_add,
    "update": cmd_update,
    "list": cmd_list,
    "remove": cmd_remove,
    "retry": cmd_retry,
    "finish": cmd_finish,
    "log": cmd_log,
    "project": cmd_project,
}


def main(argv: list[str] | None = None) -> int:
    """Run `pulse` and return its exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "tick":
        output = tick(sys.stdin.read(), args.db)
        if output:
            sys.stdout.write(output + "\n")
        return 0
    try:
        config = Config.from_env(args.db)
        cwd = str(Path.cwd())
        ctx = Context(
            args=args, config=config, conn=connect(config.db_path), cwd=cwd, project=project_of(cwd), at=utcnow()
        )
        result = COMMANDS[args.command](ctx)
    except PulseError as exc:
        sys.stdout.write(json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n")
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0
