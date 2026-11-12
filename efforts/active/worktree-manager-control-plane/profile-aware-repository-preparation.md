# Profile-aware repository preparation

- **Status:** Active; provider merged and released, dependent caller adoption and full first-touch qualification remain open.
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

At planning time, `plugin_preparation.observe` read active user/checkout declarations.
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
This reviewed option is now released; installed runtimes still require explicit
capability negotiation rather than a source-version assumption.

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

- [x] Land this proposal through the normal review and required CI gates.
- [x] Extend the existing profile/preparation owner and native acquisition
  composition; preserve ordinary observation and activation behavior.
- [x] Add the provider's explicit result semantics and actual-parser
  compatibility/refusal controls before a dependent caller migrates.
- [x] Update the owning operation's reference documentation and affected tests.
  Add the normal plugin changefile; do not hand-edit versions.
- [x] Qualify native dependency preparation and catalog consumption in
  disposable state, then publish the provider through review, CI and release.
- [ ] Retain complete first-touch setup and upgrade/rollback controls as the
  dependent caller's adoption gate after provider expansion; this provider
  change alone does not migrate that caller or complete repository setup.
- [ ] Record the source/release result and return caller adoption to its
  existing owner. Installed-host rollout remains separately authorized.

## Bounded readiness prerequisite

A complete native caller reached payload acquisition and baseline restoration,
then required-module admission refused with
`required selection needs a complete enabled-owner inventory`. The activation
census itself was complete. An optional script-only module carried
`owner-readonly-protocol-not-declared` because native repair obtains runtime
ownership and provisioning inhibition only from `payload-invocation.json`.

