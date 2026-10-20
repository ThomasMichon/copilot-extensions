# Source-backed maintenance - proposed slice

Parent: [Source-Managed Runtime Installs](README.md).

**Intent and implementation plan only.** This slice extends the existing effort,
not the number of update authorities. It must merge through review before
maintenance implementation begins. No source binding, task registration,
cadence, native deployment or role activation is enabled by this proposal.

## Goal and owner

Advance the Installer vision's
[source-backed maintenance](../../../visions/installer/README.md#source-backed-maintenance):
regular maintenance keeps a dedicated clean source current and realizes the
selected runtime through the canonical installer. A one-time installation,
canary or successful marketplace refresh does not satisfy this goal.

Manager remains the installer/update authority. Agent Machines' existing
fleet-update mechanism owns scheduled invocation; extend that mechanism rather
than introducing a second scheduler, update daemon or installer. Source
checkout synchronization is an explicitly governed update phase, not permission
for the installer to modify repositories.

Existing documented fleet-update sweeps are daily and invoke Manager's update
path. Existing Manager source configuration governs Manager self-update.
Neither fact proves a configurable fast-preview cadence, independent
service-source selection or recurring source-backed installation already works.
Those capabilities require implementation and actual observed evidence.

## Implementation sequence

- [ ] Complete the parent effort's deployment-only hook/first-use boundary and independent service-runtime source-selection slices first.
- [ ] Resolve and document effective source authority, acquisition/admission, scheduler/update locking and observable outcomes in their existing owners before changing interfaces.
- [ ] Implement source health and safe selected-ref synchronization in Manager's existing update pipeline. Accept only the dedicated standalone source, expected repository and exact resolved merged commit; retain main/stable/exact/dev semantics.
- [ ] Keep Manager's own source independent from service-runtime and marketplace plugin-content sources. Qualify its clean-source, non-editable immutable installation and supported recovery, not just its ability to update other tools.
- [ ] Route source-backed runtime updates through existing materialization, candidate readiness, activation and retention. Preserve profiles, selected roles and disabled services.
- [ ] Extend Agent Machines' existing maintenance declaration and scheduler reconciliation only as needed to express an explicit bounded cadence and runtime-source update intent. Preserve current default behavior for non-opted-in installations.
- [ ] Record source admission/synchronization/install outcomes through existing update telemetry and owner status. Overlap, unavailable/dirty source and failed candidates must remain explicit, never success-shaped skips or ambient-source fallbacks.
- [ ] Update Manager and Agent Machines owner documentation with the actual supported configuration, operating boundaries and failure/recovery behavior.

**Agent-recommended implementation safeguards:** reuse existing locks and
bounded execution rather than introducing a separate queue; skip writes for
verified unchanged generations; never reset dirty source; preserve the previous
healthy selection on source, preparation or activation failure. Exact field
names, cadence limits and commands follow the owning implementation and its
review, not this proposal.

## Validation matrix

| Boundary | Required evidence |
|----------|-------------------|
| Admission | Dedicated standalone source, expected repository and clean merged commit accepted; dirty, worktree-backed, substituted or unmerged input refused without mutation. |
| Tracking and pins | Main/dev advance only at a new run; exact/stable selections stay pinned; one run retains one exact source identity across preparation and retry. |
| Reuse and advance | Unchanged input preserves completion bytes and redirects; a new merged commit builds/selects a distinct immutable non-editable generation through the actual installer. |
| Independent sources | Manager self-update, service-runtime and Copilot plugin content keep distinct effective selections; a service-source change does not implicitly retarget the other two. |
| Scheduler ownership | Existing maintenance owner resolves opt-in and cadence, reconciles its existing scheduled surface and invokes the canonical bounded update path. No competing updater or slot-pinned task is created. |
| Failure and overlap | Dirty/unavailable source, failed candidates and concurrent runs produce attributable outcomes, preserve working edits and healthy selections, and retain a supported retry/recovery path. |
| Installed scope | The adopter's declared installed inventory, including management tools, is covered; unused roles and disabled services remain unchanged. Installer-only witnesses do not claim daemon cutover or native adoption. |
| Platforms and operation | Targeted Linux and native Windows contracts pass, followed by an observed unchanged cycle and source advance using specifically approved native configuration. A task definition or dry run alone is not observed recurring operation. |

Native source bindings, exact cadence, task/service impact, credentials and
rollback must be presented through the adopter's own change-approval flow
before enabling anything. The parent Plan and Validation Plan remain the
completion gate.
