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
  endpoint, with optional repo-allowlist scoping. Configure with the
  `session-sync-setup` skill; deploy as a 4-hourly Scheduled Task (Windows)
  or systemd user timer (Linux).
- **Background chronicling** (`agent_logger.chronicle`) — a scheduled,
  fleet-wide, single-elected daemon that turns the *synced* session corpus into
  objective **daily** logs landed in the routed harness repo. Two pluggable
  seams: **session-source** (settle gate + already-journaled skip + a
  continuation-segment reservation that fences a unit's inputs so a racing pass
  never double-logs) and **log-sink** `{router, profile, landing-policy}`
  (origin-repo routing, an `objective` default voice, and per-sink landing —
  direct-commit / squash-pr / a consumer-supplied merge-queue). Driven by
  `agent-dispatch`'s schedule management + single-producer job-lease (pinned to
  one machine, idempotent catch-up). CLI: `agent-logger chronicle status |
  scan | tick`.

## Design principles

- **Personality- and layout-neutral.** Voices, output path templates,
  repo-local Markdown skeletons, and machine naming are configuration, not
  hard-coded. The plugin ships **no persona** — a repository opts into styling
  through manifest fields in its organization config.
- **Local state stays local.** The runtime home (`~/.agent-logger/`, or
  `$AGENT_LOGGER_HOME`) holds digests (and, once the orchestrator ships, a
  SQLite state DB). It must never be a cloud-synced folder.
- **Three deployment topologies** from one plugin — see
  [`docs/deployment-topologies.md`](docs/deployment-topologies.md).

## Status

**v0.1.1 — alpha.** Shipped and usable: the segmenter, session-sync (5
targets + installers), the log-writer agent + `log-session` /
`process-backlog` skills, and the **background-chronicling** orchestrator
daemon (`agent_logger.chronicle`) with its session-source + log-sink seams.

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
`log.timezone`, `log.note_marker`, and `log.template`. Invalid or unsafe
configuration fails explicitly instead of silently falling back.

## License

MIT
