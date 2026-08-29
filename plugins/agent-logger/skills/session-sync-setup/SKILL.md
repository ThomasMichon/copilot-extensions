---
name: session-sync-setup
description: >
  Configure agent-logger's session-sync target -- where raw Copilot session
  data is pushed (local dotfolder, OneDrive subfolder, SSH, or an rsync/HTTP
  ingest sink), plus validated provider rescues through a single-writer
  compare-and-set local target. Use this skill when
  the user wants to set up, change, or troubleshoot session syncing. Trigger
  phrases include: - 'set up session sync' - 'sync my sessions' - 'ingest
  rescued sessions' - 'rescue-push' - 'change the sync target' - 'sync to
  OneDrive' - 'sync sessions over SSH' - 'session-sync config' - 'where do my
  sessions go'
---

# Session Sync Setup

> **Before you start — payload-local readiness.**
> Use command ids `agent-logger` and `session-sync` from the agent-logger
> session command catalog. Invoke each exact `argv`; never search `PATH`, scan
> installed marketplaces, or invoke the legacy venv path. The first call
> provisions the shared runtime (~30–120s; watch for
> `::agent-provisioning::`). Surface an exact failure instead of improvising a
> toolchain install.
> If either catalog entry is absent or unavailable, fail closed and ask the
> operator to select this payload explicitly through the host's plugin
> management surface.

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
only a `log:` block. The catalog's `prepare-session-log` command with `--json`
layers that block over the
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
timezones fail explicitly. Run
`<agent-logger catalog "agent-logger" argv prefix> organization` to inspect the
manifest-ready result.

## Verify

```
<agent-logger catalog "session-sync" argv prefix> status
<agent-logger catalog "session-sync" argv prefix> doctor
<agent-logger catalog "session-sync" argv prefix> run --dry-run --verbose
<agent-logger catalog "session-sync" argv prefix> run --prune
```

`doctor` reports per-check `[ok]`/`[FAIL]` lines. For `onedrive`, a `FAIL`
on "OneDrive root resolved" means no `OneDrive*` environment variable and no
`~/OneDrive` -- set `sync.targets.onedrive.root` explicitly.

## Provider rescue ingestion

Use a configured single-writer `local` target to publish verified rescue
captures that already exist in a provider-owned host state directory. Rescue
publication rejects OneDrive replicas and push-only targets until they
implement the same destination-side compare-and-set contract:

```
<agent-logger catalog "session-sync" argv[0]> rescue-push \
  --rescue-root <provider-state>/rescues \
  --provider agent-containers \
  --target-prefix container \
  --dry-run --verbose
```

The destination filesystem must honor advisory file locks across every writer
that can publish to the same venue namespace. Use one writer or a genuinely
shared filesystem with working locks; eventually-consistent replicas such as
OneDrive are intentionally rejected for rescue publication.

Repeat `--rescue-root` to scan more than one provider state root. Remove
`--dry-run` to publish. Each accepted session lands under a stable flat venue
key such as `container-worker-1`, not under an instance ID. The adapter validates
the provider metadata contract and every selected member's size/hash, accepts
only independently complete sessions from partial captures, rejects missing or
invalid event streams (strict UTF-8, one JSON object per nonblank JSONL line),
applies the normal exact allow/deny/fail-closed policy to
provider-recorded repository assignment, and writes a generic
`provenance/<session-id>.json` beside the canonical `session-state/` tree.
Rescued `origin.json` is retained as `rescued-origin.json` evidence only; it
never controls routing.

The adapter keeps its idempotence checkpoint and short-lived projection under
`$AGENT_LOGGER_HOME/rescue-sync/`. It does not modify the rescue store, restore a
session, write into a container, or expose the provider tree directly to a
target. A normal second run idempotently revalidates retained captures so it
can repair destination loss; a late older capture cannot rewind the destination.
Verbose output lists accepted/rejected entries,
and the final line always reports explicit accepted, skipped, and rejected
counts. Venue failures do not stop sibling venues, but any target failure keeps
the final exit nonzero. The compact checkpoint preserves capture-ID tombstones
and per-session high-water records across provider retention; it refuses an
oversized rewrite before replacing its last readable state.

Ordering is capture timestamp then capture ID. `--verbose` reports why an entry
was older, revalidated, rejected, or accepted. To intentionally reset ordering,
remove `$AGENT_LOGGER_HOME/rescue-sync/checkpoint.json`; this does not remove
published evidence. Renaming a provider container creates a new venue identity
under the current name-based contract. Rescue-venue destination pruning is not
yet wired to `sync.retention_days`.

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
one that the agent-worktrees `list` action still renders in the picker (pruning a worktree
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
uncompressed tree. **When `compact.enabled`, the scheduled session-sync `run`
performs the whole lifecycle itself** (no separate command needed):

```
<agent-logger catalog "session-sync" argv prefix> run
<agent-logger catalog "session-sync" argv prefix> compact
<agent-logger catalog "session-sync" argv prefix> compact-hub
```

Both `compact`/`compact-hub` remain for manual/`--dry-run` use, but the deployed
4-hourly management launcher invokes session-sync with `run --prune` from the
installed runtime.

Both `compact` and `compact-hub` are idempotent and take the sync lock, so they
never race the scheduled push. Add `--dry-run` to preview.

## Troubleshoot

- **Runtime not ready:** run
  `<agent-logger catalog "agent-logger" argv prefix> version` and keep the exact
  self-provisioning error if it fails. The plugin owns uv acquisition on
  Linux/WSL; on Windows, a missing signed Python may be reported by the
  installer.
- **Target unreachable:** run
  `<agent-logger catalog "session-sync" argv prefix> doctor`; fix the first `[FAIL]`
  before retrying
  `<agent-logger catalog "session-sync" argv prefix> run --dry-run --verbose`.
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
the session-sync `run --prune` management action:

This timer/task launch is an explicit service-management boundary; session
catalogs do not replace it in this phase.

- **Windows:** `pwsh -File plugins\agent-logger\scripts\install.ps1 install`
  (Scheduled Task).
- **Linux/WSL:** `bash plugins/agent-logger/scripts/install.sh install`
  (systemd user timer).

Set `AGENT_LOGGER_SYNC_DISABLED=1` to make a run a no-op (e.g. in automation
contexts).
