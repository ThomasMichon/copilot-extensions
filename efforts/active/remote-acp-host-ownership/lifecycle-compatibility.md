# Lifecycle-bound writer compatibility

Parent: [Remote ACP Host Ownership](README.md) / #7237.
Status: **Proposed; source-only design, not activation authority**.

## Request and scope

The operator selected:

> Prepare a source-only lifecycle compatibility amendment for review (recommended)

This document is **agent-recommended design**. It refines the
[writer-qualification plan](writer-qualification.md), specifically its owning
startup/lifecycle integration and Worktrees writer-closure prerequisites.
Review and merge must precede the new lifecycle mechanism. Public retained
transfer stays disabled until the complete implementation and qualification
gates pass.

The proposal closes existing
[consumer-enforced compatibility](../../../visions/plugin-services/README.md#compatibility-enforced-at-the-consumer)
and [state-aware rollback](../../../visions/plugin-services/README.md#state-aware-rollback)
intent. It composes the existing
[cutover owner](../../../docs/patterns/graceful-daemon-cutover.md),
[process-slot authority](../../../docs/patterns/process-slot-ownership.md)
and Worktrees' [write-authority effort](../agent-worktrees-authoritative-daemon/README.md).
It does not establish another service, ledger, process inventory or release path.
No promotion, installed update, shared restart, forced retirement, credential
grant or live peer operation is authorized.

## Source findings that select this design

The findings are source observations, not a live installation diagnosis:

| Boundary | Relevant source behavior | Consequence |
|----------|--------------------------|-------------|
| Bridge ordinary passive startup | `agent_bridge.app.lifespan` constructs the writable database before applying passive role restrictions. | `--passive` alone is not migration-free preparation. |
| Bridge private candidate | `restored_app.create_restored_app` prepares a read-only database; `db_adoption.open_existing_writable` acquires an existing exact-schema WAL database without bootstrap or an event writer. | Reuse this preparation/adoption separation rather than create a second candidate application. |
| Ordinary ZDD cutover | `CutoverOrchestrator` normally publishes before drain. Its stateful `drain_before_publish` opt-in and caller-held canonical cutover lock already preserve unresolved writer-reconciliation breadcrumbs. | A state transition needs the stateful owner path, not ordinary publish-then-drain. |
| Conditional ZDD repair | Conditional two-phase activation runs after publication but before drain; conditional and pre-publication drain cannot be combined. | Do not stack those flags or claim conditional health attestation solves this migration. |
| ZDD rollback | `cutover_rollback` can restore/undrain a predecessor using process, route and health evidence without durable-state compatibility. | Add an explicit state-compatibility decision before restoring writable admission. |
| Bridge manager drain | Drain covers the manager's own busy work and has an auto-release watchdog. Standalone `agent` commands can own independent databases and remote clients. | A clean manager response does not close installation-wide writer or peer-effect admission. |
| Worktrees writes | `tracking._save_record_unlocked` explicitly protects current-code stale snapshots only. `tracking_write.run_direct` remains a supported same-code fallback. | One upgraded resident, command or capability cannot exclude an older full-record writer. |

The prior isolated SQLite proof establishes an old-connection mutation fence,
not safe network quiescence, ordinary installer integration or Worktrees closure.
None of those results should be reinterpreted as the latter.

## Selected boundary: compatibility preparation before state transition

Adopt a **two-stage compatibility floor**, not a retroactive promise that a new
binary can make every old process cooperate.

### Stage A: compatibility-preserving admission preparation

First qualify an admission-capable generation while leaving the durable writer
fence and retained transfer inactive. Its ordinary commands, standalone ACP
agents, resident writers, event/recovery threads and supported library hosts
must participate in their existing installation owner's admission and lifetime
authority. Preparation may add compatible bookkeeping but must preserve the
old data contract.

The installation owner must identify the supported launch closure: selected and
fallback runtimes, service registrations, generated launchers, automatic
recovery and long-lived import-based callers. Preventing a new old-slot launch
and settling an already-running old holder are separate requirements. A
repointed binstub does not settle an old interpreter or caller.

**Legacy installations are not automatically eligible.** A process that predates
admission participation must reach its normal safe boundary and have exact exit
confirmed by its existing lifecycle owner. Unknown holders or an incomplete
launch closure refuse the transition. The owner may require a separately
authorized maintenance window; this proposal neither supplies that consent nor
permits killing non-resumable work to manufacture absence.

Use existing installation/slot custody, native process references and lifecycle
records. Extending their admission coverage is work for those owners, not an
auxiliary Bridge census. A registry row, version scan, lock available only to
new code or empty best-effort process search cannot certify legacy closure.
If the owning primitives cannot prove that closure on a platform, Stage A
remains unqualified there and Stage B cannot run.

### Stage B: one owner-controlled durable transition

Only an installation whose Stage A closure is qualified may prepare the
persistent Bridge writer fence. The existing Bridge installation owner should
hold its canonical cutover transaction authority across preparation, drain,
state commit, publication and recovery. Reuse stateful pre-publication drain;
do not change ordinary cutover ordering for unrelated services.

The required order is:

1. **Capture and validate.** Pin installation/configuration/database identities,
   the predecessor's native process identity, the supported writer floor and
   unresolved recovery state. Refuse stale or incomplete evidence before effects.
2. **Prepare without migration.** Stand up the existing read-only candidate.
   It starts no event writer, mutates no database, performs no remote sends and
   does not publish serving readiness. Fresh-install schema creation is a
   separate no-predecessor owner path, not a passive-start exception.
3. **Close admission and drain.** The owning installation closes new writer and
   remote-effect admission for the participating closure, including short-lived
   commands and standalone agents. Let existing work reach its safe boundary.
   Require clean, unforced acknowledgement and exact retirement where an
   incompatible holder cannot remain. Hold admission closed until a terminal
   owner decision; the ordinary manager auto-undrain watchdog cannot reopen it.
4. **Commit the state boundary.** With that closure still held, the owner performs
   the complete SQLite fence preparation and its compatibility facts atomically.
   The candidate revalidates database identity, schema and writer contract before
   migration-free writable adoption. Register the connection capability on all
   adopted/main/event/recovery connections before they can write.
5. **Publish and activate.** Only a candidate verified against the committed
   boundary may become the serving writer. Publication, activation and singleton
   adoption retain the owner's existing exact-instance checks. Retire the exact
   predecessor through its existing safe lifecycle, confirm exit as required,
   and clear recovery state only after final readiness and ownership checks.

The stateful adoption/publication adapter remains responsible for its own atomic
authorization. A failed attempt must not fall through to ordinary `serve`,
automatic service ensure, stop-and-swap or a second migration path.

The shared ZDD extension should express the generic **durable-state decision**;
Bridge supplies schema/contract interpretation and admission evidence. Integrate
the extension into the existing stateful transaction and breadcrumb recovery,
not a wrapper that calls the old rollback first and checks compatibility later.
No new HTTP/wire generation or durable receipt shape is selected by this
proposal; those belong in the reviewed implementation slice.

## Rollback is a compatibility decision, not a health decision

Record the state boundary in existing owner recovery state so restart can
distinguish these outcomes:

| Observed durable outcome | Permitted recovery |
|--------------------------|--------------------|
| Preparation or drain failed before state mutation; old contract is positively unchanged | Confirm candidate disposition and exact old ownership, then restore compatible predecessor admission through the owner. |
| Complete state boundary committed and successor remains compatible | Keep the compatible successor or commit forward through the existing owner; do not restore an incompatible predecessor. |
| State boundary committed; predecessor independently proves support for current state | Compatible rollback remains possible, but only after the owner's normal successor/route/custody checks and state revalidation. |
| State commit outcome, writer closure, process identity or publication is uncertain | Retain nonterminal recovery and closed affected admission; expose an explicit failure. Reconcile before any retry, undrain or selection change. |

The SQLite commit is a durable compatibility point even if route publication
has not happened. The old route therefore cannot by itself authorize undrain.
Never remove triggers, lower compatibility facts or rewrite state merely to
make an old runtime appear eligible. Generic stale-route repair, startup,
automatic fallback and installer rollback must consume the same refusal.
Ordinary cutovers that do not change compatibility retain their existing path.

## Worktrees owns its independent writer closure

Worktrees must qualify Stage A for the **complete participating record
authority**, not only the new retained-relationship verbs. Preserve its
authoritative-daemon owner (#3761), Manager lifecycle owner (#352) and existing
reader/process-custody owner (#3963); coordinate a bounded prerequisite with
them rather than take over those efforts.

The required contract has three parts:

- **Admission:** ordinary mutation dispatch, the same-code direct fallback,
  finalization/abandonment, registry edits and import-based full-record saves
  must enter Worktrees-owned writer admission before obtaining mutable state.
  An unavailable authority is not permission to fall back to an unqualified
  direct writer. Read-only inspection remains independent.
- **Snapshot lifetime:** a live process holding an old parsed record must
  either complete/exit before the compatibility boundary or discard and
  reconstruct it through the qualified authority before another mutation.
  Updating its next subprocess does not upgrade the already-held object.
  Existing ordered record locks and preservation checks still apply.
- **Legacy closure and reentry:** before Stage B, the owning installation must
  prove settlement of pre-admission holders and closure of supported old
  invocation/fallback paths. A legacy full-record writer that can still run
  against participating YAML keeps transfer disabled. No field that the old
  parser ignores, Bridge trigger or capability response from a newer command
  is accepted as that exclusion.

This proposal deliberately does not assert that today's Worktrees daemon or
slot primitives already deliver this contract. Their owning prerequisite must
ship and pass native qualification first. If legacy closure cannot be proven,
the supported result is an explicit blocked transition, not live adoption with
weaker guarantees or a new Bridge-owned store. Arbitrary same-user programs
bypassing the supported installation remain outside cooperative compatibility;
supported legacy invocation paths cannot be dismissed under that exclusion.

## Ordered source slices and validation

All items are agent-recommended and remain open:

1. **Review this amendment.** Reconcile the lifecycle/Worktrees prerequisites
   with their existing owners. Keep the implementation branch's completed
   prototype and ordinary schema preflight separate from this proposal.
2. **Qualify admission without changing state.** Inventory supported launch and
   writer closures through their owners. Add actual historical-process witnesses:
   a held database, an independent peer client and an old Worktrees parsed record
   must block the transition. Prove clean exit/reconstruction and denied old-slot
   reentry, not just fresh-current-command success.
3. **Integrate migration-free candidate preparation.** Exercise the ordinary
   installer adapter and real passive startup, not only the private application
   factory. Assert no database mutation, event writer or remote send before
   owner-authorized adoption. Preserve fresh no-predecessor startup.
4. **Add state-aware owner recovery.** Prove refusal at every rollback, undrain,
   startup/fallback and interrupted phase, including a state commit followed by
   failed publication. Reuse exact process handles and native transition locks;
   no name-based retirement or force mode.
5. **Prepare the production fence and qualify transfer.** Only after closure,
   adoption and recovery pass, integrate atomic fence preparation and re-run
   the original full writer/transfer matrix. Do not advertise the live capability
   or register the provider before both Bridge and Worktrees are qualified.

Use contained native Windows and Linux real-process tests, with existing
protocol budgets and a separate fixed-sample performance lane. Include already
open/cached historical connections, queued event batches, peer-send admission,
stale YAML snapshots, concurrent commands and crashes at each state/publication
boundary. Startup health, a simulated callback or one current parser is not a
proxy for the ordinary consumer contract.

Source tests use temporary installations and a synthetic peer, never shared
services. Record clean-room/first-install and native consumer evidence for
changed installer paths. A live external tier remains separately authorized;
missing authorization is a named hold, not a pass. Every unavailable closure or
platform leaves transfer activation blocked.
