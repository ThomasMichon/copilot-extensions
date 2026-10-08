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
the same layout. Configuration lives at `~/.agent-logger/config.yaml` <!-- marketplace-isolation: allow deployed-runtime-diagnostics -->
(override the home dir with `$AGENT_LOGGER_HOME`).

> **Keep the home dir out of any cloud-synced folder.** `~/.agent-logger`
> holds the sync lock, deployment metadata, and the optional chronicle SQLite
> DB. The *target* may be a synced folder; the *home* must not be.

## Targets

| Target | Use case | Required options |
|--------|----------|------------------|
| `local` (default) | Self-serve one machine; zero dependencies | `path` (optional; default `~/.agent-logger/sessions`) <!-- marketplace-isolation: allow deployed-runtime-diagnostics --> |
| `onedrive` | Fleet hub without a NAS -- many machines sync to one OneDrive folder, one machine crunches | `subfolder` (default `Apps/agent-logger/sessions`) |
| `ssh` | Push to an arbitrary host you control | `host`, `remote_path`; optional `proxy_jump` |
| `ssh-tunnel` | Same as `ssh`, routed through a jump host | `host`, `remote_path`, `tunnel_host` |
| `ingest` | Push to a processing service's rsync-daemon sink | `url` (`rsync://...` or `host::module/path`); optional `password_file`, `notify_url` |

`ssh`, `ssh-tunnel`, and `ingest` require `rsync` (and, for `ssh`/`ssh-tunnel`,
`ssh`) on `PATH`.

> **Windows:** there is no native rsync distribution. When a working WSL
> distro is reachable (`rsync` on its `PATH` for every target; `ssh` too for
> `ssh`/`ssh-tunnel`), these targets automatically run the whole rsync
> invocation wrapped in `wsl.exe -e` (direct exec, not `--`, which silently
> drops positional arguments when re-shelled through the default distro
> shell), converting the local source path through WSL's own `wslpath`. An
> `ingest` `password_file` is **staged as a fresh, owner-only-permission copy
> inside WSL's own filesystem** rather than just path-converted: a Windows
> file reached through DrvFS (the `/mnt/c/...` bridge) is normally exposed as
> group/world-readable, which rsync refuses for `--password-file`. This is
> preferred over a native MSYS2/Cygwin `rsync.exe` on `PATH`, which hits
> cross-runtime bugs (see `targets/base.py`'s `wsl_rsync_available()`
> docstring) -- that native path remains only as a fallback when no WSL
> distro is usable. **WSL has its own user, `~/.ssh` config, and SSH agent,
> separate from Windows' own OpenSSH** -- an `ssh`/`ssh-tunnel` host alias
> and key must be set up *inside* WSL (`wsl -- ssh <host>` should succeed
> non-interactively) for this to work, not just in the Windows OpenSSH
> config. Run `<agent-logger catalog "session-sync" argv[0]> doctor` to see
> which runtime (WSL or native) was selected and whether rsync/ssh were
> actually found there.

## Configure

Edit `~/.agent-logger/config.yaml`. Copy the full annotated example showing <!-- marketplace-isolation: allow deployed-runtime-diagnostics -->
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
- `sync.require_repo_opt_in` — an additional, opt-in activation gate (default
  `false`, fully backward compatible). When `true`, a session only syncs if
  the repo it was matched against (or that repo's bound knowledge repo, see
  below) *also* durably declares itself in — mirroring the `agent-index`
  repo-owned activation convention, so being `enabledPlugins`-enabled on a
  machine never by itself causes sessions to sync.
- `sync.notify` — target-independent best-effort HTTP `POST` after any
  successful push (`url`, optional `bearer_token_file`, `timeout`). Notify
  failures are logged only in verbose runs and do not fail the sync.

### Repo-owned sync opt-in (`sync.require_repo_opt_in`)

With `sync.require_repo_opt_in: true` set in `~/.agent-logger/config.yaml`, a <!-- marketplace-isolation: allow deployed-runtime-diagnostics -->
repo commits its own activation declaration at
`.copilot-extensions/agent-logger/config.yaml` (legacy:
`.agent-logger/config.yaml`):

```yaml
sync:
  opt_in: true    # or false, to explicitly stay out
```

Resolution per matched session, filesystem-backed against the session's own
recorded `git_root`/`cwd` (never guessed):

1. The matched repo's own config wins when present and it states an opinion
   (`opt_in: true` or `false`).
2. A repo with **no** opinion (no config, or a config silent on `opt_in`)
   that requires external state (bound to a knowledge repo, per
   `agent-worktrees`) forwards the check to that knowledge repo's own config.
3. Anything still unresolved — no config anywhere in the chain, no bound
   knowledge repo, or the recorded path no longer exists on this machine —
   **fails closed** (excluded). Being enabled everywhere never implies
   syncing; only an explicit, checked-in `opt_in: true` does.

