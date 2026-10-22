# Durable installation execution driver

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758.
Intent: [single installation execution owner](../../../visions/installer/README.md#single-installation-execution-owner).

## Boundary

Implement the execution side of the agreed one-owner installation contract
against the currently supported canonical updater. Recurring enrollment,
Autorun/user-service registration and maintenance-worker replacement remain
with Phase 4a. Source-managed publication and source-selection prerequisites
remain with Phases 3 and 4.

Do not wait for, duplicate or supersede #6990's staging implementation,
#6928's installer watchdog, or #6812's output work. Their inner safeguards
remain useful beneath the transaction owner. No runtime is installed, source
policy changed or recurring worker enrolled by this plan.

## Implementation plan

1. Trace the real Manager and standalone engine update entry points, existing
   transaction telemetry, kernel custody and caller-independent process launch.
   Record the actual migration boundary before changing behavior.
2. Add the smallest shared, versioned request/execution mechanism those callers
   need. Persist normalized request identity, state and final outcome before
   reporting acceptance or completion. Coalesce only equivalent pending
   requests; explicit source/ref/options and installation identity are part of
   equivalence. Never persist credentials or arbitrary caller-supplied commands.
3. Admit one execution owner using the existing cross-platform kernel-backed
   custody primitive. Hold it across the entire canonical update, including
   Manager acquisition and self-update, plugin acquisition, runtime installation
   and integration. Per-slot ownership stays nested and unchanged. Inherited
   custody must be explicit and checked rather than inferred from an environment
   flag or PID file.
4. Submit ordinary supported suite-update calls to a bounded headless worker
   through the canonical process-lifetime primitive. A synchronous client
   follows its durable request and output/result; it never becomes the
   installer. Preserve existing option semantics, authentication, source choice,
   telemetry and failure exit status. Unsupported skew must fail explicitly,
   not fall back to a second foreground installer.
5. Keep direct execution an explicitly named supervised-debug path using the
   same transaction authority. A live owner cannot be bypassed with force,
   stale timestamps, an expired lease or a second installation caller.
6. On worker loss, retain an interrupted/uncertain outcome. Recover pending
   work under exclusive authority, but do not automatically replay an ambiguous
   active installation. Do not kill unknown descendants or reclaim authority
   based only on age; the real process-tree/custody boundary must settle first.
7. Document the implemented command/status contract and migrated entry points.
   Inventory hooks, launchers and external controllers separately; do not claim
   a suite-wide migration from a Manager-only happy-path fixture.

**Agent-recommended sequencing:** land the reviewed intent first, then a
coherent request/worker plus real entry-point migration. Additional adapters may
land independently when they use the same authority rather than introducing a
competing driver. Keep each remaining integration in the parent checklist and
its named tracking issue.

## Validation plan

- Exercise the actual CLI and worker in separate processes with isolated homes
  and deterministic updater fixtures; concurrent clients produce one execution,
  not merely one database row. Check real transaction overlap, not a mocked
  lock-acquisition call.
- End the submitting process and its containment/job, then prove the admitted
  worker and final result remain available. On Windows observe no visible
  console or foreground changes; test the actual canonical spawn primitive.
- Distinct explicit source/ref/options remain distinct, identical pending work
  coalesces, a request arriving during an active update is not silently lost,
  and a waiter returns the recorded canonical outcome.
- Crash before execution, during the transaction and after outcome persistence;
  prove precise pending/interrupted/completed behavior and no unsafe replay or
  concurrent orphaned updater.
- Verify read-only status, malformed state, unavailable launch/runtime,
  authentication failure, held authority and supervised-debug contention.
  Failures preserve the previous healthy selection and remain visible.
- Run targeted unit/native process tests, the existing update consumer tests,
  install-contract and documentation-impact checks. Use fresh isolated
  environments for first-touch state/launch behavior. Live rollout remains an
  explicitly authorized later action, not a test side effect.

The parent effort remains Active until its own Plan and Validation Plan are
resolved; this independently landable slice is not completion of that campaign.
