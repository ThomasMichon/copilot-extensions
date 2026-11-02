# Manager automatic-update controls

Parent: [Source-Managed Runtime Installs](README.md#phase-4a1---manager-automatic-update-controls).
Existing owners: #7028, #8645 and the [maintenance slice](maintenance.md).
Standing intent: [Installation & Updates](../../../visions/installer/configurator/README.md#installation-and-updates).

## Request

Operator direction, 2026-11-02, verbatim:

> Ah was this work the source of the odd Toast in the Worktree Manage that says that automatic maintenance is OFF, and seems to provide no way to turn it ON?
> Right now, we have a KillSwitch which enables and disables the auto-updates available after the manager starts, and before a worktree is created or resumed. We should put this under control of the Manager's Configuration for Install/Update.
> We need to ensure that the auto-updates are run using `<repo> update --auto`, so they are handled distinct from on-demand `<repo> update` calls; `--auto` calls can be blocked by the setting, whereas direct calls from the user or agent must not be blocked (other than re-entrancy guards)

**Settled summary:** provide an actionable automatic-update control in the
existing Manager workspace. Preserve explicit automatic origin through the
canonical updater and its durable execution. Automation policy is not a veto
on manual updates and does not require the user to force a normal update.

## Current source and scope

The existing OFF toast comes from the Picker's post-binding
`maintenance.automatic` check. That Manager-local veto also suppresses
automatic housekeeping and monitor/repair activity; it is not just an update
flag. The Installation & Updates workspace currently offers component details
and explicit updates but no control for that veto.

The default installation also has the existing shared bootstrap KillSwitch,
with its own persistence, compatibility readers and installation scope.
The durable execution driver already distinguishes `--auto` from manual
requests and checks that hold before executing pending automatic work.
Namespaced installations retain their separate ownership boundary.

The public automatic flag is already understood by Manager and the engine.
The launch adapters currently submit automatic phase work through
`launch-update`; the requested follow-up puts automatic software updates
through the canonical repository update operation instead.

This is not recurring-controller enrollment, the complete pending maintenance
implementation, a new installation executor, a bootstrap-policy rewrite or
authority to change installed settings. Existing application/source, native,
setup and release owners retain their unrelated work.

## Plan

- [ ] Review and merge this bounded intent before code changes.
- [ ] Extend the existing Installation & Updates interaction so the operator
  can inspect and change supported local automatic-update controls, with
  effective ON/OFF state, target scope and any independent blocker visible.
- [ ] Reuse existing policy persistence and admission rather than a shadow
  switch. Preserve the general Manager audit veto and default-installation
  KillSwitch semantics, defaults and cell isolation.
- [ ] Route actual automatic software updates through `<repo> update --auto`
  using the existing Manager/project adapter and durable execution owner.
  Keep the automatic marker through forwarding, detachment and execution.
- [ ] Re-read applicable policy at automatic effect admission, including work
  queued before a disable; do not terminate an already admitted update.
- [ ] Preserve direct CLI, agent and selected-component update semantics
  without adding `--auto`, requiring force, bypassing single-writer custody
  or letting the automation setting block their normal path.
- [ ] Make ordinary disabled state actionable in Configuration rather than a
  dead-end toast; preserve visible configuration and execution errors.
- [ ] Add focused tests and owner documentation, publish the source with
  required changefiles, and drive independent review/CI through merge.

**Agent-recommended realization:** show the independently existing launch-time
maintenance veto and applicable update hold honestly rather than combining
them into an ambiguous boolean. Keep the existing no-update option and
non-update housekeeping separate from canonical software-update submission.
Reuse supported request forms and providers; do not add a prerequisite policy
RPC, a sibling-plugin runtime dependency or an automatic retry through a
different effectful CLI form.

## Validation plan

| Boundary | Required assertion |
|----------|--------------------|
| Configuration | The production Installation & Updates navigation reaches the control, shows effective state/scope/blockers, and offers an ON/OFF action without depending on healthy engine inventory. |
| Persistence and consent | Cancel performs no write; confirmed changes persist across a fresh read without clobbering unrelated configuration or silently clearing independent holds. Errors and stale/conflicting state remain visible. |
| Automatic callers | Startup and pre-create/pre-resume software-update paths use canonical update with `--auto`; Bash and PowerShell retain the same meaning and do not add first-paint blocking work. |
| Admission | A disabled applicable setting prevents automatic installation effects, including an already queued automatic request; active work is not retrospectively killed. |
| Explicit requests | Plain user/agent update and selected-component actions reach the same normal owner with automation disabled; no force is required and re-entrancy/exclusive execution remains intact. |
| Scope and compatibility | Existing defaults and default-installation/cell boundaries survive. Current/prior caller-provider and rollback controls preserve strict parsing, explicit incompatibility and original automatic/manual identity. |
| Other maintenance | Audit-veto housekeeping semantics and explicit no-update behavior remain intact; enabling update permission does not enroll or start the optional recurring worker. |
| Publication | Source, docs and affected changefiles pass normal review and required CI. Source merge, release, installed activation and whole-effort acceptance remain distinct. |

Use temporary test-owned configuration and synthetic update effects.
No live setting change, background enrollment, desktop/window observation,
service operation or rollout is authorized by this source refinement.
