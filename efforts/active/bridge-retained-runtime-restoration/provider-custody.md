# Provider execution custody

Back to the [effort](README.md) and [Bridge custody contract](custody-contract.md).
**Agent-recommended design prerequisite; not an implemented capability, wire
schema or operating procedure.** The
[parent contract](../acknowledged-execution-handoff/contract.md) continues to own
execution/effect acknowledgement. This specialization selects the existing
provider owners before wiring Bridge's retained provider creation into them.

## Current source boundary

Bridge's `provider_creation.start` accepts a ready, instance-pinned
`provider-exec` command target. Its ordinary path remains separate from the
internal `CreationJournal` handoff storage added in #8935. Neither a ready venue
nor `instance_pinned_exec` advertises retained execution custody.

The concrete prepared-container path is:

1. `agent_containers.dispatch_target.resolve` validates the retained dispatch
   binding and physical instance, then returns the provider's existing
   `exec --stdio` invocation with the binding and expected container identities.
2. `agent_containers.__main__._cmd_exec` selects
   `dispatch_projection.launch_task`. The provider's `lease.session_admission`
   serializes admission against lifecycle holds.
3. `dispatch_projection._launch_task` revalidates the physical identity,
   hosting policy and binding, reserves/publishes guest worker admission, then
   calls the existing stdio transport owner.
4. `dispatch_projection.build_spawn_command` holds the guest worker lock,
   checks its admission generation/nonce, and execs the ACP command.
   `__main__._exec_stdio` owns the local transport child and pipe pumps.
   Bridge's existing session-start owner initializes ACP and invokes
   `session/new` after that transport starts.

These are useful independent fences, not complete restoration evidence.
`SessionAdmission` is heartbeat/TTL liveness and is removed on ordinary wrapper
exit. The dispatch binding retains a worker-admission generation, not the
original Bridge handoff and native outcome. A guest-held lock can outlive its
wrapper without establishing which Bridge operation it represents. Idle,
missing or expired liveness therefore cannot settle an ambiguous creation.

Bridge's `remote_relationships` applies to owned `remote-acp` sessions.
`relationship_authority` confirms an existing Worktrees resource relationship;
it does not acknowledge provider-exec custody or guest native admission.
Do not fabricate a remote-ACP relationship, workspace allocation, local
`RestoreFence`, or acquisition request for this command target.

## Ownership and retained association

Extend the provider's existing dispatch-binding/admission owners rather than
adding a general provider operation ledger, queue, resident or scheduler.
Bridge continues to retain its original operation on `provider_embodiments`.
The provider retains only the corresponding provider-owned launch association
and outcome under that existing binding. These are observations of one
cross-boundary operation, not competing copies of task or native history.

The association must distinguish the exact Bridge installation/epoch,
producer and original handoff, provider runtime incarnation, physical instance,
dispatch birth/binding, worker admission and actual native owner. Keep the
original work/session identity through every response loss and reconciliation.
Provider admission must serialize with its lifecycle owner and retain an
outstanding association before launch can escape. The actual guest/native
owner must accept responsibility before the whole operation can be qualified.
A host wrapper's process reference alone covers only that wrapper.

Keep live references and unsettled associations through closure and process
exit. TTL expiry, a terminal task, container-name reuse, a changed binding,
wrapper exit or an absent response cannot clear them or authorize another
native start. Legacy bindings and launches remain unsupported/unknown unless
their actual owners establish the missing boundary; do not enroll them
retrospectively by matching labels.

Use the existing user-owned command/provider boundary and configured
cross-container proxy ownership described in [consumer-recovery.md](consumer-recovery.md).
No new per-agent credential ceremony or direct remote state reader is proposed.
Unsupported ingress or an owner unable to retain/settle the original operation
blocks qualification; it does not authorize remote inspection or intervention.

## Negotiation and final admission

Follow [service-contract evolution](../../../docs/patterns/service-contract-evolution.md).
Expand the provider first through its existing attributed resolver/selected
runtime boundary. Declare retained custody support separately from ready,
posture, task projection, package version and instance-pinned execution.
Metadata must describe the runtime that will execute, not merely the payload
that produced a command. A changed selected runtime invalidates cached support.

