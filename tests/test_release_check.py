# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import importlib.util
import json
from typing import TYPE_CHECKING

import pytest

from pulse.config import ROOT

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.3] - 2026-10-22

### Fixed

- Something.

## [1.2.2] - 2026-10-01

- Older.

[1.2.3]: https://example.com/v1.2.3
"""


def load() -> ModuleType:
    """Import scripts/release_check.py, which is a script rather than a package module."""
    spec = importlib.util.spec_from_file_location("release_check", ROOT / "scripts" / "release_check.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project(root: Path, *, pyproject: str = "1.2.3", plugin: str = "1.2.3", package: str = "1.2.3") -> Path:
    """Write the three version fields and a changelog into a fake project."""
    (root / ".claude-plugin").mkdir()
    (root / "src" / "pulse").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "pulse"\nversion = "{pyproject}"\n')
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "pulse", "version": plugin}))
    (root / "src" / "pulse" / "__init__.py").write_text(f'__version__ = "{package}"\n')
    (root / "CHANGELOG.md").write_text(CHANGELOG)
    return root


def test_the_repository_itself_is_consistent() -> None:
    release = load()

    assert release.VERSION.fullmatch(release.the_version(ROOT))


def test_a_matching_tag_releases_its_version(tmp_path: Path) -> None:
    release = load()

    assert release.check_tag(project(tmp_path), "v1.2.3") == "1.2.3"


def test_notes_are_the_version_section_only(tmp_path: Path) -> None:
    release = load()

    assert release.notes(project(tmp_path), "1.2.3") == "### Fixed\n\n- Something."


@pytest.mark.parametrize(
    ("files", "tag", "error"),
    [
        ({"plugin": "1.2.4"}, "v1.2.3", "disagree"),
        ({}, "v1.2.4", "does not match"),
        ({"pyproject": "1.3.0", "plugin": "1.3.0", "package": "1.3.0"}, "v1.3.0", "no section"),
        ({"pyproject": "next", "plugin": "next", "package": "next"}, "vnext", "not X.Y.Z"),
    ],
)
def test_a_release_that_does_not_add_up_is_refused(tmp_path: Path, files: dict[str, str], tag: str, error: str) -> None:
    release = load()

    with pytest.raises(release.ReleaseError, match=error):
        release.check_tag(project(tmp_path, **files), tag)
