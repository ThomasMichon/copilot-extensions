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
   reporting acceptance or completion. Coalesce equivalent pending requests;
   active sharing additionally requires a trusted adapter's fully pinned target,
   not just equal arguments or a moving ref. Explicit source/ref/options and
   installation identity are part of equivalence. Never persist credentials or
   arbitrary caller-supplied commands.
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
   same transaction authority. Normal callers wait for a live installer;
   operator-approved force/repair follows the bounded takeover contract below.
   Neither mode permits overlapping installation writers.
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

## Normal and force/repair operation

### Explicit calls and automatic-update inhibition

The automatic-update hold applies only to automatic work, not an explicit
`<repo> update` invocation. Public-safe capture, with only the repository
command name generalized:

> that update hold should only block *auto*-updates, not calls to `<repo> update`

> We should probably update the other call flows to pass `--auto` to make it clear, and have *those* no-op

The portable requirement is explicit/manual update by default, with `--auto`
on genuine background update callers. An active automatic hold makes those
automatic requests visibly no-op without executing installers. It does not
silently convert a manual update into a force rebuild or require the operator
to remove the automatic hold.

**Agent-recommended implementation and validation:**

1. Review this amendment, then carry explicit automatic intent through the
   existing repository-command parser, Manager bootstrap, request adapter and
   durable execution owner. Preserve strict parsing, existing force/repair
   options and declared version-skew compatibility. Reuse existing request
   authority rather than adding a second queue or a parallel inhibition flag.
2. Inventory and migrate actual background update producers to `--auto`.
   Preserve selected source, publication and authentication policy. An
   interactive invocation with no flag remains ordinary manual update.
3. Qualify manual update under an active hold, automatic no-op under that
   same hold, automatic execution without the hold, and mode propagation
   through bootstrap and engine requests. Manual and automatic requests
   must not coalesce into an outcome with the wrong inhibition semantics.
4. Preserve interrupted-transaction recovery and exclusive live-installer
   custody independently of the automatic hold. Manual intent does not
   fabricate successful settlement, bypass artifact/dependency admission,
   steal a live writer or grant unrelated administrative actions.
5. Exercise the real supported command/adapter boundary and native platform
   controls, then publish through review, CI and promotion. Source tests or
   clearing a hold are not successful fleet installation; actual update,
   selected/serving health, preservation and full timing remain required.

The [fresh-default revision](fresh-default-installation.md) governs construction
and replacement selection. Ordinary updates already select mostly fresh
installation when incremental/ZDD recognition fails; that fallback is not an
implicit request for the distinct force/repair mode. Reuse receipts qualify
incremental compatibility, not permission to install. The existing driver
remains the exclusive transaction owner in either path.

**Operator requirement summary:** exclusivity means one active installer for
the addressed installation, not a persistent dead-letter veto. Locks and owner
markers are collaboration requests tied to the live installer process or
source; a missing/non-running owner must not lock out upgrades. Normal mode
debounces same-version requests and queues differing versions from one central
desired-version authority, avoiding accidental rollback. Blocking failures
cleanly roll back failed preparation without cutover. ZDD is best effort:
handoff incompatibility permits bounded retirement of the old service rather
than abandoning the new selected version.

Force/repair remains a distinct, explicitly requested recovery or target
selection available to any intact authorized installer, not a prerequisite
for fresh fallback. Agents obtain operator approval for a concrete disruptive
host run. It
may briefly cooperate, then takes over from previous installers, invalidates
their stale installation-control state and retires older managed daemons so
only selected code remains active on success. It does not guarantee ZDD.
This requirement supersedes a blanket prohibition on takeover of a live
installer; it does not authorize a live repair merely by landing this plan.

