# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop notifications, for the outcomes the user should see at once.

Pulse calls the freedesktop notification service over D-Bus
(`org.freedesktop.Notifications.Notify`) through a D-Bus library the system
Python already has: PyGObject (`gi`, GIO), which ships with GNOME desktops,
else dbus-python (`dbus`). Pulse bundles and installs nothing; without either
library it simply sends no notification.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from pulse.config import ICON, TITLE
from pulse.model import Outcome

if TYPE_CHECKING:
    from collections.abc import Callable

    from pulse.config import Config

LABELS = {Outcome.FOUND: "", Outcome.AUTH: " · needs login", Outcome.ERROR: " · error"}

SERVICE = "org.freedesktop.Notifications"
OBJECT = "/org/freedesktop/Notifications"
METHOD = "Notify"
SIGNATURE = "(susssasa{sv}i)"
"""app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout."""
TIMEOUT_MS = 5000


def send_with_gio(title: str, body: str) -> bool | None:
    """Call Notify through GIO. None when PyGObject or GIO is not installed."""
    try:
        gi = importlib.import_module("gi")
        gi.require_version("Gio", "2.0")
        # PyGObject builds these modules on import: they are not attributes of gi.repository until then.
        gio = importlib.import_module("gi.repository.Gio")
        glib = importlib.import_module("gi.repository.GLib")
    except (ImportError, ValueError):
        return None
    icon = str(ICON)
    try:
        bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        parameters = glib.Variant(
            SIGNATURE, (TITLE, 0, icon, title, body, [], {"image-path": glib.Variant("s", icon)}, -1)
        )
        bus.call_sync(
            SERVICE,
            OBJECT,
            SERVICE,
            METHOD,
            parameters,
            glib.VariantType("(u)"),
            gio.DBusCallFlags.NONE,
            TIMEOUT_MS,
            None,
        )
    except Exception:  # noqa: BLE001 - a notification is best effort; the return value reports it
        return False
    return True


def send_with_dbus_python(title: str, body: str) -> bool | None:
    """Call Notify through dbus-python. None when dbus-python is not installed."""
    try:
        dbus = importlib.import_module("dbus")
    except ImportError:
        return None
    icon = str(ICON)
    try:
        proxy = dbus.SessionBus().get_object(SERVICE, OBJECT)
        proxy.Notify(
            TITLE,
            dbus.UInt32(0),
            icon,
            title,
            body,
            dbus.Array([], signature="s"),
            dbus.Dictionary({"image-path": dbus.String(icon, variant_level=1)}, signature="sv"),
            dbus.Int32(-1),
            dbus_interface=SERVICE,
            timeout=TIMEOUT_MS / 1000,
        )
    except Exception:  # noqa: BLE001 - a notification is best effort; the return value reports it
        return False
    return True


SENDERS: tuple[Callable[[str, str], bool | None], ...] = (send_with_gio, send_with_dbus_python)


def notify(config: Config, name: str, outcome: Outcome, summary: str) -> bool:
    """Show a desktop notification and say whether it was delivered.

    Nothing is sent when notifications are off, for an outcome that does not
    notify, or when the system Python has no D-Bus library. The first library
    found is the one used.
    """
    if not config.notify or outcome not in LABELS:
        return False
    title = f"{TITLE} · {name}{LABELS[outcome]}"
    for send in SENDERS:
        delivered = send(title, summary)
        if delivered is not None:
            return delivered
    return False
