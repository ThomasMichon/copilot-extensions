# Operator attention contract -- sources and CLI

Part of the [Operator Attention Contract](README.md) effort: the built-in adapters (Phase 2), the CLI and pluggable command sources (Phase 3).

## Built-in adapters

- **dispatch**:
  - `awaiting_steer` → `awaiting_input`;
  - `hold_reason` → `blocked` (the operator's own hold: low severity, still listed);
  - a self-tracked task in `submitted` (a completion claim awaiting confirmation) → `review`, even under a stale `awaiting_steer` flag: a submitted task is concluded and `steer submit` refuses it, so its ask can't be answered (the board applies the same precedence). A submission with an `evaluator_ref` waits on its evaluator, not the operator, and is no item. `completed` is the confirmed terminal and is never
    an item;
  - undraining buildup (vision *buildup-is-a-health-signal*) → `stalled`, one
    item per repo (`entity: queue`, `entity_ref`: the canonical repo), built on
    `backlog_health(repo=...)`. The predicate is `oldest_queued_age >
    queued_after` **or** `oldest_held_live_age > held_live_after` (strictly
    greater, in seconds), with both thresholds in agent-dispatch's environment-based configuration (`AGENT_DISPATCH_ATTENTION_QUEUED_AFTER_SECS`, `AGENT_DISPATCH_ATTENTION_HELD_LIVE_AFTER_SECS`; default 1800 each; `0` turns that half off). Held tasks whose owner's liveness is
    `unknown` or `gone` never count: only a live owner that stopped progressing
    is buildup. `reason` carries both ages and the `queued`/`held_live` counts;
    the item clears on an `ok` read where neither age exceeds its threshold.
- **bridge** -- every reason in agent-bridge's `AttentionReason` vocabulary
  (`models.py`) is mapped, so no parked session can drop out of the queue
  unnoticed:
  - `input_required` → `awaiting_input` (`reported`);
  - `permission_required` and `policy_required` (a represented snapshot /
    `wait --attention`; each waits on an operator decision) →
    `awaiting_input` (`reported`);
  - `failed` (a local result snapshot) → `failed` (`reported`);
  - `unreachable` → `failed` (`reported`): the bridge settles it only on
    authoritative terminal evidence, after its reconnect policy is exhausted,
    so it's a real operator boundary, not an unreadable observation;
  - `contract_changed` isn't an item: the session's state can't be trusted, so
    it counts toward the bridge source's `uncertain` (never `clear`);
  - `turn_complete`, `turn_cancelled`, `stopped` and `ended` are settled
    states, not operator asks: no item;
  - a reason this adapter doesn't know (a newer bridge) counts toward
    `uncertain` too, never silently dropped;
  - presence `awaiting_input` → `awaiting_input` (`scanned`/`heuristic`);
  - presence `unknown` is **not** an item: it means the transcript couldn't be
    read or holds no presence signal, not that the session stalled. It counts
    toward the bridge source's status (`uncertain: n`). `stalled` needs independent
    evidence: a session the bridge calls busy whose transcript hasn't moved for
    longer than a configured threshold (an open question below; no bridge
    `stalled` items until it's decided).

  Local reads only by default; remote venues opt in (`--include-remote`), since
  each is an SSH read.

  **Candidates:** the union of agent-bridge's two session registries, read
  through its own API (never its database): **bridge-managed sessions**
  (`agent-bridge --json sessions`, the sessions it spawned and drives) and
  **registered interactive CLI sessions** (`agent-bridge --json live-sessions
  list`, live rows only; `--json` is the bridge's global option, so it comes first). Candidates
  are keyed by their logical delegate reference (see `entity_ref`), so a session
  that appears in both registries, or a successor that replaced one after a
  restart, takeover or handoff, is one entity whose first-observed time and
  cursor position carry over; its actions name the *current* session handle,
  resolved at read time. A candidate with no logical reference yet -- today, any session no managed worktree hosts (see `entity_ref` in [contract.md](contract.md)) -- is never an item under a provisional key that could change later (an escrow, ACP or Copilot session id): it counts toward `uncertain` until agent-bridge exposes the reference. Every candidate is classified from its
  **attention state** and its presence, as listed above. The attention state is
  the reason agent-bridge's own attention evaluator would settle a `wait
  --attention` on -- the only place `policy_required` (and, for bridge-managed
  sessions, `permission_required`) is observable today; result snapshots alone
  miss them. This slice therefore adds a bounded, non-blocking read of that
  evaluator to agent-bridge for both registry types (`agent-bridge --json
  attention <session>`: the current reason or `null`, never a wait), and the
  adapter reads it per candidate. Serving that evaluation for a registered
  interactive session is new daemon behavior (today's attention endpoint
  resolves owned sessions only), so it lands as an HTTP protocol capability:
  `HTTP_PROTOCOL_VERSION` is bumped with a named capability constant, and the
  command gates on `BridgeClient.daemon_supports()`. Against an older daemon it
  reports represented sessions as `unsupported` rather than sending the request,
  and the adapter counts those candidates toward `uncertain`, so a version skew
  reads as `partial`, never as `clear` or as every session failing. A candidate whose attention can't be read
  counts toward `uncertain`. If either listing fails or
  times out, the bridge source is `failed`, never `ok` on the other alone: a
  parked session in the registry that wasn't read must not read as `clear`.
  With `--include-remote`, each remote venue's registry is a further candidate
  set under the same rule, and a venue that can't be read makes the source
  `failed` too.
- **pr** (`pr bar`, on `dev` since #5566): a PR whose `pr bar` JSON reports it
  **`OPEN`** with verdict `failed` (exit 11) → `failed` (the author has something
  to do). The live state decides, never the tracked record's: a closed or merged
  PR is no item even when its record still says `open`, and a reopened PR is a
  candidate even when its record says `closed`. Exit 12 (`unknown`)
  counts toward the source's status, not as an item. **Candidates:** every
  PR tracked by agent-worktrees, whatever its local state, across **every project registered on this
  machine**, not just the one the caller's CWD belongs to. They're enumerated
  through agent-worktrees' own CLI (never by reading its files), and each PR is
  read by its own repository and number, with explicit project context
  (`agent-worktrees -p <project> pr bar <owner/name> <number> --json`), so the
  result is the same from any CWD. A worktree can track several PRs (serial or
  parallel), so a worktree id never stands in for the PR: reading by worktree
  would re-read its active PR for every record and miss a failing older or
  reopened one. A PR tracked by more than one worktree is read once. A tracked record that doesn't resolve to a concrete `authority`, `repo` and `number` (an empty repo, or no number and no parseable PR URL) is never dropped: it counts toward the source's `uncertain`, so an incomplete inventory reads `partial`, never `clear`. A project whose
  tracked PRs can't be enumerated makes the source `failed`: a partial list
  can't claim to be complete. **Dependency:** `agent-worktrees claims find pr`
  already scans every adopted project's tracked PRs, but only for one known
  `--repo`. This slice extends that command rather than adding a parallel one:
  `--repo` becomes optional (every repo), and its `--json` output gains a
  versioned envelope, `{"schema": 1, "projects": [{"project", "status": "ok" |
  "failed", "error"?, "prs": [{"worktree_id", "authority", "repo", "number",
  "state"}]}]}` (`authority` the canonical provider authority above), over every
  adopted project on the machine. It exits non-zero only when the project
  registry itself can't be read. Tests cover two projects, one project failing
  while the other still lists, and an empty registry.
- Each adapter is bounded by a per-source timeout. A timeout is that source's
  `status: failed` (with the timeout as its `error`), not a hang.

## CLI and pluggable sources

- `agent-dispatch attention [--json] [--source <name>...] [--include-remote]`:
  the ordered queue, with the degraded banner in text mode. `--source` names a
  known, enabled source (built-in or registered); an unknown or `disabled` name is a usage error (exit
  2, nothing read), never silently omitted -- a typo must not read as `clear`.
- `agent-dispatch attention next [--after <cursor>] [--json] [--source <name>...] [--include-remote]`
  (each flag on the `next` subcommand itself, so it's accepted after `next`): the oldest worst item (a
  keyboard walk in a UI is this, repeated). The cursor is opaque but carries the
  queue position -- `(severity, created_at, id)` of the item last shown -- not just
  its id, so `next` returns the first item strictly after that position in the
  current read even when the item it names was resolved (gone) or deduped into
  another; it wraps to the top once nothing is after it.
- **External adapters:** the operator registers a source as a command (an
  `argv`) on this machine, through the CLI only (`agent-dispatch attention
  source add <name> -- <argv>`), in a machine-local file outside any repository. `source add` pins the command to an absolute path, a registration whose command isn't absolute is rejected, and the command runs from the registry's own directory, so neither its executable nor a relative argument resolves against the checkout a read runs in (a test reads from an untrusted CWD). Repository-owned content never registers or activates a command, so reading
  attention in an untrusted checkout runs nothing it brought. It is registered under a **name** that is the source's identity (the registry is keyed by name, so `source add` with a name that is already registered replaces that registration); the name must match `[a-z0-9-]+` and not be a built-in source's name (`dispatch`,
  `bridge`, `pr`), or the registration is rejected. A rejected registration is
  **not** a source -- listing it under its colliding name would give two
  `sources[]` entries one identity. It is reported in the envelope's
  `config_errors[]` (`{"name", "error"}`, empty when none), and any config error
  the read lists makes the aggregate `degraded`: a source that was meant to run
  didn't. A selective read lists only the selected names' errors (see the
  aggregate response), and a name a rejected registration claimed still counts
  as known to `--source`. The aggregator **stamps** identity at the
  boundary rather than trusting the command. A command item is the item schema
  with `source`, `id`, `created_at` and `updated_at` **optional**; validation runs in this order: (0)
  `entity` is canonicalized first: a shared kind (`task | session | pr | queue`)
  stays as-is, a bare custom kind `<kind>` becomes `x.<source>.<kind>`, an
  already-namespaced `x.<source>.<kind>` under the command's own name is kept
  (never double-prefixed), and another source's `x.` prefix rejects the item;
  every later step uses the canonical `entity`; (1) a
  present `source` or `id` that differs from the registered name or the derived
  id rejects the item; (2) the aggregator sets `source` to the registered name
  and derives `id` from `(source, entity, entity_ref)`, and fills an omitted
  `created_at` from that source's persisted first-observed time (the same
  per-source store a built-in adapter that only knows when it observed
  something uses) and an omitted `updated_at` from the read's `read_at`; (3)
  the completed item is validated against the item schema. So a command that
  doesn't know when a condition began keeps a stable position across reads, and
  an external
  source can never alias a built-in producer, mint a duplicate `id`, or clear
  another source's first-observed time. The command prints the same source-result envelope a
  built-in adapter returns, `{"schema": 1, "items": [...], "status"?,
  "uncertain"?, "error"?, "read_at"?}`, so a partial read can say so: it reports
  `status: uncertain` with the count of entities it couldn't classify. Omitted
  fields are translated, never guessed: no `status` means `ok` when `uncertain`
  is absent or 0 and `uncertain` otherwise; no `read_at` means the aggregator's
  receipt time. `disabled` is the aggregator's to set, never a command's. Its own
  kinds are namespaced `x.<source>.<kind>` in step (0) (it may also use the
  shared kinds). Its own signals (sign-in
  expiry, coordination asks) then join the same queue with no code in this repo,
  under the same timeout and degraded rules. Failure contract: a non-zero exit, a
  timeout, output that isn't JSON, a missing or unsupported `schema` (anything but
  `1`), a response without `items[]`, a `status`
  outside `ok | failed | uncertain`, a status that contradicts `uncertain`
  (`ok` with a count above 0, `uncertain` with 0), or any item that
  doesn't validate against the schema makes that source `failed` (with the reason
  as its `error`) -- never a crash of the aggregate, never an empty source. A
  command that reports `status: failed` itself is `failed` with its own `error`,
  which must be a non-empty single line (≤ 200 chars); a self-reported failure
  without one gets the aggregator's own `error`, `"source reported failed without
  an error"`, so every response maps to the aggregate shape.
- Docs: `plugins/agent-dispatch/docs/cli-reference.md`, the skill reference,
  and the attention item schema in the plugin docs; plus the two sibling
  commands in their own plugins' CLI references, each with its operands, JSON
  envelope and failure semantics: `agent-bridge --json attention <session>` in
  agent-bridge's, and `agent-worktrees claims find pr [--repo] --json`
  in agent-worktrees'.
