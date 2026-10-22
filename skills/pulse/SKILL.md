---
name: pulse
description: Use for every "remind me to/when …" and "do or check X every Y / at Z" request, instead of /loop, CronCreate or cloud routines; and when the user asks what Pulse is watching, how its checks went, to change or remove one, or to retry after a Pulse login or error notice.
---

# Pulse

Pulse keeps the user's recurring checks, reminders and notes across sessions.
Each due check runs in its own background session (`claude --bg`, agent
`pulse-check`), never in this conversation: the plugin's hooks launch what is
due and show the user the new outcomes in a message that reaches the user and
not you. Your part is to set requests up so that they work, and to manage them.

Every command runs from the session's working directory, always with this db
option, and prints JSON:

```bash
pulse --db "${CLAUDE_PLUGIN_DATA}/pulse.db" <command>
```

## Setting up a request

Never save a check or a reminder straight away. A saved request carries what
worked, so that the background session follows it instead of guessing.

1. **Understand it.** What to look at, when, and what counts as worth telling
   the user. Ask only what you cannot infer.
2. **Try it now, read only.** Do the check yourself in this session, as the
   background session will: find the tool, CLI, API, file or page that answers
   it. Change nothing while trying.
3. **Get it working with the user.** When something is missing, say exactly
   what and help set it up: a CLI to install, a login to do (`gh auth login`,
   `aws sso login --profile …`), a profile or token to choose. If the data is
   reachable only through a website the user is logged into, ask: "this check
   needs the browser, through Claude in Chrome, read only: do you want to use
   it?" Use the browser only after a yes, and never decide it alone.
4. **Save it once it works**, with what the trial proved:
   - `--method`: the steps that worked, in order, with the exact commands,
     endpoints or pages, and how to tell `found` from `ok`;
   - `--requires`: each tool and login it depends on, with the command that
     checks or renews it;
   - `--browser` only if the user said yes.

A plain reminder with nothing to look into ("remind me tomorrow at 9 to call
Marco") is a `note`: no trial, no method, no session. It is simply told to the
user when due.

## Commands

| What | Command |
| --- | --- |
| Save a check | `add --scope project\|system --kind check --name "<few words>" <schedule> --method "…" --requires "…" [--browser] "<text>"` |
| Save a reminder (speaks once, when a condition holds) | same, with `--kind reminder` |
| Save a note (just tell the user) | `add --scope … --kind note --name "…" <schedule> "<what to tell>"` |
| Change what a check does or needs | `update <check_id> [--name …] [--text …] [--method …] [--requires …] [--browser\|--no-browser]` |
| See what is watched | `list` (this project and the system scope), `list --all` |
| Stop one | `remove <check_id>` (deactivates it; its log stays) |
| How it went | `log [--check-id N] [--limit N] [--all]` |
| Retry after a login or error notice | `retry` |

Schedules, in the user's local time:

| The user says | Option |
| --- | --- |
| "every day" | `--daily` (once per calendar day, at the first chance) |
| "every 30 minutes / 2 hours / 3 days" | `--every 30m`, `--every 2h`, `--every 3d` |
| "every Monday at 9", "weekdays at 18:30" | `--cron "0 9 * * mon"`, `--cron "30 18 * * mon-fri"` |
| "tomorrow at 9", "on the 3rd at 15:00" | `--at "2026-10-23 09:00"` (once, then it switches off) |

A run starts at the first hook event after it is due: the user's next message
or the end of a turn.

## Writing it down

- **text**: the request in the user's words plus every name they gave
  (profile, repository, pull request, URL). For a reminder, the condition
  and what to tell the user. For a note, the message itself.
- **name**: a few words; it titles the background session and the desktop
  notification (`Pulse · <name>`).
- **scope**: `project` when it is about the repository of the current
  directory (its worktrees count as one project), `system` when it is about
  no project. If it is unclear, ask.

After saving, confirm in one line: id, name, schedule, and what it relies on.

## When something fails

A failed run waits for its next period, so it does not repeat on every
message. When the user has fixed the cause (renewed a login, installed a
tool), run `retry`. When the method itself stopped working, try the check
again here, then `update` it with the new method.

## What you never do

- Never read a background session's output into this conversation: keeping
  results out of it is the point of Pulse. `log` gives a run's summary when
  the user asks; the full report is in the session, which the user opens from
  `claude agents`.
- Never turn the browser on without the user's yes.
- Never edit the db by hand.
