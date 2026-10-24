# Logger legacy bootstrap adoption

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is **agent-recommended integration** of the qualified
[fixed Logger actions](logger-install-action.md), not a new operator
requirement or another installation owner.

## Boundary

Migrate only Logger's legacy session bootstrap: absent deployment manifest
selects `stamp`; runtime drift selects the existing `install` action, including
task/timer reconciliation. Do not substitute service-less `provision`.
Keep Logger's six-command required-context declaration, native staging,
canonical lifecycle source and own controls unchanged.

The foundation is not yet part of Logger's ordinary payload. Package it via
the canonical source/materialization owner and full declared dependency graph,
including the native snapshot materializers. Qualify actual raw-checkout
mapping and self-contained materialization rather than assuming the recipe
fixture already supplies production packaging.

## Implementation plan

1. Capture the exact originating project before any working-directory change.
   Preserve legacy/default-root identity, source/publication admission,
   no-self-provision/pinned controls, shared killswitch, selected-context skip
   and healthy no-op. No runtime publication refusal may disable independent
   provider or session-guidance hooks.
2. Submit only the qualified fixed `stamp` or `install` request through the
   packaged native foundation. Reuse its Python-free admission, same canonical
   queue, ordinary durable receipt/result and frozen-input executor; waiting
   observes and never becomes installation execution.
3. Remove bootstrap's age-based PID reaping, `reconcile.lock` writer and direct
   foreground/native install launches. Do not keep a second execution owner,
   infer ownership from a PID/age, recursively submit behind existing custody
   or fall back when the driver/helper is absent, skewed or unsupported.
   Retain explicit native readiness/recovery diagnostics.
4. Preserve the actual 15-second hook deadline and exactly one output-free
   `{}` response. A bounded launch acknowledgement is not durable acceptance
   or installation success; work and its failure must remain independently
   observable through the ordinary queue/result.
5. Update owning runtime/bootstrap documentation and bounded test portfolios.
   Keep cold-store provider registration and exact-session guidance separate;
   do not add another scheduler, source selector, task/timer owner or service
   option.

## Validation plan

- Run actual materialized hooks on native Windows and Linux with no Python on
  caller PATH. Measure the real 15-second deadline and exact stdout; verify
  first stamp, drift `install`, healthy no-op, killed caller and concurrent
  sessions through ordinary durable receipts/results. Prove the retained
  lifecycle source stays canonical across subsequent requests.
- Exercise real installed command/module/distribution identity, failed
  candidate preservation and raw-checkout/snapshot dependency closure without
  caller/source-checkout dependence. Do not claim selected-snapshot recipe
  support from packaged snapshot construction alone.
- Intercept all task/unit registration and start operations with isolated
  native adapters. Verify actual drift registration/reconciliation and fatal
  or tolerated failure diagnostics. Assert no personal sync/prune executes;
  a synthetic registration is not live timer or serving-worker evidence.
- Refuse managed publication, no-self/pinned/nested execution, missing/skewed
  driver, substituted source and unsupported custom-root/cell publication
  before acquisition/queue/plugin writes. Preserve independent killswitch,
  context-skip and healthy paths rather than turning non-publication reads
  into provisioning.
- Exercise the independent cold-store provider's exact payload binstub
  manifest and session-guidance hooks in isolated homes against their own
  deadlines/output contracts. Preserve their behavior and source identity;
  do not run personal logging or configure a host bridge.
- Use focused local regressions and an explicit manual Windows/Ubuntu
  portfolio; do not add this exhaustive matrix to required PR CI. Keep
  already-qualified fixed recipes and applicable other-adopter regressions.

## Custody and completion

Review and actually merge this proposal before implementation. Complete this
slice only after native qualification, review and actual implementation merge,
then update the ROOT's Plan/Validation/Journal. Preserve all #2822/#4010/#4015/
#4058 acceptance, core/schema and normal/repair reservations, source/adopter/
native/deployment/maintenance custodians, #7030 publication, #8370 diagnostics
and open contributor work. No transfer, expiry or new blanket approval occurs.

Selected snapshots, primary/auxiliary/payload command publishers, explicit
installer callers, cells/custom roots and broader native/macOS acceptance
remain separate. No host registration, personal sync/prune, deployment,
enrollment, repair run, conversation scrub or finalization is authorized.

This closes existing Installer/Plugin Service Model execution-owner intent
through the single-owner, thin-adapter and cross-platform patterns. It changes
no standing vision. Documentation impact is this bounded plan and ROOT row;
as-is bootstrap documentation changes with implementation.
