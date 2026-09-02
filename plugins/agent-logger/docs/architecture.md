# Architecture — agent-logger

`agent-logger` packages the **reusable ends** of a Copilot session-logging
pipeline as a Copilot CLI plugin. It gives you standalone runtime tools,
manifest-driven agents, and optional sync/chronicling cores; any host-specific
scheduling, voice, or landing policy is configuration or an injected runner.

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

The diagram is the per-session logging path. The optional chronicle path starts
from a synced corpus and produces daily `mode: digest` manifests for the same
writer agent.

## Components

### Segmenter (`agent_logger.segmenter`)

Collates a single Copilot session into context-ingestible Markdown digest
chunks. Four console scripts:

- `collate-session` — split a session (`~/.copilot/session-state/<id>`) into
  a context file + numbered transcript segments, written to a local store
  (`~/.agent-logger/session-digests/`) and/or an output dir.
- `read-session-digest` — read collated context / segments / manifests back.
- `prepare-session-log` — detect machine, generate a cutoff, and render a
  log path from a configurable template, including any repo-local
  organization config discovered from the current git root.
- `ramp-up-session` — **take over a dormant session.** Discovers the most
  recent session for a worktree — named by its short **suffix** (e.g. `fbc5`),
  a path, or `.` — collates it ephemerally (reusing the same engine), and prints
  a takeover **brief** (metadata + the CLI's pre-compaction checkpoints + stats
  + a "where it left off" tail of the last few turns) so a fresh session can
  pick up the torch of a session that can no longer be resumed. A worktree on
  another host is hunted over `ssh <machine>` via `--machine`. Drives the
  `ramp-up-session` skill, which by default delegates the context-expensive
  ramp-in to the neutral **`session-rampup`** agent (a context firewall — it
  absorbs the large transcript and returns a compact takeover briefing, so the
  dormant session never floods the main session's context). When a valid open
  agent-worktrees effort binding is available, the consumer reads that effort
  first as the durable objective/plan/journal/completion gate and limits
  transcript recovery to the predecessor's immediate activity. Without a valid
  binding, the standalone checkpoint-and-digest path remains unchanged.

All machine/path/voice coupling is configuration — there is no
deployment-specific hostname, shared-folder path, or persona baked in.

### Session sync (`agent_logger.sync`)

A transport-blind engine that pushes raw Copilot session data to a configurable
**target**, under a `{machine}/` subpath. It archives only `session-state/` and,
when present, sibling `provenance/` sidecars and, when unfiltered, the
session-store index files; it never copies installed plugins, credentials,
settings, or other `~/.copilot` state. Sync scoping can use a repo allowlist,
denylist, fail-closed behavior for unclassified sessions, and harness-repo
origin sidecars for downstream routing. Targets implement a small `Target`
interface
(`push` / `prune` / `doctor` / `describe`):

| Target | Destination |
|--------|-------------|
| `local` | a dotfolder under `$HOME` (default) |
| `onedrive` | a subfolder under the OS-resolved OneDrive root |
| `ssh` / `ssh-tunnel` | rsync over SSH, optionally via a jump host |
| `ingest` | an rsync-daemon sink with an optional HTTP notify |

**Post-push notify (target-independent).** A `sync.notify.url` fires a
best-effort HTTP `POST` (JSON `{"machine": <machine>}`; `{machine}` in the URL
is also substituted, optional bearer token) after **any** successful push,
regardless of target — so a downstream consumer can crunch immediately. It is
deployment-neutral: point it at a processing service directly, or at a public
webhook callback (e.g. a Home Assistant webhook that relays to a private
service). The `ingest` target's own `notify_url` option remains for back-compat
and now shares the same best-effort helper (`agent_logger.sync.notify`).

Deployed as a 4-hourly **Scheduled Task** (Windows) or **systemd user timer**
(Linux) via `scripts/install.ps1` / `install.sh`. The scheduled command is
`session-sync run --prune`; failed pushes return non-zero and `doctor` prints
per-check `[ok]` / `[FAIL]` readiness lines. Configure and troubleshoot with
the `session-sync-setup` skill.

#### Provider rescue source

`session-sync rescue-push --rescue-root <root>` validates host-owned provider
rescues and publishes them through a single-writer, compare-and-set `local`
filesystem target. OneDrive replicas and remote push-only targets remain
unavailable until they can enforce the same destination high-water contract
across writers. The adapter never exposes the provider tree to a target.
Schema-v1 captures must be `verified`; a partial capture contributes only
sessions whose event stream and declared allowlisted members are independently
complete. Missing/invalid event streams are rejected with explicit per-session
reasons; accepted `events.jsonl` must be valid UTF-8 JSONL with one JSON object
per nonblank line. Every copied member is checked against its byte count and
SHA-256 before the newest valid capture per venue/session is projected as:

```
session-state/<session-uuid>/...
provenance/<session-uuid>.json
```

The short-lived projection is created under the agent-logger home and removed
after `Target.push`. The target namespace is a flat, filesystem-safe venue key
such as `container-worker-1`, independent of the replaceable container
instance. Generic additive provenance records provider, venue/target identity,
container lineage, fleet, capture, repository/source repository, optional
recorded model/interface/origin/source, member hashes, and
`billing_scope: unknown`; it does not infer usage, cost, or account identity.
The host-recorded capture `source_repo` is the routing authority. Rescued
`origin.json` is retained only as `rescued-origin.json` evidence and can never
replace canonical origin or override routing.

An atomic host-local checkpoint under `$AGENT_LOGGER_HOME/rescue-sync/` makes
the adapter incremental. It fingerprints the full capture metadata/session/
member manifest by provider + venue + capture ID, so any reuse of an accepted
capture ID with changed declarations rejects that whole capture. The same
fingerprint is persisted in every per-session high-water record and as a compact
provider+venue+capture-ID tombstone, so source retention cannot erase the
identity proof even when a replacement declares entirely different sessions.
The checkpoint is compacted to fixed-field records, bounded by record count and
encoded bytes, and refuses an oversized rewrite before replacing the last
readable checkpoint. A newer capture replaces the selected destination session tree (dropping stale
optional members), while the same retained capture performs an idempotent
target revalidation so destination loss can be repaired. An older, unverified,
malformed, symlinked, or hash-mismatched capture cannot rewind accepted
evidence, and a session's provider-recorded repository assignment cannot change
between accepted captures without an explicit checkpoint reset. Repo policy
reuses the normal session-sync exact classification and `fail_closed`
semantics; rescued workspace/origin claims cannot opt themselves into the
corpus. The reciprocal `agent-worktrees.json` sidecar is preserved only when it
is bounded schema-v1 JSON for the enclosing session ID. It remains restored
evidence rather than an authoritative local binding; every rescued session gets
a `rescued-origin.json` marker even when the provider had no origin sidecar.
Rescue bytes are copied as data only and are never restored or executed.

Verbose output reports ordering and rejection reasons. Removing
`$AGENT_LOGGER_HOME/rescue-sync/checkpoint.json` resets ingest ordering without
deleting already-published evidence. Container renames currently create a new
venue identity because the stable key includes the provider-visible name.
Configured `sync.retention_days` pruning is not yet applied per rescue venue;
that effort item remains open.

Filesystem replacement stages all changed selected sessions and provenance
before publication, then rolls the whole venue batch back on failure. Its
machine-level `.session-sync-replacement/` area is never beneath discoverable
`session-state/`; the chronicler skips a venue while an `.active` transaction
exists. Incomplete rollback state is retained there for recovery, while residue
from a completed publish or rollback is marked for bounded cleanup on the next
pass. A retained `.active` recovery blocks later pushes until it is resolved,
and destination directory chains reject symlink leaves before publication.
An atomic machine-generation sidecar lets a scanner discard any read that
crossed a completed replacement. Symlinks and lock files are never projected.
Generated provenance is bounded to the same maximum size accepted by its
reader, so an oversized sidecar fails the venue rather than silently disabling
trusted routing.

On Windows, path validation distinguishes redirecting name-surrogate reparse
tags (symlinks and mount points, rejected) from non-redirecting cloud
placeholders (allowed to hydrate), so OneDrive Files On-Demand remains usable
without weakening the escape boundary.

A hidden per-session destination high-water receipt participates in the same
rollback-capable filesystem transaction. It compares capture timestamp/ID and a
canonical provenance fingerprint before replacement, so a stale or independent
source checkpoint cannot rewind newer evidence already present at the target.
A destination-scoped advisory lock serializes receipt validation through
transaction completion across writers sharing that filesystem; a target whose
filesystem does not honor cross-writer locks is not suitable for rescue
publication.

Rescued sessions keep their canonical session UUID for display and corpus
layout, but chronicler reservation identity additionally includes venue and
capture ID. Revalidating one capture remains idempotent; a newer accepted
capture becomes a distinct analysis unit instead of being hidden by the first
capture's journaled marker. The same transaction also publishes a hidden,
immutable per-capture session snapshot, and chronicler manifests reference that
snapshot rather than the mutable canonical-latest path.
Venue pushes continue independently, but any target failure makes the final CLI
exit nonzero even when another venue succeeds.

### Log writer (`agents/` + `skills/`)

One **voice-neutral** `session-log-writer` agent turns a manifest of 1..N
sessions into structured Markdown logs. Two skills drive it:

- `log-session` — interactive, the current session (manifest-of-one).
- `process-backlog` — local batch, a backlog of unlogged sessions.

The agent has **no personality of its own**. It produces a closing remark only
when the manifest includes instructions through its **closing-remark seam**.
The generic skills populate location, naming/template, and optional voice
fields from repository organization config, so no wrapper is required merely
to inject those choices. See [manifest-contract.md](manifest-contract.md).

## Configuration

Layered: built-in defaults → `$AGENT_LOGGER_HOME/config.yaml`
(default `~/.agent-logger/config.yaml`) → repo-local organization config →
`AGENT_LOGGER_*` env overrides. Inspect with `agent-logger config`. The home
dir is **local-only** — never place it inside a cloud-synced folder.

Repo-local config is discovered at the current git root from
`.agent-logger.yaml`, `.agent-logger.yml`, `.config/agent-logger.yaml`, or
`.config/agent-logger.yml`. The version-1 schema accepts only `root`,
`path_template`, `timezone`, `note_marker`, optional Markdown `template`, and
the optional voice-seam fields `narration_style`, `exemplars`, and
`closing_remark` under `log:`. Invalid configuration and paths outside the
repository fail explicitly. Non-logging components ignore repo-local
configuration, so a layout error cannot disrupt session sync or digest storage.

## Deployment topologies

See [deployment-topologies.md](deployment-topologies.md). In short: a local
skill (on demand), a local sync timer (self-serve one machine), or a fleet
hub (many machines sync to one shared folder).

## Background chronicling (`agent_logger.chronicle`)

The **chronicling core** is the optional "synced sessions → daily digest
manifest" path. It is not installed as a service by `agent-logger` itself and
does not contain an agent-dispatch dependency or lease. A host that wants
fleet-wide automation runs `agent-logger chronicle tick` on its elected machine
(or wraps `Chronicler.run_once`) and owns schedule/lease semantics externally.
Out of the box, the CLI's default writer persists manifest JSON files; a runner
then invokes the read-only `session-log-writer` renderer, validates and persists
its render bundle beneath the sink's output root, and only then lets the sink
landing policy commit or push those files.

One `Chronicler.run_once` pass is `scan → digest (daily) → reserve → manifest →
renderer → validate-and-persist → land`, running between two pluggable seams so
a consumer can adopt the core without re-implementing scan/digest:

**Session-source seam** (`chronicle.source`) — discovers loggable units and
enforces the idempotency locks:

- **Settle gate** — never claim a session whose synced state changed within
  `settle_seconds` (default 600s); it may be mid-sync.
- **Already-journaled skip** — a journaled segment is never rescanned, so
  multi-day gaps and catch-up replays never re-file a day.
- **Continuation-segment reservation** (`ReservationStore`) — a chronicle unit
  reserves the exact `(parent_session_id, segment_index)` segments it will log
  via a compare-and-set, with a downgrade guard. The work-locked mesh fences a
  task *record* (atomic claim + unique dedup_key) but **not** a task's *inputs*;
  the session segments live outside the mesh, so the reservation is carried in
  this seam. The mesh task's `dedup_key` (`chronicle:<parent>:<index>`) and the
  reservation key derive from the **same** identity, so the two fences can never
  disagree about "same segment".

**Log-sink seam** (`chronicle.sink`) — `{router, profile, landing-policy}`:

- **Router** (`OriginRepoRouter`) — routes a session to a sink by its recorded
  origin repo (`workspace.yaml` `repository`), machine-default fallback.
- **Profile** — the output voice/shape; `narration_style` defaults to
  `objective` (neutral, factual; consumers may layer a character voice on their
  own sink) plus a compact daily-digest template distinct from the per-session
  Summary/Key-Changes shape.
- **Landing-policy** — how produced log paths commit, pluggable per sink so the
  core never hardcodes landing: `DirectCommitLanding`, `SquashPRLanding`, or a
  consumer-supplied strategy. The default `ManifestWriter` produces no log paths,
  so `tick` reports `written` and leaves segments reserved for the runner that
  actually renders the log.

CLI: `agent-logger chronicle status | scan | tick` (`tick --force` runs even
when `chronicle.enabled` is false). See the
[manifest contract](manifest-contract.md) for the `mode: digest` manifest the
core produces for the writer agent.
