# Responsive Codespace Admission

- **Slug:** `responsive-codespace-admission`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed plan and implementation PRs
- **Created:** 2026-11-04
- **Status:** Active
- **Vision:** [agent-codespaces](../../../visions/plugins/agent-codespaces/README.md) - reliable authenticated access; [agent-bridge](../../../visions/plugins/agent-bridge/README.md) - session continuity
- **Umbrella issue:** #10601

## Guiding Intent

Slow Codespaces claim and relay operations must not stop Bridge from serving
health or unrelated clients. Cancellation must retain custody of the original
provider attempt until its result and any required cleanup are reconciled.
Required authenticated preparation must not silently launch without its relay.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner | Trace, implementation, synthetic consumer tests and PR stewardship | Managed source worktree |

## Coordination

One owner and sequential PRs. #8051 retains composed communication/process
budgets, #3194 retains full hydration/reconnect/cancellation acceptance, and
#6467 retains supervisor ownership. This slice changes the existing Codespace
admission path, not their broader contracts or the container runtime driver.
Do not resume another owner's live attempt or duplicate its worker.

## Context

The synchronous `codespace_launch_ownership` context calls a provider subprocess
from async startup. Its rollback and asynchronous teardown also call synchronous
release. Fresh start and recovery synchronously resolve `relay-launch-env`,
which has a 15-second deadline and an auth-light fallback. These operations
can block unrelated serving until their own subprocess deadlines expire.

#10597 repaired token-store transactions and offloaded relay authorization
callbacks. That does not repair these separate launch/claim operations.
The [completed token-store effort](../../2026/11/04%20relay-token-store-concurrency/README.md)
records only its own finite source acceptance.

## Request

Public-safe bounded scope from #10601: keep Codespace claim admission,
rollback/release and relay-environment resolution off the async serving loop,
preserve original attempt/session/TargetLock and claim identity across
cancellation, and fail explicitly when required relay resolution fails.
Do not increase watchdog grace, abandon an unobserved provider thread, or
replay an uncertain create/prompt.

## Scope Approvals

Source changes, synthetic caller-contained tests and normal issue/PR publication.
No live claim takeover, retained task replay, credential inspection/export,
service restart, deployment, external venue provisioning or SSO authorization.
A reviewed plan does not authorize those effects.

## Plan

### Phase 1 - Ownership and consumer trace
- [x] Trace fresh start, cold recovery and finalization claim/relay calls, including compatibility callers and tests.
- [x] Name admission, result-persistence and cleanup ordering before changing async boundaries; preserve unknown outcomes and existing claim receipts.

### Phase 2 - Responsive operations
- [x] Await slow provider operations off-loop while retaining and reconciling the exact original attempt before cancellation can release custody.
- [x] Keep local state/receipt mutations on their owning execution context; repeated cancellation must not abandon cleanup or create a second provider attempt.
- [x] Offload rollback/release and recovery resolution too; retain TargetLock/claim identity when release cannot be confirmed.
- [x] Distinguish provider failure from an explicitly disabled relay. Preserve supported relay opt-out; do not turn an unavailable required provider into an authenticated-looking launch.
- [x] Use the existing operation budgets or an explicitly named bounded provider budget; do not mask serving starvation by changing watchdog grace.

### Phase 3 - Delivery
- [x] Update affected tests, runtime/contract documentation, source registry and changefiles without rebaselining historical evidence.
- [ ] Pass targeted tests and required source gates, then steward review, CI and merge. Record promotion separately from installed acceptance.

## Validation Plan

- [x] Slow claim admission leaves an actual local health consumer responsive; watchdog does not request replacement solely from that wait.
- [x] Cancellation before/during/after provider success reconciles one attempt and its exact persisted identity, without remote create/prompt replay.
- [x] Repeated cancellation during rollback cannot orphan the provider or falsely release TargetLock/claim custody.
- [x] Confirmed release settles receipt/lock; failed, timed-out or uncertain release preserves them and reports the real cause.
- [x] Fresh start, compatible recovery/resume and teardown retain normal behavior; provider conflict/skipped/coordination refusal remain distinct.
- [x] Relay failure is explicit before authenticated plugin preparation; valid disabled-relay output and legacy-compatible JSON remain supported.
- [x] Real caller-contained synthetic provider processes exercise the async boundary, not just mocked timer helpers; all children and fixture listeners settle.
- [x] Record focused unit, local consumer, first-touch and external-venue tiers honestly; no live service/credential/venue effects are authorized by this source effort.
- [ ] Resolve every Plan/Validation item before marking Done or transfer a residual to a named tracked objective.

## Proposal

Convert the owning operation, not just one call site. A naive `to_thread`
can leave a successful claim arriving after its cancelled caller has released
the local lock. The implementation must await/reconcile the bounded original
operation, persist its result, and complete or retain cleanup custody before
propagating cancellation. This does not require a second coordinator or a new
daemon. Review the plan before implementation.

## Journal

### 2026-11-04 - Planning

- Reserved #10601 after distinguishing the synchronous Bridge serving boundary
  from #8051's composed timeout/progress contract and existing lifecycle owners.
- Token-store implementation #10597 is merged; it is a separate completed
  source slice, not proof this admission path or a live installation is healthy.
- Request, intent and plan compared against the bounded source scope; private
  motivating systems and reports remain outside this public effort.

### 2026-11-04 - Implementation and consumer validation

- Plan #10607 merged before runtime edits. Traced fresh start, cold recovery,
  launch rollback, parity finalization and ordinary teardown.
- One shielded, bounded original provider call settles before cancellation
  propagation. Claim receipts and cleanup/lock decisions remain on the owning
  loop; repeated cancellation does not detach a worker thread or replay a call.
- Reused the existing contained provider runner (claim/release 30 seconds,
  relay 15 seconds) and active provider command selection before legacy PATH.
  The shared container-claim helper retains its exit semantics under the same
  contained runner; no container dispatch route or runtime driver was added.
- Relay errors now stop preparation; existing successful two-field JSON and
  explicit empty/null opt-out remain compatible. Ordinary teardown no longer
  drops a durable claim receipt on unconfirmed release.
- Focused integrated source packet: 260 passed, one platform skip, 418
  deselected. Includes actual synthetic provider processes, the real local
  health route and watchdog, cancellation during claim and rollback, failed
  release retention, persisted teardown identity and cold recovery.
- Two existing launch-policy controls reached the availability provider before
  their intended policy boundary. They now use the existing isolated hosting
  fixture, without changing production hosting enforcement.
- Follow-up focused controls: four passed for acquired-claim cleanup despite an
  admission opt-out and contained provider timeout/tree retirement; nine passed
  for parity finalization and cancellation-safe teardown of both current and
  legacy targets. The success case caught and repaired missing legacy `venue`
  metadata before publication, and proves no duplicate release from finalization.
- Ruff, install contract (13 plugins), documentation consistency (23 plugins)
  and current-source Bridge contract checks (2 contracts, 1005 fixtures) passed.
  Only current-source registry digests changed; historical captures are intact.
- Validation tiers: focused unit/compatibility and real local consumer checks
  passed. The private synthetic provider starts from fresh temporary state;
  no installer/provisioning code changed, so a fresh-machine installer lane was
  not run. Native Linux/macOS, installed services and live external venues were
  not qualified; the effort authorizes no live provisioning or credential effects.
- Review/CI/merge and final validation reconciliation remain open.
  No live service, credential, SSO, claim or external-venue effects were taken.
