"""`pulse tick`: what the hooks run on SessionStart, UserPromptSubmit and Stop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pulse.clock import utcnow
from pulse.config import TITLE, Config
from pulse.db import connect
from pulse.model import SYSTEM, Outcome
from pulse.project import project_of
from pulse.schedule import claim
from pulse.sessions import is_check_session, remove_finished, start

if TYPE_CHECKING:
    import sqlite3


def notices(conn: sqlite3.Connection, project: str) -> list[str]:
    """Collect the finished runs in scope the user has not seen.

    Everything but `ok` becomes a line; `ok` is only marked seen.
    """
    rows = conn.execute(
        "SELECT r.run_id, r.outcome, r.summary, c.name, c.check_id FROM runs r JOIN checks c USING (check_id) "
        "WHERE r.seen = 0 AND r.outcome != ? AND c.scope IN (?, ?) ORDER BY r.finished_at, r.run_id",
        (Outcome.RUNNING, SYSTEM, project),
    ).fetchall()
    lines: list[str] = []
    for row in rows:
        conn.execute("UPDATE runs SET seen = 1 WHERE run_id = ?", (row["run_id"],))
        if row["outcome"] != Outcome.OK:
            name = row["name"] or f"check {row['check_id']}"
            lines.append(f"{name} [{row['outcome']}]: {row['summary']}")
    return lines


def tick(stdin_text: str, db: Path | None = None) -> str:
    """Launch what is due, show the user the new outcomes, tidy up.

    The only output is a hook `systemMessage`, which reaches the user and not
    the model; a failure is shown the same way, never swallowed. Returns the
    JSON to print, or an empty string when there is nothing to say.
    """
    try:
        decoded = json.loads(stdin_text) if stdin_text.strip() else {}
        payload: dict[str, object] = decoded if isinstance(decoded, dict) else {}
        config = Config.from_env(db)
        conn = connect(config.db_path)
        if is_check_session(conn, payload):
            return ""
        cwd_value = payload.get("cwd")
        cwd = cwd_value if isinstance(cwd_value, str) and cwd_value else str(Path.cwd())
        project = project_of(cwd)
        at = utcnow()
        for run in claim(conn, project, at):
            start(conn, config, run, cwd, at)
        lines = notices(conn, project)
        remove_finished(conn, config, at)
    except Exception as exc:  # noqa: BLE001 - every failure must reach the user
        lines = [f"the hook failed: {type(exc).__name__}: {exc}"]
    if not lines:
        return ""
    message = f"🤖 {TITLE}\n" + "\n".join(f"· {line}" for line in lines)
    return json.dumps({"systemMessage": message}, ensure_ascii=False)
