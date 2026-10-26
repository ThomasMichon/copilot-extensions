# Manager-first repository setup

Parent: [Worktree Manager control plane](README.md), Phase 5.
Coordination: [#8545](https://github.com/ThomasMichon/copilot-extensions/issues/8545)
for the shared setup seam; [#356](https://github.com/ThomasMichon/copilot-extensions/issues/356)
retains the broader adoption/discovery scope.

**Request and integration acceptance, not a competing setup implementation.**
The operator transferred the shared acquisition/registration/setup/trust/readiness
seam to #8545's driver. The reviewed
[optional repository bootstrap contract](optional-repository-bootstrap.md)
is the canonical mechanism. This earlier decomposition remains as acceptance
context for the requested Manager-first flow and its maintenance consumer;
it does not reserve another `register` command or supersede that owner.
The separate
optional-maintenance runtime is coordinated under
[#7028](https://github.com/ThomasMichon/copilot-extensions/issues/7028).
Neither a claim nor this plan implements repository setup.

## Request

Operator request, 2026-10-20. Only the concrete private repository URL and
adopter name are redacted; no private source is linked:

> I want a way to download worktree-manager first, and then do something like
> `worktree-manager register <repository-url>`
> And have that be the setup flow. Based on [the adopting harness]'s config, that should take care of everything.
> [The adopting harness] can have a README.MD pointer that basically amounts to running worktree-manager's own installer, then doing that step. Or worktree-manager's setup script can be augmented to take a repo as an argument.

The requested entry point is **Manager installer, then Manager registration**,
not another adopter-specific setup executable and not automatic enrollment
from a session-launch hook. A bootstrap argument may forward to the same
operation later; it is not a second implementation or a prerequisite for the
two-step flow.

This closes existing Installer vision items
[`first-harness-repo-adoption`](../../../visions/installer/README.md#first-harness-repo-adoption),
[`core-install-via-real-flow`](../../../visions/installer/README.md#core-install-via-real-flow)
and
[`consented-background-maintenance`](../../../visions/installer/README.md#consented-background-maintenance).
It does not expand the configuration workbench's source-write authority.

## Existing owners and scope

Manager's one-line bootstrap already downloads and versions the standalone app.
Its `setup` drives prerequisite detection/provisioning and the engine's own
installer. The engine already owns repository clone/registry operations,
project registration and project binstubs. Agent Machines owns declarative
machine/resource convergence. Plugin installers, installation admission,
credential routing, source selection and release remain with their existing
owners.

The **non-agentic Manager orchestration operation** and its concrete shared
contract belong to #8545. Maintenance enrollment consumes that owned result
after required setup succeeds; it does not import the engine's implementation,
clone a second runtime, invent a repository/script runner or require an open
Picker. Existing implementation and native qualification must be read from
that owner's current source and acceptance record, not inferred from this
earlier proposal.

This slice covers first-repository CLI setup. Interactive discovery panels,
knowledge-source editing, the rest of the configuration workbench, restart
recovery and source-managed publication remain with their existing slices.
Closing this slice alone does not close #356 or the umbrella.

## Plan

The sequencing and safeguards below are the **historical agent-recommended
decomposition** for the requested user flow. Shared seam implementation remains
with #8545; these unchecked outcome obligations do not assign it to the
maintenance implementer. The local continuation owns maintenance enrollment
and actual adopter acceptance through that seam.

- [ ] Review this contract before implementing `worktree-manager register`.
- [ ] Trace the bootstrap, engine clone/register, effective repository config,
  installer and machine-convergence seams. Reuse their public process/API
  boundaries and retain each owner's installation and permission checks.
- [ ] Add a Manager entry point accepting a repository URL or an existing local
  checkout. Resolve the configured source root or ask where to put a new clone;
  noninteractive use must supply missing choices. Reuse an existing matching
  checkout, but refuse a foreign remote, non-repository target, dirty overwrite,
  ownership/locus conflict or unsafe URL rather than taking it over.
- [ ] Bootstrap missing prerequisites/core through the existing real installer,
  honoring restart and authentication blockers. Register the addressed checkout
  through the engine and derive a durable project identity for later setup and
  updates; never infer it from the future worker's cwd.
- [ ] Define the smallest data-only repository setup declaration needed to
  select the existing owners' stages and optional automatic-maintenance owner.
  Reuse existing plugin enablement and machine requirements as their canonical
  sources, not parallel copies. Unknown fields/owners, unsafe paths and ambiguous
  machine identity block apply. Repository source remains unchanged.
- [ ] Treat `register` as an explicit guided setup request, not blanket
  permission to execute downloaded repository code. Present the addressed
  source/revision, stage plan and trust decision before applying repository
  policy. Pin/recheck the trusted inputs at mutation boundaries. Discovery,
  clone-only inspection and ordinary session launch do not execute setup or
  enroll maintenance. Noninteractive execution fails closed on missing consent.
- [ ] Apply selected plugin/prerequisite and machine-resource stages through
  their existing owners. Preserve machine/platform scope and unused/disabled
  roles. Surface required elevation, destructive changes, service impact and
  credential ceremonies as explicit gates, not a global bypass flag. Do not
  add a background setup agent or silently run arbitrary shell commands.
- [ ] At the end of successful required setup, apply the repository's explicit
  automatic-maintenance recommendation through Manager's enrollment seam.
  Preserve an explicit user disable, exclusive external ownership and legacy
  retirement/in-flight checks. No recommendation means no enrollment.
- [ ] Report complete, partial, deferred or blocked per stage with actionable
  reasons. A registration record, startup artifact, installer exit zero or
  launched process is not overall setup readiness. Rerunning resumes/reconciles
  the addressed setup idempotently without resetting dirty source or replacing
  another controller.
- [ ] Document the generic installer/register flow in the component's setup
  documentation. Adopters keep their own config and README pointer downstream;
  no private repository URL, machine inventory or setup rationale lands here.
- [ ] After reviewed release, qualify real consuming-harness setup through this
  path and verify its requested owner is running, disabled by user choice, or
  precisely blocked. A queued adopter task is not adoption.

## Validation Plan

| Boundary | Required proof |
|----------|----------------|
| Fresh entry | From a disposable Windows and Linux user environment, the published Manager installer followed by registration reaches the addressed repository's supported setup without an agent or pre-existing project. |
| Local/remote/repeat | Existing local checkout and URL clone paths work; repeating registration reuses the correct checkout and preserves source/registry choices. Foreign remotes, occupied targets and locus/ownership conflicts refuse without takeover. |
| Trust | Preview/discovery does not execute setup. Unknown or changed inputs, unsafe declarations and missing noninteractive consent cause attributable refusal before the consequential stage. |
| Existing owners | Real engine registration/binstub and selected installer/convergence paths execute through their existing authority. No test double standing in for the whole pipeline counts as end-to-end setup. |
| Permission and failure | Authentication, restart, elevation, destructive-action and service-impact gates remain explicit. Failed/partial setup is not reported complete; rerun heals supported partial state without overwriting healthy or dirty inputs. |
| Maintenance | No declaration keeps automatic updates off. A trusted setup recommendation can enroll only at the end; local disable and external ownership win. Prove actual native worker readiness and unchanged/advancing background cycles separately from registration. |
| Scope | Repository source, knowledge/preset content, unused roles and unrelated host resources remain unchanged. Windows/WSL/Linux identity is not substituted across environments. |
| Adopter | A real adopter README needs only Manager installation and registration; its repository config supplies setup and maintenance policy. Public artifacts remain organization-neutral. |

## Coordination

The #8545 driver owns shared first-repository orchestration, preserving #356's
broader adoption/discovery scope. The maintenance implementer owns its enrollment
consumer, not another acquisition/setup engine. Existing configuration-workbench
and source-management participants retain their scopes. Optional maintenance's own worker/startup/custody
qualification remains a prerequisite, not something this setup wrapper proves
merely by calling it.

See also: [Manager overview](../../../worktree-manager/README.md) and
[automatic maintenance plan](../source-managed-runtime-installs/maintenance.md).
