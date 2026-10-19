# Native Target Preference Authority

- **Slug:** `native-target-preference-authority`
- **Repo:** copilot-extensions
- **Branch(es):** Serial plan, implementation and completion PRs targeting `dev`
- **Created:** 2026-10-16
- **Status:** Draft
- **Vision:** [Agent Bridge](../../../visions/plugins/agent-bridge/README.md), ownership clarity and negotiated contracts; [Agent Fabric](../../../visions/agent-fabric/README.md), derive-don't-duplicate and proportionate trusted cooperation
- **Umbrella issue:** #5708
- **Sub-issues:** None

## Guiding Intent

Extend opt-in execution-user preference inheritance to explicitly supported
native Windows and Linux/WSL launch boundaries. Read the executing user's
settings only when their authority is bound to the selected execution space
and final owned child. Preserve explicit choices, intentional providers,
confirmed resume selections, and the public caller-settings default.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Native authority driver | Contract, implementation, contained validation and PR stewardship | Managed worktree `gleaming-resistor`, issue #5708 |

## Coordination

- **Topology:** Serial plan, implementation and completion PRs.
- **Host (owns PRs):** Native authority driver.
- **Delegates:** None; independent design/implementation review is read-only.
- **Handoff:** Journal exact source and validation state here; retain every open
  release or acceptance obligation through a named continuation.
- **Adjacent owners:** #7069 owns derived resolver identity; #6987 owns
  read-only locality; #5839 owns cross-registry allocation/ownership; #7057 owns
  owner-label migration. Existing native cold-resume work retains its source
  and lifecycle scope. Coordinate overlapping modules before editing; do not
  take another live driver's checkout.
- **Deployment:** Source publication does not authorize installation, service
  restart, account/credential changes, or paid model probes.

## Context

The foundation in #5860 added target-settings as an opt-in policy. The trusted
Linux container launcher in #5968 supplied an execution-bound receipt through
the existing Session Host. The remaining #5708 scope explicitly excludes native
Windows and undeclared opaque launchers because configured executable names and
caller-side settings reads do not establish final execution identity.

The current receipt verifier binds Linux container instance/user, process-start,
namespace, home/workspace hashes and the attributable Host implementation.
Native targets must not masquerade as container instances or inherit POSIX
FD/exec assumptions on Windows. Conversely, native support should not create a
new daemon, session owner, identity registry, or redundant general attestation
service within an already trusted local control boundary.

This is vision-closing on ownership clarity, target-local process ownership,
cross-platform parity, negotiated compatibility, and derive-don't-duplicate.
The design follows `docs/patterns/README.md`: preserve existing safeguards and
make new ones earn their cost against concrete identity/provenance failures.

## Request

Public-safe summary of the approved scope: extend #5708's opt-in target authority
to native Windows and Linux/WSL through reviewed design and implementation.
Preserve independent execution spaces, explicit/operator selections, intentional
backends, fail-closed missing authority, and truthful ACP capability reporting.
No live rollout or credential operation is included.

## Plan

### Phase 1 - Reviewed native contract

- [ ] Claim the remaining native slice under #5708 and land this plan before
  implementation.
- [ ] Trace the existing native/SSH Host acquisition, execution-space selection,
  process creation and preference application seams against current source.
- [ ] Specify the smallest explicit native launch declaration, authority fields,
  capability negotiation and compatibility refusal rules; independently review
  the authority/lifecycle contract before source changes.
- [ ] Confirm module boundaries with adjacent active owners. Consume their
  authoritative execution identity rather than adding another standing selector.

### Phase 2 - Shared validation and native adapters

- [ ] Extend the existing receipt/Host path with shared validation and bounded
  platform adapters. Preserve caller-settings and existing trusted-container
  behavior without weakening their receipts.
- [ ] Bind declared execution space/user, effective home/workspace, and final
  owned child. Use native Windows token/process creation identity and supported
  private handle/channel semantics; retain Linux/WSL process-start and namespace
  checks. No basename, caller flag, guessed home, or fabricated instance proof.
- [ ] Limit the first native Windows adapter to an explicitly declared,
  identity-preserving direct launch under the Host's own user. Keep the Host
  as the sole owner of the actual agent PID and stdio; no relay child, token
  impersonation, logon ceremony, elevation, or POSIX-exec emulation.
  Undeclared shell/batch and local cross-user launch shapes remain unsupported.
