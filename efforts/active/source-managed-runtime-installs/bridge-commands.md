# Bridge legacy command execution adoption

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is **agent-recommended integration**, not another operator requirement
or a transfer of source, publication, service or lifetime custody.

## Boundary and current behavior

The selected-source prerequisite actually merged through #8996. Its fixed
single `provision` recipe accepts the canonical installed Bridge payload or
its exact source-qualified selected legacy snapshot at the default root.
`stamp`, registration-reconciling `install` and bootstrap remain
canonical-installed-source-only. Native source and process qualification
does not itself adopt ordinary command publishers.

The generated primary command selects its retained source-qualified snapshot.
Ordinary unreceipted Windows copies instead retain exact-publisher
compatibility; ordinary POSIX stamps retain the publisher path. Neither
contract permits discovering a newer payload through PATH or a marketplace
glob, or silently treating an ordinary copied snapshot as qualified source.

Both primary and payload routes reach Bridge's existing native runtime gate.
That gate already resolves governance and read-only readiness before cold
publication admission, but still owns the cold outer provision lock,
provision-status writes and direct native installer invocation.
Replace only that cold execution ownership with the qualified driver.

## Implementation plan

1. Keep the existing governance and source-aware read-only ready path first.
   A healthy selected runtime must remain invocable without installation
   state, tracing, repair, an available publisher or a cold request.
   Preserve healthy selected-cell behavior without adding cold cell support.
2. Before acquisition, queue or plugin writes, retain publication permission
   and no-self-provision controls and explicitly refuse pinned, nested,
   missing/skewed-driver and unsupported cold cell/custom-root invocations.
   Context validity is not publication authority. Use the actual filesystem
   project and exact canonical default root; do not inspect or print custody
   secrets.
3. Submit only the existing fixed single `provision` action through the
   payload's packaged native first-use helper with synchronous observation.
   Reuse the same foundation, ordinary queue, durable receipt/result and
   frozen-input executor. Waiting observes that owner; it is not an installer
   or another execution authority. Remove cold outer locks, stale-PID
   reaping, private provision-status execution and direct installer fallback.
   Do not delete historical locks, status, pending or failed records.
4. Retain exact selected-source routing in generated primary commands and
   payload dispatch. Require the qualified command/driver contract before
   cold execution; an older retained dispatcher must not silently keep its
   old direct-installer path. Preserve source-independent healthy fallback
   through the qualified read-only resolver. Keep ordinary unreceipted
   exact-publisher behavior distinct from selected-source admission and
   refuse unsupported cold copies without substituting another publisher.
5. Preserve native command arguments, quoting, object-pipeline and raw stdin,
   binary stdout, stderr and exit status across the queued wait. Installation
   diagnostics and launch acknowledgements must not enter the application's
   stdout protocol or consume its input. A launch acknowledgement remains
   distinct from an ordinary committed receipt. Re-resolve actual completed
   readiness after successful operation; no interpreter-only success fallback.
   Retain the documented `::agent-provisioning::` wait cue on stderr, binding
   any status reference to canonical driver-owned progress rather than
   recreating private provision status. Do not present an unsupported ETA as
   readiness or installer ownership.
6. Preserve Bridge's real platform-specific provision behavior and context.
   Do not substitute Logger's service-less options or bootstrap's Windows
   unattended `install` choice. Registration, start, failure and prepared
   provision semantics remain owned by the actual Bridge installer.
7. Update directly related driver/command documentation, shared publication
   fixtures and explicit bounded native qualification. Reuse canonical
   source/materialization and generation ownership; introduce no arbitrary
   argv recipe, second queue, scheduler, snapshot builder or repair mode.
   Land this reviewed proposal before implementing these changes.

## Validation plan

- Invoke the actual materialized primary and payload commands, including
  Windows PowerShell and `.cmd` fronts, on native Windows/Linux. Exercise
  canonical-installed first use and the real source-qualified selected
  snapshot with its publisher/source checkout unavailable. Separately
  preserve ordinary unreceipted compatibility; do not use copied payloads
  as evidence of canonical source admission.
- Prove Python-free caller admission/loss, the original sole committed request
  completing before any replacement submission, and separately concurrent
  ordinary callers. Use actual caller Job/process-group destruction and pin
  the acknowledged owner. Wait for a real committed receipt before queue
  reads; malformed state must still fail, not become a polling fallback.
- Exercise real construction and safe installed Bridge CLI, module,
  distribution, selected-generation and source-independent consumers.
  Preserve immutable completion, failed-candidate selection, unrelated
  pending/failed history and healthy subsequent invocation. Require no new
  request, private provision status or outer execution lock on healthy paths.
- Preserve existing binary-frame, held-open raw stdin, prompt-file/pipeline,
  quoting, nonzero-exit and stderr controls across relevant Windows hosts
  and POSIX. Qualify the cold queued wait's transport behavior separately
  from real installed Bridge health; label synthetic transport consumers
  precisely and never rewrite a completed slot to create a probe.
  Assert the documented provisioning cue, driver-owned progress and actual
  wait outcome separately; a time estimate, created file or launch
  acknowledgement is not operation readiness.
- Run actual no-write refusal for managed/changed policy, no-self-provision,
  pinned/nested calls, malformed or unavailable source/selection, ordinary
  copies, old or missing dispatchers/drivers/resolvers and unsupported cold
  roots/cells. Compare source, runtime, queue/acquisition and historical
  state, not just a return code. Refusal must not execute an unqualified
  dispatcher or find another installed publisher.
- Intercept only isolated registration/start/health effects for the product
  construction cases. Preserve actual ordinary Bridge task/service options
  and failure outcomes without host registrations, credentials, remote
  sessions or a live bridge. Platform C tools may support explicit source
  construction while host Python/UV stay absent; do not claim compiler-free
  host acceptance.
- Preserve canonical-installed-only bootstrap and fixed recipe/source
  regression portfolios, publication binding and warm transport controls.
  Require exact-candidate native Windows/Linux, actual passing review and
  required CI before an implementation merge. Keep existing finite
  runner/job/process/storage bounds; partition independent fixtures rather
  than weakening limits or counting skips as native proof.

## Custody and completion

Complete only this bounded publisher row after actual command qualification,
review/CI and an implementation merge. Reconcile its delivered ROOT rows and
append the journal before selecting another stretch. ROOT and #7028/#7758
remain Active/open; command adoption is not blanket driver completion.

Preserve all #2822/#4010/#4015/#4058 acceptance, core/schema/normal/repair
reservations, source/adopter/native/deployment/maintenance custodians,
#7030 publication, #8370 diagnostics, #7833/#8720 lifetime and CodeSpaces
#6909. Retained-source verification and contributor-owned watchdog work
remain independent; do not supersede or wait on their open implementations.

Explicit installers, compound actions, cold cells/custom roots, unreceipted
snapshot migration, macOS, live service/session deployment, host repair,
rollout, enrollment, conversation scrubbing and finalization remain outside
this slice.

This closes existing Installer/Plugin Service Model execution-owner and
exact-source intent through the existing thin-adapter, attributable-command,
single-owner and cross-platform patterns. No standing vision change.
Documentation impact is this plan, its direct ROOT row and the dated journal;
as-is runtime documentation changes with implementation, not this proposal.
