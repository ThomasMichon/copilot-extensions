# Standalone indexer architecture and four-piece decomposition

Back to the [effort](README.md). **Status: implementation design; four-piece
amendment proposed before code extraction.**

## Boundary and ownership

The system decomposes into four logical components with explicit contracts,
not four mandatory containers. The API/controller, execution and persistence
components may be independently hosted; native installation may colocate them.
The client remains lightweight. Standalone service distributions have their own
executables, release identities and installers rather than marketplace identities.
Reuse the existing implementation: extraction is a boundary change, not a rewrite.

The optional `agent-index` plugin/client keeps agent skills, configuration
resolution, trusted transport and read commands. It can describe how to reach
or explicitly provision a service, but plugin loading is not the service's
installation, update or restart authority.

Keep the existing `agent-index-engine` as the warm model/embedding program within
the execution component. Indexing workers perform source acquisition, chunking
and embedding coordination. They may share an execution deployment or use a
separate model companion; that physical choice does not collapse the API,
persistence or client responsibilities.

| Component | Owns | Does not own |
|---|---|---|
| Client/plugin adapter | Activation, scoped reads, transport, integration | Host provisioning on reads, store/model dependencies |
| API/master controller | Public API, admission, orchestration, worker assignment, query coordination | Direct database handles, model loading, fleet-specific placement |
| Indexing/embedding execution | Source acquisition, chunking, embedding, progress/result reporting | Queue arbitration, direct ownership of another component's database files |
| Persistence/actual DB | Durable jobs, corpus/vector state, query/storage operations and atomic state transitions | Worker/model execution, public client routing |
| Selected lifecycle authority | Restart, candidate activation, routing continuity | Private credentials encoded in portable artifacts |
| External release reconciler | Poll cadence, desired released version, deployment selection | A second implementation of installer/activation logic |

An embedding runtime is not interchangeable with the hosted indexing service.
The existing engine package split is prior art, not completion of this effort.

## Contracts and incremental extraction

The initial deployment remains colocated and backward-compatible. Establish
high-level component interfaces first, backed by the existing local adapters;
introduce remote adapters only after their contracts are exercised.

- **Controller to persistence:** durable enqueue/claim/progress/completion and
  source-scoped full/incremental ordering. The persistence owner commits state
  transitions atomically; multiple controller or worker processes must not
  bypass that arbitration.
- **Execution to persistence:** source checkpoints and idempotent content/vector
  batches, followed by safe reconciliation. Preserve existing crash-safe
  deletion ordering and content/vector fidelity guarantees.
- **Controller to execution:** typed task assignments, progress and cancellation/
  drain semantics; worker adoption remains valid across controller upgrades.
- **Query path:** the controller composes query embedding with persistence
  search/hydration. The client does not know which processes perform those steps.

The current persistence implementation is embedded LanceDB plus SQLite job
state and local checkpoint data. A DB container therefore needs a real storage
service adapter, not a mount that lets every component open those files.
Do not share SQLite files over a network filesystem or export LanceDB handles
as a pretend remote interface. Existing storage adapters remain valid for
colocated native deployment; a remote persistence adapter must enforce the
same behavioral contracts and schema/version compatibility.

**Package naming/location remain implementation choices.** The first standalone
API/controller program may compose the existing core as a normal Python library
while ownership is extracted incrementally. It must not rename the embedding
program and claim that the master/controller or DB separation has been built.

## Distribution and version-slot installation

Start from `worktree-manager`'s normal package/console-script and self-install
precedent. Reuse shared versioned-runtime primitives rather than copying its
entire installer or coupling the new executable to worktree management.

Proposed installer operations: discover a released candidate, install/validate
a slot, inspect status, activate, roll back and retain/prune eligible slots.
These are proposed semantics, not committed command spellings.

- Candidate slots are immutable and become eligible only after dependencies,
  source provenance and completeness validation succeed.
- The stable launcher selects the active complete slot. Activation and rollback
  serialize through one lifecycle authority and retain a last-known-good slot.
- Configuration, source credentials, index/task data, model caches and routing
  live outside disposable executable slots.
- Preserve legacy installation roots and configuration through an explicit
  compatibility adapter/migration; do not silently move or reinterpret them.
- A native installer and an optional image consume the same selected release
  identity and configuration contract.

**Open implementation choices:** final package location, whether any shared
client/config contracts need a small common package, and whether legacy extras
remain shims or migrate through a documented installer. Decide these against
the existing import graph and compatibility tests, not naming aesthetics.

## Configuration contract

Keep repository/knowledge/machine activation layers in the client adapter.
The service accepts an explicit validated host configuration without requiring
a checkout, a bound knowledge repository, Copilot or plugin discovery.

