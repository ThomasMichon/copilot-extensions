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
- **Log renderer** — one voice-neutral, read-only `session-log-writer` agent
  that turns a manifest of 1..N sessions into structured Markdown artifacts
  for its caller to validate and persist, plus the
  `log-session` (interactive) and `process-backlog` (local batch) skills
  that drive it. Personality is never built in; repository organization config
  can declaratively supply optional voice-seam instructions
  (see [`docs/manifest-contract.md`](docs/manifest-contract.md)).
- **session-sync** — push raw session data to a configurable target: a
  `local` dotfolder, `onedrive`, `ssh`/`ssh-tunnel`, or a generic `ingest`
  endpoint. It archives only Copilot session state, can scope by repo
  allow/deny lists, and can fire a target-independent best-effort HTTP notify
  after a successful push. `session-sync health` classifies freshness and
  repeated partial passes for one machine or a filesystem-backed fleet, with
  JSON output and alert-friendly exit status. A bounded detritus policy detects
  Chromium-family user-data roots captured under session `files/`, excludes
  them from every transport, and removes stale copies from filesystem-backed
  targets without deleting the local source evidence. Its `rescue-push` source adapter validates
  provider-owned rescue captures, accepts only independently complete sessions,
  and projects them into the same target layout under stable venue keys, with
  host-authoritative generic per-session provenance. Configure with
  the `session-sync-setup` skill; deploy as a 4-hourly Scheduled Task (Windows)
  or systemd user timer (Linux).
- **Background chronicling core** (`agent_logger.chronicle`) — an optional
  `agent-logger chronicle status | scan | tick` pass over a *synced* corpus. It
  discovers settled sessions, routes them by recorded origin, groups them into
  compact daily digest manifests, and reserves segment identities in SQLite so
  racing passes do not double-log. The plugin does **not** install a chronicle
  scheduler or job lease by itself; a host/runner owns scheduling and, when it
  wants real logs rather than manifests, runs the `session-log-writer` agent,
  persists its render bundle, and applies the configured landing policy.

## Design principles

- **Personality- and layout-neutral.** Voices, output path templates,
  repo-local Markdown skeletons, and machine naming are configuration, not
  hard-coded. The plugin ships **no persona** — a repository opts into styling
  through manifest fields in its organization config.
- **Standalone runtime.** The plugin does not require a repo to be registered
  as an agent-worktrees harness. A session-start hook cheaply stamps a
  self-provisioning `agent-logger` binstub when hooks are available, and the
  operational skills include the same readiness path for hosts that only load
  skills.
- **Local state stays local.** The runtime home (`~/.agent-logger/`, or
  `$AGENT_LOGGER_HOME`) holds digests, sync locks, deployment metadata, and the
  optional chronicle SQLite DB. It must never be a cloud-synced folder.
- **Three deployment topologies** from one plugin — see
  [`docs/deployment-topologies.md`](docs/deployment-topologies.md).

## Status

**0.1.1-dev series — alpha.** Shipped and usable: the segmenter, session-sync
(5 targets + installers), the log-writer/ramp-up agents, the `log-session`,
`process-backlog`, `ramp-up-session`, and `session-sync-setup` skills, and the
optional background-chronicling core with its session-source + log-sink seams.

## Quick start

1. Enable the plugin in Copilot CLI. In a plain host, run `agent-logger version`;
   if the runtime was only stamped, the binstub self-provisions on first use and
   prints `::agent-provisioning::` while it builds.
2. For one-off logs, use the `log-session` skill for the current session or
   `process-backlog` for a local batch. Both hand a manifest to the neutral
   `session-log-writer` agent.
3. To archive raw sessions continuously, use `session-sync-setup`, choose a
   target in `~/.agent-logger/config.yaml`, verify with `session-sync doctor`,
   then install the 4-hourly timer (`scripts\install.ps1 install` on Windows or
   `scripts/install.sh install` on Linux/WSL).
   To publish host-side provider rescues through a single-writer,
   compare-and-set `local` filesystem target, run
   `session-sync rescue-push --rescue-root <provider-state>/rescues`.
   Use `--verbose` to see exact older/revalidation/rejected reasons. The
   checkpoint is `$AGENT_LOGGER_HOME/rescue-sync/checkpoint.json`; removing it
   resets ingest ordering without deleting destination evidence. Provider container renames
   intentionally create a new venue identity because the current key includes
   the provider-visible container name. Host-recorded capture provenance owns
   routing; rescued `origin.json` is retained only as `rescued-origin.json`
   evidence. Independently complete sessions may be accepted from a partial
   capture, while missing/invalid event streams are rejected visibly. Rescue
   capture-ID fingerprints remain as compact durable tombstones after provider
   retention, and checkpoint rewrites are size/record bounded before atomic
   replacement. A rescued `agent-worktrees.json` sidecar is accepted only as
   bounded schema-v1 data whose session ID matches its enclosing directory; it
   remains inert restored evidence at the destination. Invalid or newer
   sidecars are omitted without discarding the session, and every rescued
   session receives a restored-origin marker. Venue pushes remain isolated, but
   any target failure makes the
   final command nonzero. Rescue destination pruning is not yet wired to
   `sync.retention_days`.
   Use `session-sync health --max-age-hours 12 --partial-threshold 3` for one
   machine, or add `--fleet --json` on a filesystem-backed hub. Repeat
   `--machine NAME` to restrict a fleet alert to active machines. A fresh
   one-off partial result is degraded but exits successfully; stale metadata,
   unreadable/missing metadata, and a partial streak at the threshold are
   unhealthy and exit nonzero.
   `session-sync status` reports the number, size, and bounded path sample of
   generated browser-profile roots omitted by the latest filesystem sync.
