# Fleet control V2 - architecture and migration proposal

Back to [the canonical effort](README.md).
**Proposed target, not shipped runtime or authorization to deploy.**

## Decision

Use fleet-manager as one independently installable control product with a
Gateway coordination core, thin CLI/Worktree Manager/Copilot clients, and
node-local execution. The same core supports standalone local and explicitly
adopted multi-machine deployments. Do not add a second broker above fleet-manager.

One release/lifecycle owner does not mean one OS process. Transactional control
state belongs together; accepted agent/mux execution, credential custody,
providers and heavyweight workloads require independent failure boundaries.
One selected manifest pins roles, dependencies and supported protocol ranges;
users do not independently negotiate every integrated component's version.

## Alternatives and adversarial limits

| Option | Benefit | Limitation | Decision |
| --- | --- | --- | --- |
| Harden existing suite only | Least migration | Fragmented cross-domain ownership remains | Support/stabilization lane |
| Unified release, existing managers | Removes hidden setup and version confusion | Does not make agency/task/effect joins transactional | First delivery step |
| One process for all capabilities | Small apparent topology | Couples crashes, dependencies, privilege and lifetime | Reject |
| Integrated Gateway product | One operation authority, reusable isolated executors | Requires distributed protocol and explicit migration | Target |
| Rewrite every component | Maximum freedom | Recreates platform/provider edge cases at once | Only if reuse demonstrably blocks target |

A DB cannot atomically commit Git, SSH, provider or prompt side effects.
A unified release cannot upgrade remote nodes, schemas and external providers
atomically. Heartbeat expiry does not prove remote execution stopped. Enrollment
is not authority migration. These are constraints, not implementation details to
leave for later.

## Deployment and ownership

```text
Standalone: clients -> local socket/pipe -> Gateway core -> local node/executors

Fleet:      thin clients -> authenticated encrypted API -> Gateway core
                                      outbound connector channels
                                          /             \
                                      node A           node B
                                 execution/receipts execution/receipts
```

| Role | Owns | Must not own implicitly |
| --- | --- | --- |
| Gateway core | Domain objectives/tasks, assignments, canonical agency, claims, operation/outbox log, accepted results; scoped routing/enrollment | Provider capacity, substrate identity, secret issuance, unmigrated external state |
| Client | Explicit endpoint/authority selection and command/result presentation | Controller hosting, credential feeding or worker deployment |
| Node | Scoped local admission, process/resource identity, target receipts and result spool | Second global task queue, speculative reassignment or unrestricted remote shell |
| Session/provider executor | Actual effect, dedup/fence capability and honest outcome | Global objective completion or automatic trust widening |
| Credential provider | Issuance, identity/audience, expiry/revocation and custody | Generic event/task secret storage |
| Installer/deployment manager | One declared application lifecycle; external manager retains container placement | Competing updater, arbitrary apply payload or authority from a healthy container |

Default to one logical Gateway authority per explicitly configured domain.
Standalone commonly scopes that domain to an OS user. Fleet clients do not each
create a competing coordinator. Namespaces are not hostile-tenant isolation;
strong separation may require OS principals and separate authority domains.

Existing authorities remain owners until an explicit migration replaces them.
An offered V1 task/worktree service is a route to its current owner, not a
Gateway-side parallel authoritative copy. Domain modules may share the V2
transaction store without each becoming a separately installed daemon.
Independent index/evidence/credential services need not move into that store.

## Command, effect and receipt contract

Every mutation carries an operation ID, canonical request digest, authority
domain, principal/account grants, target and relevant allocation generation.
Same identity/content returns the original result; conflicting reuse is refused.
Persist task/claim/audit/outbox admission together, with short transactions and
no lock held across external work.

Distinguish durable Gateway admission, node acceptance, effect outcome, and
obligation settlement. A transport acknowledgment is not objective completion.
For cooperative targets, persist effect-side deduplication and receipts. For
uncooperative effects with lost acknowledgment, report UNKNOWN and block blind
retry/finalization until explicit reconciliation.

Nodes persist accepted work/results independently of the connection. On reconnect
reconcile receipts and replay/snapshot cursors with authenticated generations;
old connector cleanup cannot erase a successor's registration.
Nodes never write a Gateway SQLite DB over a network mount.

## Distributed fencing and partition policy

Check authority where effects occur. A Gateway epoch alone cannot instantly
revoke an isolated node. Use target-enforced resource fences and scoped delegated
grants with bounded validity and explicit renewal/revocation. A node-local token
cannot fence a shared resource on another node.

