# Durable PR-Watch Delegation

- **Slug:** `durable-pr-watch-delegation`
- **Repo:** copilot-extensions
- **Branch(es):** Separate proposal, notification-contract, and integration PRs against `dev`
- **Created:** 2026-10-09
- **Status:** Active
- **Vision:** `visions/plugins/agent-dispatch/README.md` / `hibernate-the-wait`; `visions/plugins/agent-worktrees/pull-requests/README.md` / `durable-shared-pr-transition-subscriptions`
- **Umbrella issue:** #6007
- **Sub-issues:** None yet

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

## Plan

### Phase 1 - Review the recovery contract

- [x] Land this proposal through automated review before implementation.
- [x] Reconcile the preserved implementation with current source and enumerate only the delegation changes still needed.

### Phase 2 - Reliable watch notification delivery

_Agent-recommended implementation requirements._

- [ ] Introduce an opt-in, capability-advertised acknowledged notification contract owned by `agent-pull-requests`; legacy subscriptions retain their existing wire behavior.
- [ ] Persist the fired event before attempting delivery. Retain a stable event identity and replay the same payload until a successful callback acknowledgement, explicit cancellation, or a visibly recorded terminal recovery outcome.
- [ ] Keep callback execution bounded; retry failures with bounded backoff without blocking unrelated subscriptions or holding persistence locks during subprocess execution.
- [ ] Recover pending deliveries across daemon restart and supported cutover. Guard acknowledgement against unregister/re-register races so an old delivery cannot remove a newer registration.
- [ ] Validate notification specifications and timeout inputs explicitly. Report delivery failures and pending retry state without logging credentials.
- [ ] Land and verify the notification-contract PR before enabling dispatch delegation.

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
