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
    conn: sqlite3.Connection, config: Config, *, run_id: int, outcome: str, summary: str, at: datetime
) -> dict[str, object]:
    """Close a run.

    A reminder that is `found` and a one-off that is done deactivate
    themselves; anything but `ok` sends a desktop notification. The outcome is
    stored unseen, so the next tick shows it to the user.
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
    conn.execute(
        "UPDATE runs SET outcome = ?, summary = ?, finished_at = ?, seen = 0 WHERE run_id = ?",
        (final, summary, stamp(at), run_id),
    )
    check = conn.execute("SELECT * FROM checks WHERE check_id = ?", (run["check_id"],)).fetchone()
    # A reminder that spoke, and a one-off that did its job, are over.
    deactivated = bool(check["active"]) and (
        (final is Outcome.FOUND and check["kind"] == Kind.REMINDER) or (final.done and check["cadence"] == Cadence.ONCE)
    )
    if deactivated:
        conn.execute("UPDATE checks SET active = 0 WHERE check_id = ?", (check["check_id"],))
    name = check["name"] or f"check {check['check_id']}"
    notified = final is not Outcome.OK and notify(config, name, final, summary)
    return {
        "run_id": run_id,
        "check_id": check["check_id"],
        "outcome": final.value,
        "reminder_deactivated": deactivated,
        "notified": notified,
    }
