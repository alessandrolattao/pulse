"""Pulse: recurring checks and reminders for Claude Code, run in background sessions.

The requests (`checks`) and the log of every run (`runs`) live in one SQLite
file. Every check runs in its own background session (`claude --bg`, agent
`pulse-check`), never inside the user's conversation: the plugin hooks call
`pulse tick`, which launches what is due and shows the user the new outcomes as
a hook `systemMessage`, which reaches the user and not the model.
"""

__version__ = "0.1.0"
