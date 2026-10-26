# Source-coherent release implementations

Parent: [Dev/Main Release Pipeline](README.md#phase-9e---source-coherent-release-implementations).
Tracker: #8983. Participant: Release implementation coherence contributor.
Status: Proposed; implementation and Main activation require review.

## Operator direction and bounded ownership

The parent Request preserves the operator's 2026-10-26 direction verbatim:
durably unblock Main releases without recurring version-pin maintenance,
including authorized Main workflow PRs and local rollout.

This is one bounded recovery slice in the existing #3336 campaign, not a second
release controller. #6237 retains portfolio/control coordination; Phase 9d and
#8736 retain wider complete-source/late-control work. #8720 retains daemon
lifecycle and runtime ownership. Existing source, review and artifact owners
are not transferred by this proposal.

The design and sequence below are **agent-recommended**. The release-pipeline
vision's `one-consistent-composition`, `pr-time-integrity-contracts` and
`independently-repairable-control`, and the Main-control vision's
`one-compatible-execution`, govern this slice.

## Confirmed recurrence and constraints

Main's release workflow repeatedly selects an older helper generation through
a manually edited literal commit pin. Reviewed changes can therefore add build
contracts to Dev that the executing producer and verifier do not understand.
The resulting generic setup-hook refusal first appears during release.

The Containers-only component was independently reviewed and merged as #9050
into an isolated helper-maintenance branch, not Main. It preserves #8976's six
canonical resources, exact registered hook and helper, source-bound wheel
members and missing-input refusals. An actual frozen build then identified
#8720's separate procutil native hooks in fourteen vendored inputs. That
component merge is not an unblocked release and will not be treated as one.

Blindly advancing the whole pin is not sufficient either: newer publisher
interfaces require complete-source subjects while the older Main workflow
does not produce or pass them. The controller and implementations must agree
on the same release contract.

Immutable identities remain necessary for a single run. The operator's
rejection of recurring pin maintenance does not authorize mutable branch
execution, candidate-supplied helper imports or dropping source integrity.

## Proposed authority and execution contract

Main remains the authority for workflow policy, source admission, pause,
serialization, privileges, artifact/signing requirements and exact-head,
strict-base, non-administrator release landing.

The read-only bootstrap uses implementation code from the exact executing
Main snapshot for the existing stable lifecycle/admission operation. It does
not need a separately maintained helper-version constant.

After Main admits one immutable protected-Dev source revision through the
existing source/ancestry checks, that revision supplies the downstream release
implementations as well as the release's development input. The workflow
selects the complete immutable SHA from the trusted gate output, never a
mutable branch, dispatch-provided revision, downloaded executable or arbitrary
candidate import path. Checkouts do not persist credentials.

Construction and source-unit qualification may overlap without signing or
release-write authority. Any job executing those source-selected
implementations with signing, attestation or publication authority must wait
for successful qualification of the exact composed source and retain the
canonical Main/run admission. Unit success is not itself artifact integrity:
the existing source-bound wheel, lock/feed, attestation, independent readback,
complete-source, freshness and final merge checks remain required.

Qualification must bind the repository-owned privileged helper import closure
to the admitted unit inputs and exercise its decision rules through eligible
unit coverage. Merely listing a new helper's hash is not coverage. Newly used
resource/native admission helpers and their explicit executable build inputs
must not enter privileged execution through an unqualified import or an
unrecorded dynamic load. Real compiler/process behavior remains separately
qualified rather than relabeled as unit execution.

The helper selection is therefore derived from the release's admitted source,
not a second hand-maintained version. New controller policy remains an
independently reviewed Main change; source helper evolution does not grant
permission to redefine workflow privileges or omit a control requirement.

The one-time Main bootstrap must use a coherent controller/implementation
contract, including the already reviewed complete-source construction,
attestation and publication interfaces. It cannot merely replace one pin
expression while leaving incompatible call sites.

## Plan

- [ ] Reconcile this proposal with the existing source/control owners and
      obtain independent review. Resolve any concrete source-authority or
      privilege-boundary defect before execution.
- [ ] Implement source-coherent helper selection in the ordinary Dev workflow
      proposal. Retain and prove the existing privileged-job qualification
      prerequisites rather than adding redundant gates. Keep Main bootstrap
      code tied to its exact executing snapshot.
- [ ] Bind the privileged helper closure and executable build inputs to
      qualification, enroll missing helper decision-rule coverage and reject
      uncovered/unbound additions before privileged execution.
- [ ] Add bounded PR-time compatibility checks against the actual Main control
      contract. Exercise the known missing complete-source input and
      producer/verifier disagreement before publication, not for the first time
      in release. Preserve the positive unit/effect/coverage and time budgets;
      actual native/build integration remains in its existing development or
      periodic lane.
- [ ] Validate the complete prepared Main workflow with the existing
      publication-boundary and merge-wait controls plus focused source/identity
      and privilege regressions. Do not credit tests of only legacy/default
      YAML as proof of the prepared control.
- [ ] Land ordinary implementation/tests/docs through Dev review. Land only
      the authorized critical workflow/control delta through a separate Main
      PR, with current-head review and required checks.
- [ ] Follow the existing single release writer through a real composed
      candidate, source units, artifact construction and verification,
      attestation, complete-source publication/readback, final PR, atomic Main
      merge and release tag. Diagnose actual failures without bypasses or
      competing publishers.
- [ ] Perform the authorized local updater flow and verify selected and
      running state. Preserve unrelated worktrees, claims, backups and ongoing
      operations; do not infer cleanup or unrelated restart authority.

## Validation Plan

- [ ] Prove every bootstrap/helper checkout uses the intended immutable
      Main/admitted-source identity; no literal helper-version constant,
      mutable branch or candidate-controlled import can substitute.
- [ ] Prove fork/manual-feature refs, forged dispatch payloads, missing source
      qualification, failed/skipped units and wrong owning run cannot enter
      privileged implementation execution or release landing.
- [ ] Prove a newly added privileged helper or dynamic executable input cannot
      be omitted from the qualified source/coverage contract. Positive units
      cover new native/resource decision rules without invoking real actors.
- [ ] Prove that a source helper change reaches its matching producer/verifier
      in the next admitted run without editing a Main helper pin. Required
      controller capabilities and complete-source inputs still fail visibly
      when missing.
- [ ] Preserve exact six-resource Containers wheel/source/independent-sdist
      behavior and procutil's source-bound native artifact verification; no
      generic setup-hook exemption or self-asserted binary digest.
- [ ] Exercise actual frozen-candidate artifact construction with both reviewed
      build-hook contracts, then the complete prepared controller qualification.
      Component tests or a successful composition are not release completion.
- [ ] Verify a natural/manual authorized Main run publishes and reads back all
      required subjects before its generated PR, then actually merges and tags
      the admitted source. Retain pause, strict-base freshness, concurrent
      Main-control handling and non-administrator landing.
- [ ] Verify the authorized local rollout consumes that released source and
      reports real selected/running versions, not merely a merged PR.

## Journal

- 2026-10-26: Operator redirected bounded recovery from recurring helper
  backports/pin updates to durable source/controller coherence. #9050 is merged
  only into its isolated helper review branch. Main activation, release
  qualification and local rollout remain open.

## See also

- [Parent effort](README.md)
- [Release-pipeline vision](../../../visions/release-pipeline/README.md)
- [Main-control vision](../../../visions/main-owned-workflow-control/README.md)
- [Complete source and late Main controls](complete-source-and-late-controls.md)
