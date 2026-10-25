# Logger selected-snapshot provision prerequisite

Parent: [Source-Managed Runtime Installs](README.md), Phase 4b.
Coordination: #7758; same Installation execution implementer.
This is **agent-recommended integration** of the qualified Logger recipes,
not another installation authority or a new operator requirement.

## Boundary and source facts

Logger's canonical native stamp publishes an immutable versioned snapshot,
its canonical provenance and the `payload-dir`/`stamped-version` selection.
The snapshot now retains the full Driver dependency closure. Logger keeps its
six-command required-context declaration rather than a single top-level
command/module or provision mode. Its actual runtime gate invokes service-less
`provision`; the separately qualified bootstrap uses first `stamp` and drift
`install`, which reconciles task/timer registration.

The Driver currently admits Logger only from its canonical installed payload.
Existing Pull Requests/SSH selected-source admission and the canonical
installation-context staged-origin reader provide prior art, not proof that
Logger's different lifecycle and snapshot shape already work.

Admit only the exact selected legacy Logger snapshot for service-less
`provision` at the default legacy root. Preserve canonical-installed-source
admission for `stamp`, registration-reconciling `install` and the bootstrap.
Do not change ordinary command publishers or add compound actions in this
prerequisite.

## Implementation plan

1. Extend Python and both Python-free native admission paths using the existing
   canonical selected-snapshot provenance, publication and staged-origin
   readers. Preserve exact source/payload/version/selection and full input
   identity; no ordinary copy, ambient newer payload or custom root/cell may
   substitute for the selected snapshot.
2. Use the same fixed `provision` recipe, request identity/coalescing,
   caller-independent supervisor, canonical queue/results and frozen-input
   execution. Revalidate the accepted source and selection before execution
   and after freezing; preserve unavailable/reselected-source failures rather
   than silently switching inputs.
3. Keep the canonical Logger lifecycle binding against the queued selected
   source while executing its frozen installer. Preserve the complete
   materialized dependency graph, native staging and same-source repeat
   invocation. If actual snapshot copying/publication needs correction, fix
   only the directly demonstrated owning boundary through its canonical
   writer; never rewrite accepted provenance to make an arbitrary copy pass.
4. Retain managed-publication, no-self/pinned/nested and unsupported-source
   refusals before acquisition/queue/plugin writes. Do not turn healthy
   non-publication reads into provisioning. Snapshot admission must not
   broaden the already-qualified bootstrap or registration-bearing actions.
5. Update owning Driver documentation and the existing explicit manual
   Windows/Ubuntu Logger recipe portfolio. No exhaustive matrix is added to
   required PR CI.

## Validation plan

- Perform an actual materialized native stamp, verify its canonical snapshot
  provenance and exact selection, then make the installed payload unavailable.
  Submit selected-snapshot `provision` on Windows and Linux and verify the
  ordinary durable request/result, immutable generation and real safe installed
  command/module/distribution identity for Logger's declared command family.
  Use synthetic inputs and help/version/status consumers only; never process
  personal sessions.
- Prove complete retained dependency closure and no source-checkout/payload
  fallback. Exercise actual repeated provision and canonical lifecycle
  ownership against the queued snapshot rather than a temporary frozen path.
  Credit only the safe consumers actually exercised.
- Reject missing/malformed provenance, unselected/substituted snapshots,
  ordinary copies, initially unavailable source, managed publication,
  no-self/pinned/nested execution and unsupported custom roots/cells before
  acquisition/queue/installation writes. Selection/source changes or loss
  after acceptance must settle the accepted request with an explicit nonzero
  durable result and no installation effects, never erase its queue history.
- Preserve a verified healthy selected runtime and completion bytes when a
  candidate fails. Verify nonzero durable outcomes and exact accepted source;
  installed metadata or a successful copy alone is not consumer health.
- Exercise Python-free callers, actual caller loss and overlapping cold
  submissions through the same authority. Preserve applicable fixed-action,
  bootstrap and other-adopter regressions on both native lanes.
- Keep bootstrap, `stamp` and `install` canonical-installed-source-only.
  Prove selected snapshots cannot become another bootstrap publisher or a
  registration-bearing action through the widened provision recipe.
- Intercept native stop/registration/start effects in isolated homes. Preserve
  service-less provision's existing bounded task-stop behavior while proving
  no registration, worker start or personal sync/prune occurs. Synthetic
  registration is not host service or live periodic-worker evidence.

## Custody and completion

Review and actually merge this plan before implementation. Complete this
bounded slice only after native qualification, review and actual merge; then
update the ROOT and its dated Journal. Preserve all #2822/#4010/#4015/#4058
acceptance and incumbent core/schema, normal/repair, source/adopter/native/
deployment/maintenance, #7030 publication and #8370 diagnostic custody.
The separately owned CodeSpaces #6909 work is neither incorporated nor
superseded.

Ordinary primary/auxiliary/payload command publishers, explicit installers,
cells/custom roots, compound actions, macOS and broader native acceptance,
live periodic workers and rollout remain separate. No host registration,
session processing, deployment, enrollment, repair, conversation scrub or
finalization is authorized.

This closes existing Installer/Plugin Service Model execution-owner intent
through single-owner, thin-adapter and cross-platform patterns. It changes
no standing vision. Documentation impact is this proposal and its ROOT/
Journal links; actual runtime documentation changes with implementation.
