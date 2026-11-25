# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recording the outcome of a run."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pulse.clock import stamp
from pulse.errors import PulseError
from pulse.model import ABANDONED, FINAL_OUTCOMES, Cadence, Kind, Outcome
from pulse.notify import notify

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from pulse.config import Config


def finish(  # noqa: PLR0913 - every argument is a distinct input of the record
    conn: sqlite3.Connection,
    config: Config,
    *,
    run_id: int,
    outcome: str,
    summary: str,
    at: datetime,
    ended: bool = False,
) -> dict[str, object]:
    """Close a run.

    A reminder that is `found`, a one-off that is done and a run that found
    its check's end condition true (`ended`, only for a check that has one)
    deactivate the check; anything but `ok`, and an ended run, sends a desktop
    notification. The outcome is stored unseen, so the next tick shows it to
    the user.
    """
    if outcome not in FINAL_OUTCOMES:
        msg = f"outcome must be one of {', '.join(FINAL_OUTCOMES)}"
        raise PulseError(msg)
    final = Outcome(outcome)
    if not summary:
        msg = "the summary is empty"
        raise PulseError(msg)
    run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        msg = f"run {run_id} does not exist"
        raise PulseError(msg)
    # A late result still replaces the abandoned marker: it is the real outcome.
    if run["outcome"] != Outcome.RUNNING and run["summary"] != ABANDONED:
        msg = f"run {run_id} is already finished ({run['outcome']})"
        raise PulseError(msg)
    check = conn.execute("SELECT * FROM checks WHERE check_id = ?", (run["check_id"],)).fetchone()
    # Refused before anything is written: the run stays open for a corrected finish.
    if ended and not check["until"]:
        msg = f"check {check['check_id']} has no end condition: record the outcome without --ended"
        raise PulseError(msg)
    conn.execute(
        "UPDATE runs SET outcome = ?, summary = ?, finished_at = ?, seen = 0, ended = ? WHERE run_id = ?",
        (final, summary, stamp(at), int(ended), run_id),
    )
    # A reminder that spoke, a one-off that did its job, and a check whose end came, are over.
    deactivated = bool(check["active"]) and (
        ended
        or (final is Outcome.FOUND and check["kind"] == Kind.REMINDER)
        or (final.done and check["cadence"] == Cadence.ONCE)
    )
    if deactivated:
        conn.execute("UPDATE checks SET active = 0 WHERE check_id = ?", (check["check_id"],))
    name = check["name"] or f"check {check['check_id']}"
    notified = notify(config, name, final, summary, ended=check["until"] if ended else None)
    return {
        "run_id": run_id,
        "check_id": check["check_id"],
        "outcome": final.value,
        "ended": ended,
        "reminder_deactivated": deactivated,
        "notified": notified,
    }
