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

## Baseline and scope

The existing OFF toast comes from the Picker's post-binding
`maintenance.automatic` check. That Manager-local veto also suppresses
automatic housekeeping and monitor/repair activity; it is not just an update
flag. The Installation & Updates workspace offered component details
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

- [x] Review and merge this bounded intent before code changes (#10056).
- [x] Extend the existing Installation & Updates interaction so the operator
  can inspect and change supported local automatic-update controls, with
  effective ON/OFF state, target scope and any independent blocker visible.
- [x] Reuse existing policy persistence and admission rather than a shadow
  switch. Preserve the general Manager audit veto and default-installation
  KillSwitch semantics, defaults and cell isolation.
- [x] Route actual automatic software updates through `<repo> update --auto`
  using the existing Manager/project adapter and durable execution owner.
  Keep the automatic marker through forwarding, detachment and execution.
- [x] Re-read applicable policy at automatic effect admission, including work
  queued before a disable; do not terminate an already admitted update.
- [x] Preserve direct CLI, agent and selected-component update semantics
  without adding `--auto`, requiring force, bypassing single-writer custody
  or letting the automation setting block their normal path.
- [x] Make ordinary disabled state actionable in Configuration rather than a
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

## Implementation and qualification

The source implements two independently confirmed existing controls: the
default-installation bootstrap KillSwitch and Manager's broader launch-time
maintenance veto. The first invokes the unchanged canonical shell/PowerShell
controllers shipped in Manager's own payload; it has no Machines runtime
dependency or shadow state. Independent cells cannot edit that global hold.
The second edits only the existing TOML preference with stale-view checks and
preserved unrelated values/comments. Neither action starts an update or worker.

Automatic software updates use the existing project/Manager `update --auto`
seam and one durable execution owner, with optional detached or blocking
observation. Explicit updates retain their original admission, engine-target
identity and re-entrancy behavior. Actual queued automatic requests re-read
applicable policy before becoming running and keep a non-success receipt
when inhibited; active work is not retrospectively terminated.

Local qualification includes real private-fixture controller changes and
payload copies, production virtual Configuration confirmation/cancellation and
refresh, queue/manual/scope controls, and the available Bash argument matrix.
Initial source qualification passed the 3,674-case actor-free packet with 465 bound sources, 214
production coverage targets, coverage once and no observed effects inside its
unchanged 300-second budget. The additional early-bootstrap addressed-root
regression is included in the final current-source packet rather than credited
to that earlier result. All 21 currently admitted strict typing projects
pass Linux and Windows static views; pending projects are not thereby admitted.
Actual Windows shell execution, independent implementation review/required CI
and normal source merge remain explicit publication gates. No local result
qualifies desktop behavior, an installed update or the complete parent effort.
