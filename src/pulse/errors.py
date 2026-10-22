# Copyright (C) 2026 Alessandro Lattao
# SPDX-License-Identifier: GPL-3.0-or-later
"""The one error Pulse reports to its caller."""


class PulseError(Exception):
    """A request Pulse refuses: reported as JSON with a non-zero exit."""
