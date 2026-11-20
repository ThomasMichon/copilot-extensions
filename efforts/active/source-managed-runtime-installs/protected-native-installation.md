# Protected native installation handoff

Back to [Source-Managed Runtime Installs](README.md).
Tracked by [#13504](https://github.com/ThomasMichon/copilot-extensions/issues/13504).

**Status: Linux source implementation and containing release qualified; native adoption open.**
This bounded slice complements #12905's Manager reference and #12900's
provider/profile reconstruction. It takes neither program nor live installation
custody. It targets the installer's
[independent installation](../../../visions/installer/README.md#purpose--intent)
and [authoritative replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
intent through the existing construction and application owners.

## Problem and boundary

The prepared-application adapter composes retained source/profile inputs,
numbered runtime construction and the application's installation callbacks.
Independent incoming-source approval remains with its caller. An ordinary
controller's writable checkout, toolchain or installed-package receipt does
not make those inputs safe to execute with another identity's authority.

The concrete failure to prevent is privileged execution of code or installation
inputs that the less-privileged controller can replace. Native identity and
filesystem access control are the primary boundary, not a new attestation
service. This proposal adds no daemon, credential client, elevation mechanism
or competing constructor.

The existing installer/runtime libraries own the reusable handoff. Manager
continues to orchestrate; promotion/source and wheel owners supply admitted
artifacts; each application retains setup, publication, readiness and uncertain
effect recovery. Provider-root desire remains with #12900.

## Proposed contract

1. **Explicit operation and authority.** An already-authorized installation
   owner selects the application, installation identity, addressed root,
   incoming source and allowed lifecycle operation through its existing native
   configuration/selection boundary. Controller-supplied data cannot select
   arbitrary privileged code, toolchains, destinations or callbacks.
2. **Trusted entry before imports.** The native bootstrap must obtain its
   executing code, interpreter and required tooling through an independently
   trusted installation path before loading controller-provided code.
   Checking a user-writable Python program after it has started cannot
   retroactively make that execution trustworthy. Do not solve this boundary
   by running the ordinary controller CLI with elevation.
3. **Protected incoming custody.** Bind independently admitted source/artifacts
   and native execution identity to the addressed operation. Retain the
   incoming code, interpreter/toolchain and necessary ancestry under the
   installation owner's native protection through use. Handle indirect paths
   through the existing native owner: a link or replacement opportunity must
   not redirect execution or writes into controller-writable content.
   A manifest's self-asserted hash, package version or boolean approval field
   is not administrative trust. Reuse existing artifact verification and
   retained-input machinery; do not repeatedly fingerprint already-admitted
   inputs during construction.
4. **Canonical construction and lifecycle.** Compose the existing
   `install_source` application entry, installer engine, versioned runtime
   and application setup/start owners. Preserve selected roles, root and
   configuration/data separation, complete closed-wheel inputs and their
   existing source/profile association. Establish the trust boundary before
   affected writes; keep it effective at the actual execution/publication
   boundary, not merely at an earlier path check.
5. **Finite outcomes and custody.** Use the existing process owner, deadlines,
   cancellation, descendant settlement and result vocabulary. A partial write,
   accepted callback or unknown startup is not a pre-effect rejection.
   Preserve the exact affected resource and its existing reconciliation owner;
   never replay a legacy form, silently change identity or clean retained
   state to make a retry appear fresh.

Native protection must cover the actual execution chain, including interpreted
code and tool inputs, not merely the final executable file. Unsupported or
unverifiable native protection refuses this optional operation before effects.
Implementation must name Linux, Windows and macOS support separately; a POSIX
mode check is not a Windows ACL or a Linux process-identity proof.
The source leg may deliver providers incrementally with explicit unsupported
outcomes; it cannot report cross-platform or privileged acceptance from inert
fixtures. No platform's existing ordinary installation is disabled by the
absence of this optional protected mode.

## Compatibility and authoritative replacement

Expose required protected semantics through a versioned declaration belonging
to the selected installation/runtime, using the existing immutable descriptor
or prepared-application contract where appropriate. Settle the precise API in
the owning implementation, not by adding an unrelated preflight command.
An incoming caller uses protected semantics only when that executing owner
declares and enforces them. An old or missing provider refuses the affected
protected operation before writes, without guessing an equivalent legacy form.
Old callers retain ordinary syntax, defaults and effects.

Follow [provider-first contract evolution](../../../docs/patterns/service-contract-evolution.md):
expand the owner, qualify current/prior and rollback combinations, then migrate
consumers conditionally. Package equality, a newer staged payload or a completed
source PR is not executing capability.

Protected admission concerns **incoming** source and the user's authority over
the addressed installation. Missing incumbent provenance, recovery capability,
old locks or stale transaction records must not veto an independently admitted
authoritative replacement. Preserve user configuration, credentials, application
data, sessions and protected connectivity. Existing application state-compatibility
and incoming permission/I/O/readiness failures remain real failures.

Construction is not activation. Whole installation success still requires the
requested incoming readiness and complete current selections. Outgoing central
retirement remains generation/role-scoped under the existing replacement owner;
this source slice adds no new process-kill or cleanup authority.
Automatic inhibition remains automatic-only.

## Plan

- [x] P1: Review this handoff contract and retain the existing source,
  construction, Manager, provider/profile and native lifecycle owners.
- [x] P2: Implement the smallest protected provider/admission seam in the
  existing installation/runtime libraries, with a trustworthy native bootstrap
  entry and unchanged ordinary defaults. Document its actual platform support
  and the source of independent administrative trust.
- [x] P3: Qualify the real owning functions and compatibility boundary with
  focused inert unit controls and existing contained process/filesystem fixtures.
  Complete normal source review, required CI, merge and containing release.
- [ ] P4: Migrate a capable consumer through the released contract, retaining
  its explicit target authority and separately owned native installation,
  provider adoption and readiness qualification. Report remaining gates.
  The [closed-wheel downstream Source preparation proposal](closed-wheel-source-preparation.md)
  supplies the input-contract sub-slice for applications whose valid development
  graph is distinct from their independently selected closed-wheel runtime.

## Validation plan

- [ ] V1: A supported, independently admitted incoming handoff reaches the
  canonical constructor with its addressed identity/root and complete inputs;
  no second constructor, ambient tool selection or source fallback is used.
- [ ] V2: Unregistered/unsupported selection, malformed capability and changed
  source, identity or toolchain fail before affected writes. Test the actual
  owning entry and descriptor selection, not a mock that skips admission.
- [ ] V3: Unsafe code/interpreter/toolchain ancestry and link/path replacement
  cannot substitute less-privileged content between admission and execution.
  Inert metadata fixtures do not establish native protection; actual native
  qualification remains an explicit separate outcome.
- [ ] V4: Collision, cancellation, partial/unknown construction or callback
  effects retain their real outcome and resource owner. Bound and independently
  verify owned process settlement; no guessed downgrade, blind replay or
  retained-state deletion is allowed.
- [ ] V5: Cover new/new behavior, ordinary old caller/new provider, new protected
  caller/old provider and supported rollback through the real selected-owner
  boundary. Required protected semantics are never silently ignored.
- [ ] V6: Independently admitted incoming construction does not require
  incumbent recovery/provenance approval. Preserve ordinary state, selections
  and automatic/manual classification until their existing lifecycle owner
  authorizes the applicable transition.
- [ ] V7: Record source, merge, release, actual native protection/adoption and
  installed readiness separately. No live elevation, credential operation,
  service transition or deployment is authorized by this proposal.

The source deliverable ends at reviewed implementation and release with honest
native/adoption limits. The parent effort's full Plan and Validation Plan remain
the installation completion gate; neither this proposal nor a contained witness
completes F1-F3 or provider reconstruction.

## Source implementation

The implementation uses the canonical installer library's
[protected entry](../../../libs/installer-engine/protected-installation.md):
an independently installed native bootstrap loads a protected packaged seed,
never an elevated controller-owned Python program.

The first provider is explicitly Linux root-only and requires independently
admitted, already protected source/toolchain and addressed output roles.
It composes the existing Source/application entry, leaves ordinary callers
unchanged and refuses unsupported selected-owner contracts. It does not supply
an installer for its own privileged seed or establish that a native deployment
is qualified. P3's source review/CI/merge and containing release are recorded
below. P4's capable-consumer adoption, the full parent and native acceptance
gates remain open.

## Journal

### 2026-11-19 - Contract proposal

The contract proposal merged in
[13506](https://github.com/ThomasMichon/copilot-extensions/pull/13506) as
`62693117778137089298374d4dcb506c2185532f` after accepted current-head review
and required CI. Its protection-before-import boundary and independently owned
construction/lifecycle requirements govern the source implementation.

### 2026-11-19 - Implementation and containing release

The implementation merged in
[13517](https://github.com/ThomasMichon/copilot-extensions/pull/13517) as
`30ab3a8263cabee8c559c35f6423b72cf41cfc50` after current-head Copilot review,
disposition of its already-addressed carried findings and successful required
checks. Focused source qualification covers 16 admitted in-memory controls,
110 separate protected/materialization/ordinary controls and the existing
packaged-provider case, with strict Linux/Windows typing and lint.
The first provider requires static native UV without `PT_INTERP` or
`PT_DYNAMIC`; ordinary callers retain their existing defaults.

Initial containing promotions correctly refused a retained-helper closure
omission and a later distinct native Manager consumer failure. Those nonpasses
remain in their producing runs; neither was bypassed or represented as a
qualified release. The existing provider-root owner supplied
[13555](https://github.com/ThomasMichon/copilot-extensions/pull/13555)'s
retained-helper correction without transferring its larger program.

Producing run
[44655473309](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44655473309)
subsequently passed the exact source-unit/coverage, artifact construction and
attestation, installed Manager Windows TTY, Windows Resume, immutable reuse,
publication/readback and normal Main-merge gates. Promotion
[13573](https://github.com/ThomasMichon/copilot-extensions/pull/13573)
actually merged Main `e3b27e01febf4f7edbf58425011e2379f1c48d83`, from validated
Dev `3a5ab4c81640c86fd9887643ffc3fe204ad04327`.
The source artifacts were actually published before that merge.

Nonexecuting content readback confirms that the validated Dev source contains
the implementation merge, and that Main's Manager source-provider bundle
retains the exact protected provider, native executable helper, bootstrap,
staging-environment helper and generated-launcher helper. Main's Vault payload
also contains the exact protected provider, native executable helper and
bootstrap, with the declared `linux-root/v1` capability. This is content
qualification, not an inference from Main ancestry.

P3 is complete for source and containing release. P4 remains open for its
capable consumer; no privileged installation, native protection against another
identity, provider adoption, installed target readiness or deployment is claimed.
The parent N1 and global Validation remain open at their complete scope.