Select the existing provider operation conditionally only after compatible
support is established. The implementation review must define the exact
versioned declaration, required operation fields and outcomes together with
their strict readers. This proposal does not add a guessed CLI verb/flag,
universal handshake or per-effect preflight subprocess.
Old callers retain their ordinary behavior on an expanded provider. A selected
retained-custody caller encountering a legacy, missing, malformed or unsupported
declaration refuses before starting the wrapper or performing guest preparation.
It must not fall back to ordinary unqualified creation.

An observation of capability is not final admission. Revalidate the retained
association and current authority at the actual provider launch and guest/native
boundaries. Serialize closure against admissions that can still start a child;
an admitted-but-not-yet-executed launch remains outstanding until its owner
settles or fences it. Do not hold a Bridge SQLite transaction across provider
calls or nest it with provider/guest locks. Use retained revisions and final
validation under the existing owners, as required by the Bridge contract.

Distinguish provider admission, transport start, native `session/new` acceptance,
attributed consumption and semantic completion. The provider's launch result
alone cannot assert the native session ID. Only the actual native owner can
report it against the original handoff. A Bridge crash between native acceptance
and persistence remains unknown until that same owner can reconcile it.
No failure-shaped response proves absence; no automatic second `session/new`,
new consumer, fresh key or older invocation may replay an ambiguous operation.

Closure permits original outcome reporting and necessary cleanup, not a fresh
launch or prompt. Closing a local pipe or retiring a resource claim does not
cancel remote work. Custody transfer uses explicit acceptance by the existing
provider/native owner, bound to the same operation and native birth; if that is
unsupported, settle naturally or remain blocked.

## Bounded implementation and validation order

The following are obligations, not completed tests or live permissions:

| Slice | Required assertion before advancing |
|-------|------------------------------------|
| Provider-first declaration and read-only selection | Exact executing runtime supplies compatible support; ready/pinned metadata, a new payload, missing/invalid metadata and rollback cannot impersonate support. Old caller/new provider retains ordinary syntax and effects; new retained caller/old provider refuses before effects. |
| Existing provider binding/admission storage | Original association survives closure, wrapper/process death and response loss; strict inventory preserves legacy uncertainty and rejects malformed or conflicting association. Ordinary TTL cleanup cannot remove unsettled custody. |
| Provider and guest final fences | Race closure before/after admission, delayed Docker exec, physical replacement and binding/generation change. Exactly one admitted launch remains accounted for; no DB transaction spans provider/guest calls. |
| Bridge transport/native integration | Retain the actual child before initialization can fail; associate native new/acceptance with the original handoff. Disconnect, cancellation and crash at each boundary preserve uncertainty without a new native conversation. |
| Outcome reconciliation and handback | Same-operation inspection distinguishes confirmed absence, accepted and unknown. Stale/reordered responses cannot change identity or grant replay; unsupported transfer blocks instead of retiring a live owner. |
| Independently updated peers | Exercise actual parser/process or transport seams for old/new, new/old, new/new, changed runtime and supported rollback. A required custody request never silently downgrades or retries an effectful older form. |
| Native/platform qualification | Separately authorized isolated guest/native rehearsals prove wrapper-loss survival and delayed-launch closure. Name Linux, Windows and macOS support or explicit pre-effect unsupported results; a Linux guest lock does not prove host-platform parity. |

Use focused synthetic unit contracts for required PR CI, then the existing
bounded, isolated scheduled/manual process and native lanes. Do not expand
required CI into a Docker/native matrix. Measure launch/reconciliation work at
the actual boundary and preserve source/runtime invalidation without adding
polling or per-message subprocess discovery.

Each implementation slice requires its own source review and relevant contract
fixtures/changefiles. Source merge, release, installation, running adoption and
parent acknowledgement remain separate gates. This proposal performs no
provider/guest effects and closes no full-cohort Plan or Validation Plan item.
