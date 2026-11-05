# Responsive Codespace Admission

- **Slug:** `responsive-codespace-admission`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed plan and implementation PRs
- **Created:** 2026-11-04
- **Status:** Draft
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
- [ ] Trace fresh start, cold recovery and finalization claim/relay calls, including compatibility callers and tests.
- [ ] Name admission, result-persistence and cleanup ordering before changing async boundaries; preserve unknown outcomes and existing claim receipts.

### Phase 2 - Responsive operations
- [ ] Await slow provider operations off-loop while retaining and reconciling the exact original attempt before cancellation can release custody.
- [ ] Keep local state/receipt mutations on their owning execution context; repeated cancellation must not abandon cleanup or create a second provider attempt.
- [ ] Offload rollback/release and recovery resolution too; retain TargetLock/claim identity when release cannot be confirmed.
- [ ] Distinguish provider failure from an explicitly disabled relay. Preserve supported relay opt-out; do not turn an unavailable required provider into an authenticated-looking launch.
- [ ] Use the existing operation budgets or an explicitly named bounded provider budget; do not mask serving starvation by changing watchdog grace.

### Phase 3 - Delivery
- [ ] Update affected tests, runtime/contract documentation, source registry and changefiles without rebaselining historical evidence.
- [ ] Pass targeted tests and required source gates, then steward review, CI and merge. Record promotion separately from installed acceptance.

## Validation Plan

- [ ] Slow claim admission leaves an actual local health consumer responsive; watchdog does not request replacement solely from that wait.
- [ ] Cancellation before/during/after provider success reconciles one attempt and its exact persisted identity, without remote create/prompt replay.
- [ ] Repeated cancellation during rollback cannot orphan the provider or falsely release TargetLock/claim custody.
- [ ] Confirmed release settles receipt/lock; failed, timed-out or uncertain release preserves them and reports the real cause.
- [ ] Fresh start, compatible recovery/resume and teardown retain normal behavior; provider conflict/skipped/coordination refusal remain distinct.
- [ ] Relay failure is explicit before authenticated plugin preparation; valid disabled-relay output and legacy-compatible JSON remain supported.
- [ ] Real caller-contained synthetic provider processes exercise the async boundary, not just mocked timer helpers; all children and fixture listeners settle.
- [ ] Record focused unit, local consumer, first-touch and external-venue tiers honestly; no live service/credential/venue effects are authorized by this source effort.
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
