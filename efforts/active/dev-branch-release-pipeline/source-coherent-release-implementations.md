# Source-coherent release implementations

Parent: [Dev/Main Release Pipeline](README.md#phase-9e---source-coherent-release-implementations).
Tracker: #8983. Participant: Release implementation coherence contributor.
Status: Done for bounded Phase 9e; plan #9054, source implementation #9061,
Main bootstrap #9078 and qualified release #9080 merged; authorized local
selected/running-state proof passed. The parent campaign remains Active.

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

- [x] Reconcile this proposal with the existing source/control owners and
      obtain independent review. Resolve any concrete source-authority or
      privilege-boundary defect before execution: #9054,
      `c4868a1bbaab3f822aacc3a2b57d5ad5417a9851`.
- [x] Implement source-coherent helper selection in the ordinary Dev workflow
      proposal. Retain and prove the existing privileged-job qualification
      prerequisites rather than adding redundant gates. Keep Main bootstrap
      code tied to its exact executing snapshot.
- [x] Bind the privileged helper closure and executable build inputs to
      qualification, enroll missing helper decision-rule coverage and reject
      uncovered/unbound additions before privileged execution.
- [x] Add bounded PR-time compatibility checks against the actual Main control
      contract. Exercise the known missing complete-source input and
      producer/verifier disagreement before publication, not for the first time
      in release. Preserve the positive unit/effect/coverage and time budgets;
      actual native/build integration remains in its existing development or
      periodic lane.
- [x] Validate the complete prepared Main workflow with the existing
      publication-boundary and merge-wait controls plus focused source/identity
      and privilege regressions. Do not credit tests of only legacy/default
      YAML as proof of the prepared control.
- [x] Land ordinary implementation/tests/docs through Dev review: #9061,
      `3e1dba5cf3d84c9809eb74e1d99caaf89fb94637`.
- [x] Land only the authorized critical workflow/control delta through a separate
      Main PR, with current-head review and required checks: #9078,
      `f6a1acf9592ef574f83ae2de5c2cc5fe0e84400a`.
- [x] Follow the existing single release writer through a real composed
      candidate, source units, artifact construction and verification,
      attestation, complete-source publication/readback, final PR, atomic Main
      merge and release tag. Diagnose actual failures without bypasses or
      competing publishers: natural run `40315448433`, final PR #9080.
- [x] Perform the authorized local updater flow and verify selected and
      running state. Preserve unrelated worktrees, claims, backups and ongoing
      operations; do not infer cleanup or unrelated restart authority.

## Validation Plan

- [x] Prove every bootstrap/helper checkout uses the intended immutable
      Main/admitted-source identity; no literal helper-version constant,
      mutable branch or candidate-controlled import can substitute.
- [x] Prove fork/manual-feature refs, forged dispatch payloads, missing source
      qualification, failed/skipped units and wrong owning run cannot enter
      privileged implementation execution or release landing.
- [x] Prove a newly added privileged helper or dynamic executable input cannot
      be omitted from the qualified source/coverage contract. Positive units
      cover new native/resource decision rules without invoking real actors.
- [x] Prove that a source helper change reaches its matching producer/verifier
      in the next admitted run without editing a Main helper pin. Required
      controller capabilities and complete-source inputs still fail visibly
      when missing.
- [x] Preserve exact six-resource Containers wheel/source/independent-sdist
      behavior and procutil's source-bound native artifact verification; no
      generic setup-hook exemption or self-asserted binary digest.
- [x] Exercise actual frozen-candidate artifact construction with both reviewed
      build-hook contracts, then the complete prepared controller qualification.
      Component tests or a successful composition are not release completion.
- [x] Verify a natural/manual authorized Main run publishes and reads back all
      required subjects before its generated PR, then actually merges and tags
      the admitted source. Retain pause, strict-base freshness, concurrent
      Main-control handling and non-administrator landing.
- [x] Verify the authorized local rollout consumes that released source and
      reports real selected/running versions, not merely a merged PR.

## Journal

- 2026-10-26: The unified local updater exited 0 and refreshed all fifteen
  enabled payloads. Native manager/monitor, Bridge, coordinator and MCP cutovers
  completed cleanly without forced termination or lost in-flight claims.
  Independent checks confirm selected manager/live monitor `1.24.293-dev1`,
  ready Bridge `0.9.174.dev1`, healthy non-draining coordinator
  `0.18.112-dev1`, and actual MCP serving interpreter `0.2.96-dev1` with a
  successful ping. The dirty anchor and newer status owner were left untouched.
  All bounded Plan/Validation items are complete; parent and other participants
  retain their wider obligations.
- 2026-10-26: Natural first-activation run `40315448433` passed every build,
  source-unit, exact-reuse, attestation, complete-source publication/readback,
  nonadministrator atomic merge and tagging gate in twelve minutes. Source
  `3e1dba5cf3d84c9809eb74e1d99caaf89fb94637` owns the nineteen-asset
  `python-artifacts-3e1dba5cf3d84c9809eb74e1d99caaf89fb94637` release.
  Complete source was published for qualified candidate
  `9c17ba85e17e32b5600da3d1ab7ce30eefe3c60d` before final PR #9080
  merged as `dd6e548fa7009852a5d8c5a02b44c797f0a8dc79`. Its immutable
  promotion tag independently peels to that landing. Main control and pause
  boundaries remain preserved. Queued manual run `40315520821` correctly
  skipped duplicate work. The authorized unified local update is running;
  actual installation/running-state proof remains open.
- 2026-10-26: Main bootstrap #9078 merged as
  `f6a1acf9592ef574f83ae2de5c2cc5fe0e84400a` through a normal direct
  merge after clean current-head review and the required Main source gate.
  Only the qualified workflow delta landed. One authorized, normal manual
  dispatch started the existing serialized writer as run `40315520821`; actual
  signing, publication/readback, atomic Main landing/tagging and local rollout
  are still pending, not inferred from the bootstrap merge.
- 2026-10-26: Source implementation #9061 merged as
  `3e1dba5cf3d84c9809eb74e1d99caaf89fb94637` after current-head review
  with no open findings and all required checks. The final admitted packet
  passed 2,352 cases in 26.244 seconds; exact frozen composition passed the same
  portfolio in 27.177 seconds. The independent workflow-only Main bootstrap
  passes 25 explicit boundary controls but remains unactivated. Main review,
  actual release and local running-state proof are the next gates.
- 2026-10-26: Hosted review follow-up closes non-tools and already-visited
  file-loader decision enrollment, narrows native `exec` admission to its exact
  declared-input site, and refuses captured helper calls beside valid calls.
  All 2,352 admitted cases passed in 26.381 seconds without unit effects.
  Prepared-controller/publication controls passed 70 cases and 222 subtests.
  Main remains the explicit legacy generation pending the independent bootstrap;
  source qualification is not activation or release completion.
- 2026-10-26: Hosted follow-up now checks actual Main against Main's own helper
  inventory, including commands absent from the source proposal. Lexical loader
  provenance refuses dynamic import aliases and shadowing. Source-contract tests
  use recorded Dev while explicit Main inputs remain unsubstituted. The complete
  2,330-case packet passed in 28.618 seconds with the compatibility CLI itself
  positively covered; source/explicit-controller regressions passed 101 cases.
  The single-workflow Main bootstrap is prepared locally, not activated.
- 2026-10-26: The next hosted review follow-up restricts migration warnings to
  the explicit legacy pin generation, makes active source-coherent interface
  mismatches fail, and checks each direct/indirect helper invocation separately.
  File-spec loads now contribute qualified repository edges or fail explicitly;
  the unchanged Windows retention actor retains only its exact private boundary.
  All 2,270 admitted cases passed in 29.149 seconds, including 79 helper-source
  inputs and 119 coverage targets. Prepared-controller/publication regressions
  passed 94 cases and 222 subtests. Main bootstrap and release/rollout remain open.
- 2026-10-26: Hosted review follow-up now derives qualification roots from
  controller-discovered commands, including development and preserved Main
  recipes, and requires actual executable function-body hits rather than
  import-phase line attribution. Added a real import-inside-test refusal,
  new-command enrollment refusals, generated-Main source-recipe replay and an
  explicit obsolete-helper selection witness. All 2,041 admitted cases passed
  in 23.517 seconds; the focused helper/materializer/controller cohort passed
  328 cases. Main activation, publication and rollout remain open.
- 2026-10-26: Architecture follow-up closed structural dynamic-input coverage,
  synchronized executable-copy origins and conditional CLI compatibility gaps.
  Runtime publication validation and controller checking now share the same
  mode-input declarations; legacy index behavior remains unchanged. The
  resulting 2,032-case packet passed in 25.769 seconds, and the complete prepared
  controller/publication regressions passed separately. Main activation and
  real release/rollout remain open.
- 2026-10-26: Implemented source-coherent workflow selection and qualification
  of the 73-source release-helper closure. The admitted 2,029-case packet
  passed with zero actor effects or skips in 24.096 seconds, including actual
  helper decision-call coverage and hash-bound native compiler input. Prepared
  controller and publication/merge boundary regressions passed separately.
  The compatibility guard correctly reports current Main as incompatible
  because it lacks complete-source construction. Dev review, the independent
  Main workflow bootstrap, actual signed release and local rollout remain open.
- 2026-10-26: Plan #9054 merged after current-head approval and required CI.
  Existing source-coherent Dev helpers built all fifteen bundles for the frozen
  source and a separate fresh validator accepted the complete artifact set,
  including Containers resources and procutil native bindings. This is
  construction/source-binding evidence only; Main authority, signed publication,
  release merge and local rollout remain open. Qualification must still close
  the privileged helper coverage/import-closure contract before activation.
- 2026-10-26: Operator redirected bounded recovery from recurring helper
  backports/pin updates to durable source/controller coherence. #9050 is merged
  only into its isolated helper review branch. Main activation, release
  qualification and local rollout remain open.

## See also

- [Parent effort](README.md)
- [Release-pipeline vision](../../../visions/release-pipeline/README.md)
- [Main-control vision](../../../visions/main-owned-workflow-control/README.md)
- [Complete source and late Main controls](complete-source-and-late-controls.md)