4. For takeover, use `ramp-up-session`; it delegates the transcript-heavy read
   to the neutral `session-rampup` agent by default.

When agent-worktrees exposes a valid open effort binding, takeover is
effort-first: the effort README supplies the durable objective, plan, journal,
and completion gate. Session history is read only as needed to recover the
predecessor's immediate delta. Standalone worktrees retain the bounded
checkpoint-and-digest reconstruction path.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — components + data flow
- [`docs/deployment-topologies.md`](docs/deployment-topologies.md) — local
  skill / local timer / fleet hub
- [`docs/manifest-contract.md`](docs/manifest-contract.md) — the log-writer
  manifest + closing-remark injection seam

## Configuration

Layered: built-in defaults → `$AGENT_LOGGER_HOME/config.yaml` → repo-local
organization config (`.agent-logger.yaml` / `.agent-logger.yml` /
`.config/agent-logger.yaml` / `.config/agent-logger.yml`, `log:` block plus
schema v3's single `sync.local_path` field)
→ `AGENT_LOGGER_*` environment overrides. Repo-local config is only honored
for a checkout that is both a project registered with `agent-worktrees` and
currently on that project's registered default branch -- see
[`docs/manifest-contract.md`](docs/manifest-contract.md#trust-gate-only-a-registered-projects-default-branch-is-honored).
Inspect runtime config with:

```
agent-logger config
```

Inspect the repository organization fields exactly as they enter a writer
manifest with `agent-logger organization`.

Aggregate operational policy is a separate surface. Machine admission lives
under `aggregate:` in `$AGENT_LOGGER_HOME/config.yaml`; each admitted
authoritative checkout may publish
`.copilot-extensions/agent-logger/config.yaml`. Inspect the same compiled plan
used by aggregate diagnostics with:

```
agent-logger config --resolved --json
agent-logger doctor --json
agent-logger chronicle status
```

These commands are read-only. `config --resolved` and `doctor` exit non-zero
when the complete plan is unauthorized; `chronicle status` remains a successful
status read and exposes the decision under its `aggregate` field. Checkout
identity, default-branch state, declaration provenance, machine selectors,
normalized claims, resource readiness, and conflicts are resolved before any
later execution integration can perform side effects.

To inspect an explicitly selected synced corpus without changing configuration,
catalogs, or source files:

```bash
agent-logger corpus inventory --root /path/to/corpus
agent-logger corpus inventory --root /path/to/corpus --include-sessions
```

The root contains source directories, each with `session-state/` and/or
`archived/`, including legacy and qualified provider groups. JSON reports
per-source live/archive counts, total source-qualified observations, and errors.
`--include-sessions` adds session IDs and representation kinds; it does not emit
transcripts. Missing or unreadable roots and source validation failures set
`complete: false` and exit nonzero; counts in an incomplete report are partial.
An empty readable corpus is a successful zero-count inventory.

This is an explicit on-demand inventory, not a periodic refresh or index.
It uses the shared archive reader's live precedence and overlap validation,
without repository, settle-window, or already-journaled filters. Counts cover
primary session stores, not rescue snapshot versions; they do not claim logical
alias deduplication, full archive-content integrity, or available usage meters.
It is not a point-in-time snapshot: a concurrently updated corpus can change
during traversal. `complete` means the observed traversal finished without
errors, not that every session's content or concurrent publication was verified.
Ordinary single-archive discovery does not decompress transcript contents;
conflicting compressed representations still require shared content validation.

Repository files declare a `schema_version` (an omitted version is treated as
the current schema, 3 as of this release) and may set `log.root`,
`log.path_template`, `log.timezone`, `log.note_marker`, `log.template`,
`log.narration_style`, `log.exemplars`, and `log.closing_remark`. Schema v3
additionally allows a single `sync.local_path` (an absolute path, the same
value for every machine in the fleet) -- no other sync setting, machine
identity, or credential is ever repo-configurable. Invalid or unsafe
configuration fails explicitly instead of silently falling back.

### Discovering a fleet config repo for the scheduled sync

The scheduled sync (a systemd user timer on POSIX, a Scheduled Task on
Windows) runs with no meaningful working directory of its own, so it can't
rely on CWD-based repo-local config discovery the way an interactive shell
does. To still pick up a fleet repo's schema v3 `sync.local_path`
declaration, set `config_repo: <name>` in
`$AGENT_LOGGER_HOME/config.yaml` at install time -- `<name>` is a project
name resolvable via `agent-worktrees repos find`. When set, the installer
(`install.sh` / `install.ps1`) resolves that project's checkout, validates
it against the same registered-project + default-branch trust gate as
normal discovery, and wires the resolved config file into the scheduled
unit/task as `AGENT_LOGGER_REPO_CONFIG`. Leaving `config_repo` unset (the
default) only omits *repo-local* discovery for the scheduled run -- the
generated unit/task still sets `AGENT_LOGGER_HOME`, so the scheduled sync
continues to read the machine-local `$AGENT_LOGGER_HOME/config.yaml` (and
any `AGENT_LOGGER_*` environment overrides) exactly as it always did; only
a fleet repo's own `sync.local_path` declaration is unreachable without it.

## License

MIT
