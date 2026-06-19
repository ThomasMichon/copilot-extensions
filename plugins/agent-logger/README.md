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
  endpoint. Configure with the `session-sync-setup` skill; deploy as a
  4-hourly Scheduled Task (Windows) or systemd user timer (Linux).
- **Orchestrator** *(optional, later phase)* — a scheduled daemon that
  crunches a backlog of sessions into committed logs, with pluggable
  session-source and log-sink seams. Exposes an HTTP read API so a richer
  UX service can consume it as a data source.

## Design principles

- **Personality- and layout-neutral.** Voices, output path templates, and
  machine naming are configuration, not hard-coded. The default ships no
  persona.
- **Local state stays local.** The runtime home (`~/.agent-logger/`, or
  `$AGENT_LOGGER_HOME`) holds digests and — in later phases — a SQLite
  state DB. It must never be a cloud-synced folder.
- **Three deployment topologies** from one plugin: local skill (on demand),
  local daemon (self-serve one machine), and fleet hub (one processor for
  many machines via a shared sync target).

## Status

Early scaffolding. See
[`docs/plans/agent-logger-plugin.md`](https://github.com/ThomasMichon/copilot-extensions)
in the aperture-labs design repo for the full phased plan.

## Configuration

Layered: built-in defaults → `$AGENT_LOGGER_HOME/config.yaml` →
`AGENT_LOGGER_*` environment overrides. Inspect the resolved config with:

```
agent-logger config
```

## License

MIT
