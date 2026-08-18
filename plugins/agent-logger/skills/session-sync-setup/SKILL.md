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

> **Before you start — readiness (self-provisioning, no agent-worktrees required).**
> agent-logger provisions its own runtime on first use and works standalone in any
> host (CLI, Copilot app, cloud agent). If `agent-logger` is not on PATH, deploy
> this plugin's own binstub first; it then self-provisions on first call.
>
> - Windows:
>   `pwsh -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.copilot\installed-plugins\copilot-extensions\agent-logger\scripts\install.ps1" stamp`
> - Linux/WSL:
>   `bash "$(ls ~/.copilot/installed-plugins/*/agent-logger/scripts/install.sh | head -1)" stamp`
>
> Then run `agent-logger version` once. The first call provisions the runtime
> (~30–120s; watch for `::agent-provisioning::`) and deploys `session-sync` and
> the other auxiliary tools. If it reports a provisioning failure (e.g. missing
> uv / network), surface the exact message — don't improvise a toolchain install.

`session-sync` pushes raw Copilot session data from `~/.copilot` to a
configurable **target**, under a `{machine}/` subpath, so any consumer sees
the same layout. Configuration lives at `~/.agent-logger/config.yaml`
(override the home dir with `$AGENT_LOGGER_HOME`).

> **Keep the home dir out of any cloud-synced folder.** `~/.agent-logger`
> holds the sync lock, deployment metadata, and the optional chronicle SQLite
> DB. The *target* may be a synced folder; the *home* must not be.

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

Optional sync controls:

- `sync.source` — source state root; defaults to `~/.copilot`.
- `sync.repo_allowlist` — include only sessions whose workspace/origin matches.
- `sync.repo_denylist` — exclude matching repos; with an empty allowlist this
  becomes "sync everything except these repos".
- `sync.repo_allowlist_fail_closed` — with an allowlist, exclude sessions that
  cannot be classified instead of keeping metadata-less sessions.
- `sync.harness_repos` — repo names used to stamp each session's `origin.json`
  sidecar for downstream chronicle routing.
- `sync.notify` — target-independent best-effort HTTP `POST` after any
  successful push (`url`, optional `bearer_token_file`, `timeout`). Notify
  failures are logged only in verbose runs and do not fail the sync.

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
  narration_style: null
  exemplars: null
  closing_remark: "End with one concise takeaway."
```

Repo-local config cannot change `sync:` targets; those remain in
`~/.agent-logger/config.yaml`. Only `root`, `path_template`, `timezone`,
`note_marker`, `template`, `narration_style`, `exemplars`, and
`closing_remark` are accepted under `log:`. Invalid YAML, unknown
fields/placeholders, unsupported schema versions, unsafe paths, and invalid
timezones fail explicitly. Run `agent-logger organization` to inspect the
manifest-ready result.

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

## Compaction (cold-session archival)

Very old, inactive sessions are compressed into per-session `<id>.tar.gz`
bundles to reclaim space (`events.jsonl` is ~95% of the bytes and compresses
~5x). Opt in under `sync.compact` (see [`references/config.yaml`](references/config.yaml)):

```yaml
sync:
  compact:
    enabled: true
    codec: targz            # stdlib tar.gz; pluggable (zstd later)
    min_age_days: 30
    require_untracked_worktree: true
    archive_root: null      # null => <home>/archived-sessions
```

A session is *cold* when it is at least `min_age_days` old (from
`workspace.yaml` timestamps, never filesystem mtime) and -- when
`require_untracked_worktree` -- it does **not** belong to a *tracked* worktree:
one that `agent-worktrees list` still renders in the picker (pruning a worktree
deletes its directory and `.<repo>` registry entry together, dropping it from
that set; the fallback when agent-worktrees is absent is on-disk existence of
the worktree dir, reliable for the same reason). Because the picker only renders
tracked worktrees and compaction only archives non-tracked ones, an archived
session is never one the picker needs -- no picker archive-awareness required.
Compaction also honors the **sync repo scope** (`repo_allowlist`/`repo_denylist`):
only sessions sync itself would publish are archived, so the archive store (which
Pair B pushes to the hub wholesale) never leaks a repo the allowlist excludes.
Archives keep uncompressed `workspace.yaml`/`origin.json` sidecars beside the
bundle so listing/selection never decompresses; readers (`ramp-up-session`,
`collate-session`) resolve and read archived sessions transparently.

Two-pair model -- the compressed store syncs to the hub alongside the
uncompressed tree. **When `compact.enabled`, the scheduled `session-sync run`
performs the whole lifecycle itself** (no separate command needed):

```
session-sync run           # scheduled service: on-device compact + reclaim,
                           #   push (Pair A), push archive store to
                           #   {machine}/archived/ (Pair B), compact the hub-only
                           #   backlog, and reconcile away uncompressed duplicates
session-sync compact       # (manual) on-device compaction only
session-sync compact-hub   # (manual) hub backlog compaction + reconcile only
```

Both `compact`/`compact-hub` remain for manual/`--dry-run` use, but the deployed
4-hourly `session-sync run --prune` already drives everything from config.

Both `compact` and `compact-hub` are idempotent and take the sync lock, so they
never race the scheduled push. Add `--dry-run` to preview.

## Troubleshoot

- **Runtime not ready:** run `agent-logger version` and keep the exact
  self-provisioning error if it fails. The plugin owns uv acquisition on
  Linux/WSL; on Windows, a missing signed Python may be reported by the
  installer.
- **Target unreachable:** run `session-sync doctor`; fix the first `[FAIL]`
  before retrying `session-sync run --dry-run --verbose`.
- **Scheduled sync not firing:** use the plugin installer status command first:
  `pwsh -File plugins\agent-logger\scripts\install.ps1 status` or
  `bash plugins/agent-logger/scripts/install.sh status`. On Windows the task is
  `Agent Logger Session Sync`; on Linux/WSL inspect
  `systemctl --user status agent-logger-sync.timer agent-logger-sync.service`.
- **Expected fail-loud behavior:** a missing source or failed push returns exit
  code 1 and writes the reason to stderr. A held sync lock exits successfully
  with "another sync holds the lock; skipping". HTTP notify failures are
  best-effort and never fail a push.

## Schedule (deployed service)

Installed via the plugin's installers, which register a 4-hourly run of
`session-sync run --prune`:

- **Windows:** `pwsh -File plugins\agent-logger\scripts\install.ps1 install`
  (Scheduled Task).
- **Linux/WSL:** `bash plugins/agent-logger/scripts/install.sh install`
  (systemd user timer).

Set `AGENT_LOGGER_SYNC_DISABLED=1` to make a run a no-op (e.g. in automation
contexts).
