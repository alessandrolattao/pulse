# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Notifications go through the system's D-Bus library; these tests stand in fakes for it."""

from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING, Any

import pytest

from pulse.config import ICON, Config
from pulse.model import Outcome
from pulse.notify import notify

if TYPE_CHECKING:
    from pathlib import Path


class Recorder:
    """Collects what a fake library was asked to send."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[tuple[Any, ...]] = []


def fake_gi(recorder: Recorder, *, fail: bool = False) -> dict[str, object]:
    """Stand in for PyGObject: the modules `gi`, `gi.repository`, and the GIO and GLib Pulse imports by name."""

    class Variant:
        def __init__(self, signature: str, value: object) -> None:
            self.signature, self.value = signature, value

    class Bus:
        def call_sync(self, *args: object) -> None:
            if fail:
                raise RuntimeError
            recorder.calls.append(("gio", *args))

    gio = types.SimpleNamespace(
        BusType=types.SimpleNamespace(SESSION="session"),
        DBusCallFlags=types.SimpleNamespace(NONE=0),
        bus_get_sync=lambda *_: Bus(),
    )
    glib = types.SimpleNamespace(Variant=Variant, VariantType=lambda text: text)
    gi = types.ModuleType("gi")
    gi.require_version = lambda *_: None  # type: ignore[attr-defined]
    # Like the real gi.repository, the package itself holds no Gio or GLib attribute.
    return {
        "gi": gi,
        "gi.repository": types.ModuleType("gi.repository"),
        "gi.repository.Gio": gio,
        "gi.repository.GLib": glib,
    }


def fake_dbus(recorder: Recorder) -> types.ModuleType:
    """A stand-in for dbus-python, with the types Pulse builds its arguments from."""

    class Proxy:
        def Notify(self, *args: object, **kwargs: object) -> None:  # noqa: N802 - the D-Bus method name
            recorder.calls.append(("dbus", *args, kwargs))

    dbus = types.ModuleType("dbus")
    dbus.SessionBus = lambda: types.SimpleNamespace(get_object=lambda *_: Proxy())  # type: ignore[attr-defined]
    for name in ("UInt32", "Int32", "String", "Array", "Dictionary"):
        setattr(dbus, name, lambda value, **_: value)
    return dbus


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(db_path=tmp_path / "pulse.db", notify=True)


def install(monkeypatch: pytest.MonkeyPatch, *, gi: bool, dbus: bool, recorder: Recorder, fail: bool = False) -> None:
    """Make exactly the requested fake libraries importable."""
    if gi:
        for name, module in fake_gi(recorder, fail=fail).items():
            monkeypatch.setitem(sys.modules, name, module)
    else:
        monkeypatch.setitem(sys.modules, "gi", None)
    monkeypatch.setitem(sys.modules, "dbus", fake_dbus(recorder) if dbus else None)


def test_gio_is_used_first(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    recorder = Recorder()
    install(monkeypatch, gi=True, dbus=True, recorder=recorder)

    assert notify(config, "release", Outcome.AUTH, "run `gh auth login`")

    [call] = recorder.calls
    assert call[0] == "gio"
    assert call[1:5] == (
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
        "Notify",
    )
    parameters = call[5]
    assert parameters.signature == "(susssasa{sv}i)"
    assert parameters.value[:5] == ("Pulse", 0, str(ICON), "Pulse · release · needs login", "run `gh auth login`")


def test_dbus_python_is_the_fallback(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    recorder = Recorder()
    install(monkeypatch, gi=False, dbus=True, recorder=recorder)

    assert notify(config, "release", Outcome.FOUND, "v1.56.0 is out")

    [call] = recorder.calls
    assert call[0] == "dbus"
    assert call[1:6] == ("Pulse", 0, str(ICON), "Pulse · release", "v1.56.0 is out")


def test_without_a_dbus_library_nothing_is_sent(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    install(monkeypatch, gi=False, dbus=False, recorder=Recorder())

    assert not notify(config, "release", Outcome.FOUND, "anything")


def test_a_failing_bus_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    install(monkeypatch, gi=True, dbus=False, recorder=Recorder(), fail=True)

    assert not notify(config, "release", Outcome.ERROR, "boom")


def test_ok_and_disabled_notifications_are_not_sent(
    monkeypatch: pytest.MonkeyPatch, config: Config, tmp_path: Path
) -> None:
    recorder = Recorder()
    install(monkeypatch, gi=True, dbus=True, recorder=recorder)

    assert not notify(config, "release", Outcome.OK, "fine")
    assert not notify(Config(db_path=tmp_path / "pulse.db", notify=False), "release", Outcome.FOUND, "x")
    assert recorder.calls == []