**Operator clarification (2026-10-23):** an explicit order to repair,
clean-install or force-reinstall is sufficient authorization for this mode.
Historical cleanup evidence is not required. A separate, request-bound audited
pre-launch recovery is allowed, but cannot become a mandatory gate before
repair. Record operator authorization as such, never as native cleanup proof;
retain the original failed outcome and any unknown historical effects without
giving them continuing control of the installation. The
[verbatim request](inception-transcript.md#explicit-repair-authorization-and-legacy-pre-launch-audit)
does not order a concrete host repair by itself.

**Agent-recommended implementation and validation scope:**

1. Define one authoritative target resolver using the existing installation/
   product source selections. Bound background producers and coalesce requests
   for the same target already pending or executing. Queue different targets,
   discard superseded normal requests against current desired state, and
   prevent ordinary stale callers from rolling a newer selection backward.
   Explicit rollback/repair targeting is distinguishable from that mistake.
2. Tie active-install coordination to the actual installer PID/birth identity
   and source, with bounded liveness checks and no permanent marker veto.
   **Operator-suggested, not yet selected:** a short lease refreshed by the live
   source installer. Qualify whichever mechanism is chosen against stalled
   refresh, dead/source-missing owners, PID reuse, split repair callers and
   installation children, preserving one actual writer.
3. Separate failed preparation from an incompatible handoff. Qualify I/O,
   network, ACL and crash failures leaving clean candidate/selection state;
   qualify compatible handoff and bounded cold replacement independently.
   A failed old-generation drain is not permission to abandon the new target,
   and a killed predecessor is not proof that the newcomer is ready.
4. Add explicit repair admission outside the broken old owner's veto while
   retaining existing source/authentication and installation-scope selection.
   Do not demand historical native settlement, a pre-launch audit or a
   successful old-owner callback after the operator has explicitly ordered
   repair. Separate operator-authorized retirement of stale control from
   positive native proof, retaining original failure and pending history.
   Bound cooperation, retire the exact old installer process scope, then
   invalidate its locks/owner/queue-control evidence under the new takeover
   authority. An installer losing authority cannot continue installation or
   reacquire it from a stale lease. Competing repairs must not overlap writers
   or livelock by repeatedly terminating one another.
5. Qualify completion against actual installed/runtime identity and all older
   managed daemons, service registrations, routing and maintenance reactivation
   paths. Old retained files may remain inert; application data and unrelated
   services/installations are not repair debris. Retain attributable failure
   history where possible without turning it back into a lockout. Refuse to
   report success if old code still serves or the selected runtime is unready.
6. Exercise both modes with native Windows/Linux process boundaries, a damaged
   or absent old installation/driver, active installer supersession, stale
   markers/leases, newer normal targets, explicit repair targets, process death,
   service incompatibility and bounded cold cutover. No kernel restart,
   manual journal surgery or test-only owner override qualifies recovery.

These are coordinated changes to #7758, not another queue or lifecycle owner.
The existing implementer retains foundation and first-use publication work.
Review this revised mode contract before implementation; current commands and
runtime docs must not imply the proposed repair is already available.

## Live-cutover recovery correction

**Operator requirement:** “It is inapproriate that the installer would require
evidence of a kernel restart. That would break expectations of live-cutover
support.”

The installer vision's
[single execution owner](../../../visions/installer/README.md#single-installation-execution-owner)
therefore requires a supported recovery path on the running host. A changed
boot can remain useful evidence after a restart that actually occurred; it
cannot be the only way to settle an interrupted installation. This correction
does not authorize a reboot, manual queue reset, alternate execution root,
parallel installer or bypass of a live owner's authority.
The explicit operator-approved repair mode above is the supported exception
for retiring an old live installer before the new writer proceeds; it is not
an uncoordinated concurrent installation.

**Agent-recommended implementation sequence**, retained under #7758 and the
existing installation execution implementer:

1. Distinguish capability/admission failures proven to precede process creation
   from uncertain execution after effects may have started. Preserve an
   attributable failed outcome and diagnostics for pre-launch refusal rather
   than imposing an indefinite global interruption fence. Missing logs or an
   exception type alone do not establish that boundary.
2. Extend the existing native process-custody and transaction owner to recover
   interrupted work on the same boot. A live owner may yield through supported
   handoff or be retired by approved repair. Dead markers cannot retain authority;
   settle old installation descendants before a new writer enters, reusing the
   current authority, request journal and native cleanup primitives.
3. Provide an explicit supported path for existing interrupted records,
   including records whose older producer lacks the new admission evidence.
   Automatic settlement must not assume a free lock, process-name/PID absence,
   elapsed time or a reboot proves cleanup. Missing historical evidence does
   not veto the newly selected fresh-install path: settle known scoped effects
   through the existing owner and manifest, and report a concrete failure when
   that cannot complete. Explicit operator repair uses its
   separate authorization instead and requires no historical cleanup proof;
   audited pre-launch recovery is optional. Neither route may fabricate native
   settlement, a successful prior result or erase the original failure.
4. Preserve healthy serving generations, pending requests and unrelated
   services across recovery and subsequent normal update. A newly requested
   installation is distinct from replaying an ambiguous prior transaction.
   Keep foundation/native bootstrap and Python-runtime recovery consistent.

The plan amendment clears review before this recovery implementation. Existing
foundation packaging, first-use publishers and publication owners retain their
current scope; this is not a competing driver or proof of a live repair.

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
- Qualify same-boot recovery with real Windows and Linux process boundaries:
  proven pre-launch refusal, live-owner handoff, owner loss with descendants,
  conflicting recovery callers, unavailable observation and older interrupted
  records. Keep the kernel boot identity unchanged through recovery and a later
  successful normal update, preserve original failure/pending history, and prove
  no overlapping installer or unrelated service interruption. No reboot,
  timestamp/PID shortcut, arbitrary queue edit or success-shaped reset qualifies.
- Exercise explicit operator repair with absent historical cleanup evidence
  and separately with an optional pre-launch audit. Also prove ordinary
  missing-recognizer fallback reaches fresh installation without requiring
  that separate mode. Preserve the distinct authorization/native-proof
  labels and old failure history; verify current scoped takeover and selected
  readiness rather than requiring the broken producer to certify its past.
- Verify read-only status, malformed state, unavailable launch/runtime,
  authentication failure, held authority and supervised-debug contention.
  Failures preserve the previous healthy selection and remain visible.
- Run targeted unit/native process tests, the existing update consumer tests,
  install-contract and documentation-impact checks. Use fresh isolated
  environments for first-touch state/launch behavior. Live rollout remains an
  explicitly authorized later action, not a test side effect.

The parent effort remains Active until its own Plan and Validation Plan are
resolved; this independently landable slice is not completion of that campaign.
