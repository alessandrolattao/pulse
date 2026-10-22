"""Which project a directory belongs to."""

from __future__ import annotations

import subprocess
from pathlib import Path


def project_of(cwd: str) -> str:
    """Return the project a directory belongs to.

    The main checkout of the directory's git repository, so every worktree
    counts as the same project; the directory itself outside a repository.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv; the directory is one argument
        ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],  # noqa: S607 - git from PATH, as the user runs it
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        common = Path(result.stdout.strip())
        return str(common.parent if common.name == ".git" else common)
    return str(Path(cwd).resolve())
