# Logger legacy install action

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is an **agent-recommended prerequisite**, not an additional operator
requirement or a replacement effort.

## Why this precedes bootstrap adoption

The [qualified stamp/provision recipes](<journal/2026/10.24 logger-first-use.md>)
do not yet cover Logger's bootstrap drift behavior. Its native bootstrap invokes
`install`, not `provision`: Windows registers/reconciles the sync/prune Scheduled
Task, while Linux writes the user service/timer and enables the four-hour timer.
Replacing that action with service-less `provision` would silently remove
existing behavior. Preserve the distinction rather than copying another
plugin's bootstrap or inventing a service flag.

This bounded action prerequisite clears review before implementation. Ordinary
bootstrap adoption needs its own subsequent reviewed intent and actual hook
qualification; existing primary, auxiliary, payload and explicit installer
publishers remain unchanged here.

## Implementation plan

1. Extend only the existing fixed owning-plugin action roster to admit Logger's
   legacy `install` using its actual native options. Keep `stamp` and `provision`
   behavior and typed request equivalence intact; `install` must not coalesce
   with service-less `provision`. Do not generalize `install` to other plugins.
2. Reuse the same native foundation, canonical queue/results, exclusive custody,
   frozen-input owner and staged-origin reader. Preserve exact canonical
   installed payload, default legacy root and originating project, complete
   dependency closure, own context/controls and foreign-hint clearing. Retain
   Logger's unchanged six-command required-context declaration; do not add a
   top-level command/module or another provision mode to force compatibility.
3. Execute the actual materialized native installer through its existing
   `install` action. Keep package construction/selection and native registration
   outcomes distinguishable: fatal registration failure is a nonzero durable
   result, not success merely because a healthy runtime was built. Retain the
   canonical Windows access-denied warning/unchanged-task compatibility path
   and its visible diagnostics; its successful process exit is not proof of
   successful registration.
   Preserve healthy-prior selection on failed construction and do not claim
   transactional registration rollback where the canonical installer has none.
4. Document the newly qualified fixed action and its limits. Do not route
   ordinary callers, change timer cadence or registration ownership, add a
   queue/schema/scheduler, permit arbitrary argv or introduce a foreground
   execution fallback.

## Validation plan

Use bounded local tests plus the existing manual native Windows/Ubuntu
installation-context portfolio. Do not grow exhaustive required PR CI.

- Verify fixed native arguments, own context, exact source/root/project,
  cleared foreign hints, action identity/coalescing and durable nonzero results.
  Retain applicable stamp/provision and other fixed-adopter regressions.
- Exercise actual materialized cold construction and safe installed command,
  module/distribution/build identity with the queued source independent of the
  caller. Retain failed-candidate selection and immutable-source guarantees.
- Run actual Windows task-registration code behind isolated native command
  adapters; capture launcher path/arguments, runtime home and four-hour trigger.
  Run actual Linux unit generation behind an isolated `systemctl` adapter;
  inspect the service/timer's command, selected runtime/home and cadence and
  assert the exact reload/enable calls. Exercise existing-registration reuse
  or reconciliation as supported by the canonical installers, without assuming
  the two platforms implement identical registration mechanics.
- Inject registration failure after successful construction and verify the
  original failure reaches the ordinary durable result; inspect resulting
  selection and registration state rather than assuming rollback. Separately
  exercise Windows' tolerated access-denied path and retained warning; do not
  silently strengthen its established exit contract. Prove `stamp`/`provision`
  still perform no task/timer registration.
- Prove Python-free concurrent callers and caller loss retain the same
  canonical operation/receipt/result on both native lanes. Refuse source
  substitution, unsupported snapshot/custom-root/cell/options, managed
  publication, pins, no-self-provision and nested execution before acquisition,
  queue or plugin mutation. Healthy non-publication dispatch stays unchanged.
- Fixtures must isolate homes, native registrations and configuration; intercept
  registration/start operations before any real task, user unit or sync/prune
  process runs. Assert no personal-session access or processing. Actual
  production registration, timer firing and deployment remain unqualified.

## Custody and completion

Retain #7030 publication, #8370 diagnostics, source/adopter/native/deployment
and maintenance custodians, core/schema and normal/repair reservations, and
the ROOT's complete #2822/#4010/#4015/#4058 acceptance. This does not supersede
any open contributor PR, including watchdog or CodeSpaces snapshot work.

The prerequisite is complete only after implementation, native qualification,
passing review and actual merge, followed by the ROOT's Plan/Validation/Journal
update. The ROOT and #7028/#7758 remain Active/open. No host registration,
personal sync/prune, rollout, enrollment, repair run, conversation scrub or
worktree finalization is authorized.

This closes part of existing Installer/Plugin Service Model intent using the
single-owner, thin-adapter and cross-platform patterns; it changes no standing
vision. Documentation impact is this bounded plan and parent row; as-is runtime
documentation changes with implementation, not this proposal.

## Journal

The [dated Logger journal](<journal/2026/10.24 logger-first-use.md#logger-install-action-prerequisite>)
records reviewed intent, actual qualification and failures. Current scope and
the completion gate remain above.

## Implementation and qualification in progress

The fixed Python and both native front doors now admit `install` only for
Logger. The existing executor supplies the action to the canonical frozen
installer; no queue, schema, service option or ordinary publisher changed.
Tests preserve distinct action identity, own context and native arguments,
other-plugin refusal and no-write controls. The existing manual native
portfolio now also exercises actual registration code through isolated
ScheduledTasks/`systemctl` adapters, reuse/reconciliation, fatal failures and
Windows' tolerated access-denied warning. Caller-loss qualification includes
the fixed `install` action.

Local bounded action/identity/other-plugin checks passed 12 cases. A full
local Logger-file attempt reached its 480-second aggregate wall budget after
38 cases without an assertion failure; it is not a complete passing run.
All 13 install contracts, canonical helper synchronization and touched Python
lint pass. Final native cold/registration qualification and implementation
review/merge remain pending. No host task/unit registration, personal
processing, ordinary publisher adoption or rollout occurred.
