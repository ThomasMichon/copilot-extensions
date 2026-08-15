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
  dormant session never floods the main session's context).

All machine/path/voice coupling is configuration — there is no multi-machine system
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

**Post-push notify (target-independent).** A `sync.notify.url` fires a
best-effort HTTP `POST` (JSON `{"machine": <machine>}`; `{machine}` in the URL
is also substituted, optional bearer token) after **any** successful push,
regardless of target — so a downstream consumer can crunch immediately. It is
multi-machine system-neutral: point it at a processing service directly, or at a public
webhook callback (e.g. a Home Assistant webhook that relays to a private
service). The `ingest` target's own `notify_url` option remains for back-compat
and now shares the same best-effort helper (`agent_logger.sync.notify`).

Deployed as a 4-hourly **Scheduled Task** (Windows) or **systemd user
timer** (Linux) via `scripts/install.ps1` / `install.sh`. Configure with the
`session-sync-setup` skill.

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
`path_template`, `timezone`, `note_marker`, and an optional Markdown
`template` under `log:`. Invalid configuration and paths outside the
repository fail explicitly. Non-logging components ignore repo-local
configuration, so a layout error cannot disrupt session sync or digest storage.

## Deployment topologies

See [deployment-topologies.md](deployment-topologies.md). In short: a local
skill (on demand), a local sync timer (self-serve one machine), or a fleet
hub (many machines sync to one shared folder).

## Background chronicling (`agent_logger.chronicle`)

The **orchestrator daemon** — the automated "sessions → committed logs"
service — turns the *synced* Copilot session corpus into objective,
matter-of-fact **daily** logs landed in a target harness repo. It is the
scheduled, fleet-wide, single-elected chronicler: `agent-dispatch`'s schedule
management + single-producer job-lease drives *when* and *where-once* (pinned to
one machine, idempotent catch-up); `agent_logger.chronicle` supplies *what* and
*how-once-per-segment*.

One `Chronicler.run_once` pass is `scan → digest (daily) → reserve → manifest →
writer → land`, running between two pluggable seams so a consumer can adopt the
daemon without re-implementing scan/digest:

**Session-source seam** (`chronicle.source`) — discovers loggable units and
enforces the idempotency locks:

- **Settle gate** — never claim a session whose synced state changed within
  `settle_seconds` (~10 min); it may be mid-sync.
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
- **Landing-policy** — how a produced log commits, pluggable per sink so the
  daemon core never hardcodes landing: `DirectCommitLanding` (dotfiles: one
  scoped daily commit), `SquashPRLanding`, or a consumer-supplied strategy (e.g.
  a governed single-flight merge-queue).

CLI: `agent-logger chronicle status | scan | tick`. Only the elected host sets
`chronicle.enabled`; its scheduled `chronicle tick` is the recurring job the
`agent-dispatch` registry + job-lease pins fleet-wide. See the
[manifest contract](manifest-contract.md) for the `mode: digest` manifest the
daemon produces for the writer agent.
