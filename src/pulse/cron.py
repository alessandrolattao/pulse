# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Standard five-field cron expressions, evaluated in local time.

`minute hour day-of-month month day-of-week`, with `*`, lists (`1,15`),
ranges (`1-5`), steps (`*/15`, `9-17/2`) and names (`mon`, `jan`). As in cron,
when both day fields are restricted a day matches either of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Self

from pulse.errors import PulseError

MONTHS = {
    name: number
    for number, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
WEEKDAYS = {name: number for number, name in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
FIELDS = 5
LOOKBACK_DAYS = 366
"""An expression that matched no day in a year (`0 0 30 2 *`) is never due."""


@dataclass(frozen=True, slots=True)
class Field:
    """The allowed values of one field, and whether it was restricted at all."""

    values: frozenset[int]
    restricted: bool


def parse_field(text: str, low: int, high: int, names: dict[str, int] | None = None) -> Field:
    """Parse one cron field into the set of values it allows."""
    values: set[int] = set()
    for part in text.lower().split(","):
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if base == "*":
            start, end = low, high
        else:
            first, _, last = base.partition("-")
            start = names[first] if names and first in names else int(first)
            end = (names[last] if names and last in names else int(last)) if last else (high if step_text else start)
        if step < 1 or not low <= start <= end <= high:
            msg = f"cron field {text!r} is out of range {low}-{high}"
            raise PulseError(msg)
        values.update(range(start, end + 1, step))
    return Field(values=frozenset(values), restricted=text != "*")


@dataclass(frozen=True, slots=True)
class Cron:
    """A parsed cron expression."""

    minutes: Field
    hours: Field
    days: Field
    months: Field
    weekdays: Field

    @classmethod
    def parse(cls, expression: str) -> Self:
        """Parse an expression, refusing anything that is not five valid fields."""
        parts = expression.split()
        if len(parts) != FIELDS:
            msg = f"a cron expression has {FIELDS} fields, {expression!r} has {len(parts)}"
            raise PulseError(msg)
        try:
            minute, hour, day, month, weekday = (
                parse_field(parts[0], 0, 59),
                parse_field(parts[1], 0, 23),
                parse_field(parts[2], 1, 31),
                parse_field(parts[3], 1, 12, MONTHS),
                parse_field(parts[4], 0, 7, WEEKDAYS),
            )
        except ValueError as exc:
            msg = f"invalid cron expression {expression!r}: {exc}"
            raise PulseError(msg) from exc
        # 7 is Sunday too, as in cron.
        weekdays = Field(values=frozenset(day % 7 for day in weekday.values), restricted=weekday.restricted)
        return cls(minutes=minute, hours=hour, days=day, months=month, weekdays=weekdays)

    def matches_day(self, day: date) -> bool:
        """Whether the expression can fire on this calendar day."""
        if day.month not in self.months.values:
            return False
        by_month_day = day.day in self.days.values
        by_weekday = (day.isoweekday() % 7) in self.weekdays.values
        if self.days.restricted and self.weekdays.restricted:
            return by_month_day or by_weekday
        return by_month_day and by_weekday

    def previous(self, at: datetime) -> datetime | None:
        """Return the latest time the expression fired at or before `at` (local), within a year."""
        local = at.astimezone()
        hours = sorted(self.hours.values, reverse=True)
        minutes = sorted(self.minutes.values, reverse=True)
        for offset in range(LOOKBACK_DAYS):
            day = local.date() - timedelta(days=offset)
            if not self.matches_day(day):
                continue
            for hour in hours:
                for minute in minutes:
                    candidate = datetime.combine(day, time(hour, minute)).astimezone()
                    if candidate <= local:
                        return candidate
        return None