Already admitted work may continue within its scoped local authority and spool
results. New fleet-wide allocations/nondelegated effects fail closed. Offline
autonomy requires explicit resource/time/budget limits, never hidden local
coordinator failover. Expiry bounds new permission, not an already-running effect.
Reassignment waits for release, verified retirement, or a provider fence that
actually excludes the old holder. Revocation is not instantaneous during a
partition unless each effect requires online authorization.

Start with a recoverable single stateful Gateway writer. Local OS locks are not
cross-host fencing. Moving the Gateway requires proven storage continuity and
writer exclusion, or an explicitly chosen database/consensus authority.
Active-active SQLite copies and generic container rescheduling are not HA.

## Installation, activation and adapters

Independent fleet-manager setup/update/doctor/recovery operates before Copilot.
Worktree Manager is the workspace/launch client, not a UI monopoly or another
fleet authority. Hooks/stubs bind context and invoke APIs; they never download,
install, upgrade or implement supervisors.
Bounded activation of an installed configured service is distinct from installation.
Missing/incompatible/auth-needed states fail explicitly; guidance stays usable.

One lifecycle implementation owns all integrated role slots and registrations,
including isolated workers. Credential/OS separation may require several
registrations, not several independently implemented updater/keepalive systems.
Third-party adapters use typed named operations, not in-process unrestricted
code or arbitrary executable/URL/config payloads.

Stage immutable candidates; preflight dependencies/schema/protocol/storage;
stop new admissions; transfer writer ownership with fencing; reconcile hosts/
receipts; verify readiness; then retire only the verified old generation.
Prefer bounded admission interruption to unfenced overlapping writers.
Accepted execution survives control replacement.

Failed pre-transfer activation leaves the old owner usable. Post-transfer
recovery uses a compatible binary under new authority or commit-forward;
never resurrect a stale epoch or overwrite acknowledged writes with a backup.
An import/open port is not runtime readiness.

Release activation is rolling and per-target: desired, admitted, installed,
active, ready and converged are distinct. Offline nodes remain unresolved;
compatible old nodes can drain work. Incompatible new admissions fail without
destroying existing receipt paths. Gateway self-update is performed by an
independent installed lifecycle authority, not its own executing environment.

## Finite migration and existing campaigns

1. Establish release/role/readiness contracts while pinning reusable components.
2. Prove one objective-to-settlement slice locally and across two nodes.
3. Freeze the selected V1 domain's admissions, import stable identities, enumerate
   hosts/claims/receipts, reconcile and explicitly transfer ownership.
4. Adopt compatible hosts or drain them under their old authority; no dual writes.
5. Retire verified old writers/feeders/registrations only after obligations settle.

Publish a drain deadline with named residual owners and escalation. Do not kill
live work merely because the deadline elapsed. Unresolved adoption/retirement
requires explicit permission and leaves migration blocked, not falsely complete.
Compatibility shims call V2 only and have a published support window.
The [source inventory and migration gates](v1-migration.md) refine the required
owner-approved snapshot, identity, receipt and transfer evidence; they do not
authorize or establish a live cutover.

Routing #5789, Linux role deployment #6030, named reconciliation #5861,
observation #5862 and standalone client PR #6133 keep their existing scopes.
Agree interface/ownership changes before borrowing implementation. This effort
does not open a competing patch to another contributor's still-open work.

## Proof before adoption

The effort's Validation Plan is the completion gate. In particular require
clean first-install, real process concurrency/crashes, real host survival,
Gateway plus two nodes, revoked/stale/cross-domain denial, mixed-version/offline
outcomes, partition plus attempted reassignment, actual target fencing, storage
relocation, and V1 retirement/reinstall behavior.

An earlier synthetic local SQLite model motivates intent/receipt recovery,
content-bound retries, stale-owner refusal and UNKNOWN. It does not prove remote
fencing, security, installer cutover, power-loss durability or performance.
Set measured performance/availability targets from baseline, not package count.
Record unit/E2E/clean-room/live tiers and blocked or skipped platform evidence.

## Intent reconciliation

Extends machine-fleet with the same coordination core in local/Gateway roles and
explicit replacement of migrated owners; preserves external service authority.
Extends agent-fabric/plugin-services with an opt-in integrated product profile:
capability selection and guidance independence survive, while integrated
runtime-backed adapters can depend on their explicitly adopted product.
Existing standalone defaults and lifecycle/security invariants remain in force.
