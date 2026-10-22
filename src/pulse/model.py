# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""The vocabulary of Pulse: scopes, cadences, kinds, outcomes and a claimed run."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import StrEnum

from pulse.errors import PulseError

SYSTEM = "system"
"""The scope of a check that belongs to no project."""

STALE_AFTER = timedelta(minutes=30)
"""A run still `running` after this long belongs to a session that went away."""

ABANDONED = "abandoned: no result within 30 minutes"

KEEP_SESSION = timedelta(hours=24)
"""A finished check session leaves `claude agents` at once when it ended ok,
after this long otherwise, so that the user can still open it."""

INTERVAL = re.compile(r"(\d+)([mhd])")
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
MIN_INTERVAL = timedelta(minutes=5)


class Cadence(StrEnum):
    """When a check is due."""

    DAILY = "daily"
    """Once per local calendar day."""
    EVERY = "every"
    """Once per interval, counted from the last run."""
    CRON = "cron"
    """At the times of a cron expression, local time."""
    ONCE = "once"
    """Once, at the first chance after a given time."""


class Kind(StrEnum):
    """What a run of the check does."""

    CHECK = "check"
    """A background session looks into something and reports."""
    REMINDER = "reminder"
    """A background session watches a condition and speaks once when it holds."""
    NOTE = "note"
    """No session: the user is simply told the text when it is due."""


class Outcome(StrEnum):
    """How a run ended; `RUNNING` while its session works."""

    RUNNING = "running"
    OK = "ok"
    FOUND = "found"
    AUTH = "auth"
    ERROR = "error"

    @property
    def done(self) -> bool:
        """Whether the run did its job (as opposed to failing)."""
        return self in {Outcome.OK, Outcome.FOUND}

    @property
    def failed(self) -> bool:
        """Whether a retry should run the check again."""
        return self in {Outcome.AUTH, Outcome.ERROR}


FINAL_OUTCOMES = tuple(outcome for outcome in Outcome if outcome is not Outcome.RUNNING)


def parse_interval(text: str) -> int:
    """Turn `30m`, `2h` or `3d` into seconds, refusing anything shorter than five minutes."""
    match = INTERVAL.fullmatch(text.strip().lower())
    if match is None:
        msg = f"an interval is a number and m, h or d, like 30m, 2h or 3d: not {text!r}"
        raise PulseError(msg)
    seconds = int(match.group(1)) * UNIT_SECONDS[match.group(2)]
    if seconds < MIN_INTERVAL.total_seconds():
        msg = f"the shortest interval is {int(MIN_INTERVAL.total_seconds() // 60)}m"
        raise PulseError(msg)
    return seconds


def format_interval(seconds: int) -> str:
    """Write seconds back in the largest unit that divides them."""
    for unit in ("d", "h", "m"):
        if seconds % UNIT_SECONDS[unit] == 0:
            return f"{seconds // UNIT_SECONDS[unit]}{unit}"
    return f"{seconds}s"


@dataclass(frozen=True, slots=True, kw_only=True)
class ClaimedRun:
    """A run taken for one check, waiting for its background session."""

    run_id: int
    check_id: int
    name: str
    kind: Kind
    schedule: str
    scope: str
    browser: bool
    reuse_session: bool
    kept_session: str | None
    """The short id of the session the check keeps for its next run, when it has one."""
    text: str
    method: str
    requires: str

    def as_dict(self) -> dict[str, object]:
        """Return the run as plain JSON-ready values."""
        return asdict(self)
