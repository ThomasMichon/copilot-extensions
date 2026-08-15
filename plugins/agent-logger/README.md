# agent-logger

Reusable Copilot CLI **session logging** for the GitHub Copilot CLI,
packaged as a copilot-extensions plugin. It factors the reusable ends of a
session-to-log pipeline out of any single bespoke service:

- **Segmenter** — collate one Copilot session into context-ingestible
  Markdown digest chunks (`collate-session`, `read-session-digest`,
  `prepare-session-log`), and **ramp up into a dormant session**
  (`ramp-up-session`) — discover a worktree's most recent session, collate it
  ephemerally, and print a takeover brief so a fresh session can pick up the
  torch of one that can no longer be resumed.
- **Log writer** — one voice-neutral `session-log-writer` agent that turns a
  manifest of 1..N sessions into structured Markdown logs, plus the
  `log-session` (interactive) and `process-backlog` (local batch) skills
  that drive it. Personality is never built in; repository organization config
  can declaratively supply optional voice-seam instructions
  (see [`docs/manifest-contract.md`](docs/manifest-contract.md)).
- **session-sync** — push raw session data to a configurable target: a
  `local` dotfolder, `onedrive`, `ssh`/`ssh-tunnel`, or a generic `ingest`
  endpoint. It archives only Copilot session state, can scope by repo
  allow/deny lists, and can fire a target-independent best-effort HTTP notify
  after a successful push. Configure with the `session-sync-setup` skill; deploy
  as a 4-hourly Scheduled Task (Windows) or systemd user timer (Linux).
- **Background chronicling core** (`agent_logger.chronicle`) — an optional
  `agent-logger chronicle status | scan | tick` pass over a *synced* corpus. It
  discovers settled sessions, routes them by recorded origin, groups them into
  compact daily digest manifests, and reserves segment identities in SQLite so
  racing passes do not double-log. The plugin does **not** install a chronicle
  scheduler or job lease by itself; a host/runner owns scheduling and, when it
  wants real logs rather than manifests, runs the `session-log-writer` agent and
  applies the configured landing policy.

## Design principles

- **Personality- and layout-neutral.** Voices, output path templates,
  repo-local Markdown skeletons, and machine naming are configuration, not
  hard-coded. The plugin ships **no persona** — a repository opts into styling
  through manifest fields in its organization config.
- **Standalone runtime.** The plugin does not require a repo to be registered
  as an agent-worktrees harness. A session-start hook cheaply stamps a
  self-provisioning `agent-logger` binstub when hooks are available, and the
  operational skills include the same readiness path for hosts that only load
  skills.
- **Local state stays local.** The runtime home (`~/.agent-logger/`, or
  `$AGENT_LOGGER_HOME`) holds digests, sync locks, deployment metadata, and the
  optional chronicle SQLite DB. It must never be a cloud-synced folder.
- **Three deployment topologies** from one plugin — see
  [`docs/deployment-topologies.md`](docs/deployment-topologies.md).

## Status

**0.1.1-dev series — alpha.** Shipped and usable: the segmenter, session-sync
(5 targets + installers), the log-writer/ramp-up agents, the `log-session`,
`process-backlog`, `ramp-up-session`, and `session-sync-setup` skills, and the
optional background-chronicling core with its session-source + log-sink seams.

## Quick start

1. Enable the plugin in Copilot CLI. In a plain host, run `agent-logger version`;
   if the runtime was only stamped, the binstub self-provisions on first use and
   prints `::agent-provisioning::` while it builds.
2. For one-off logs, use the `log-session` skill for the current session or
   `process-backlog` for a local batch. Both hand a manifest to the neutral
   `session-log-writer` agent.
3. To archive raw sessions continuously, use `session-sync-setup`, choose a
   target in `~/.agent-logger/config.yaml`, verify with `session-sync doctor`,
   then install the 4-hourly timer (`scripts\install.ps1 install` on Windows or
   `scripts/install.sh install` on Linux/WSL).
4. For takeover, use `ramp-up-session`; it delegates the transcript-heavy read
   to the neutral `session-rampup` agent by default.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components + data flow
- [`docs/deployment-topologies.md`](docs/deployment-topologies.md) — local
  skill / local timer / fleet hub
- [`docs/manifest-contract.md`](docs/manifest-contract.md) — the log-writer
  manifest + closing-remark injection seam

## Configuration

Layered: built-in defaults → `$AGENT_LOGGER_HOME/config.yaml` → repo-local
organization config (`.agent-logger.yaml` / `.agent-logger.yml` /
`.config/agent-logger.yaml` / `.config/agent-logger.yml`, `log:` block only)
→ `AGENT_LOGGER_*` environment overrides. Inspect runtime config with:

```
agent-logger config
```

Inspect the repository organization fields exactly as they enter a writer
manifest with `agent-logger organization`.

Repository files use schema version 1 (an omitted version is accepted as v1
for compatibility) and may set only `log.root`, `log.path_template`,
`log.timezone`, `log.note_marker`, `log.template`, `log.narration_style`,
`log.exemplars`, and `log.closing_remark`. Invalid or unsafe configuration
fails explicitly instead of silently falling back.

## License

MIT
