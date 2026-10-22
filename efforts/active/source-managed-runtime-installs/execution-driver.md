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

## Standalone foundation amendment

The operator selected `driver-foundation`: "Cover first installation too:
establish a standalone driver foundation before plugin provisioning".
The existing Python-runtime queue is the execution core to extend, not a
reason to exempt Python-free first-use callers or require Manager.

The following implementation sequence is **agent-recommended**:

1. Inventory the existing canonical prerequisite acquisition, immutable runtime
   assembly, native admission and command/snapshot provenance mechanisms.
   Select the smallest self-contained foundation packaging and prove its
   cold-start admission before changing a plugin publisher. A cold host may
   lack Python, uv, Manager and every plugin runtime; a PATH-named sibling
   command is not foundation discovery or ownership proof.
2. Establish one independently installable driver foundation using those
   mechanisms and the existing execution core. Its bootstrap is narrowly
   limited to constructing, verifying and selecting that foundation. It may
   not execute arbitrary submitted commands or perform plugin provisioning
   before the ready driver takes custody. Reuse existing acquisition and
   dependency governance rather than introducing a separate package policy.
3. Make concurrent cold callers converge on one positively owned bootstrap
   operation and observable outcome. Admit construction before mutable staging,
   preserve exact selected source and installation identity, and retain durable
   progress/failure across caller loss. Prove native admission on each supported
   host; an age-based PID file, successful process spawn or per-slot lock alone
   is not evidence of complete transaction ownership. If a prerequisite or
   ownership guarantee is unavailable, fail explicitly before plugin mutation.
4. Publish only a fully verified immutable foundation, preserving a healthy
   selection on failed construction. After it is ready, requests use the same
   queue, transaction lock, result model and interruption fence as current
   updates. No parallel native plugin executor, always-on coordination service,
   recurring maintenance owner or additional source-selection authority is
   introduced. Foundation repair and upgrades preserve active workers and
   requests through the existing custody/immutable-generation contracts.
5. Migrate shared generated first-use wrappers and the remaining suite-owned
   bootstrap/payload callers through fixed, owning-plugin provisioning requests.
   Reconstruct actions from validated source/snapshot/installation metadata;
   never accept a caller's arbitrary argv as an installation recipe. Preserve
   canonical publication admission, installer staging/watchdogs, per-generation
   safeguards, healthy runtime dispatch, no-self-provision controls and original
   failure outcomes. An absent, broken or incompatible driver is an explicit
   readiness/repair outcome, never a legacy foreground-install fallback.
6. Qualify the foundation and adapters independently, then their assembled
   first-use path. Record the actual migrated roster and remaining boundaries.
   Installing source changes does not authorize host rollout or maintenance
   enrollment. Keep the broader source and maintenance owners intact.

**Acceptance:** cold first use with no Python or sibling runtime establishes
the independently owned foundation and submits plugin work to it; simultaneous
callers do not produce overlapping installation owners. Destroy the originating
caller and its cleanup boundary during foundation construction and subsequent
plugin provisioning, then verify the same owned operation or precise
interrupted/recovery state and durable result. Prove source substitution,
failed/incomplete candidates, unavailable bootstrap capability, upgrade during
an active request, managed-publication refusal and healthy standalone dispatch.
Use real native Windows and Linux process boundaries and a fresh clean-room
host; synthetic receipts or an already-provisioned repair do not qualify genesis.
Native macOS/stock-shell compatibility remains explicit rather than inferred.

This amendment clears its own review gate before foundation implementation.

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
