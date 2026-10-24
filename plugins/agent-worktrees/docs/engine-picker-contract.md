# The engine ↔ Picker `--json` contract

The interactive front-end (the **Worktree Manager**'s Picker) reaches the
`agent-worktrees` engine **only** by shelling out to its machine-readable CLI
verbs: `<project> <verb> --json`, never `import agent_worktrees`. That process
boundary is what keeps the coupling one-way and dependency-free (the Picker owns
no worktree logic or state), and it is why the TUI framework (Textual) stays
entirely out of the plugin engine.

## Repository hosting admission

The read-only [`agent-policy check`](repository-agent-policy.md) API is
project-independent and reads the explicit `--path`, including denied or
unadopted repositories. Policy version 1 is distinct from launch transport
versions. It returns the policy provenance and `allowed: true`, or nonzero JSON
`version`, `code`, `error`. A successful negative-policy check is not adoption
or launch permission.

Manager launch planning and SSH receivers require `--require-agent-hosting`;
the flag is a compatibility fence, not an opt-in to enforcement. The current
engine enforces repository policy even without the flag. Unsupported engines
must refuse, with no fallback that removes it. Source-only `create` omits
optional launch planning and returns no `launch` member.

## SSH launch-prompt admission

The Worktrees composer is host-rendered for local and machine-SSH sources.
Prompted remote `resolve --json --machine <target>` invokes the target's own
`resolve --json --launch-request-b64 <envelope>` first. The UTF-8 JSON envelope
is bounded to 7168 bytes (7 KiB, including JSON metadata) and carries version 1, a 32-character hexadecimal
`request_id`, `kind` (`new` or `resume`), `worktree_id` (null for New), `text`
and Boolean `no_mux`. POSIX shell arguments are quoted; PowerShell commands
are encoded. Prompt text never enters the interactive SSH launch string.
Structured admission/status accepts only its protocol options, `--json`, and
the hosting-admission capability fence;
ordinary resolve selectors, modifiers and positional Copilot arguments are
rejected before reading or mutating target launch state. Exactly one nonempty
protocol option is required; an empty value never enters ordinary resolve.
Nonprotocol modifiers, including `--machine`, use the same structured
launch-request error envelope and exit status 3 for both admission and status;
they never enter ordinary or remote machine resolution.

The target's existing tracking-write daemon owns admission and seed staging.
Hosting-aware admission and seed stage/take require verb version 7; older
writers cannot receive a weak fallback. The receiver checks fresh policy before
writing an admission receipt or seed.
Admission retains a text-free fingerprint and allocated worktree/intent IDs
under external tracking state. The returned receipt carries those identities,
not prompt text. The host's subsequent remote plan launches the same worktree
with `--stage-launch-seed --seed-id <id>`. The existing target setup and native
backend boundary perform cold checks, reservation, failed-start release and
successful handoff discard.

`resolve --json --launch-request-status <request-id>` is read-only recovery.
It returns `staged`, `handoff-unconfirmed`, `creation-incomplete`,
`admission-incomplete` or
`completed-or-superseded`, plus the allocated identities. A repeated admitted
request cannot allocate another New worktree or restore a completed/replaced
prompt. A matching durable seed is recoverable even if acceptance-receipt
publication was interrupted, but an incomplete New creation is never ready.
A machine/platform change that cannot honor the persisted New allocation
refuses before creation rather than allocating a differently prefixed worktree.
Every admission-file read validates its stored identity and field types;
corrupt state returns a structured error without disconnecting the writer.
Replay also checks the stored fingerprint and prompt kind against the request,
and Resume must retain its requested worktree identity, including after the
locked receipt reload.
A target without the request capability rejects before mutation.
## Resident current-roster reads

The separate [`record --worktree-id <exact-id> --json`](cli-reference.md#exact-durable-record-read)
API reads one ground-owned durable record in the receiving project's tracking
namespace. It is metadata, not a roster fallback, resident computation,
live-state probe, or launch/takeover permission. Consumers needing an exact
allocation identity may use it before roster publication; ordinary listing
readers still must not scan tracking YAML or independently compute facts.

The Manager requires `list --json --current`. Ordinary JSON/stream listings
also select that authority while resident monitoring is enabled; explicit
profile-history inspection and glance retain their separate workflow paths.
`--classify`, `--cache-only`, `--fresh`, exact IDs/codenames and `--refresh`
do not authorize a different caller-side computation. A row refresh selects
one indexed slot; it does not backfill sessions or enumerate the fleet.

The existing authenticated monitor control endpoint advertises
`worktrees-current-v1`. Its request carries `contract_version: 1`, project,
optional status/platform/ID/codename selection and Boolean `include_missing`.
Results carry `ok`, `contract_version`, `worktrees` and `projection`:
schema version, generation, revision, observation timestamp, complete-roster
and selected-row counts, and quality. Current rows are independently bounded to 64 KiB and omit
growing resources, PR/session/profile histories and graphs. Exact claim totals
and kinds remain in `claims_assets`; full claims use explicit on-demand details.
The bounded current identity fields include string `creation_nonce` and Boolean
`head_yielded`, preserving creation identity and resumable yielded-head semantics
without carrying the creation or session-transition histories.

The resident publishes complete project transactions into its SQLite WAL
store, changing only semantic rows whose values changed, then swaps the
in-memory publication. It claims the writer generation
only after monitor election. Drained/superseded endpoints refuse current reads;
a late predecessor cannot reclaim the durable owner. Failure retains the
last complete publication with explicit `availability.status: stale`.
Missing publication, malformed data and incompatible capability are explicit
errors, never fabricated empty rosters. Confirmed successful empty publication
is distinct and deletes the prior rows.

If rollback elects a previously superseded resident again, it replaces its
fenced publisher with a new generation before republishing. The obsolete writer
remains permanently closed; current election authority is checked again during
the durable ownership transaction. An elected resident retries failed
publication activation at its existing monitor iteration boundary, not in a
reader. Cutover health rejects unavailable roster subsystems even when an older
resident reports broad readiness. A passive, unpublished candidate may report
an unready roster before promotion, but a published candidate must serve the
roster capability. Rollback eligibility is separate: a reachable, nonterminal
predecessor may be restored and undrained even while its old publisher is
fenced. Shutdown closes admission under the same lifecycle lock as recovery
and cannot be undone by promotion or undrain. A retiring feed's notices retain
its last committed generation and cursor, even after the resident creates a
replacement publisher.

Unreachable, slow, upgrading or incompatible residents permit only an
independently indexed durable read, labeled `quality: advisory` with an
availability reason, or an explicit unavailable error. Ordinary readers never
load tracking YAML or compute git/session/mux facts in any such branch.
Readers use the existing owner token without creating or replacing it.
The Manager displays advisory/stale labels and never overlays Group C
reconcile onto a current row. Unsupported `--current` is an actionable upgrade
error; optional-flag retries cannot remove it.

The one-shot `--stream` current snapshot emits `begin` with the publication
and selected row count, `worktree` frames from that same publication, then
`done`. The Manager admits a complete barrier before replacing its rows and
rejects mixed revisions, duplicates and incomplete snapshots. This one-shot
contract remains one-shot for existing consumers.

### Explicit-project held-feed entry

Canonical `--project NAME list ... --follow` invocations share an early entry
between `python -m agent_worktrees` and the package's console declaration.
The syntactic selector performs no configuration, filesystem or process work.
Duplicates, abbreviations, implicit routing, help, unknown options and missing
values decline to the existing dispatcher exactly once. Both paths use the
same list argument declaration; accepted feeds retain canonical project/CWD,
registry and installation-cell resolution, demand registration, authentication
and shutdown behavior.

The healthy current-roster client supplies its canonical runtime root to the
existing control path helpers rather than loading monitor recovery machinery
to discover that root. Recovery remains lazy and retains its existing
unavailable/advisory behavior. Library import of `agent_worktrees.__main__`
never runs the early selector. This is import/dispatch separation, not a new
read authority, a new receipt operation, or a latency guarantee.

### Negotiated observation contract (v2)

The owner additionally advertises `worktrees-observations-v2`. A consumer
that has discovered that capability may request `contract_version: 2` with
the **same bounded selection fields** on `worktrees-read` or `worktrees-feed`.
`worktrees_observation_contract.negotiate(request, capabilities)` selects v2
only from already-observed capability evidence; it performs no discovery,
ownership probe or read. Old strict request parsers must receive v1. Existing
CLI flags and Manager loaders still select v1; the v2 parsers are available
for separately staged consumer integration, not an implicit wire upgrade.

V2 retains the existing projection fields and **schema_version: 1**. The wire
contract version, durable schema version and opaque elected-writer generation
are distinct. Every row adds `semantic_revision` (last semantic change in the
current generation). Its `fact_provenance` retains quality, with observation
times moved into a separate `observations` list:

The 65,536-byte row limit counts the complete compact UTF-8 v2 row, including
the actual decimal width of `semantic_revision`. Legacy field/type validation
still applies separately to its semantic fields. The publisher checks every
projected row before committing the atomic wave; one oversized row rejects the
whole wave without advancing its head. Engine and Manager admission enforce
the same complete-row limit. Manager's four locally derived metadata fields
are excluded only when revalidating already-admitted rows, never at raw
ingress. V1 row capacity and the schema-1 storage limit remain unchanged;
a compatible older writer's row that cannot fit v2 is refused explicitly on
that v2 read, not truncated or rewritten.

V1 reconstruction starts from semantic fields and overlays only the current
observation entry. Removing an optional closure observation therefore removes
its `computed_at` from live and advisory v1 output, even when the unchanged
semantic payload is retained without a row write.

Publication snapshots each input row from its validated encoding before
advancing the input iterable. Reusing or clearing an iterator-owned dictionary,
including its nested fact values, cannot alter earlier row identities or
observations in the atomic wave.

```json
{
  "id": "tree",
  "facts": {
    "session": {
      "quality": "current",
      "observed_at": 1000.0,
      "observation_revision": 11
    }
  },
  "derivations": {
    "closure": {
      "computed_at": "2026-10-22T12:00:00",
      "observation_revision": 11
    }
  }
}
```

There is exactly one observation entry per selected row, including an empty
`facts` object for rows without fact provenance. The fact groups are `record`,
`git`, `mux` and `session`. An unavailable fact has a null `observed_at`;
timestamps and revisions are validated, not truthiness-coerced. Optional
`derivations.closure` carries the closure descriptor's computation time,
which is also observation metadata; its domain facts remain in the semantic
row. No arbitrary `*_at` fields are removed: activity/lifecycle timestamps
remain semantic. Unchanged fact observations retain their observation revision.

The v2 feed adds one bounded `observation` frame per changed observation entry,
with the same `projection` and `observation` object above. Initial observations
follow their semantic rows; updates retain the full order frames. Receivers
stage both kinds until done/commit. Duplicate, unknown, mismatched-quality,
future or regressing observations are rejected. A heartbeat changes neither
facts nor observation time; publication revision may advance without a
semantic row delta. Manager parsers expose admitted metadata as `_observations`
and `_observation_derivations` alongside `_projection`; source-store/UI
integration is separate. A publication cursor is not a point-row floor.

Durability remains backward-compatible: additive observation tables are bound
to a project generation/revision; the original schema-1 row/head/index tables
and election mechanism remain usable by older supported writers. The semantic
row keeps its last changed v1-shaped payload. Current v1 reads reconstruct
latest actual observation timestamps from the matching side data; an older
reader can still read the valid, conservatively older timestamps. If an older
elected writer publishes, its generation/revision no longer matches the side
data: its complete v1 payload wins and no stale metadata is overlaid.
No new source-version election rule or per-read owner attestation is added.

Rows, observations, deletion, head and order/index changes commit in one
transaction before live visibility or notification. Failed writes preserve
the prior complete publication. V1 retains truthful whole-row deltas, including
observation changes; zero semantic-row deltas are the negotiated v2 guarantee.
Neither protocol implements targeted observation requests or mutation receipts
yet; those remain separate owner operations.

### Test-only contended-host tier

The native Worktrees and synthetic Manager deadline fixtures accept
`WORKTREES_TEST_BUDGET_FACTOR=1|2|3`, default `1`. This is an explicit
per-run test opt-in, not a runtime setting or a new production deadline.
Selected fixture waits, deliberate delays and teardown budgets scale together;
semantic, source/cursor, restart-count and cleanup assertions remain intact.
The original nominal two-second goal is still measured separately.

Reports distinguish `contended_test_pass`, factor, actual elapsed time and
`nominal_qualified`. Factors above one never set nominal qualification true.
A contended-tier pass does not erase a failed nominal run, authorize merge,
or change production settings. The source effort's validation record carries
the bounded selectors, actual results and remaining performance work.

### Held current-roster feed and cutover

`list --current --stream --follow` is an additive held contract. `--follow`
implies current/stream authority even without the other flags. The same
authenticated control endpoint advertises `worktrees-feed-v1`; the CLI
negotiates that capability, relays complete transactions over stdout and
uses the existing owner token. It never silently removes `--follow` or
`--current` on capability skew. The Manager's local and machine-SSH loaders
use this path by default; `AGENT_WORKTREES_PICKER_STREAM=0` explicitly selects
the current one-shot compatibility path instead.

An admitted feed registers its project notification before reading its initial
publication. Its `begin` includes the complete projection, `selected_count`,
`cursor` (the project publication revision) and null `base_cursor`; `done`
commits all initial `worktree` rows. A later `update` names the same generation,
a strictly greater cursor and the prior committed `base_cursor`. Changed rows
use `worktree`, removals use `delete`, and `commit` atomically admits the result.
Each update also includes the complete committed identity order in `order`
frames of at most 128 IDs. Receivers require an exact permutation of the final
selected roster and rebuild in that order at commit, including reorder-only
publications and front insertions without re-emitting unchanged row values.
Selection changes caused by current facts are inserts/removals in that same
transaction. A publication racing initial delivery remains signaled until the
reader has incorporated it; readers never splice partial rows into a visible
roster.

At most 32 feeds are admitted per resident. Each subscription holds one
notification stamp, not a queue of row payloads. Slow readers diff their last
committed roster against the latest complete publication, coalescing any number
of intervening revisions without losing the final selected state. CLI/Manager
transaction buffers remain bounded by the two current rosters and each row's
schema, not historical update volume. Individual frames have a byte/character
bound. Initial and update transactions have total deadlines; heartbeat progress
cannot extend an unfinished publication. Idle heartbeats do not re-read or
decode an unchanged roster. Producer failure emits an explicit availability
change while preserving the last complete rows.

Relay error frames carry `error.code`, `error.message` and Boolean
`error.retry`. Disconnect and unready responses permit reconnect; live-peer
timeouts, malformed transactions, incompatible capabilities and authentication
failures are terminal for that load. The Manager retains labeled last-good
rows but stops automatic CLI respawn for terminal errors until an explicit
reload. The Manager separates its existing bounded source process/SSH startup
budget (20 seconds by default) from two-second snapshot/update publication
and five-second idle heartbeat deadlines. Only an admitted `begin` or `update`
starts the transaction deadline; intermediate frames cannot extend it.
Its own transient transport watchdog expiry permits cancellable reconnect
with bounded backoff and visible failed/stale state. Explicit terminal peer
errors remain terminal. Neither classification authorizes daemon restart,
polling fallback or caller-side fact computation.

Held projects remain demanded for the subscription lifetime; cancellation
releases the subscription and resumes the ordinary bounded linger. Planned
drain, update, shutdown or supersession sends `going-away` with reason,
generation and last cursor before the feed closes. Both feed subscriptions
and ordinary current-read handlers participate in monitor drain accounting,
including ordinary response delivery. The existing status-monitor installer
and activation/cutover path still own the daemon; no service, competing updater
or cutover deadline increase is added.
Successor promotion refuses adoption when current-roster authority activation
fails. The unpublished successor retains retry state; it cannot sweep or
acknowledge adoption until activation succeeds.

Each committed publication advances the Manager's relative age, last-used and
pulse presentation, including unchanged rows, without recomputing domain facts.
The Manager retains explicitly stale last-good rows during reconnect, then
replaces them only after a complete new-generation snapshot. Each retry starts
the selected CLI again, re-resolving daemon discovery. A retained route whose
listener refuses/resets a connection gets the existing bounded recovery;
a slow, malformed or incompatible live resident does not get rebooted or
license independent recomputation. Graceful notices and abrupt disconnects are
reported distinctly. Provider-exec sources retain their separately negotiated
one-shot contract. Full claims remain an explicit background details request,
not a feed payload.

## Provider-backed source registry

Venue providers may add project-scoped Picker sources by writing JSON registry
documents under `~/.agent-worktrees/sources/` (or
`AGENT_WORKTREES_SOURCES_DIR`). Each file owns one provider namespace and uses:

```json
{
  "schema_version": 1,
  "provider": "example-provider",
  "sources": [
    {
      "kind": "provider-exec",
      "project": "example-project",
      "target_id": "target:stable-id",
      "instance_id": "replaceable-instance-id",
      "label": "Restricted target",
      "alias": "restricted-target",
      "shell": "bash",
      "resolve": ["/absolute/provider-command", "resolve", "target"],
      "connect": ["/absolute/provider-command", "connect", "target"],
      "venue": {
        "provider": "example-provider",
        "target_id": "target:stable-id",
        "instance_id": "replaceable-instance-id",
        "transport": "provider-exec",
        "ready": true,
        "posture_verified": true,
        "assignment": {
          "kind": "lease",
          "effort": "example-effort",
          "acquired_at": 1700000000.0
        }
      },
      "capabilities": {
        "list": true,
        "messages": true,
        "sessions": true,
        "refresh": true,
        "create": false,
        "resume": false,
        "cleanup": false
      }
    }
  ]
}
```

The provider and stable target ID form the canonical source ID. The replaceable
instance ID does not: when live resolution reports a different instance, the
Picker invalidates rows derived from the prior instance before fetching again.
The assignment identifies the provider-owned lease captured at registration.
Live resolution must return the same assignment; a released or reassigned target
fails closed even if its stable target name is reused.
Descriptors are filtered to the active project, validated independently of
provider packages, and rejected unless their provider/target/instance identity,
assignment, transport, readiness/trust fields, capabilities, exact SSH alias,
and absolute resolve and connection commands are structurally valid. On POSIX,
the registry directory and descriptor files must be owned by the current user
and must not be group/world-writable. Display labels are bounded and may not
contain control characters. Schema version 1 permits only `list`, `messages`,
`sessions`, and `refresh` to be enabled. Mutation dispatch also rejects
provider sources independently of the capability map. One malformed source
entry does not suppress valid siblings in the same provider document, while
every descriptor sharing an ambiguous canonical source ID is rejected.

Each Picker setup or refresh captures the machine roster and provider registry
once. Tabs and the live loader consume that same snapshot, so a concurrent
registration cannot create a tab without a matching loader source (or vice
versa).

Provider rows retain blank legacy `machine` and `env` fields; they are scoped by
`source_id` and display `source_label` rather than inventing a physical-machine
identity. A source whose descriptor is not ready and posture-verified remains a
disabled tab and is not contacted. Operations fail closed against the source
capability map. The first
provider-exec contract permits only listing, recent-message/session reads, and
source refresh; unsupported create, launch, lifecycle, maintenance, profile,
and contributed worktree actions are not rendered and cannot fall through to a
local execution path. Row selection uses canonical source identity plus the full
worktree ID for provider rows; the four-character suffix remains display-only.
Message and session reads re-resolve the venue immediately and require the
row's recorded instance and assignment before SSH dispatch. Provider reads use
the descriptor's absolute connection command directly as an explicit
`ProxyCommand`; the provider rechecks the stable target, instance, and lease
assignment while admitting that exact connection. Active provider reads block
lease release and expired-lease reassignment, so validation and transport cannot
straddle a reassignment. The row also records a fingerprint of the provider's
alias, shell, resolve argv, and connection argv; immediate reads reject a
registry rewrite that changes any transport field after the row was displayed.
Provider worktree IDs are quoted as one remote-shell argument before execution.
The provider command uses an owner-private stable launcher that resolves the
currently active runtime and starts it in Python isolated mode, so registrations
survive runtime-slot updates without admitting project-local module shadowing.

Because the Manager self-updates **out of band** from the plugin (it wraps
Copilot launches; the plugin can't self-install mid-session), the two can be at
different versions on the same machine. So the `--json` verbs the Picker consumes
are a **stable, versioned contract**: the engine keeps them backward-compatible
within a contract window, and a newer Manager tolerates an older engine —
degrading a single feature rather than failing (the *version-skew-tolerant
contract* property). The current-roster authority capability is a deliberate
exception: older engines require an upgrade, because dropping that capability
would admit an independent computation rather than merely omit presentation.

This document **pins** that contract: the verbs, their required flags, and the
compatibility rules. Change a pinned verb's output shape only additively;
retiring or renaming one is a breaking change that must bump the contract
version and be coordinated with the Manager.

> **Contract version: `1`.** Bump on any breaking change (removed verb, removed
> or retyped field, changed flag semantics). Additive fields do **not** bump it.

## Git-disposition computation

Batch `list --json --classify` and per-worktree `worktree-status-bundle` share
`worktree_git_facts.compute`: checkout existence, git classification, finalized
tracking override, and session-turn CONVO refinement. The batch path does not
fetch and retains its observed live-session ACTIVE precedence. The bundle
requests a fetch with `active_paths=None`, keeping its independently reported
liveness fact from short-circuiting actual git inspection. Bundles use the
record's durable `session_turns`; batch lists use their targeted session scan.
Those observations and fetch modes may legitimately yield different answers.
Neither refinement discards the leaf's fetch-attempt/failure or sync fields.

Both coalescing request servers run inside the one resident status-monitor.
Their project-batch and single-worktree request shapes, rendezvous fields, and
cache policies remain distinct. Worktrees current-roster requests now read
owner publications rather than invoking those compute callbacks. Other bundle
consumers retain their separate contract; this does not assert consolidation
or a held incremental feed for every pivot.

Resident warming of recently requested list shapes shares read enrichment
within one pass when their tracking-status, platform and checkout-existence
selection is identical. The selected records, session scan, git classification,
mux observation and reclaim observations are computed lazily once for that
selection. Each requested shape still renders and publishes its own payload:
classification, mux details and assignment history are not leaked into shapes
that did not request them. Different selections remain separate.
Shared enrichment cannot be used for session-state stamping; resident warming
keeps that existing write path disabled.

These observations are discarded when the pass returns, including unavailable
reclaim observations. The next pass re-reads mutable state; ordinary standalone
reads, `--fresh`, output cache keys and existing freshness leases are unchanged.
This is per-pass work coalescing, not a new cross-pass cache or read authority.

## Invariants for every pinned verb

New and Resume prompts use one typed pending intent in
`<worktree-state-dir>/launch-seed.json`, staged through the resident daemon's
existing `tracking_write` authority. The same-code logged fallback follows
that authority's established no-request-sent/version-skew rules; ambiguous
sent writes are never silently retried.

Receiving-side mutation checks load the named project's execution-space
configuration without control-plane PR grafts. Machine-registry overlay
resolution likewise excludes PR discovery: neither authority path needs to
verify unrelated repositories' plugin activation. PR commands still load their
normal layered policy. This keeps prompt staging within the existing write
deadline without weakening project, machine, platform, or ownership fences.

Foreground New/Resume planning has no fixed aggregate deadline. The Manager
streams engine stderr while retaining stdout as the JSON plan and preserving
stderr error detail for version-skew handling. Configuration discovery,
repository synchronization, prompt staging and projection preparation report
start/end and elapsed-time heartbeats. Projection discovery/render waits for
completion at this boundary; sessionStart queues its separate bounded backup.
Individual Git, transport and mutation failure bounds remain in force, and
bounded automation may explicitly set a plan timeout. Slow preparation is not
a reason to abandon a successfully created checkout or skip projection work.

Preparation stages also append `launch_preparation` events to the owning
installation's rolling `logs/activity.jsonl`, without requiring a trace flag.
Each stage has a unique `step_id`, `phase`, `state` (`started`, `waiting`,
`finished`, or `failed`), monotonic `elapsed_ms`, process ID and, once selected,
worktree ID. Failures record only their exception type, never prompt text,
exception messages, environment values or command arguments. Seed transaction
execution records its own start/end in the receiving daemon, so a client
timeout can be distinguished from a transaction that later committed. Use
`activity --worktree-id <id>` or filter the JSONL by process and step ID; an
unmatched `started` event identifies the interrupted stage.

New and exact JSON Resume reuse the selected launch configuration when building
hook and owner environment values, rather than rediscovering plugin PR overlays
twice. The selected PR policy, including foreign-repo overlays, remains intact;
allocation-time locked policy revalidation is never cached or omitted.
Single-worktree liveness uses only that worktree's registered session IDs,
including bare sessions resumed from a different cwd, plus the mux probe.
An empty registry is cheap and does not trigger historical session discovery;
explicit recovery and the resident reconciliation cursor own registry repair.
Thus unrelated session history cannot multiply the cost of a resume check.

`session-role --hosting-evidence --worktree-id <exact-id> --json` is a separate
read-only corroboration mode for the Manager's private restart-evidence journal.
Its payload identifies `schema: "native-hosting-evidence"` and `version: 1`.
`confirmed: true` requires a current, unpaused active native head with real
conversation data, a live native process and a unique PID/birth-correlated mux
binding, followed by an unchanged owner-record reread. The result includes the
exact native UUID, native process birth identity, owner lifecycle revision,
worktree path, native platform, head revision and live mux/pane/incarnation
identity from the same binding observation. Dotted worktree ids are matched
using their canonical encoded mux name, not a lossy reverse-decoded id.
Positive Linux/WSL proof also matches the exact native session's open
`inuse.<pid>.hold` inode to that process and revalidates its birth identity.
`binding_proof: "inuse-hold-v1"` identifies this mandatory positive capability;
stale PID lock names, unavailable descriptors and unsupported native ownership
formats remain unconfirmed. Ordinary conservative liveness/death-detector
semantics are unchanged.
`recovery_binding_capability: "worktree-head-v1"` additionally binds the owner's
existing `worktree_incarnation` (creation nonce) and `head_revision`. The reader
never creates a missing nonce. Legacy or malformed nonce metadata leaves this
capability and incarnation null without changing ordinary hosting evidence.
The Manager preserves this historical binding for restart-candidate freezing;
it is not current task/hold admission or permission to launch.
Missing, uncertain, foreign, superseded or hosted-provider evidence
returns `confirmed: false` with a reason; unreadable owner state is a JSON error.
The mode requires an exact worktree id and never infers the daemon caller's
session, selects a filesystem-latest replacement, mutates a head, or launches.
Native Windows hosting proof remains explicitly unqualified. The payload's
`automatic_eligible` is always false: task policy, claims, holds, recovery
enrollment and launch admission remain their existing owners' responsibilities.

`embody --worktree-id <exact-id> --restart-request <json> --json` is an explicit
restart-only owner operation, separate from ordinary Resume and Bridge's
unseeded acquisition. Its 8 KiB object requires exactly `request_id` (canonical
UUID), `expected` (the restart-check object below), `source_epoch` (old
`platform`, `environment_id`, `boot_id`) and `native_incarnation` (`PID:birth`).
The old epoch must be different-boot/same-environment, and the old UUID/PID/birth
hash must match the recorded source activation. Current owner policy, dead
native process, absent mux and no staged typed/legacy seed are rechecked under
the existing lifecycle/native-admission guards and strict record lock before
invocation. Only an empty claim ledger or one canonical active producer-free,
nonopaque self-session claim for the exact original UUID and current
machine/project/worktree is admitted. The claim is retained unchanged. Mixed,
other or released claims, inbound ownership, dispatch provenance and profile
assignment remain unqualified; no task snapshot is promoted to an action-time
reservation. The same claim predicate governs explicit native acknowledgement.

The request ID is the Manager's existing frozen attempt UUID, not a replacement
conversation ID. Normalized native launch uses the exact original `--resume`
and full `--interactive` STATUS FIRST seed. Before mux invocation the owner
durably publishes `state: "launch-uncertain"`; only a successful mux return can
advance it to `mux-created`. Crashes, timeouts and failed outcome publication
never release the request for retry. An exact repeat returns the saved result;
a different request cannot overwrite an unacknowledged outcome. The receipts
contain identities and outcomes only, no transcript, seed text or executable
command. The private per-worktree store retains at most 16 receipts within
32 KiB aggregate, without eviction. No automatic caller or receipt-clearing
command is introduced.

Adding `--restart-receipt` is strictly read-only, even when no receipt exists.
It selects an exact retained request, including an older target boot in the
same platform/environment, without changing that frozen target or probing
current liveness. Launch and ACK still require their exact current boot.
The payload has exactly `schema: "native-restart-launch"`, integer `version: 1`,
the exact typed `request`, `state` (`not-reserved`, `launch-uncertain` or
`mux-created`) and false `seed_acknowledged`, `restored`, `automatic_eligible`.
The launch response additionally has `ok` and `repeated`; for these version-1
outcomes `ok` means only mux invocation succeeded, not that native restoration completed. `not-reserved`
cannot authorize launch. There is no unseeded, filesystem-latest, task-writer or
fresh-conversation fallback. Platform notification/startup and actual original
native identity/ready/seed acknowledgement remain separate qualification gates.

`embody --worktree-id <exact-id> --restart-ack <request-uuid>
--status-reconciled --json` is a mutually exclusive explicit acknowledgement
intent. The original resumed agent calls it only after the seed's status
reconciliation; neither Manager nor native registration emits it. No supplied
session environment variable, launch argv or caller-provided source witness
establishes this proof. Unsupported or unavailable native-source capture refuses
before configuration, fences or platform-specific receipt I/O. Under the
existing lifecycle/native-admission and strict record locks, it corroborates
the original UUID's live native hold, mux PID/birth, unique current activation
and exact target epoch against the captured invoking ancestry.
Current worktree incarnation/head revision must match the frozen request,
owner/activation revisions must advance, and the frozen original source must
remain unambiguous in retained history. Its inferred next-Start closure is
allowed; explicit/reasoned closure is not. Current pending handoff/seed,
pause/profile/dispatch policy and other/opaque/producer claims refuse. Only the
canonical active self-session claim introduced by normal native Start is allowed.

Successful publication advances either version-1 reserved state to integer
`version: 2`, `state: "seed-acknowledged"`, `seed_acknowledged: true`, and adds
exactly one `acknowledgement` object. Its exact fields are `native_session_id`,
`target_epoch`, `source_identity`, `native_incarnation`, `session_incarnation`,
`head_revision`, `owner_revision`, `activation_ordinal`, `status_reconciled`.
The status field is exactly true; the identities/revisions bind the actual new
execution to the frozen request. The bounded `session_incarnation` retains the
mux producer's token, including tmux's leading dollar sign (for example `$1:100`).
`restored` and `automatic_eligible` remain false.
`--restart-receipt` returns that version-2 object unchanged; the ACK response
adds `ok`, not launch/retry authority. An identical ACK revalidates current
execution/policy without rewriting storage. Missing/mismatched receipts,
changed executions and failed persistence refuse; no retry, clear or destructive
supersede operation is provided.

A new later-boot request can append only after the latest receipt is
acknowledged, with the same project/worktree/path/incarnation/head and original
native UUID. Its source epoch must equal that ACK's target epoch, and its source
PID/birth/identity and activation must match the ACK, with owner revision at
least the acknowledged revision. Existing current-owner fences still apply.
The entire history is validated on every read/write: request UUIDs and target
epochs are unique, no target revisits the initial source boot, and every
predecessor is acknowledged. Existing single-entry version-1/2 files remain
readable; multiple entries use an internal `native-restart-launch-history`
version-1 envelope without changing the individual public response schemas.
Reservation checks the 16-entry/32-KiB bounds, including maximum future ACK
growth and owner/activation counter capacity, before invocation. A failed
publication never authorizes clearing or retrying a launch. Historical ACKs
remain immutable while the latest request advances; ACK intent addresses only
that latest request. These contracts are not actual native CLI qualification.

`session-role --restart-check <expected-json> --json` checks a **single explicit
frozen identity** against the current worktree owner. The bounded (8 KiB)
object requires exactly `project`, `worktree_id`, `worktree_path`, `native_session_id`,
`worktree_incarnation`, `head_revision`, `owner_revision`, `activation_ordinal`,
`source_identity` and `target_epoch` (`platform`, `environment_id`, `boot_id`).
No ambient session, suffix lookup, nonce allocation or head mutation is used.
The response has `schema: "native-restart-worktree-check"`, `version: 1`, and
the exact typed `expected` object. `worktree_qualified: true` means the current
reduced worktree state, source-bound activation, transcript presence, proven
native death and absent mux matched, followed by an unchanged owner reread.
It is a provisional snapshot, **not** a reservation or a launch capability.
Changed heads/incarnations/lifecycle, terminal or paused worktrees, pause
history, pending handoffs/seeds, indirect/missing paths, ended or ambiguous
activations and uncertain/live processes refuse with a reason.
Mux listing errors/timeouts remain `mux-state-unknown`, not confirmed absence;
an empty successful listing is distinct from a failed probe.
The exact original self-session claim is also accepted by this provisional
check without a provider callback or new authority. Unknown/opaque,
producer-bearing, released, duplicate or foreign self claims refuse, as do
inbound ownership and a retained released-claim ledger.
Task provenance is not current task policy. A single active, producer-free task
claim with an exact `dispatch-local-task:` or `dispatch-central-task:` ref can
request `claim-status <id> --native-restart-policy` through its existing verified
claim provider, with a two-second callback budget and a required
no-self-provision guard. The registered writer prefix is preserved in both
legacy payload and same-cell peer invocation; a failed/old callback never
retries without the opt-in. There is no default-writer inference.
The versioned task snapshot must bind the exact task id, machine/worktree,
project lane and original native UUID to a started, positive-generation task
with no hold, steer, requested resume or active/preparing waiter.
Both `awaiting_steer` and the required Boolean `pending_steer` must be false;
an answered form with unread instructions is not an empty steering inbox.
Optional `task_policies` retains the matching ref and reduced owner snapshot.
Current generation is evidence for a future fence, not a generation reservation
or proof of continuity with an earlier task incarnation.
Opaque/unmatched dispatch provenance, legacy task refs, inbound ownership,
mixed/other/historical/producer claims and bound/profile-assigned worktrees
remain unqualified.
Unknown persisted state/ref/kind metadata is not canonical active-claim proof:
the owner codec retains `metadata_opaque: true` through projections and saves,
while preserving conservative active finality treatment. Malformed claim
collections/items retain opaque placeholders, not apparent absence.
Even a matching worktree always returns
`launch_authorized: false` and `automatic_eligible: false`.
Action-time task/claim admission, launch fencing and acknowledged restart-seed
pickup remain separate requirements. Ended shutdown activations are not reopened.
Malformed input or unreadable evidence is a JSON error. Native Linux/WSL
identity must match the requested target epoch; Windows, macOS and containers
remain unsupported rather than falling back to a different launch mode.

The Manager requests `resolve --stage-launch-seed --require-native-seed` for
prompted requests. The latter is a capability handshake: engines without the
native execution-leg fence reject it before allocation or staging. The fence
itself is unconditional in current engines, including direct stage callers.
Direct engine requests requiring this fence refuse remote/cross-environment
dispatch before SSH admission; the capability requirement is never silently dropped.
Launch-seed stage/take mutation protocol version 5 and structured
`launch_request_stage` version 4 exclude older resident handlers that lack that
fence, using the existing same-code fallback. Both retain launch-admission guards.

Staging validates the actual execution leg while holding the same strict
per-record lock that publishes the intent. Active, unknown, or opaque hosted
bindings refuse staging without replacing pending intent. Execution-leg
reservation and active/unknown publication acquire that same lock and refuse
while a typed or legacy launch prompt is pending. Thus a concurrent native seed
and hosted-provider activation have one winner, not two separately valid
preflights. Seed handoff also validates the leg in its record transaction.
Finishing the native handoff releases the pending-intent fence. An incompatible
activation never silently consumes or discards the seed; recovery history is
separate and does not reserve an execution leg.

`launch.seed_pending: true`, `launch.seed_id` and `launch.seed_kind` identify
the saved intent. The second resolve selects it with `--seed-id`, preserving
New/Resume provenance without restaging text or inferring configured argv.
Setup retains the seed until backend invocation; successful startup
acknowledges/discards it and supplies full `--interactive`. Setup refusal or
proven backend-start failure retains/releases it for the next cold attempt.
An ambiguous handoff keeps its intent and fencing identity, refusing automatic
resubmission. Older engines must refuse prompted requests before mutation;
ordinary seedless launches remain version-skew compatible.
Explicit replacement is a new intent identity. Reservations and acknowledgements
are fenced by that identity and a handoff token, so an old attempt cannot clear
or restore over a newer prompt.

Cooperative setup scripts declare the exact line
`# agent-worktrees:launch-seed-boundary-v1` and accept `-LaunchSeedRecord`,
`-LaunchSeedId` and `-LaunchSeedRuntimePython` (PowerShell), or the corresponding
`--launch-seed-record`, `--launch-seed-id` and `--launch-seed-runtime-python`
(Bash). They retain the saved intent through all setup
validation, then invoke the supplied runtime with
`-I -m agent_worktrees.launch_seed_exec --invoke --record <record> --seed-id <id> -- <backend> <args>`.
Only that backend invocation reserves ownership, and successful startup
discards it. The standard setup scripts implement this contract. Uninstrumented
legacy/configured setup commands fail closed for staged prompts, without execution
or record mutation; migrate to normalized setup or explicitly adopt this
boundary. Ordinary seedless commands are unaffected. A failure after backend
invocation may follow delivery and must not
automatically restore a potentially submitted prompt.

- **`--json` → stdout is JSON only; stderr is logs only.** No TTY prompts, no
  color, no picker. `--json` implies `--no-mux`.
- **Non-zero exit on error, with a JSON error envelope on stdout.**
- **Project scope is explicit.** The Picker always names the project — either the
  `<project>` binstub or `agent-worktrees --project <name>` — never relying on an
  ambient cwd.
- **Provider invocation is attributable.** The agent-worktrees front door hands
  the Manager an exact immutable runtime argv. A directly-invoked Manager
  uses the deployment manifest as the provider identity attestation, then
  follows the provider's marker/fallback order to its immutable runtime. It never
  resolves the engine from an ambient same-named command on `PATH`.
- **Unknown flags degrade, not crash.** A verb must treat an unrecognized
  forward-compat flag it does not know as a no-op where practical, so a newer
  Manager passing a new flag to an older engine still gets a valid response
  (the Picker already falls back — e.g. dropping `--classify` — when an older
  engine rejects it).
- **Additive-only evolution.** New fields may appear; existing fields keep their
  name and type within a contract version.
- **Invoked off the render flow.** Because every read and action is a subprocess,
  the Picker calls these verbs from **background workers**, never on its Textual
  event-loop thread — reads via the `LiveLoader` threads, actions via
  `PickerScreen._run_bg`. A slow or hung verb must degrade a single row/action, not
  freeze the UI (see the render-flow invariant in
  [architecture.md](architecture.md#the-picker-render-flow----never-block-on-cross-processio-invariant)).

## Pinned read verbs (the Picker's data plane)

| Verb (as invoked) | Purpose | Notes |
|---|---|---|
| `<project> list --json --current --classify --mux-details` | The core enumeration from one resident publication, with git-derived `state`, sync tags, mux details and normalized reciprocal presentation. | `--current` is mandatory and cannot be dropped. Other optional presentation/selection flags retain compatibility retries. An older authority is an upgrade error or explicitly advisory durable read, never caller-side recomputation. |
| `<project> list --current --stream --follow` | Held current-roster snapshot, incremental upserts/deletes, atomic commit barriers and cutover notices, relayed by the CLI. | Requires `worktrees-feed-v1`; cursor/reset/lifecycle rules above apply. Unsupported authority is an upgrade error, never silent caller-side fallback. |
| `<project> list-sessions --worktree <id> --json` | Sessions belonging to a worktree. | |
| `<project> recent-messages --worktree <id> --limit N --json` | Last few conversation turns (the read-only Messages overlay). | |
| `<project> picker-paths --json` | Low-frequency Picker runtime paths: `{"version":1,"install_dir":"<abs>","installed_plugins_dir":"<abs>"}`. | Contract v1 additive addition for Phase 3d Group A. These are one-shot setup reads (pivot registry scan), not render-tick data. |
| `<project> picker-bootstrap --json` | Runner bootstrap decisions: `{"version":1,"project":"<resolved-project>","should_switch_cwd":<bool>,"cwd":"<normalized-abs-path>\|null","default_live":<bool>}`. | Contract v1 additive addition for Phase 3d Group B. This exposes the high-level decision `runner._prepare()` needs without pinning private helper names or config objects. `default_live` is the engine-owned "not inside SSH" decision; `cwd` is populated only when the caller should switch to the resolved anchor first. |
| `<project> picker-reconcile-local --json [--worktree-id <id> ...]` | Group C's coarse-grained local reconcile-and-stamp batch: `{"version":1,"rows":[{"id":"<wt>","pr":{...}\|null,"prs":[...],"pr_count":N,"session_bound_live":true?,"session_lock_live":true?,"session_lock_stale":true?,"stale_lock_pids":[...],"mux_session":<bool>,"mux_clients":N,"mux_attached":<bool>},...],"summary":{"platform":"windows\|wsl\|linux","requested_worktree_ids":["..."],"record_count":N,"pr_terminal_count":N,"bound_visible_change_count":N,"had_unresolved_bound":<bool>,"mux_scan_ok":<bool>}}`. | Contract v1 additive addition for Phase 3d Group C. Omitting `--worktree-id` means "all current-platform local tracking records"; repeating it narrows the sweep for a future per-row refresh path. The row keys intentionally reuse the Picker's existing list-row names for the Group C-owned subset only, so the Manager can merge this payload onto today's downstream consumers without inventing a second vocabulary. Best-effort lock semantics stay unchanged: record enumeration is lock-free, provider/network work never holds a batch-wide tracking lock, and any tracking writes still happen only through the engine's existing short-lived stamp helpers. |
| `<project> state-root --json` | Resolve the state-root checkout for visibility-gated pivots. | Existing state-root envelope (`state_root`/`source`/`repo`/`stateless`/`requires_external`/`bound`/`error`) is now also consumed by the Picker's contributed-pivot visibility gate. Callers may parse the JSON even when the command exits non-zero for an unbound external state root. |
| `<project> stage-update --indicator-state --json` | Cosmetic staged-update glyph state: `{"version":1,"indicator_state":"paused|checking|available|current|idle"}`. | Additive Phase 3d Group A extension on the existing `stage-update` verb. A newer Picker tolerates an older engine rejecting `--indicator-state` by degrading this glyph rather than failing startup. |
| `<project> get <key>` | Scalar project value (e.g. `machine`, paths). | Plain value on stdout, **not** JSON — a deliberate exception for single-scalar reads. |

`picker-bootstrap` also returns `workspace`, the tracked invocation worktree's
absolute root or `null`. The anchor and its ordinary subdirectories do not
enumerate worktrees; a linked
checkout first validates its Git worktree identity against one tracking record
and the invocation path. Unconfirmed identities retain the legacy tracked-path
fallback. Bootstrap does not adopt a checkout or rewrite tracking state.
Bare-launch headless detection skips unrelated control-plane PR topology while
preserving the normal layered headless setting.

The JSON bootstrap also carries optional
`project_config: {"config_dir":"<canonical-project-state-directory>"}`. It uses
the same project-state path authority as `get config-dir` without loading layered
configuration or machine topology. `repo_dir` and `machine` remain absent.
The registry anchor used for bootstrap is not an effective `repo-dir` substitute:
a machine-local repo anchor override can legitimately differ. Compatible
consumers retain their existing effective repo-dir and machine queries, while
older consumers may ignore the additive field.

## Pinned action verbs (the Picker's control plane)

| Verb (as invoked) | Purpose |
|---|---|
| `<project> create [--json]` | Make a worktree, no launch (the programmatic "New worktree"). |
| `<project> resolve --json (--worktree-id <id> \| --new \| --base) [--bare-resume] [--machine <name> --environment <env> --target-no-mux]` | Emit the launch plan the front-end acts on: local resume/create/base-repo execution, or an environment-specific remote SSH handoff carrying the same selection. | Existing Group B remote seam; no payload/version change was needed for Step 2 because the remote plan already carries the Picker's public machine/environment answer (`action`/`ssh_alias`/`remote_command`/`machine`/`display_name`). |
| `<project> repair-stale-anchor --json` | Targeted stale-anchor self-heal: `{"version":1,"project":"<resolved-project>","status":"unchanged\|repaired\|still-missing","self_present_before":<bool>,"self_present_after":<bool>}`. | Contract v1 additive addition for Phase 3d Group B. One-shot background-safe repair hook replacing the in-process `_heal_stale_anchor_if_self_missing` dependency. |
| `<project> restart <id> --json` | Restart a worktree's session. |
| `<project> reclaim --worktree-id <id> …` | Kill the exact bound orphan process so a session can be re-opened. |
| `<project> reap-sessions --json [--worktree-id <id> ...] [--include-manager-owned] [--dry-run] [--grace-hours <hours>]` | Reap orphaned mux sessions in one background sweep. | Phase 3d Step 8 uses this for Manager-owned housekeeping: repeatable `--worktree-id` scopes the sweep to the Manager's own candidates, `--include-manager-owned` lifts the ordinary "hands off the Manager's mux lane" guard only for that caller, and the Manager may reuse the verb's existing dry-run / grace-window controls when validating or tuning that sweep. |
| `<project> reap-shells --json --yes` | Reap orphaned launcher shells in one background sweep. | Existing public verb, now consumed by the Manager's housekeeping thread instead of via an in-process import. |
| `<project> sweep-managed --json` | Reap leaked managed (`system`/`bridge`) worktrees in one background sweep. | Contract v1 additive addition for Phase 3d Step 8's focused housekeeping cutover. |
| `<project> sweep-finished-sessions --json` | Run the finished-session auto-clean sweep in one background pass. | Contract v1 additive addition for Phase 3d Step 8; preserves the engine-owned cleanup semantics while removing the Manager's last in-process import of the sweep helper. |
| `<project> finalize <id> --json` | Finalize a merged/completed worktree. |
| `<project> sync --worktree-id <id> --json` | Fast-forward a clean, strictly-behind worktree. |
| `<project> cleanup [--worktree-id <id>] [--clean] [--bare-only --yes] --json` | Remove completed/gone worktrees (single or bulk). |
## What is *not* in the contract

- **Human-formatted (non-`--json`) output.** Only the machine-readable shapes
  above are pinned; the pretty CLI rendering may change freely.
- **The bare, no-args invocation itself.** `<project>` with no args is the
  **seam** (DQ7): it resolves to the front-end (Manager if on PATH, else the
  bundled Picker) — it is not a data verb and returns no contract payload.
- **Backend-profile reads/writes.** The old `profiles get/apply` engine verbs
  are gone; the backend-profile grid now belongs to Worktree Manager's own
  control plane rather than this engine contract.
- **Internal helpers** the engine does not expose as `--json` verbs.

## See also

- [picker.md](picker.md) — the operator walkthrough of the front-end.
- [cli-reference.md](cli-reference.md) — the full verb catalog.
- Effort: `efforts/active/copilot-extensions/installer-configurator/` — the
  Phase 6 design (the binstub seam, DQ7/DQ8/DQ9) this contract serves.
