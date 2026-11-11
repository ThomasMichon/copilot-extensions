# Profile-aware repository preparation

- **Status:** Proposed; review before implementation.
- **Parent:** [Worktree Manager control plane](README.md), Phase 5.
- **Coordination:** #11989; source participant `sluggish-gasket`.
- **Scope:** Native dependency preparation only. #356/#8545 retain broader
  setup/onboarding ownership; #11976 retains knowledge acquisition.

## Request and intent

Public-safe request summary: preserve explicitly trusted first-repository setup
when a repository declares its tool dependencies in `copilot_plugin_profile`
instead of unconditionally enabling those plugins in committed Copilot settings.
Prepare those dependencies through the existing native owner before managed
project adoption, without silently adopting or activating the checkout.

The original request's private context is not reproduced here. The design and
validation below are agent recommendations for this bounded source change,
not authority to update an installed host or change another owner's resources.

This closes existing installer
[`one-line-bootstrap`](../../../visions/installer/README.md#one-line-bootstrap)
and optional-control-plane intent, rather than introducing a new runtime
framework. Preserve
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming readiness and actual current selections define installation success;
incumbent recovery and outgoing retirement are separate. This slice changes
payload preparation, not runtime replacement or daemon retirement. Remaining
runtime-install convergence stays with #7028 and its existing owners.

## Source-grounded discrepancy

`repository_bootstrap.acquire` establishes a reference/no-agent checkout.
`repository_setup.run` then performs the explicitly trusted repository
check/apply/check sequence. That route must not be changed into implicit
managed-project adoption.

`plugin_preparation.observe` currently reads active user/checkout declarations.
The profile contributor stages tool choices only through its registered-project
owner. Consequently, an unstaged profile's dependencies are absent from the
preparation census. Acquiring only the visible subset cannot establish complete
repository readiness. Registering a project first solves staging but changes
the first-touch contract and is not the selected remedy.

## Proposed contract

Extend `prepare-plugin-payloads` with explicit `--include-tool-profile`
semantics, leaving the ordinary invocation unchanged. Keep observation and
explicit apply in the existing operation and its plan-digest protocol; do not
add another preparation daemon, installer or repository/global overlay writer.
This option is proposed syntax, not current supported runtime behavior.

The selected repository's existing profile declaration and schema determine
the additional dependency input. Reuse its native parser/source validation,
not a consumer-maintained roster. The result must distinguish dependency
selection from active plugin selection; preparation is not activation readiness.
An absent profile preserves ordinary behavior. Invalid, escaping, unsupported
or conflicting inputs refuse before acquisition, rather than silently dropping
dependencies or overriding an operator's explicit choice.

Inspect the complete intended dependency set before effects. Bind apply to that
observed input, including profile/configuration and marketplace identity. A
changed declaration, conflicting marketplace, changed checkout or unqualified
payload must not produce a partial-success readiness claim. Preserve authored
local-source qualification, normal remote-source/authentication selection and
the existing acquisition owner's bounds and activation-preservation protocol.
Do not pin a repository marketplace to a backend commit or alternate branch
merely to align test inputs.

Acquire through native Copilot and the existing acquisition/preservation owner.
The implementation must supply the declared marketplace to that real consumer
without persisting active profile choices, replacing its source owner, or
leaking a temporary selection into user/repository settings. Review and test
this exact seam before treating a prospective declaration census as working
preparation. If the supported native consumer cannot preserve these boundaries,
record that concrete dependency and revise this reviewed plan before expanding
the implementation.

The setup caller opts into this requirement only after the provider expands.
Advertise support additively in the existing ordinary observation response from
the actual selected executable, before the caller emits new syntax. Missing
support means an actionable pre-effect refusal for this requirement, not an
active-only retry. Keep parser rejection strict as a second boundary. No
installed package-version equality or latest-payload inference establishes
support. Existing callers retain the version-1 active-only result contract;
new callers must require the provider's explicit profile-aware result semantics
before accepting completeness. Define the capability and result discriminator
with the implementation and cover their absence through a real process boundary.

Preparation does not grant setup trust. Retain the setup owner's exact
checkout/revision binding, no-code-before-consent behavior, reference/no-agent
registration and refusal of conflicting managed checkout ownership. A prepared
payload is not an installed backend, healthy service, adopted project, enabled
plugin or successful repository setup.

## Plan

- [ ] Land this proposal through the normal review and required CI gates.
- [ ] Extend the existing profile/preparation owner and native acquisition
  composition; preserve ordinary observation and activation behavior.
- [ ] Add the provider's explicit result semantics and actual-parser
  compatibility/refusal controls before a dependent caller migrates.
- [ ] Update the owning operation's reference documentation and affected tests.
  Add the normal plugin changefile; do not hand-edit versions.
- [ ] Qualify the complete first-touch setup consumer in disposable state,
  then publish the implementation through review, CI and release.
- [ ] Record the source/release result and return caller adoption to its
  existing owner. Installed-host rollout remains separately authorized.

## Validation Plan

- [ ] Focused unit controls: absent/valid/invalid profile, complete dependency
  census, explicit false/conflicting settings, malformed/source-escaping input,
  changed plan and marketplace/source mismatch. No acquisition on refusal.
- [ ] Real process controls: unchanged old caller/new provider; new caller/new
  provider; strict old-provider refusal; absent result discriminator; rollback
  to an unsupported provider without an active-only fallback.
- [ ] Simple consumer: actual native preparation of a synthetic reference-only
  Git checkout with dependencies only in its profile. Observe, explicitly apply
  and re-observe through the real operation, not a mocked acquisition success.
- [ ] Fresh-environment consumer: new HOME and disjoint checkout roots, no
  inherited payload/overlay/ready daemon. Prove complete qualified payloads
  before the actual trusted repository check/apply/check flow reports ready.
- [ ] Preservation: compare user/repository activation, registration/class,
  unrelated payloads, another owner's checkout and personal choices before and
  after success and failure. Prepared dependencies must not become enabled.
- [ ] Exercise the available authorized external/native lane separately from
  portable source tests. Record actual platform, source and skipped-tier reason;
  neither synthetic success nor a source merge proves installed-host rollout.
- [ ] Run affected source checks, exact-head required CI and accepted review.
  Keep #356/#8545 and parent effort acceptance open after this finite slice.

## Journal

- 2026-11-10: Reserved #11989 after checking existing setup coordination and
  the adjacent knowledge-acquisition PR. This proposal precedes runtime changes;
  no installation, activation, adoption or rollout has been performed.
