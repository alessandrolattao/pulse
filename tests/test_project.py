# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from pulse.project import project_of

if TYPE_CHECKING:
    from pathlib import Path

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]


def test_a_worktree_counts_as_its_main_checkout(tmp_path: Path) -> None:
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    subprocess.run([*GIT, "init", "-q", str(main)], check=True)
    subprocess.run([*GIT, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run([*GIT, "-C", str(main), "worktree", "add", "-q", str(worktree)], check=True)

    assert project_of(str(worktree)) == str(main.resolve())
    assert project_of(str(main)) == str(main.resolve())


def test_outside_a_repository_the_directory_is_the_project(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert project_of(str(plain)) == str(plain.resolve())
