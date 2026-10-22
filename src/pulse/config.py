# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where Pulse keeps its data and what it runs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pulse.errors import PulseError

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin" / "pulse"
ICON = ROOT / "assets" / "robot.png"

TITLE = "Pulse"
PLUGIN = "pulse"
"""The plugin's name, as `.claude-plugin/plugin.json` declares it."""
CHECK_AGENT = "pulse-check"
CHECK_AGENT_REF = f"{PLUGIN}:{CHECK_AGENT}"
"""How `claude --agent` finds it: a plugin agent answers only to its plugin-scoped name."""
CHECK_MODEL = "sonnet"


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    """What one invocation works with.

    Background sessions inherit no environment, so the db path always travels
    explicitly, in their prompt.
    """

    db_path: Path
    launcher: str = "claude"
    notify: bool = True

    @classmethod
    def from_env(cls, db: Path | None = None) -> Self:
        """Resolve the configuration of this process.

        The db comes from `--db`, then `PULSE_DB`, then the plugin's data
        directory (`CLAUDE_PLUGIN_DATA`, exported to hooks). Without any of
        them Pulse refuses rather than guess a second, separate db.
        """
        if db is None:
            explicit = os.environ.get("PULSE_DB")
            data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
            if explicit:
                db = Path(explicit)
            elif data_dir:
                db = Path(data_dir) / "pulse.db"
            else:
                msg = "no database: pass --db, or run Pulse from its plugin (CLAUDE_PLUGIN_DATA)"
                raise PulseError(msg)
        return cls(
            db_path=db.expanduser().resolve(),
            # Tests point these at fakes; background sessions never see them.
            launcher=os.environ.get("PULSE_LAUNCHER", "claude"),
            notify=os.environ.get("PULSE_NOTIFY", "1") != "0",
        )
