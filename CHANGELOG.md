# Changelog

All notable changes to Pulse are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Pulse uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Every push runs the checks; a `vX.Y.Z` tag on `main` publishes the release
  with its notes from this changelog.

### Fixed

- Check sessions start as the `pulse:pulse-check` agent. `--agent pulse-check`
  named no agent, since a plugin agent answers only to its plugin-scoped
  name, so the sessions ran on the default template, without the read only
  rules and tool limits of the check agent.

## [0.2.0] - 2026-10-22

### Changed

- Desktop notifications go over D-Bus (`org.freedesktop.Notifications`)
  through the D-Bus library of the system Python: PyGObject (GIO) first,
  dbus-python otherwise. `notify-send` is no longer needed, and Pulse still
  bundles and installs nothing.

## 0.1.0 - 2026-10-22

### Added

- Checks, reminders and notes, per project or system wide, kept across
  sessions in a SQLite file in the plugin's data directory.
- Each due check runs in its own background session (`claude --bg`, agent
  `pulse-check`, read only, Sonnet), launched by the plugin hooks; outcomes
  reach the user as a desktop notification and a hook message the model
  never reads.
- Schedules: daily, every interval, cron expressions, once at a time.
- Requests are saved only after a trial run, with the verified method and
  their requirements; failed runs wait for the next period or a retry.
- Optional read-only browser access through Claude in Chrome, only with the
  user's consent.

[Unreleased]: https://github.com/alessandrolattao/pulse/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/alessandrolattao/pulse/releases/tag/v0.2.0