- [ ] Keep per-launch consent/binding private, reject replay/substitution and
  version skew, and refuse unsupported identity-changing wrappers explicitly.
  Reuse existing cleanup, containment, stdio/PTY and windowless launch owners.
- [ ] Preserve request/profile/provider precedence and confirmed resume/load
  selections. Apply required choices only through advertised ACP options and
  final readback; missing context remains unsupported.
- [ ] Update the owning preference/lifecycle docs and add the required plugin
  changefile with the implementation.

### Phase 3 - Validation, merge and release evidence

- [ ] Execute focused contained native Windows and Linux/WSL process tests and
  existing preference/resume compatibility contracts.
- [ ] Obtain independent review of authority and lifecycle changes, then drive
  exact-head Copilot review and required checks through normal merge.
- [ ] Verify a covering promotion separately from dev merge. Record the
  installed/runtime acceptance obligation with its authorized owner rather
  than claiming that released source is a deployed receiving daemon.
- [ ] Resolve or explicitly transfer all Plan and Validation Plan items, then
  land the completion/archive delta and release this source worktree's effort.

## Validation Plan

- [ ] Real contained children with unequal caller/target settings prove final
  user/home/workspace/process binding on both Windows and Linux/WSL.
- [ ] Reject missing authority, malformed/oversized/replayed receipts, wrong
  process start or selected space/user, substituted children, changed identity/
  home, missing settings and unsupported launcher shapes before readiness.
  No fallback to caller defaults.
- [ ] Exercise native Windows handles/process semantics rather than mocking a
  Linux FD contract. Failure/cancellation closes only the owned fixture tree;
  no visible console or foreground takeover.
- [ ] Prove Windows Host/child user-token and process creation binding for the
  same-user direct shape; reject requested cross-user or shell/batch shapes
  before effects. Prove WSL has a guest-local Host and selected native space,
  never the Windows `wsl.exe` carrier or a fabricated container descriptor.
- [ ] Preserve caller-mode, existing container authority, explicit selections,
  intentional local providers, and confirmed resume/load behavior.
- [ ] Fake ACP advertisement/set/readback covers supported model/effort/context,
  unoffered or changed required choices, RPC failure and missing context.
  No real credentials or external inference required.
- [ ] Keep required CI smoke focused and change-scoped; use suitable existing
  full/platform lanes for exhaustive subprocess combinations.
- [ ] Audit public artifacts, docs and changefile; distinguish source test,
  released code, installed runtime and authorized live acceptance evidence.

## Proposal

_(Agent-recommended)_ Extend the current Host receipt boundary, with an explicit
native capability and platform-specific process binding around shared
preference validation. Reuse the existing authoritative execution-space and
installation provenance inputs. Native Windows and Linux/WSL remain distinct
authorities. Do not add a competing process owner, custom identity database,
credential broker, generic attestation daemon, or equally privileged hostile
peer defense unrelated to the demonstrated boundary.

For native Windows, use the existing direct process creation owner, bind the
returned real child process/token and creation identity before readiness, and
read settings under that proven same-user execution context. Windows cannot
replace a process image while retaining its PID: a POSIX-style witness process
that spawns an unowned grandchild is not an implementation option. Existing
Job/transport cleanup stays with the Host; this design does not remove it.

For Linux/WSL, require a Host inside the selected execution space and reuse
the Linux consent/process-start/namespace machinery. Add the native declaration
and descriptor binding needed to select that path without container preparation
or a synthetic Docker ID. A Windows-side `wsl.exe` carrier is not the guest
process and remains an opaque cross-boundary launcher for this capability.

Use strict authority-kind-specific closed schemas within the existing
versioned receipt envelope where compatible. Preserve every existing
`wrapper-v1` Linux check; do not widen it into an optional-field schema that
accepts Windows or arbitrary platform claims. Negotiate the native capability
explicitly before accepting its Host-bound proof.

## Journal

### 2026-10-16 - Native continuation proposed

- Continued the remaining capability scope of #5708 after its opt-in foundation
  and trusted-container slices merged.
- The native authority gap is explicit in the current preference contract;
  this plan does not relabel a settings candidate as execution proof.
- Generic scope is separated from adopter configuration and deployment.
  Implementation remains behind plan review and adjacent-owner coordination.
- Independent contract review identified the lack of PID-preserving exec on
  Windows. The selected minimal shape is a same-user direct Host-owned child,
  not a witness relay or a privileged cross-user token launcher. WSL retains a
  guest-local Host and Linux process proof, with explicit native descriptor
  selection rather than container identity substitution. No code was changed.
