# Durable PR-Watch Delegation

- **Slug:** `durable-pr-watch-delegation`
- **Repo:** copilot-extensions
- **Branch(es):** Separate proposal, notification-contract, and integration PRs against `dev`
- **Created:** 2026-10-09
- **Status:** Active
- **Vision:** `visions/plugins/agent-dispatch/README.md` / `hibernate-the-wait`; `visions/plugins/agent-worktrees/pull-requests/README.md` / `durable-shared-pr-transition-subscriptions`
- **Umbrella issue:** #6007
- **Sub-issues:** #6286 (PR-owner installation-cell adoption)

## Guiding Intent

Resume the deferred PR-watch delegation work without trading away wake
reliability. Eligible suspended dispatch tasks should share the existing PR
watch owner's observation stream rather than retain one operating-system
wait process per task. Ordinary process waiters remain a working fallback.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Delegation driver | Proposal, implementation, validation, PR stewardship, deployment | This effort's source worktree and linked issue |

## Coordination

- **Topology:** Serial, independently reviewable PRs against `dev`.
- **Host (owns PRs):** Delegation driver.
- **Delegates:** Any bounded implementation delegate receives an exclusive slice; the driver owns integration and completion.
- **Handoff:** Continue from this README, its next unchecked item, and the current PR state. Do not replay the preserved branch wholesale.

## Context

The delegation implementation was split out of #5701 after review identified
an architectural reliability gap and rolling-upgrade/credential defects.
The local `pr-watch-daemon-delegation-wip` branch is reference material, not a
ready patch. The steer-idempotency fix from that PR has already landed and
must not be reintroduced as new work.

The existing watch owner persists pending subscriptions, but
`WatchRegistry.apply_snapshot()` removes a fired subscriber before
`WatchDaemon` invokes its callback. `default_notify()` does not check the
callback's exit status. A callback failure can therefore permanently strand
a delegated suspended task after the subscription has disappeared.

The current process waiter already prepares suspension transactionally and
uses generation-fenced arm/finish operations. Delegation must compose with
those contracts rather than invent a second task lifecycle or a sentinel
machine identity.

This work closes the two named vision items. It follows the patterns'
independent-installation, optional-composition, attributable-command,
cross-platform, and graceful-cutover invariants. It adds no new resident
service.

## Request

Operator continuation, verbatim:

> Okay. Resume driving

The driver identified the deferred PR-watch delegation work as the resumed
subject and proposed preserving process fallback while fixing delivery
reliability rather than accepting a regression. The operator confirmed the
effort slug:

> durable-pr-watch-delegation

The detailed protocol and validation work below are **agent-recommended
implementation requirements**, not additional operator quotations.

Follow-up scope selection, verbatim:

> Adopt installation cells for agent-pull-requests before finishing delegation

The real installation-cell integration test found that the PR owner had not
adopted the shared installation-context payload contract. The operator selected
adoption rather than a legacy-runtime-only optimization.

Service-lifecycle scope selection, verbatim:

> governed-cutover

This selects the offered option to add and review canonical graceful cutover,
then continue adoption. It does not waive the installation-cell lifecycle
contract or authorize a stop/start exemption.

## Plan

### Governed watch-owner cutover prerequisite

_Agent-recommended design and validation, explicitly selected for review._

- [ ] Review and merge the [watch-owner lifecycle plan](watch-owner-cutover.md) before implementing the service transaction.
- [ ] Adopt canonical graceful-cutover primitives with installer-driven activation, passive health, exact-instance promotion and retirement, and crash-consistent marker/manifest/route recovery.
- [ ] Define and enforce one installation-scoped watch-state writer across predecessor drain and successor subscription/delivery adoption; preserve event and registration identities without overlapping live-generation delivery.
- [ ] Prove rollback, commit-forward, governance refusal, crash recovery, callback drain, and first upgrade from a lease-only owner on Windows and Linux before landing the cutover prerequisite.
- [ ] Land and deploy the governed lifecycle prerequisite as part of PR-owner adoption before enabling installation-cell dispatch delegation.

### Installation-cell prerequisite

_Agent-recommended prerequisite, explicitly selected by the operator._

