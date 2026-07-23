---
name: session-sync-setup
description: >
  Configure agent-logger's session-sync target -- where raw Copilot session
  data is pushed (local dotfolder, OneDrive subfolder, SSH, or an rsync/HTTP
  ingest sink). Use this skill when the user wants to set up, change, or
  troubleshoot session syncing. Trigger phrases include: - 'set up session
  sync' - 'sync my sessions' - 'change the sync target' - 'sync to OneDrive'
  - 'sync sessions over SSH' - 'session-sync config' - 'where do my sessions
  go'
---

# Session Sync Setup

`session-sync` pushes raw Copilot session data from `~/.copilot` to a
configurable **target**, under a `{machine}/` subpath, so any consumer sees
the same layout. Configuration lives at `~/.agent-logger/config.yaml`
(override the home dir with `$AGENT_LOGGER_HOME`).

> **Keep the home dir out of any cloud-synced folder.** `~/.agent-logger`
> holds the sync lock and (in later phases) a SQLite state DB. The *target*
> may be a synced folder; the *home* must not be.

## Targets

| Target | Use case | Required options |
|--------|----------|------------------|
| `local` (default) | Self-serve one machine; zero dependencies | `path` (optional; default `~/.agent-logger/sessions`) |
| `onedrive` | Fleet hub without a NAS -- many machines sync to one OneDrive folder, one machine crunches | `subfolder` (default `Apps/agent-logger/sessions`) |
| `ssh` | Push to an arbitrary host you control | `host`, `remote_path`; optional `proxy_jump` |
| `ssh-tunnel` | Same as `ssh`, routed through a jump host | `host`, `remote_path`, `tunnel_host` |
| `ingest` | Push to a processing service's rsync-daemon sink | `url` (`rsync://...` or `host::module/path`); optional `password_file`, `notify_url` |

`ssh`, `ssh-tunnel`, and `ingest` require `rsync` (and `ssh`) on PATH.

## Configure

Edit `~/.agent-logger/config.yaml`. Copy the full annotated example showing
every target, [`references/config.yaml`](references/config.yaml), and keep the
one block you need. The local default at a glance:

```yaml
sync:
  target: local             # local | onedrive | ssh | ssh-tunnel | ingest
  retention_days: 90        # or "infinite" to keep everything
  targets:
    local:
      path: ~/SessionArchive
```

See [`references/config.yaml`](references/config.yaml) for the `onedrive`,
`ssh`, `ssh-tunnel`, and `ingest` target blocks.

## Repo-local log organization

Session-sync is machine-local, but log organization can be repo-local. A
repository may commit `.agent-logger.yaml` (or `.agent-logger.yml`,
`.config/agent-logger.yaml`, `.config/agent-logger.yml`) at its git root with
only a `log:` block. `prepare-session-log --json` layers that block over the
machine-local config and passes it through the manifest:

```yaml
schema_version: 1
log:
  root: .
  path_template: "logs/{year}/{month}.{day} {title}.md"
  template: |
    # {title}

    **Date:** {date}
    **Branch(es):** {branches}
    **PR(s):** {prs}

    ## Summary

    {summary}

    ## Key Changes

    {key_changes}

    ## Commits

    {commits}

    ## Open Items

    {open_items}
```

Repo-local config cannot change `sync:` targets; those remain in
`~/.agent-logger/config.yaml`. Only `root`, `path_template`, `timezone`,
`note_marker`, and `template` are accepted under `log:`. Invalid YAML, unknown
fields/placeholders, unsupported schema versions, unsafe paths, and invalid
timezones fail explicitly.

## Verify

```
session-sync status     # show resolved machine, source, target, retention
session-sync doctor     # check the target is reachable/usable (no transfer)
session-sync run --dry-run --verbose
session-sync run --prune
```

`doctor` reports per-check `[ok]`/`[FAIL]` lines. For `onedrive`, a `FAIL`
on "OneDrive root resolved" means no `OneDrive*` environment variable and no
`~/OneDrive` -- set `sync.targets.onedrive.root` explicitly.

## Schedule (deployed service)

Installed via the plugin's installers, which register a 4-hourly run of
`session-sync run --prune`:

- **Windows:** `pwsh -File plugins/agent-logger/scripts/install.ps1 install`
  (Scheduled Task).
- **Linux/WSL:** `bash plugins/agent-logger/scripts/install.sh install`
  (systemd user timer).

Set `AGENT_LOGGER_SYNC_DISABLED=1` to make a run a no-op (e.g. in automation
contexts).
