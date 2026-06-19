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
pwsh -File plugins/agent-logger/scripts/install.ps1 install
# Linux / WSL
bash plugins/agent-logger/scripts/install.sh install
```

Point it at a `local` dotfolder (default) or an `onedrive` subfolder. Then
log on demand with `process-backlog` against the archive, or against
`~/.copilot` directly.

Configure the target with the `session-sync-setup` skill, or edit
`~/.agent-logger/config.yaml`. Use `sync.repo_allowlist` to scope a sync to
specific repos — so one machine can run several syncs for different repo
sets (e.g. one to a NAS, one to OneDrive).

## 3. Fleet hub (many machines, one shared folder)

Every machine runs **session-sync** pointed at a **shared folder** that
mirrors a common layout (`<root>/<machine>/session-state/<id>/`):

- An `onedrive` subfolder (a NAS-free aggregation point — many machines write
  to the same OneDrive folder), or
- an `ssh` / `ingest` target to a server you control.

Because the layout is uniform, a single machine with access to that folder
can process the whole fleet's sessions into logs. Today that processing is
done by running the `process-backlog` skill against the shared root; the
automated **orchestrator daemon** that does it on a schedule is *Coming
Soon* (see [architecture.md](architecture.md)).

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
`(Copilot)/sessions/<machine>/...` for every machine and writes logs.
