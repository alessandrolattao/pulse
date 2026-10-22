# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared fixtures: a throwaway db, a fake `claude`, and no desktop notifications."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from pulse.config import Config
from pulse.db import connect

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator
    from pathlib import Path

FAKE_CLAUDE = """#!{python}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as log:
    log.write(json.dumps({{"argv": argv, "cwd": os.getcwd()}}) + "\\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if argv[:1] == ["--bg"]:
    if mode == "fail":
        print("error: agent view is off", file=sys.stderr)
        sys.exit(1)
    short = argv[2][:8] if argv[1] == "--resume" else "a1b2c3d4"
    print("\\x1b[1mbackgrounded\\x1b[0m · " + short + " · " + argv[2])
elif argv == ["agents", "--json", "--all"]:
    print(os.environ.get("FAKE_CLAUDE_AGENTS", "[]"))
elif argv[:1] == ["stop"]:
    print("stopped " + argv[1])
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand in for `claude`.

    Logs its calls, answers `--bg` (new or `--resume`), `agents --json --all`
    (the sessions in FAKE_CLAUDE_AGENTS) and `stop` like the real one, and
    fails `--bg` on demand (FAKE_CLAUDE_MODE=fail).
    """
    script = tmp_path / "claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable))
    script.chmod(0o755)
    log = tmp_path / "claude.log"
    log.touch()
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    return script


@pytest.fixture
def config(tmp_path: Path, fake_claude: Path) -> Config:
    """A config on a throwaway db, the fake `claude`, and no notifications."""
    return Config(db_path=tmp_path / "pulse.db", launcher=str(fake_claude), notify=False)


@pytest.fixture
def conn(config: Config) -> Iterator[sqlite3.Connection]:
    """A connection to the throwaway db."""
    connection = connect(config.db_path)
    yield connection
    connection.close()
