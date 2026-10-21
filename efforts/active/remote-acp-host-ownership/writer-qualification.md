# Retained-transfer writer qualification and exclusion

Parent: [Remote ACP Host Ownership](README.md) / #7237.
Status: **Proposal; source-only review before implementation.**

## Request and boundary

The operator selected:

> Prepare a source-only writer-exclusion design for review before enabling transfer (recommended)

Everything below is **agent-recommended design**, not an installed-state claim.
This amendment adds a prerequisite to the reviewed
[relationship authority](relationship-authority.md), not another claim ledger,
service manager or release process. Public retained offers/provider registration
remain disabled until the implementation and qualification matrix pass.
Reviewing or merging this proposal authorizes no promotion, installed update,
daemon restart, credential grant or live external request.

This proposal closes existing intent in
[externally hosted ACP agents](../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents),
[consumer-enforced compatibility](../../../visions/plugin-services/README.md#compatibility-enforced-at-the-consumer)
and [state-aware rollback](../../../visions/plugin-services/README.md#state-aware-rollback).
It composes the existing
[graceful-cutover pattern](../../../docs/patterns/graceful-daemon-cutover.md)
and installation-scoped authority. No new resident process, global inventory,
cross-plugin database access or arbitrary same-user threat model is proposed.

## Why the existing gates are insufficient

`db_schema._SchemaMixin._init_schema` prepares writable databases even when
their version exceeds the writer's `SCHEMA_VERSION`. Read-only adoption's
exact-version check does not protect writable constructors. A new schema number
or a new CLI's HTTP capability check therefore cannot exclude an already-open
old SQLite connection, a background writer or an old command accessing the same
database.

`db_core._CoreMixin` owns thread-local SQLite connections, an event-writer
connection and only an in-process write lock. It is not a cross-generation
writer lease. `db_relationships` also performs explicit multi-statement
transactions; guarding only `execute_write` would miss those paths.

Conversely, database write exclusion cannot stop an old client already sending
to the peer. The existing lifecycle's safe drain must settle that work first.
Neither a route flip, a health response, a package version nor an empty process
search proves that boundary.

Worktrees' ledger is a different authority. Bridge's database protection cannot
qualify a legacy full-record YAML writer or replace Worktrees' ordered locks,
protected metadata, bundle schema and provider callback checks.

## Proposed contract

### 1. Bridge-local durable write-compatibility boundary

Use SQLite's own mutation boundary rather than a second advisory lock that
historical writers do not acquire. The proposed mechanism is a persistent set
of write-compatibility triggers backed by one connection-local, stdlib SQLite
function declaring the supported writer contract. Its contract generation is
separate from the schema, plugin version and HTTP protocol.

- Register the function on **every** compatible writable connection before
  migrations or application writes, including event-writer and recovery
  threads. Keep database-core bootstrap stdlib-only and inexpensive.
- Install and verify the complete trigger set atomically through ordinary
  writable schema preparation. Passive/read-only candidates neither install
  it nor register a writer. Read-only inspection remains possible.
- Fence INSERT/UPDATE/DELETE on every table reachable by a participating old
  writer, including session/target rows, relationship rows, prompts, turns,
  events, live-state and schema metadata. Inventory that closure from the
  supported source generations; do not claim coverage from a hand-picked
  relationship-table guard.
- A connection without the function, or declaring an insufficient contract,
  must fail the SQLite statement before effects commit. Verify preexisting
  connections and previously prepared statements, not only fresh constructors.
  Missing-function failures must remain visible as incompatible/unknown
  outcomes, never success, an empty queue or a silently dropped event.
- Compatible writes still use existing transactions and locks. No trigger
  transfers ownership, closes an obligation, changes a native ID, reconnects
  the peer or repairs a partially understood journal.
- New writable startup also rejects unsupported future schema/contract values
  **before** schema scripts or backfills. This improves future behavior but is
  not represented as retroactive protection of historical binaries.

Missing SQLite functions can prevent preparation of a mutation even when a
row condition would not match. The intended compatibility boundary is therefore
the **whole shared Bridge database**, not one session. An incompatible writer
sharing that database is excluded from unrelated writes too; a separate
installation/database and read-only consumers remain unaffected. Do not market
this as a transparent row-scoped safeguard or disguise that compatibility cost.

This is cooperative version exclusion, not protection against an administrator
editing SQLite, dropping triggers or registering a fake capability function.
Do not add signatures, secret tokens or a daemon to address that different
threat model.

### 2. Quiescence and activation use the existing lifecycle owner

Before installing the fence or admitting retained transfer, the existing
installer/cutover owner must establish that all predecessor generations which
can hold this installation's database or local remote clients have reached
their safe boundary. Reuse installation provenance, PID/start identity,
read-only preparation and the existing drain/retirement protocol.

- A passive candidate cannot migrate a shared database merely to become ready.
- A compatible predecessor may overlap only when its writer contract is
  qualified and its active local work is covered by ordinary cutover.
- An incompatible predecessor must finish an acknowledged clean drain and
  release its local admission before the new boundary becomes usable.
- Missing identities, an incomplete predecessor census, a busy/unknown drain,
  a stale activation record or an unqualified rollback target refuses
  activation. It never authorizes process-name killing or treating a timeout
  as absence. If the current lifecycle cannot establish this proof, return to
  design review rather than substitute an ad hoc supervisor.
- After the durable fence commits, rollback may select only a generation
  compatible with both the writer contract and current state. Otherwise keep
  the healthy compatible owner, commit forward or expose explicit recovery.
  Never remove the fence or restore an old writable runtime automatically.

Persist necessary compatibility facts in the existing Bridge-owned database
and installation lifecycle state. Identify them by that authority, not a
checkout path. A surviving persistent fence is not a permanent certificate of
current lifecycle quiescence; revalidate the existing owner at the actual
activation/transfer boundary.

### 3. Transfer capability is stronger than an HTTP route

The guarded transfer route alone is not qualified transfer support. Define an
explicit live writer-qualification capability on the installed service and
gate the CLI/API/provider on it, separately from the existing remote-owner and
attention capabilities. Allocate any new HTTP generation during implementation
without reusing an unrelated capability's generation.

The service must confirm its prepared fence, qualified writable connections,
current lifecycle ownership and supported Worktrees participants before
acknowledging transfer. Unsupported, malformed, stale or unavailable evidence is
a refusal/unknown outcome, not automatic service ensure, schema migration,
claim release or an unowned fallback. Maintain exact bundle/subject receipts,
no automatic mutation retry, and pending-journal admission fences.

### 4. Worktrees qualification remains independently mandatory

Use the existing installation-qualified provider and normal command boundary.
Do not import Worktrees or read its private records from Bridge.

Qualify the complete participating Worktrees writer generation: retained
metadata preservation, generic claim writes, stale full-record saves,
finalization/abandon, registry edits, reservation inspection and version-2
acceptance/cancellation. A compatible command in isolation does not qualify a
long-lived process holding an older parsed record. Such holders must be drained
or reconstructed through the existing owning lifecycle and record locks.

Known unsupported generations remain excluded from the participating release/
installation closure. Unknown or unaudited full-record writers keep transfer
disabled. A database trigger, JSON capability from one command, or a new YAML
field that an old parser ignores is not their exclusion proof. If existing
Worktrees ownership cannot establish that closure, stop at this prerequisite
and amend the design; do not add a second ledger or silently narrow the matrix.

## Ordered implementation and acceptance

Do not implement the new exclusion mechanism before this proposal merges.

1. **Inventory and independent witnesses.** Enumerate Bridge and Worktrees
   writers and their actual command/connection paths. Freeze exact historical
   source/blob evidence for the supported boundary. Add failing tests for
   future-version writable startup and old writes against a transferred
   journal. Stop with a named gap for any writer not exercised or excluded.
2. **Isolated SQLite prototype.** Prove the persistent function/trigger
   mechanism against already-open and fresh historical connections, prepared
   statements, transactions, multiple threads and foreign-key cascades.
   Verify supported SQLite versions on Windows and Linux. If the prototype
   cannot exclude old writes without an unreviewed compatibility expansion,
   revise this proposal before production integration.
3. **Owning startup/lifecycle integration.** Add compatible connection setup,
   atomic ordinary preparation, read-only refusal and lifecycle-bound
   activation/rollback checks. Cover explicit transactions and event batches,
   not only the generic helper. Retain existing safe cutover and unknown-error
   behavior; no installer invocation or shared restart is part of source tests.
4. **Worktrees writer qualification.** Run the full preservation and
   interruption portfolio against each participating generation. Demonstrate
   how incompatible long-lived holders are excluded; unresolved holders remain
   blocking rather than becoming a supported-old-writer assertion.
5. **Transfer activation and evidence.** Only after the preceding gates pass,
   register the provider and advertise public retained transfer. Repeat the
   real command/CLI/API/TLS peer acceptance, cancellation, lost-reply,
   concurrency and restart matrix against the qualified boundary. Re-attest
   changed immutable wire/durable-state evidence from committed source.

All steps remain source-only. Release and installed qualification belong to
separately authorized owners; a source PR does not assert they happened.

## Required validation matrix

| Area | Required evidence |
|------|-------------------|
| Historical writable startup | Actual old source accepts the newer schema before the repair; its mutations are excluded after preparation, including an already-open connection. |
| Complete write closure | INSERT/UPDATE/DELETE, direct transactions, upserts, event batches, queued prompts, session/target updates, schema backfills and cascades cannot evade the fence. |
| Compatible concurrency | Main/event/recovery connections retain atomicity, visibility and existing transaction semantics; no process/thread-local bypass or recursive trigger dependency. |
| Preparation failure | Interruption leaves the old compatible state or the complete new fence, never a half-installed boundary or a writable passive candidate. |
| Old in-flight work | An actual historical managed caller cannot send after confirmed drain; fence installation alone is not credited as network quiescence. |
| Restart and rollback | Pending/committed/cancelled transfer journals survive; a prepared compatible owner resumes, and an incompatible predecessor cannot be selected for writable rollback. |
| Worktrees writers | Generic/stale saves, finalization, registry writes and version-2 acceptance/cancellation preserve blocking obligations and exact acknowledgements at every interrupted boundary. |
| Negotiation | Old/unversioned services, missing proof and an available route with unqualified writers refuse before mutations or provider registration. |
| Isolation and platforms | A fresh contained database, ordinary CLI/API and synthetic TLS peer pass on qualified native Windows/Linux lanes; separate installations and read-only inspection remain unaffected. |
| Performance and operations | Existing protocol budgets and separate fixed-sample perf flow remain intact; no new per-turn process/hash scan and no claim of an established performance baseline. |

Record exact commands, source identities and results. Explicitly name an
unavailable native lane, first-install clean-room tier or live venue and why;
do not substitute a version stamp or waive the missing gate. Any inability to
prove the exclusion/activation contract leaves public transfer disabled.
