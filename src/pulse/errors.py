"""The one error Pulse reports to its caller."""


class PulseError(Exception):
    """A request Pulse refuses: reported as JSON with a non-zero exit."""
