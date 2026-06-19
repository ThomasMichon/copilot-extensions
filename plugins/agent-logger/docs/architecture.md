# Architecture — agent-logger

`agent-logger` packages the **reusable ends** of a Copilot session-logging
pipeline as a Copilot CLI plugin. It deliberately stops short of any single
bespoke "process everything" service: it gives you the pieces and three ways
to run them.

```
   capture            transform                 present
┌────────────┐   ┌──────────────────┐   ┌────────────────────────┐
│ session-   │──▶│ segmenter        │──▶│ session-log-writer     │
│ sync       │   │ (collate /       │   │ agent (manifest-driven)│
│ raw → tgt  │   │  read-digest)    │   │ + log-session /        │
└────────────┘   └──────────────────┘   │   process-backlog      │
   targets:                              └────────────────────────┘
   local · onedrive · ssh · ssh-tunnel · ingest
```

## Components

### Segmenter (`agent_logger.segmenter`)

Collates a single Copilot session into context-ingestible Markdown digest
chunks. Three console scripts:

- `collate-session` — split a session (`~/.copilot/session-state/<id>`) into
  a context file + numbered transcript segments, written to a local store
  (`~/.agent-logger/session-digests/`) and/or an output dir.
- `read-session-digest` — read collated context / segments / manifests back.
- `prepare-session-log` — detect machine, generate a cutoff, and render a
  log path from a configurable template.

All machine/path/voice coupling is configuration — there is no facility
hostname, NAS path, or persona baked in.

### Session sync (`agent_logger.sync`)

A transport-blind engine that pushes raw session data to a configurable
**target**, under a `{machine}/` subpath, with optional repo-allowlist
filtering. Targets implement a small `Target` interface
(`push` / `prune` / `doctor` / `describe`):

| Target | Destination |
|--------|-------------|
| `local` | a dotfolder under `$HOME` (default) |
| `onedrive` | a subfolder under the OS-resolved OneDrive root |
| `ssh` / `ssh-tunnel` | rsync over SSH, optionally via a jump host |
| `ingest` | an rsync-daemon sink with an optional HTTP notify |

Deployed as a 4-hourly **Scheduled Task** (Windows) or **systemd user
timer** (Linux) via `scripts/install.ps1` / `install.sh`. Configure with the
`session-sync-setup` skill.

### Log writer (`agents/` + `skills/`)

One **voice-neutral** `session-log-writer` agent turns a manifest of 1..N
sessions into structured Markdown logs. Two skills drive it:

- `log-session` — interactive, the current session (manifest-of-one).
- `process-backlog` — local batch, a backlog of unlogged sessions.

The agent has **no personality of its own**. It produces a closing remark
only when a caller injects instructions through the manifest's
**closing-remark seam** — see [manifest-contract.md](manifest-contract.md).
A host repo (e.g. one with its own character voices) injects them; the
plugin never contains a persona.

## Configuration

Layered: built-in defaults → `$AGENT_LOGGER_HOME/config.yaml`
(default `~/.agent-logger/config.yaml`) → `AGENT_LOGGER_*` env overrides.
Inspect with `agent-logger config`. The home dir is **local-only** — never
place it inside a cloud-synced folder.

## Deployment topologies

See [deployment-topologies.md](deployment-topologies.md). In short: a local
skill (on demand), a local sync timer (self-serve one machine), or a fleet
hub (many machines sync to one shared folder).

## Coming soon

A scheduled **orchestrator daemon** — the automated "sessions → committed
logs" service (scan → digest → batch → spawn the writer agent → commit/merge)
with pluggable session-source and log-sink seams — is planned but not yet
shipped. Today the same end result is achievable manually via the
`process-backlog` skill; the daemon will automate it on a schedule.
