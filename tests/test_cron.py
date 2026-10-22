from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pulse.cron import Cron
from pulse.errors import PulseError
from support import local

if TYPE_CHECKING:
    from datetime import datetime


@pytest.mark.parametrize(
    ("expression", "at", "expected"),
    [
        ("0 9 * * *", local(2026, 10, 22, 10), local(2026, 10, 22, 9)),
        ("0 9 * * *", local(2026, 10, 22, 8), local(2026, 10, 21, 9)),
        ("30 18 * * mon-fri", local(2026, 10, 25, 12), local(2026, 10, 23, 18, 30)),
        ("*/15 * * * *", local(2026, 10, 22, 10, 44), local(2026, 10, 22, 10, 30)),
        ("0 9 1 * *", local(2026, 10, 22, 10), local(2026, 10, 1, 9)),
        ("0 9 * * 7", local(2026, 10, 22, 10), local(2026, 10, 18, 9)),
        ("0 9 13 * fri", local(2026, 10, 22, 10), local(2026, 10, 16, 9)),
    ],
)
def test_previous_occurrence(expression: str, at: datetime, expected: datetime) -> None:
    assert Cron.parse(expression).previous(at) == expected


def test_an_impossible_date_never_fires() -> None:
    assert Cron.parse("0 0 30 2 *").previous(local(2026, 10, 22, 10)) is None


@pytest.mark.parametrize("expression", ["0 9 * *", "60 9 * * *", "0 25 * * *", "0 9 * * funday", "*/0 * * * *"])
def test_invalid_expressions_are_refused(expression: str) -> None:
    with pytest.raises(PulseError):
        Cron.parse(expression)
