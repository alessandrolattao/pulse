# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop notifications, for the outcomes the user should see at once."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from pulse.config import ICON, TITLE
from pulse.model import Outcome

if TYPE_CHECKING:
    from pulse.config import Config

LABELS = {Outcome.FOUND: "", Outcome.AUTH: " · needs login", Outcome.ERROR: " · error"}


def notify(config: Config, name: str, outcome: Outcome, summary: str) -> bool:
    """Send a notification through `notify-send` (Linux).

    Where `notify-send` does not exist nothing is sent, and the return value
    says so. The summary is an argv element, never shell text.
    """
    sender = shutil.which("notify-send")
    if not config.notify or outcome not in LABELS or sender is None:
        return False
    title = f"{TITLE} · {name}{LABELS[outcome]}"
    result = subprocess.run(  # noqa: S603 - fixed argv; the summary is one argument
        [sender, f"--app-name={TITLE}", f"--icon={ICON}", title, summary],
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result.returncode == 0
