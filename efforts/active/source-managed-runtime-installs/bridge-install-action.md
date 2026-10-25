# Bridge fixed legacy install action

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is an **agent-recommended prerequisite**, not another operator
requirement or a transfer of service/lifetime/publication custody.

## Boundary and native semantics

The [qualified recipes](bridge-first-use.md) admit only Bridge's canonical
installed payload/default legacy root for `stamp` and `provision`. Both
native bootstrap drift paths invoke `install`, not `provision`, without an
`init` wrapper. The fixed action roster currently permits `install` only
for Logger.

On Windows, prepared `provision` only reconciles the task; `install` applies
payload content admission, construction/activation when required, command/
config/manifest publication, task reconciliation and service start. Exact
healthy-content `install` returns without rebuilding or restarting. POSIX
maps `install` and `provision` to the same owning `do_install`, including its
immutable reuse/refusal and registration/start behavior. Preserve those native
differences; submitting `provision` from a migrated bootstrap would silently
narrow Windows drift handling.

This action prerequisite clears review before implementation. No ordinary
bootstrap, command or explicit-installer publisher is migrated here.

## Implementation plan

1. Extend only the existing fixed owning-plugin action roster to admit
   Bridge's literal legacy `install`. Reuse its actual native arguments;
   preserve action identity so `install` does not coalesce with `provision`
   even where the native installer maps them to one function. Keep Logger
   support and refuse `install` for other unqualified plugins.
2. Reuse the standalone foundation, native admission, canonical queue/results,
   exclusive custody and frozen-input executor. Preserve the direct
   required-context declaration, exact canonical installed source/default
   root/originating project, complete materialized dependencies, own controls,
   publication/no-self/pinned refusal, foreign-hint clearing and existing
   lifecycle/staging ownership. No new queue, schema, source selector,
   scheduler, arbitrary argv or foreground/nested fallback.
3. Execute the actual materialized owning installer. Retain content-qualified
   healthy no-op, immutable same-version mismatch refusal, cold/new-target
   construction and failed-candidate preservation. Keep construction,
   completion/selection, registration, start/health observations and durable
   process outcome distinguishable; do not imply transaction rollback after
   a later registration/start failure.
4. Preserve fatal and tolerated registration/start outcomes and their visible
   diagnostics. A successful native exit after a tolerated warning is not
   proof of registration or serving health. Unavailable unattended elevation
   must fail explicitly without UAC or task mutation; interactive explicit
   repair and Bridge's existing lifecycle contracts remain separate.
5. Update Driver owner documentation and the dated Journal with actual
   qualification and unadopted boundaries. Bootstrap migration requires its
   own later reviewed intent and actual hook/deadline/output proof.

## Validation plan

Use focused local regressions and the explicit manual native Windows/Ubuntu
portfolio, not an exhaustive addition to required PR CI.

- Verify exact native arguments, own source/root/project/context, distinct
  action identity/coalescing and ordinary durable results. Exercise Python
  and Python-free no-write refusal for malformed/missing helpers, ordinary
  copies/selected snapshots, wrong roots, managed/no-self/pinned controls,
  nested custody and unsupported action/plugin combinations.
- Exercise actual materialized cold `install`, safe installed CLI/module/
  distribution identity and source-independent use, retaining canonical
  dependency acquisition/construction/health/completion/publication owners.
  No installer stand-in or provenance-only proxy qualifies this gate.
- Prove unchanged healthy-content reuse without completion rewrites,
  registration churn or restart. On Windows explicitly distinguish prepared
  `provision` from `install`: changed content in an already-completed version
  must refuse without rewriting that generation; a failed new target must
  retain the healthy prior selection/completion/redirects.
- Exercise registration/start code only through isolated native adapters and
  synthetic state. Inspect actual task/unit launcher, installation/config
  paths and start calls. Inject applicable fatal and tolerated failures;
  verify the existing nonzero or warning contract in the durable result/log,
  inspect actual selectors and distinguish synthetic health from serving.
  Prevent fallback from launching a real bridge when an adapter refuses.
- Prove Python-free concurrent `install` callers and caller Job/group loss
  retain the same accepted canonical operation/result. Join every
  acknowledged supervisor/worker even on assertion or timeout, preserving
  foundation/queue settlement and kernel custody before cleanup.
- Retain Bridge stamp/provision, frozen repair-source, unattended elevation,
  fixed-recipe/Foundation and applicable other-adopter regressions.
  Fixtures isolate homes/config/registrations; no host task/unit, production
  bridge, remote session, credentials or personal data is touched.

## Custody and completion

Complete only after implementation, native Windows/Linux qualification,
passing review and actual merge, followed by the ROOT's Plan/Validation/
Journal reconciliation. Keep the ROOT and #7028/#7758 Active/open.
Preserve #2822/#4010/#4015/#4058 acceptance, core/schema/normal/repair
reservations, source/adopter/native/deployment/maintenance owners,
#7030 publication, #8370 diagnostics, #7833/#8720 lifetime, Bridge
#4477/#5750/#4022/#5225 and CodeSpaces #6909. Do not supersede an open
contributor's implementation or wait on unrelated work.

Selected snapshots, ordinary command/bootstrap/explicit-installer publishers,
compound actions, cells/custom roots, macOS qualification, live service
deployment, host repair, rollout, enrollment, conversation scrubbing and
worktree finalization remain outside this prerequisite.

This closes existing Installer/Plugin Service Model execution-owner intent
through the single-owner, thin-adapter, attributable-command and
cross-platform patterns; no standing vision changes. Documentation impact
is this plan, its direct ROOT row and the existing dated Journal. As-is
runtime documentation changes with implementation, not this proposal.