- [x] Review and merge the installation-cell plan amendment before adoption implementation (confirmed by #6146; the lifecycle amendment above remains a separate review gate).
- [ ] Adopt the canonical installation-context payload, installer/runtime-resolution and state-root contract in `agent-pull-requests`, preserving supported legacy installs without silent cell-to-legacy fallback.
- [ ] Include the PR owner in the generated same-cell peer-launch contract and declare release impact for every generated consumer.
- [ ] Prove first installation, upgrade, invalid/foreign receipt refusal, state isolation, actual two-cell PR-owner invocation, and pending watch-subscription recovery using the shipped payload rather than a fabricated sibling contract.
- [ ] Land and deploy the adoption prerequisite before enabling installation-cell dispatch delegation. Keep the saved dispatch integration resumable while this prerequisite clears.

### Phase 1 - Review the recovery contract

- [x] Land this proposal through automated review before implementation.
- [x] Reconcile the preserved implementation with current source and enumerate only the delegation changes still needed.

### Phase 2 - Reliable watch notification delivery

_Agent-recommended implementation requirements._

- [x] Introduce an opt-in, capability-advertised acknowledged notification contract owned by `agent-pull-requests`; legacy subscriptions retain their existing wire behavior.
- [x] Persist the fired event before attempting delivery. Retain a stable event identity and replay the same payload until a successful callback acknowledgement, explicit cancellation, or a visibly recorded terminal recovery outcome.
- [x] Keep callback execution bounded; retry failures with bounded backoff without blocking unrelated subscriptions or holding persistence locks during subprocess execution.
- [x] Recover pending deliveries across daemon restart and supported cutover. Guard acknowledgement against unregister/re-register races so an old delivery cannot remove a newer registration.
- [x] Validate notification specifications and timeout inputs explicitly. Report delivery failures and pending retry state without logging credentials.
- [x] Land and verify the notification-contract PR before enabling dispatch delegation.

### Phase 3 - Fenced dispatch delegation _(agent-recommended)_

- [ ] Recognize only supported PR-watch invocations and discover the optional watch owner through its attributable installation boundary, never ambient `PATH`.
- [ ] Negotiate support from both owners before suspension. Missing, legacy, or incompatible capabilities select the existing process waiter without sending new fields to strict legacy schemas.
- [ ] Record an explicit validated waiter kind and reuse the coordinator's prepare/arm/finish generation fences. Prepare before subscribing; make callback completion retryable and idempotent for that exact generation.
- [ ] Preserve explicit `--token` and control-token behavior without persisting secrets in subscriptions or callback argv. Use process fallback when the daemon cannot reconstruct the caller's credential context safely.
- [ ] On ambiguous prepare/register failures, reconcile or abort the exact prepared generation before considering process fallback. Never arm two independent wait owners for one task.
- [ ] Validate a finite positive PR-watch timeout, preserve timeout wake behavior, and provide visible recovery for delegated waits whose subscription or delivery owner is unavailable.
- [ ] Keep arbitrary-command detached waits, ordinary non-detached waits, and independent installation unchanged; do not add an inert manifest `dependencies` field.
- [ ] Update the owning plugin documentation, CLI help, and changefiles; land the integration PR after the required review and checks.

### Phase 4 - Deploy and close _(agent-recommended)_

- [ ] Confirm promotion of the changed source to the release branch by content, then deploy through the unified update surface.
- [ ] Exercise the deployed contract in isolated state without interrupting shared coordinators, watch subscriptions, or peer sessions.
- [ ] Synchronize consumer guidance when changed payload provenance requires it, through the consumer's own PR flow.
- [ ] Record outcomes, resolve every validation item or transfer it to a named tracked objective, archive this effort, release its binding, and finalize owned worktrees.

## Validation Plan

All items below are **agent-recommended** acceptance checks.

- [ ] Unit: failing/nonzero/timeout callback stays pending; acknowledgement removes only its own registration; replay preserves event identity and payload; cancellation and re-registration are generation-safe.
- [ ] Unit: durable event survives crash before delivery, crash after remote commit before acknowledgement, restart, and supported daemon cutover; unrelated subscribers continue to receive notifications.
- [ ] Unit: already-terminal PR, finite timeout, polling outage, expired wait, and malformed inputs produce the intended explicit results.
- [ ] Compatibility: old/new client, coordinator, and watch-owner combinations retain legacy schemas and a functioning process fallback; missing optional plugin affects only the optimization.
- [ ] Credentials: explicit CLI credentials and command-backed configuration still authenticate; no credential value is persisted in callback argv, subscription state, result diagnostics, or logs.
- [ ] Integration: real isolated watch owner and coordinator exercise subscribe/prepare/arm/finish, coordinator outage then recovery, duplicate delivery, stale-generation callback, and registration failure without a duplicate wait owner.
- [ ] Fresh state: the supported first-touch path establishes only the owning plugin's state; two independent subscribers share one PR observation stream and need no per-task waiter process.
- [ ] Platform: bounded callback launch and shutdown/restart behavior pass on Windows and Linux, using existing contained test tooling.
- [ ] Source gates: touched-plugin tests, fatal Python lint, module-size, headless-launch, install-contract, and required CI pass; add a changefile for each runtime plugin changed.
- [ ] Live deployment: exercise an existing, authorized PR target with isolated subscriber/coordinator state when available; otherwise record the exact unavailable lane and a named follow-up before closure.
- [ ] Documentation: review the final diff's impact on plugin behavior, failure/retry semantics, fallback, configuration, CLI help, and source-of-truth docs.

## Proposal

Reliable delivery is a prerequisite, not an optional follow-up. The watch
owner retains a durable event until the callback acknowledges it. Dispatch
callbacks must tolerate replay because a remote commit can succeed even if
the local acknowledgement is lost. Delivery is therefore at-least-once;
task-generation fencing and idempotent completion prevent duplicate wake
effects. This does not claim exactly-once subprocess execution.

The optimization is negotiated and optional. A caller whose credentials
cannot safely be recovered by the watch owner keeps the process waiter,
which already forwards those credentials through its process environment.
This preserves functionality without making a secret-bearing durable
callback an implicit credential store.

## Journal

### 2026-10-12 - Adoption service-boundary investigation

- Stopped before implementation at the service-cutover architecture gate. `agent-pull-requests serve` starts the watch owner: it immediately acquires its singleton lease, restores subscriptions, starts polling/delivery, and publishes its authenticated control endpoint through the installation-owned rendezvous record. `agent-pull-requests serve status --json` probes that endpoint. Its current `serve restart` stops the predecessor and waits for lease release before starting a successor; it has no passive-instance promotion, route-bound ownership attestation, or installer-driven service transaction.
- The installation-cell pattern's lifecycle contract and `docs/install-contract.md` requirements 10–12 require a durable marker/manifest selection transaction, passive health and exact-instance promotion, governance rechecks, and attributable recovery. The graceful-cutover pattern's invariant 0 applies to this endpoint-serving daemon even though its subscriptions and pending events are durable.
- Reusable canonical boundaries are `validate_context_receipt`, `resolve_installation_mode`, `compare_and_swap_activation`, `provision_runtime_slot`, `complete_runtime_slot`, and `cutover_runtime_slot`, plus `peer-launch`'s receipt/governance validation and rebound child environment. Slot cutover publishes runtime markers; it does not supply a watch-service selection transaction or transfer its singleton lease. The shared installer engine supplies build/manifest helpers, not a generic service coordinator. Agent Index's selection/service coordinator remains plugin-specific and must not be copied or treated as a standalone PR-owner adapter.
- Implementation stopped pending a scope decision: review a watch-owner service transaction using the canonical graceful-cutover primitives, including the safe subscription/delivery adoption boundary, or explicitly review a different lifecycle contract before coding. Merely vendoring validators, adding a peer name, or redirecting state would not satisfy adoption. No implementation or validation item was marked complete; no runtime, subscription, generated consumer, or release metadata was changed.
- The operator then selected `governed-cutover`. Added the agent-recommended lifecycle plan for review before resuming implementation; the approved adoption and saved dispatch integration remain the parent objective.

### 2026-10-12 - Adoption prerequisite authorized

- #6146 merged after a current-head approving review with zero findings and successful required CI. The reviewed installation-cell prerequisite now governs implementation.
- Claimed #6286 for PR-owner installation-cell adoption after checking for overlapping issues and pull requests.
- Continuing from the merged plan; saved dispatch integration remains separate and gated on adoption.

### 2026-10-12 - Adoption amendment review reconciliation

- Refreshed the amendment onto current `dev` after review exposed the old payload working directory in its source head.
- The separately merged #6183 resolves #6161 with profile-directory execution on both platforms and absolute coordinator inputs. Retained that upstream fixture unchanged and removed this amendment's redundant agent-index changefile.
- The adoption amendment remains documentation-only; adoption implementation still awaits this PR's current-head review and merge.

### 2026-10-10 - Installation-cell prerequisite selected

- The notification prerequisite #6019 merged. Dispatch integration remains saved locally, not publication-ready.
- Reconciled its six completed Phase 2 Plan items; the remaining adoption, integration, and deployment items stay open.
- The real peer-boundary test found missing PR-owner installation-context payload support, so merely adding the peer name cannot satisfy cell-scoped delegation.
- Operator selected full PR-owner installation-cell adoption before finishing delegation. This amendment adds that reviewed prerequisite without discarding the saved integration.

### 2026-10-09 - Resumed deferred work

- Operator confirmed the effort slug and authorized continuation.
- Claimed #6007 after checking for overlapping issues and open PRs.
- Read the current watch owner and dispatch preparation path; confirmed that fired callbacks are not durably acknowledged today.
- Created this proposal for review. No implementation has been replayed from the preserved branch.

### 2026-10-09 - Proposal approved and merged

- #6011 merged after an approving automated review with zero findings and successful CI.
- Pulled the source worktree forward onto the reviewed proposal.
- Beginning the watch owner's acknowledged-delivery contract first; dispatch integration remains gated on that prerequisite.

### 2026-10-09 - Notification contract implementation

- Implemented opt-in `acknowledged_notifications/v1`, immutable durable fired events, independent retry workers, generation-safe cancellation, and legacy wire compatibility.
- Independent review reproduced callback shutdown and failed-registration persistence gaps. Corrected the drain budgets and rollback behavior and added boundary regressions before publication.
- The preserved dispatch patch cannot be replayed wholesale: current transactional prepare/arm/finish must be extended with a negotiated waiter kind, authenticated process fallback, and explicit delegated-owner recovery. Steer-idempotency work remains excluded.
- Full contained verification is waiting behind another live, bounded test-runner admission owner; structural and fatal-lint gates have passed. No shared peer was interrupted.
- Coordinated a short admission gap with the owning peer; full contained verification completed with 114 passing tests. The slot was returned to that peer after the run.
- Review-driven refinements now validate persisted baselines and protocol echoes, persist cancellation before memory removal, sync the POSIX state directory, and bound callback concurrency to eight workers. Shutdown ignores snapshots fetched after draining begins; malformed absolute deadline state fails closed. Full contained verification reached 127 passing tests.
