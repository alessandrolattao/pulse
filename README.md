<p align="center">
  <img src="assets/robot.png" alt="Pulse" width="128">
</p>

<h1 align="center">Pulse</h1>

<p align="center">
  Recurring checks, reminders and notes for <a href="https://code.claude.com">Claude Code</a>,
  run in background sessions that never touch your conversation.
</p>

---

You are deep in a hard problem with Claude. Meanwhile you would like someone
to keep an eye on things: has that pull request been released, did last
night's deploy go through, is the cloud bill drifting, has the issue you are
waiting for been closed. Pulse does that without pulling any of it into the
conversation you are having, so the model stays on your work.

- **Say it once.** "Check every morning that PR 652 has been released",
  "every Monday at 9 tell me which dependencies are outdated", "remind me
  tomorrow at 15:00 to call Marco". Pulse keeps the request across sessions,
  per project.
- **It is set up to work.** Before saving, Claude tries the check with you:
  it finds the CLI, API or page that answers it, helps you install a tool or
  log in, and asks before it would ever use your browser. The method that
  worked is saved with the check.
- **It runs on its own.** When a check is due, Pulse starts a background
  session (`claude --bg`) that follows the saved method. You see it in
  `claude agents` as `Pulse · <name>` and can open it any time.
- **It tells you, not the model.** A desktop notification with the summary,
  and a short note in your terminal that the model never reads.

## How it works

```
 your session                                  background
 ────────────                                  ──────────
 open · message · end of turn
        │
        ▼
  hook: pulse tick ──── due? ────────────────▶ claude --bg -n "Pulse · <name>"
        │                                          --agent pulse-check
        │                                          (read only, Sonnet,
        │                                           the saved method)
        │                                                │
        │                                                ▼
        │                                      pulse finish → db + notification
        ▼
  next tick: new outcomes ──▶ hook message (you see it, the model does not)
```

| Kind | What a run does |
| --- | --- |
| `check` | A background session looks into something and reports. Quiet when all is as expected. |
| `reminder` | A background session watches a condition and speaks once, when it holds, then switches off. |
| `note` | Nothing runs: you are simply told the message when it is due. |

| Schedule | When it is due (local time) |
| --- | --- |
| `--daily` | once per calendar day |
| `--every 30m` · `2h` · `3d` | once per interval since the last run |
| `--cron "0 9 * * mon"` | at fixed times, standard five-field cron |
| `--at "2026-10-23 09:00"` | once, then it switches off |

- A due run starts at the first hook event after it becomes due: your next
  message, or the end of a turn. Two sessions opening together never run the
  same check twice.
- `ok` stays quiet. `found`, `auth` (a login to renew) and `error` notify
  you. A failed run waits for its next period instead of repeating on every
  message; say "retry" once you have fixed the cause.
- Finished sessions leave `claude agents` on their own: at once when ok,
  after a day otherwise.
- The requests and the log of every run live in a SQLite file in the
  plugin's data directory (`~/.claude/plugins/data/…`), which survives
  updates.

### The browser

A check may read through [Claude in Chrome](https://code.claude.com/docs/en/chrome),
with the sessions you are already logged into, only if you say yes when it
is set up. It stays read only: its own tab, no forms, no uploads, no scripts,
nothing submitted, bought, sent or deleted. Unattended sessions reading web
pages can meet text written to mislead them; keeping them read only bounds
what that can do.

## Install

In Claude Code:

```
/plugin marketplace add alessandrolattao/pulse
/plugin install pulse@pulse
```

Requirements:

- Claude Code with background sessions (`claude --bg`, `claude agents`).
- Python 3.11 or newer as `python3`. Pulse uses the standard library only.
- For desktop notifications, a D-Bus library in the system Python: PyGObject
  (`gi`, shipped with GNOME desktops) or dbus-python (`dbus`). Pulse bundles and
  installs nothing; without them it still works and shows outcomes in the
  terminal only.
- For browser checks, the Claude in Chrome extension.

Checks run with your Claude Code permission settings: in the default mode a
background check that needs a command approved waits for you in
`claude agents`.

## Use

Just ask Claude:

> Every day, check that `aws-lambda-go` has released the `ReplayName` change
> from PR 652.

> Every hour, tell me if the production deploy workflow failed.

> Every Monday at 9, list the open pull requests waiting for my review.

> Remind me tomorrow at 15:00 to call Marco.

> What is Pulse watching? · How did the release check go this week? · Stop
> checking 3. · Retry.

## Development

```bash
uv sync
uv run ruff format --check . && uv run ruff check .
uv run mypy
uv run pytest
```

The code is a small standard-library package under `src/pulse`, run by
`bin/pulse`. Ruff runs with every rule enabled and mypy in strict mode.

## License

Pulse is free software, released under the [GNU General Public License v3.0 or later](LICENSE).
You can use it, change it and share it, also at work and for money, as long as what
you distribute stays under the same license, with its source.

The robot icon (`assets/robot.png`) is the
[Robot icon](https://www.flaticon.com/free-icon/robot_6134346) created by
Magnific - [Flaticon](https://www.flaticon.com/free-icons/robot), used under the
Flaticon License with attribution. It is not covered by the GPL; see
[assets/CREDITS.md](assets/CREDITS.md).
