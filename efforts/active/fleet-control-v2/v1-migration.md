# V1 authority inventory and migration gates

Back to [the canonical effort](README.md) and
[reviewed architecture](architecture.md#finite-migration-and-existing-campaigns).

**Source-only inventory and agent-recommended implementation gates.** This
document neither implements migration nor authorizes live inventory, admission
freeze, enrollment, state import, process termination or registration retirement.
The Phase 3 and full Validation Plan gates remain open.

## Evidence boundary

Three bounded source reports inspected agency/worktrees, dispatch and bridge
storage and their selected existing contracts. They are not an exhaustive writer
inventory, a live-host census or proof that migration APIs do not exist.
The inspected baseline includes #6475; owner adapters must bind their own exact
source revision and actual schema before interpreting any adopter state.

| Domain | Observed source boundary | Preserve or reconcile | Not established |
| --- | --- | --- | --- |
| Agency/worktrees | YAML `WorktreeRecord` and locked record writes in `tracking.py`; session registration/deregistration writers; Git-ref `LeaseRecord` with OID compare-and-swap in `lease_store.py` | Workspace identity, sessions and rightful head, outbound claims, at-rest obligations, lease/ref identity and current holder | Complete writer roster, live session custody, a general export/import or admission-freeze protocol |
| Dispatch | SQLite task/event storage, transactional claim/lifecycle paths, wake outbox and spawn-reservation schema | Original task/request identity, status/outcome, audit order, held ownership, wake delivery and reservation obligations | Complete reservation producer/consumer inventory, external execution exclusion, a stable fleet-import mapping |
| Bridge/session hosts | SQLite sessions/events and delivery cursors; acquisition journal/store; JSON `HostIndex` custody records | Bridge and ACP session identities, delivery/acquisition identity and digest, pending effects, result cursors, host incarnation and custody | Complete provider roster, actual live host survival/reattachment, a general custody-transfer/freeze protocol |

Source homes are `plugins/agent-worktrees/src/agent_worktrees/`,
`plugins/agent-dispatch/src/agent_dispatch/` and
`plugins/agent-bridge/src/agent_bridge/`. The reports observed lease schema 1,
HostIndex format 1 and bridge migration steps through version 25; these are
bounded observations, not promises about every current or deployed format.
No general YAML-record version contract was established.

Selected contained contracts ran successfully: worktrees 69 passed, dispatch
246 passed, bridge 67 passed with one platform skip. Selectors covered
`claims_reconcile_at_rest`, `classify_lease`, `bind_session`, queue storage/wake
outbox, host-index/session-host claims and session stop. These tests support
the local storage/custody observations, not a migration or retirement claim.

## Identity and responsibility mappings

The current owner must approve each mapping before an adapter writes V2 state.
Use source-qualified stable identities within an explicit authority,
installation and account/principal scope; do not merge equal-looking names
from separate installations or invent grants from local file access.

| Source concept | Required migration treatment |
| --- | --- |
| Worktree/session identifier | Keep a durable correlation to the original workspace/session; a new V2 identifier does not create a second execution workspace |
| Git lease OID, lifecycle/head revision, dispatch generation, connector epoch | Retain their original meaning and provenance; none is automatically a fleet allocation generation or effect-side fence |
| Task result, transport/delivery ACK, node outcome | Keep the distinct admission, acceptance, effect and settlement evidence; none alone releases resource responsibility |
| Pending acquisition, spawn reservation, uncertain prompt/provider effect | Preserve the obligation and original request/delivery correlation; absent acknowledgment is UNKNOWN, not permission to replay |
| At-rest claim, expired lease, apparently dead owner | Record observed state and unresolved responsibility; expiry, silence or a missing process observation does not prove external retirement |
| Host custody and process identifiers | Reconcile actual provider/host incarnation and rightful owner; PID alone or a copied HostIndex record is not custody |

Credentials, prompt/transcript bodies and provider secret material are outside
the generic control-state import. Preserve only necessary nonsecret references;
credential providers retain issuance/custody and authorize any separately
required reattachment. Diagnostic evidence stays in the adopter's state home,
not in this public effort.

## Per-domain transfer gate

This is a required sequence, not a shipped state machine or an instruction to
run it now. A migration attempt is bound to a selected source domain,
source incarnation/schema/revision, destination authority/installation,
explicit owner-approved mapping and an idempotent transfer identity.

1. **Inventory and agree.** Enumerate every admission/writer path, live or
   at-rest obligation, accepted host and receipt path through the current
   owner's supported interface. Record unknown or unsupported entries as
   blockers. Establish a named residual owner, drain deadline and escalation
   decision for this domain; an elapsed deadline never authorizes a kill.
2. **Freeze new admissions at the current owner.** Close all relevant writer
   entry points, including background feeders, and reconcile already-admitted
   mutations to an owner-acknowledged boundary. Keep accepted execution and its
   receipt/drain paths usable. A stopped endpoint, routing-marker flip or
   read-only copy does not establish this boundary.
3. **Capture and validate.** Obtain a consistent owner-produced snapshot plus
   the necessary receipt/cursor boundary. Multi-store domains need an explicit
   owner-coordinated consistency protocol; independently copied YAML, Git and
   SQLite files are not one transaction. Bind content digests, identity,
   schema and cutoff; unsupported formats or changed source bindings refuse.
4. **Import without admission authority.** Stage restartable source-correlated
   records, claims, receipts, cursors and unresolved obligations. Exact retries
   reconcile to the same import; conflicting identities/content refuse.
   Persist checkpoints and validate conservation of responsibility before
   making the destination authoritative. Import does not launch or replay work.
5. **Adopt or drain every obligation.** Prove compatible host/provider custody
   transfer and target-enforced ownership, or leave accepted work draining
   under its old owner. Source-owned drain and V2-owned new admissions must
   have explicit, nonoverlapping resource authority. Keep incompatible or
   uncertain effects blocked; never infer their release from a task outcome.
6. **Transfer one writer authority.** Recheck the source cutoff, imported
   state/receipts and effect-owner exclusion at the commit boundary. Publish
   the destination's ownership only with verified source exclusion and durable
   transfer evidence. Independent domains may transfer separately; cross-domain
   operations remain blocked unless their required domains are compatible.
7. **Verify, then retire.** Demonstrate readiness and original-operation
   query/receipt reconciliation. Remove only identity-verified owned writers,
   feeders or registrations whose obligations are adopted or settled. Repeated
   install/update/uninstall must honor the durable transfer and not revive V1.
   Compatibility clients invoke the selected owner, never a second queue.

A crash or lost reply at any step requires querying the original transfer and
reconciling actual owner/effect evidence. It does not license restarting from a
fresh transfer identity. Before authority transfer, rollback may restore the
old admission path only after proving no destination authority/effects escaped.
After transfer or acknowledged destination writes, recovery preserves the new
authority and receipts through compatible recovery or commit-forward; restoring
a stale backup or old writer is prohibited. Ambiguous ownership stays blocked.

## Ownership and implementation prerequisites

#6236 retains this cross-domain plan. #6240 owns release/lifecycle and #6241
the operation/receipt contract; #6424 owns the common authenticated staging
and client lifecycle seam, not a competing installer. #6443 retains resident
Gateway/index integration. #5789, #6030, #5861 and #5862 retain authentication,
deployment, reconciliation and recovery scopes. #6076 session acquisition and
#6419 standalone-service layout work are distinct and must not be superseded
by migration adapters.

The next migration implementation slice requires current agency/worktrees,
dispatch and bridge owners to agree their supported export/freeze/custody
interfaces and remaining writer coverage. No report's limited absence finding
is sufficient reason to add a parallel adapter or directly manipulate its
database. The product lifecycle owner must also agree how durable transfer
evidence prevents old registrations from returning during reinstall.

## Validation obligations

These refine, but do not close, the parent Phase 3 and Validation Plan:

- [ ] Owner-approved complete writer and live/at-rest obligation inventory,
  with explicit schemas/revisions, private evidence location and unsupported cases.
- [ ] Real concurrent source admission, background feeders and receipt delivery
  across freeze; no unaccounted accepted mutation and no dual resource authority.
- [ ] Fresh import and each supported schema upgrade: exact retries, conflicting
  content, missing/corrupt snapshot, changed source binding and cursor gaps refuse.
- [ ] Crash/lost-reply injection at snapshot, import checkpoint, custody transfer
  and writer publication; query the original attempt without duplicate effects.
- [ ] Pending prompt/acquisition, unresolved task/claim and live mux/provider host
  survive adoption or remain usable under the named drain owner; UNKNOWN blocks
  unsafe replay/reassignment.
- [ ] Receipt and obligation conservation across source/destination, including
  post-snapshot results, tombstones and at-rest obligations; no status-only release.
- [ ] Real target fencing, source-writer reappearance, stale snapshots and
  reinstall/update/uninstall cannot resurrect retired authority.
- [ ] Pre-transfer recovery and post-transfer commit-forward preserve ownership
  and acknowledged writes; separate-domain partial transfer fails explicitly.
- [ ] Windows/Linux process-boundary E2E, fresh-environment rehearsal and
  separately authorized controlled live migration; skipped tiers remain explicit.

This realizes the existing machine-fleet `unified-coordination-product` migration
intent without extending it. It preserves the patterns' standalone defaults,
installation provenance, immutable lifecycle and effect-side fencing boundaries.
No runtime, publisher, installer, migration service or new authority is added.
