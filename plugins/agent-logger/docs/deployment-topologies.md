# Deployment topologies

The same plugin supports three escalating levels of automation. They differ
only in *who runs the work and how often* — the per-session logging is
identical.

## 1. Local skill (on demand, no service)

Run a skill to write logs by hand:

- `log-session` — log the **current** session now.
- `process-backlog` — work through a **backlog** of unlogged sessions on
  this machine.

No sync, no timer, no daemon. Good for a laptop that wants logs occasionally.

## 2. Local sync timer (self-serve one machine)

Install the **session-sync** timer so this machine's raw sessions are
continuously archived to a target of your choice:

```
# Windows
pwsh -File plugins\agent-logger\scripts\install.ps1 install
# Linux / WSL
bash plugins/agent-logger/scripts/install.sh install
```

Point it at a `local` dotfolder (default) or an `onedrive` subfolder. Then
log on demand with `process-backlog` against the archive, or against
`~/.copilot` directly.

Configure the target with the `session-sync-setup` skill, or edit
`~/.agent-logger/config.yaml`. The same engine also supports `ssh`,
`ssh-tunnel`, and `ingest` targets. Use `sync.repo_allowlist`,
`sync.repo_denylist`, and `sync.repo_allowlist_fail_closed` when a machine
should archive only particular repos (or everything except particular repos).

## 3. Fleet hub (many machines, one shared folder)

Every machine runs **session-sync** pointed at a **shared folder** or service
that mirrors a common layout (`<root>/<machine>/session-state/<id>/`):

- An `onedrive` subfolder (a NAS-free aggregation point — many machines write
  to the same OneDrive folder), or
- an `ssh` / `ingest` target to a server you control.

Because the layout is uniform, a single machine with access to that folder can
process the whole fleet's sessions into logs. Today there are two ground-truth
paths:

- run `process-backlog` against the shared root for local/batch logging; or
- configure the optional chronicle core and run `agent-logger chronicle tick`
  from an external scheduler/runner. The built-in tick writes daily digest
  manifests by default; the runner is responsible for spawning the
  read-only `session-log-writer` renderer, validating and persisting its render
  bundle under the configured output root, and landing those files.

### Example: OneDrive hub

```yaml
# ~/.agent-logger/config.yaml on each machine
sync:
  target: onedrive
  repo_allowlist: [my-project]      # optional; omit to sync all
  targets:
    onedrive:
      subfolder: "(Copilot)/sessions"
```

This yields, in every machine's OneDrive:

```
OneDrive/(Copilot)/sessions/<machine>/
  ├─ session-state/<id>/  (events.jsonl, workspace.yaml, checkpoints, ...)
  └─ sync-meta.json
```

A hub machine that has the folder synced locally then reads
`(Copilot)/sessions/<machine>/...` for every machine, persists validated render
bundles, and lands the resulting logs.

The same hub can report bounded fleet health without reading task logs:

```
session-sync health --fleet --max-age-hours 12 --partial-threshold 3 --json
```

Fresh complete machines are healthy. A fresh partial result remains degraded
until it reaches the repeated-partial threshold; stale, missing, unreadable, or
repeatedly partial machines are unhealthy and make the command exit nonzero.
Repeat `--machine NAME` to restrict alerting to the active fleet while retaining
historical or ephemeral machine data in the shared corpus.
