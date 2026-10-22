#!/usr/bin/env python3
# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""Release checks: one version everywhere, a changelog entry for it, and its notes.

release_check.py check            the three version fields agree (every CI run)
release_check.py tag v1.2.3       the tag matches them and CHANGELOG.md has 1.2.3
release_check.py notes 1.2.3      print the CHANGELOG.md section of 1.2.3
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"\d+\.\d+\.\d+")


class ReleaseError(Exception):
    """A release that must not go out."""


def versions(root: Path) -> dict[str, str]:
    """Read the version from each file that declares it."""
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text())["version"]
    match = re.search(r'^__version__ = "([^"]+)"', (root / "src" / "pulse" / "__init__.py").read_text(), re.MULTILINE)
    if match is None:
        msg = "src/pulse/__init__.py declares no __version__"
        raise ReleaseError(msg)
    return {"pyproject.toml": pyproject, ".claude-plugin/plugin.json": plugin, "src/pulse/__init__.py": match.group(1)}


def the_version(root: Path) -> str:
    """Return the version, refusing files that disagree or a version that is not X.Y.Z."""
    found = versions(root)
    distinct = set(found.values())
    if len(distinct) != 1:
        listing = ", ".join(f"{path}={version}" for path, version in found.items())
        msg = f"the versions disagree: {listing}"
        raise ReleaseError(msg)
    version = distinct.pop()
    if VERSION.fullmatch(version) is None:
        msg = f"the version {version!r} is not X.Y.Z"
        raise ReleaseError(msg)
    return version


def notes(root: Path, version: str) -> str:
    """Return the body of the `## [version]` section of CHANGELOG.md."""
    changelog = (root / "CHANGELOG.md").read_text()
    heading = re.compile(rf"^## \[{re.escape(version)}\][^\n]*\n", re.MULTILINE)
    start = heading.search(changelog)
    if start is None:
        msg = f"CHANGELOG.md has no section for {version}"
        raise ReleaseError(msg)
    rest = changelog[start.end() :]
    end = re.search(r"^## \[|^\[[^\]]+\]: ", rest, re.MULTILINE)
    body = (rest[: end.start()] if end else rest).strip()
    if not body:
        msg = f"the CHANGELOG.md section for {version} is empty"
        raise ReleaseError(msg)
    return body


def check_tag(root: Path, tag: str) -> str:
    """Return the version a tag releases, once the tag, the files and the changelog all agree."""
    version = the_version(root)
    if tag != f"v{version}":
        msg = f"the tag {tag} does not match the version {version}: it should be v{version}"
        raise ReleaseError(msg)
    notes(root, version)
    return version


def main(argv: list[str]) -> int:
    """Run one check and return the exit code."""
    try:
        if argv == ["check"]:
            sys.stdout.write(f"version {the_version(ROOT)}\n")
        elif len(argv) == 2 and argv[0] == "tag":  # noqa: PLR2004 - command and tag
            sys.stdout.write(f"releasing {check_tag(ROOT, argv[1])}\n")
        elif len(argv) == 2 and argv[0] == "notes":  # noqa: PLR2004 - command and version
            sys.stdout.write(notes(ROOT, argv[1]) + "\n")
        else:
            sys.stderr.write(__doc__ or "")
            return 2
    except ReleaseError as exc:
        sys.stderr.write(f"release check failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
