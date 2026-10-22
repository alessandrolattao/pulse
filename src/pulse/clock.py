"""Timestamps as Pulse stores them: UTC, ISO 8601, to the second."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """The current time, aware, in UTC."""
    return datetime.now(UTC)


def stamp(at: datetime) -> str:
    """The stored form of a time: comparable as a string between rows."""
    return at.astimezone(UTC).isoformat(timespec="seconds")


def parse(text: str) -> datetime:
    """A stored time back to an aware datetime."""
    return datetime.fromisoformat(text)
