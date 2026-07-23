# agent-logger

Reusable Copilot CLI **session logging** for the GitHub Copilot CLI,
packaged as a copilot-extensions plugin. It factors the reusable ends of a
session-to-log pipeline out of any single bespoke service:

- **Segmenter** — collate one Copilot session into context-ingestible
  Markdown digest chunks (`collate-session`, `read-session-digest`,
  `prepare-session-log`).
- **Log writer** — one voice-neutral `session-log-writer` agent that turns a
  manifest of 1..N sessions into structured Markdown logs, plus the
  `log-session` (interactive) and `process-backlog` (local batch) skills
  that drive it. Personality is never built in; a host repo injects a
  closing remark through the manifest's closing-remark seam
  (see [`docs/manifest-contract.md`](docs/manifest-contract.md)).
- **session-sync** — push raw session data to a configurable target: a
  `local` dotfolder, `onedrive`, `ssh`/`ssh-tunnel`, or a generic `ingest`
  endpoint, with optional repo-allowlist scoping. Configure with the
  `session-sync-setup` skill; deploy as a 4-hourly Scheduled Task (Windows)
  or systemd user timer (Linux).
- **Orchestrator** *(Coming Soon)* — a scheduled daemon that crunches a
  backlog of sessions into committed logs automatically, with pluggable
  session-source and log-sink seams and an HTTP read API. Not yet shipped —
  today the same result is achievable by hand via the `process-backlog`
  skill.

## Design principles

- **Personality- and layout-neutral.** Voices, output path templates,
  repo-local Markdown skeletons, and machine naming are configuration, not
  hard-coded. The plugin ships **no persona** — a host repo injects a closing
  remark via the manifest seam.
- **Local state stays local.** The runtime home (`~/.agent-logger/`, or
  `$AGENT_LOGGER_HOME`) holds digests (and, once the orchestrator ships, a
  SQLite state DB). It must never be a cloud-synced folder.
- **Three deployment topologies** from one plugin — see
  [`docs/deployment-topologies.md`](docs/deployment-topologies.md).

## Status

**v0.1.0 — alpha.** Shipped and usable: the segmenter, session-sync (5
targets + installers), and the log-writer agent + `log-session` /
`process-backlog` skills. The automated **orchestrator daemon** is *Coming
Soon*.

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
→ `AGENT_LOGGER_*` environment overrides. Inspect the resolved config with:

```
agent-logger config
```

## License

MIT