That configuration names corpus sources, durable storage, engine profiles,
resource limits and transport/listener policy. Secrets are injected through
operator-owned runtime mechanisms, not bundled into release artifacts or images.
Existing native configuration remains a supported input through the adapter.

Deployment placement, gateway device identity, fleet APIs, leases and provider
credentials remain outside this portable package. Operator-selected host versus
client roles must not degrade into machine-global accidental activation.

## Health, supervision and routing

Health must distinguish process liveness, service readiness, task progress and
model readiness. The route is valid only when its advertised instance identity
and endpoint are verified; a written file or live PID alone is not success.

The stable lifecycle authority, not a short-lived plugin hook, owns restart.
Supported native supervision must work without requiring Windows Scheduled
Tasks; optional OS-supervised adapters remain an operator decision. A container
can use its runtime's restart policy, with the same readiness/state guarantees.

**Boundary requiring implementation design:** event-driven `ensure` repairs a
dead endpoint when invoked; it is not continuous crash supervision. A persistent
native service owner must be explicitly installed/configured to promise
unattended recovery. Do not disguise a per-session watcher as that owner.

Reuse zdd's passive successor, health gate, routing flip, drain/adoption and old
instance retirement. Success must include survival after the updater exits.
Keep a single queue-writer/adoption authority across overlap. Full reindex,
source-scoped reindex and incremental requests retain their established ordering.

## Released-main reconciliation

The external deployment controller periodically checks **released** `main`,
not `dev`, then delegates service lifecycle actions to the package's installer
or deployment adapter. The controller and its concrete fleet integration are
downstream; upstream defines only the portable release/lifecycle contract.

Recommended release descriptor fields _(agent-recommended)_:
service version, exact released source commit/artifact identity, digest,
configuration/API compatibility information, and supported runtime platform.
Do not use contribution-SHA ancestry as promotion proof: generated `main`
snapshots have a different history.

Reconcile one pinned candidate per transaction:

1. Discover and verify the released descriptor; apply the operator's update policy.
2. Acquire the deployment lease and stage the exact candidate without mutating
   the active slot or durable state.
3. Validate slot/image completeness and compatibility, then start a passive candidate.
4. Verify readiness and activate through the existing cutover protocol.
5. Confirm the successor remains healthy after controller exit; retain the
   previous eligible version and emit a truthful result.

Controller outage leaves the current healthy version serving. Repeated polls
are idempotent, failed candidates back off, and concurrent controllers cannot
both activate or prune a live slot. Poll interval, credentials, placement and
container-controller implementation are configuration, not upstream constants.

## Optional generic container

A reference Dockerfile is optional. If useful, it builds the standalone service
from a pinned release, runs as a non-root user, advertises readiness and handles
graceful termination. It must not require a named fleet platform.

Persistent corpus/task data and engine model caches are explicit mounts.
Configuration and credentials are injected at runtime. Document whether the
engine is a separate companion or explicitly bundled; do not implicitly pull
the heavy stack into every client or lightweight service image.

Native slot activation and container image replacement are separate deployment
adapters: do not mutate running containers in place to imitate host slots.
The shared contract is selected release, health gating, durable state,
single-writer queue authority and supported rollback.

## Compatibility and rollout gates

- Existing read commands, source designation, transports and endpoint shapes
  remain usable through a documented compatibility boundary.
- Install/update/ensure use one effective-config resolution path.
- Preserve durable corpus and task schema. Executable rollback is allowed only
  when the target understands the current data/queue/config; incompatible
  migration requires its own backed-up, reviewed plan. _(agent-recommended)_
- Preserve independently managed warm-engine upgrades and Windows
  normal-priority model-load/background-inference sequencing.
- Validate native client/host parity first, then a clean optional-container
  installation, then authorized live deployment and route migration.

See the effort's [validation plan](README.md#validation-plan) for acceptance.
The current proposal is not permission to migrate an existing live index.

## Prior art

- `worktree-manager/pyproject.toml`, `src/worktree_manager/self_install.py` and
  `docs/configuration.md`: standalone distribution, version slots and source config.
- `plugins/agent-index/pyproject.toml`: existing light/store/server split.
- `plugins/agent-index/server/pyproject.toml`: separately packaged embedding engine.
- `plugins/agent-index/src/agent_index/indexing/runner.py`: queue and worker adoption.
- `docs/patterns/durable-vs-versioned-runtime.md`: separate model-runtime lifecycle.
- `plugins/agent-index/docs/standalone-service-lifecycle.md`: current native lifecycle.
- `docs/pipelines.md`: generated release snapshots and contribution/release separation.

Paths above are repository-root waypoints, not architecture already shipped
under the proposed package name.