The [installer/readiness pattern](../../../docs/patterns/installer-readiness-modules.md#bounded-attributable-invocation)
explicitly permits script-only declarations without that generated-command
manifest. Requiring a plugin to invent a Python module or generated launcher
would contradict that independence boundary. Ignoring the owner's refusal or
removing it from the required census would instead weaken admission.

The next bounded source proposal is explicit installer-runtime ownership for
script-only producers, consumed by the existing repair admission:

- A producer may name a contained `installer-runtime.json` through its plugin
  manifest's `installerRuntime` field. The version-one
  `copilot-extensions.installer-runtime` object declares only its dedicated
  home-relative runtime root and no-self-provision environment variable.
  It does not declare another executable, installer, readiness operation,
  service, activation writer or package-construction mechanism.
- Retain the existing `payload-invocation.json` path unchanged. Refuse an
  ambiguous dual declaration, unknown schema/fields, unsafe or linked path,
  invalid root/environment name and changed declaration before effects.
  A producer with neither protocol remains refused; older consumers retain
  their current pre-effect refusal instead of guessing support.
- Feed the declared runtime root into the same complete ownership census,
  same-cell/mode decision, duplicate-writer refusal and plan digest. Preserve
  the current ordinary-versus-core mutation gate, payload/source identity,
  read-only provisioning inhibition and exact interpreter/action binding.
  Do not bypass an unresolved or foreign runtime claim.
- Keep actual installer and probe selection in the existing module manifest.
  The producer's read-only script must honor its no-provision contract.
  Optional classification is not permission to hide a broken owner.

This closes the existing script-only module contract and installer independence
intent; it is not a new runtime framework or permission to deploy. Required
modules on undeclared platforms remain unsupported and unsatisfied. Platform
support belongs to each producer's real installer/runtime, not a consumer
exception or automatic reclassification.

### Prerequisite plan and validation

- [x] Review and land this amendment before implementation.
- [x] Add the bounded ownership reader to existing native repair admission;
  document its exact producer contract and fail-closed old-consumer behavior.
- [x] Test a real script-only fixture alongside a required owner, incomplete
  and ambiguous declarations, duplicate runtime writers, changed plans and
  ordinary/core refusal. No installer/probe may run before admission.
- [x] Retain a control proving an unsupported required platform remains
  unsatisfied. Do not repair first-touch qualification by dropping that gate.
- [ ] Exercise the actual CLI with a cold disposable HOME and independently
  acquired payload sources. Preserve all original failed observations and
  source/publication identities; source correction needs a distinct run.
- [ ] Publish through normal focused tests, source gates, exact-head CI,
  accepted review and release, then return to the complete caller proof.

## Validation Plan

- [x] Focused unit controls: absent/valid/invalid profile, complete dependency
  census, explicit false/conflicting settings, malformed/source-escaping input,
  changed plan and marketplace/source mismatch. No acquisition on refusal.
- [ ] Real process controls: unchanged old caller/new provider; new caller/new
  provider; strict old-provider refusal; absent result discriminator; rollback
  to an unsupported provider without an active-only fallback.
- [x] Simple consumer: actual native preparation of a synthetic reference-only
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
- [x] Run affected source checks, exact-head required CI and accepted review.
  Keep #356/#8545 and parent effort acceptance open after this finite slice.

## Journal

- 2026-11-12: A first-touch setup callback lost its caller's disabled-bytecode
  policy because Python isolated mode ignores `PYTHONDONTWRITEBYTECODE`.
  The selected-engine argv now carries `-B` only when the caller's effective
  policy disables writes. Native Windows source controls passed both cold
  callback policies and the existing selected-engine trust/preparation flow
  (four passed, one POSIX-only skip). Runtime install contracts and focused
  Python lint passed. POSIX execution, source review/release and the distinct
  complete caller qualification remain open; no installed runtime was changed.
- 2026-11-11: Review of #12142 found that a declaration below a hash-excluded
  payload directory could evade full-plan invalidation. The full-mode digest
  now binds parsed script-owner metadata independently, while retaining the
  existing generated-command digest behavior. Normal and readiness-only
  excluded-directory regressions join the final 34-control passing packet;
  persistent PATH remained unchanged. One older-protocol apply exceeded its
  original 20-second fixture parent limit. The final functional fixture uses
  a 30-second apply bound and 60-second whole-test observer, without changing
  production action or runner aggregate limits. This is not a pass of the
  earlier timing bound. Updated exact-head review and CI remain required.
- 2026-11-11: The focused ownership packet passed 32 controls, including actual
  installed-console first touch for both protocols and both selection forms.
  A distinct prior-reader process loaded the exact accepted pre-change source
  and refused the new producer before apply effects. The existing required
  unsupported-platform control remains satisfied as a negative.
  Initial tests exposed a compound fixture's excessive native work, an
  incorrect observation-status assertion and an outer observer shorter than
  its unchanged child deadlines; those test issues were corrected. A broad
  file run exhausted its unchanged 900-second aggregate and is not a full pass.
  One functional pass was separately rejected for persistent user-PATH drift;
  no writer was attributed and no rollback was attempted. A distinct focused
  run passed the containment guard with matching independent before/after PATH
  hashes. Complete caller qualification and provider publication remain open.
- 2026-11-11: Readiness amendment #12128 merged as
  `f4d72a34fe242928aae3cb3814adfb705a2c630d` after exact-head Copilot approval
  and successful required CI. Implementation now adds the bounded script-only
  ownership reader inside existing repair admission. Validation, provider
  publication/release and the distinct complete caller run remain open; the
  original first-touch refusal remains a failed observation.

- 2026-11-11: Provider #12007 merged as
  `c0f5eccf8bb9eb0361b26cc7515c3320a4481bda` after exact-head Copilot approval,
  all review threads resolved and successful required CI. The frozen provider
  packet passed 101 controls. Promotion run `42960204584` subsequently
  succeeded; the actual `main` preparation blob
  `9fe680c937872e7b849f53c141f95654137683c6` matches the accepted source.
  An earlier installed-Manager TTY failure and promotion refusal remain
  recorded separately; a later success is not a rewritten earlier result.
  Source publication/release is not installed-host rollout. Full first-touch
  setup, caller skew/rollback, complete preservation and adoption gates remain
  open with the existing caller owner; the broader parent is not complete.
- 2026-11-10: Reserved #11989 after checking existing setup coordination and
  the adjacent knowledge-acquisition PR. This proposal precedes runtime changes;
  no installation, activation, adoption or rollout has been performed.
- 2026-11-11: Proposal #11990 merged after exact-head Copilot approval with no
  findings and successful required CI. The source worktree synchronized to the
  accepted plan. Next is the existing native acquisition/source-context seam
  and its focused consumer proof, not a settings cutover or installed rollout.
- 2026-11-11: Implemented the opt-in prospective census and explicit
  `tool-profile-dependencies-v1` capability/result discriminator. The native
  profile parser reads the selected checkout; configuration bytes and revision
  bind the plan. Profile-only remote acquisition reuses the shared installer's
  isolated homes, bounded native execution and activation preservation. It never
  calls repository registration. Exceptional owned-runner exits retain their
  native working directory rather than deleting potentially active write state.
- 2026-11-11: Focused native/source controls passed for profile refusal and
  preservation, actual strict CLI parsing, source-default acquisition and
  read-only machine catalog consumption. A synthetic native catalog control
  also passed through an existing repository adapter without provisioning a
  backend. An initial control incorrectly expected a catalog binding field;
  the machine owner's actual in-process catalog has none. That control error
  does not justify expanding the installation-context primitive.
  Two broad local test groups reached their unchanged 300-second limits with
  no assertion failures reported; narrower affected-source controls passed.
  A temporary cleanup regression involving Windows read-only Git objects was
  caught and fixed by retaining standard-library cleanup on settled completion.
  The final frozen provider selection passed 37 controls, including actual
  public default-source machine-plugin acquisition and its native catalog;
  ten shared acquisition controls and eighteen targeted existing
  profile/preservation regressions also passed. Review, required CI and release
  remain gates. Complete repository setup, caller skew/rollback controls and
  broader setup ownership remain open; no installed-host update was performed.
- 2026-11-11: Provider PR #12007 exposed three concrete completeness gaps:
  portable identity normalization omits acquisition selectors, catalog
  normalization does not validate the payload's manifest name, and raw Git
  failures cannot participate in positively witnessed no-launch recovery.
  The candidate now compares complete source options, checks the manifest and
  private native source definition, and converts Git errors/timeouts through the
  existing profile/refusal owner. Remote aliases use the native marketplace
  classifier. The corrected frozen packet passed 51 controls, including actual
  default-source acquisition/catalog consumption. Six shared-library strict
  typing errors from the first CI run were corrected; both 25-file platform
  targets and the two focused acquisition/retention regressions passed locally.
  The corrected source still requires a new published-head CI/review result.
- 2026-11-11: The next review identified contradictory portable/native source
  identities, deleted payloads blocked by stale completion receipts, and final
  revalidation time omitted from the launch budget. The candidate now rejects
  identity overrides that disagree with native acquisition, observes absent
  payloads as needed while retaining malformed/foreign-state refusals, and
  invalidates the owned stale receipt before reinstalling. The existing
  checkout lock serializes acquisition-only cache writers. Both missing-payload
  paths recheck the caller and total deadlines after final revalidation, with
  cleanup/settlement reserves and positively witnessed no-launch refusal.
  The frozen preparation/native packet passed 61 controls; two real shared
  acquisition controls include deletion/reinstallation without premature
  receipt readiness. Strict Linux/Windows type targets, lint, install contracts
  and vendored parity passed. New exact-head CI and review remain required.
- 2026-11-11: The next review found two further medium defects in the candidate:
  an original catalog entry may omit the version supplied by a now-deleted
  payload, and a repeated credential lookup used the neutral execution cwd
  instead of the selected checkout. Missing-root observation now returns
  needed after receipt identity and complete source checks without requiring
  deleted manifest metadata. Acquisition retains checkout-scoped authentication
  context independently of its neutral execution directory. The combined
  native/preparation and existing authentication packet passed 96 controls,
  including versionless receipt recovery and differing checkout/global
  credential selection. The published-head CI and review gates still apply;
  no caller migration, full repository setup or host rollout is claimed.
- 2026-11-11: Review identified an authentication-policy gap before native
  execution: shared acquisition prepared credentials before Worktrees could
  enforce off/report-only or an existing anonymous-source decision. The
  acquisition-only entry now accepts the caller's authentication callback
  before lookup; standalone behavior remains the default. Worktrees reuses
  its existing mode/anonymous policy for both preparation and execution.
  The frozen native/preparation/authentication packet passed 99 controls,
  requiring zero lookup and no token injection in all three non-authenticated
  cases. Two standalone acquisition/reinstall controls, both strict platform
  targets, vendored parity and install contracts also passed. Exact-head
  publication, CI and accepted review remain gates.
- 2026-11-11: A review summary raised Windows execution bounds and local-profile
  discovery without an enumerated finding. Direct inspection confirmed that
  the Windows installer's ordinary deadline is inactivity-based. Preparation
  now also supplies that owner's existing absolute native deadline, preventing
  continuous output from extending the shared acquisition budget. The frozen
  packet passed 101 controls, including an actual sustained-output Windows
  child that times out and settles its Job/activation transaction. A real CLI
  control also proves canonical repository-local directory-profile discovery
  without activation; no local-profile behavior change was needed. New
  exact-head CI and review remain required.