This is an *additional* requirement layered on top of
`repo_allowlist`/`repo_denylist`, not a replacement — both still apply.

> **Enabling the gate does not retroactively purge prior compaction
> archives.** `sync.compact` (below) already applies this same gate to which
> *new* cold sessions it selects, but sessions compacted into
> `sync.compact.archive_root` **before** the gate was turned on (or while an
> opted-out repo's cutover was still pending) remain on disk and are still
> shipped wholesale by `compact-hub`. If you enable `require_repo_opt_in`
> after compaction has already run, manually prune
> `sync.compact.archive_root` for any repo that stays opted out, or clear it
> and let compaction rebuild it under the new policy.

> **`rescue-push` fails closed entirely when this gate is on.** A rescued
> session (see [Provider rescue ingestion](#provider-rescue-ingestion) below)
> carries only a provider-reported repo *name*, never a resolvable local
> path -- there is nothing on disk for the opt-in check to read. Rather than
> silently ignore `require_repo_opt_in` for rescue publication, it is
> intentionally treated as unresolvable and every rescued session is
> rejected while the gate is enabled, regardless of `repo_allowlist`.

## Repo-local log organization

Session-sync is machine-local, but log organization can be repo-local. A
repository may commit `.agent-logger.yaml` (or `.agent-logger.yml`,
`.config/agent-logger.yaml`, `.config/agent-logger.yml`) at its git root with
a `log:` block, plus (as of schema v3) a single `sync.local_path`. The
catalog's `prepare-session-log` command with `--json` layers that block over
the machine-local config and passes it through the manifest:

```yaml
schema_version: 3
sync:
  local_path: /mnt/nas/Lake/Copilot/sessions
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

Repo-local config cannot change which `sync:` target is active, credentials,
or machine identity -- those remain in `~/.agent-logger/config.yaml`. The <!-- marketplace-isolation: allow deployed-runtime-diagnostics -->
one exception, `sync.local_path` (schema v3+), exists because that value is
genuinely the same absolute path for every machine in the fleet (a shared NAS
mount) rather than a per-machine choice. Under `log:`, only `root`,
`path_template`, `timezone`, `note_marker`, `template`, `narration_style`,
`exemplars`, and `closing_remark` are accepted. Invalid YAML, unknown
fields/placeholders, unsupported schema versions, and invalid timezones fail
explicitly.

**Platform-neutral syntax (non-empty, no `~`, absolute on *some* platform's
syntax, no `..`) still fails explicitly for `sync.local_path` on every
target.** Only the final *host-native* absoluteness check -- whether the
value is absolute on *this specific* platform -- is deferred until a
machine's own resolved `sync.target` is known: a mixed Windows/POSIX fleet
has no single `local_path` string that's a native absolute path on every
platform. On a machine targeting `local`, a value that's foreign to this
platform (valid syntax elsewhere, but not here) still fails the config load
exactly as before; on any other target (`ssh`, `onedrive`, `ingest`, ...) a
foreign-but-otherwise-valid value is inapplicable there and is silently
dropped back to whatever `sync.targets.local.path` that machine's own
`~/.agent-logger/config.yaml` set (or the default), rather than failing the
whole load over a value it never consumes.

Repo-local config of any kind is honored only for a checkout that is both a
project registered with `agent-worktrees` and currently on that project's
registered default branch -- an unregistered clone or a feature/PR branch
gets no repo-local config at all, silently. See
`plugins/agent-logger/docs/manifest-contract.md`'s trust-gate section for the
full mechanics. Run
`<agent-logger catalog "agent-logger" argv[0]> organization` to inspect the
manifest-ready result.

## Verify

```
<agent-logger catalog "session-sync" argv[0]> status
<agent-logger catalog "session-sync" argv[0]> doctor
<agent-logger catalog "session-sync" argv[0]> run --dry-run --verbose
<agent-logger catalog "session-sync" argv[0]> run --prune
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
<agent-logger catalog "session-sync" argv[0]> run
<agent-logger catalog "session-sync" argv[0]> compact
<agent-logger catalog "session-sync" argv[0]> compact-hub
```

Both `compact`/`compact-hub` remain for manual/`--dry-run` use, but the deployed
4-hourly management launcher invokes session-sync with `run --prune` from the
installed runtime.

Both `compact` and `compact-hub` are idempotent and take the sync lock, so they
never race the scheduled push. Add `--dry-run` to preview.

## Process-log evidence publication

Opt-in publication of process-log evidence (`process-*.log[.gz]`/`.zip`,
under `<sync.source>/logs/`) alongside session-state, for the accounting
pipeline downstream of `agent_logger.process_logs`. Opt in under
`sync.process_logs` (see [`references/config.yaml`](references/config.yaml)):

```yaml
sync:
  process_logs:
    enabled: true
```

Two deliberate scope limits:

- **Filesystem targets only** (`local`/`onedrive`). SSH and ingest targets
  do not yet support this -- `session-sync` logs that the capability is
  unsupported for those and continues the rest of the pass normally.
- **Unfiltered passes only.** A process log can span several repos/
  worktrees across one CLI process's lifetime, so it is not scoped per-repo
  the way session-state is -- a narrowed pass (`repo_allowlist`/
  `repo_denylist`/`require_repo_opt_in` above) **skips** process-log
  publication entirely rather than attempting to filter it.

See [`process-log-evidence.md`](../../docs/process-log-evidence.md#sync-publication-session-sync)
for the full design and current gaps (scheduled compaction and SSH/ingest
target support remain outstanding).

## Change tracking (incremental sync)

A large corpus (thousands of sessions) makes every scheduled push expensive if
it has to re-walk/re-diff the whole tree through a slow transport bridge (e.g.
WSL's DrvFS view of a Windows path) -- expensive enough to exceed the engine's
own rsync subprocess timeout. **Change tracking is on by default** to fix
this: a local, stat-only SQLite record (`<home>/sync-state.db`, or
per-tenant `sync-state-<tenant_id>.db`) remembers each session's last-synced
content signature, so a routine run only pushes sessions that actually
changed -- and skips the push entirely when nothing did.

```yaml
sync:
  change_tracking:
    enabled: true                  # false restores pre-feature behavior
    full_sync_interval_hours: 24   # periodic full reconciliation cadence
    batch_size: 100                # sessions per push call during a full pass
    db_path: null                  # null => <home>/sync-state.db
```

A **full reconciliation** pass (first run ever, the periodic cadence, or an
explicit `run --full`) still happens, but **segmented**: sessions are pushed
in `batch_size`-sized groups so one invocation never has to walk the entire
corpus, and each batch's signatures are recorded as it lands. Every
tracker-driven push (incremental or segmented-full) passes `batch_mode=True`
to the target whenever the repo scope itself is unfiltered -- this still
transfers the global `session-store.db` index and defers (rather than
hard-fails on) a locked in-use file, exactly like a legacy unfiltered push
would, even though any one call only carries a transport-size slice of
sessions. A genuine repo-allowlist/denylist filter is unaffected: it still
goes through the atomic rescue/replace path with the index excluded.

> **Known tradeoff: `--delete-excluded` no longer fires.** A full/segmented
> pass always narrows to an explicit (even if complete) set of session ids --
> it never passes `include_sessions=None` to the target. `--delete-excluded`
> only applies when `include_sessions is None`, so it is no longer triggered
> by any `run` invocation once change tracking is enabled (the default).
> Plain `--delete` still cleans up content within directories rsync actually
> visits; what's lost is the narrower case of previously-synced detritus that
> became newly excluded by a repo-scope change. Destination-side cleanup of
> sessions that no longer exist locally at all is unaffected -- that's
> `Target.prune`'s job (age-based, run via `run --prune`), not deletion
> semantics on the push itself.

**Doctor / drift realignment.** The tracker is a local optimization, never a
second source of truth -- the destination is always authoritative. If the
local db ever drifts (corrupted, stale after manual destination surgery, or
just suspect), realign it from scratch:

```
<agent-logger catalog "session-sync" argv[0]> run --full
```

This forces a full, segmented reconciliation against the real destination
regardless of the periodic cadence and rebuilds every signature from what
that reconciliation actually pushed. There is no separate `reset` CLI verb;
deleting the tracker db file (`sync-state*.db`) and re-running `run --full`
achieves the same from-scratch rebuild if the db itself is suspect.

A full reconciliation also triggers **automatically**, regardless of
cadence, the first time a changed `sync.target`/path or machine name is
detected -- the tracker binds its signatures to the effective
source/destination/machine identity, so pointing the same db at a different
destination forces one full reconciliation rather than silently
reusing stale "already synced" state from the old one.

## Troubleshoot

- **Runtime not ready:** run
  `<agent-logger catalog "agent-logger" argv[0]> version` and keep the exact
  self-provisioning error if it fails. The plugin owns uv acquisition on
  Linux/WSL; on Windows, a missing signed Python may be reported by the
  installer.
- **Target unreachable:** run
  `<agent-logger catalog "session-sync" argv[0]> doctor`; fix the first `[FAIL]`
  before retrying
  `<agent-logger catalog "session-sync" argv[0]> run --dry-run --verbose`.
- **Scheduled sync not firing:** use the plugin installer status command first:
  `pwsh -File plugins\agent-logger\scripts\install.ps1 status` or
  `bash plugins/agent-logger/scripts/install.sh status`. On Windows the task is
  `Agent Logger Session Sync`; on Linux/WSL inspect
  `systemctl --user status agent-logger-sync.timer agent-logger-sync.service`. <!-- marketplace-isolation: allow deployed-runtime-diagnostics -->
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
