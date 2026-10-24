# Source-Owned Release Pipeline

- **Subject:** continuous publication of development snapshots and their artifacts
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-24
- **Reality docs:** [`docs/pipelines.md`](../../docs/pipelines.md) ·
  [`docs/patterns/dev-main-promotion-pipeline.md`](../../docs/patterns/dev-main-promotion-pipeline.md)

## Purpose & Intent

A verified development commit is the durable identity of a release and its
associated artifact set. The release branch is a generated view of that source,
not a second product history or an independent artifact owner. Consumers and
operators should be able to answer which development snapshot a release carries
without treating an incidental generated Git commit as another release identity.

Promotion should continuously deliver qualified development changes in less
than thirty minutes end-to-end, including queueing, while changes await release.
Its test portfolio consists only of unit tests with coverage; required PR testing
selects that unit portfolio conditionally by impact. A separate, bounded
construction and installed-entry-point qualification must establish that every
declared release environment can be assembled and launched under the selected
package-feed restrictions before publication. This is an explicit exception to
unit-only release execution, not permission to reclassify integration as unit
evidence or restore the general integration portfolio. Repeated bookkeeping,
composition or bootstrap
failures should not turn release control into a barrier to its own repair.
Agreement between artifact production and verification belongs in ordinary
contribution checks, not as a first discovery during release qualification.

## Concepts & Components

- **Source release:** the verified development commit that owns the release.
- **Generated release view:** the wholesale generated payload and source marker
  published on the release branch.
- **Artifact set:** content-verified installation inputs associated with the
  source release, including explicit compatibility and approved lock variants.
- **Composition:** the declared, immutable transformation inputs that determine
  generated versions, materialized content and artifact bytes.
- **Tested candidate:** the frozen, composed release content on which release
  tests execute; it belongs to the development source release rather than
  introducing another release identity.
- **Coverage projection:** attributable correspondence between tested candidate
  locations and their original development sources, distinct from raw execution
  evidence.
- **Landing receipt:** evidence of the actual candidate, atomic publication and
  landed commit; operational provenance, not a competing source identity.
- **Release control:** separately governed execution and recovery authority,
  following the [main-owned control vision](../main-owned-workflow-control/README.md).
- **Complete source snapshot:** reconstructible final release source, including
  its admitted Main-owned controls, retained alongside the release artifacts.

## Features

### development-commit-release-identity

The full verified development commit identifies the release. Generated release
commits record that source identity and can be tagged by it after landing.
Squashing, parentage, publication timestamps or bookkeeping-only changes do not
by themselves create a new source release or invalidate its admitted artifacts.

### wholesale-generated-view

Promotion replaces the release payload with the generated view of one verified
development snapshot rather than incrementally merging unrelated release-branch
history. Any explicitly authorized control exception remains attributable and
separately governed; it does not become another artifact owner or silently admit
unrelated release-branch drift.

### source-owned-artifacts

Artifact discovery and publication identify the source release and its admitted
content, not an incidental candidate or generated commit. Equivalent authorized
payloads and locks can reuse immutable artifacts across different Git containers.
Different bytes, dependency locks or compatibility variants remain distinguishable
and cannot silently replace an existing immutable binding.

### one-consistent-composition

Build, verification and publication agree on the immutable inputs and canonical
representation of a source release. Version state, materialization and approved
control inputs are explicit where they affect the result. Re-reading mutable
release state at another stage must not make the same admitted artifact appear
to belong to a different release.

### complete-before-promotion-pr

The final promotion PR is a proposal to land an already constructed, qualified
and published release view. Its complete source snapshot, declared artifact
inventory and admission evidence exist and have been read back before the PR
is opened or updated. They bind known source and composition identities, not
an unknowable future Main merge commit. Landing does not build missing release
artifacts or silently replace published bytes.

### late-main-control-reconciliation

Independently governed Main controls may advance while a release is prepared.
Promotion preserves the latest admitted controls without overwriting newer
Main payload or silently enlarging the control exception. A control-only
refresh may reuse artifacts whose complete inputs remain equivalent, but its
new complete source snapshot and landing admission must describe the actual
composite being proposed. Changed control policy can invalidate earlier
admission even when package bytes are unchanged. Stale-base refusal and
server-enforced freshness protect the final merge.

### qualified-before-complete

Complete admitted unit evidence, production artifact-availability/integrity and
complete configured-feed construction/launch qualification establish the actual
release, not merely successful submission or an open PR. Apart from that bounded
construction/launch exception, real-Git, multi-process, native, installation and
end-to-end integration tests belong to periodic containerized validation.
Publication and runtime activation remain distinct outcomes. Optional client
artifact use and governed source assembly follow the
[runtime admission contract](../plugin-services/runtime-assembly/README.md).

### composed-before-release-testing

Version stamping, vendoring and other shipped-source transformations precede
authoritative release tests. Tests, artifact production and qualification use
the same frozen composition; successful tests on an earlier development tree
do not qualify later transformed bytes. A change to tested content or its
admitted execution inputs invalidates incompatible evidence before publication.
Independent artifact construction may overlap tests on that frozen composition,
but release authority remains gated on their successful qualification.

### pr-time-integrity-contracts

Changes affecting hashing, materialization, source-closure declarations or
normalization prove producer and verifier agreement before entering development
trunk. Bounded in-process units exercise decision rules and representations;
actual platform, checkout and composition integration runs periodically in
containers. Coverage follows the relevant unit inputs, not just edits to the
verifier's file. Promotion does not become an integration-test discovery loop.

## Behaviors

### canonical-source-comparison

Producer and verifier share one declared canonical source representation.
Known non-semantic checkout, staging or container differences are handled
consistently before source comparison rather than becoming unexplained release
refusals. Normalization never makes changed shipped code, dependencies or an
incorrect source binding equivalent. Published archive and wheel digests still
verify their actual stored bytes; downloads are not rewritten to make a digest
pass.

### attributable-development-coverage

Raw coverage identifies the candidate content and locations actually executed.
Its development-source view retains the originating development snapshot,
vendored-copy provenance and the transformations that justify each coordinate
mapping. Generated, transformed and unmappable regions remain distinguishable;
a projection never claims that different development bytes were executed.
Coverage consumers use only compatible, validated projections and expose
missing attribution rather than silently treating it as covered.

### idempotent-source-publication

Repeating publication for the same source and admitted content returns or reuses
the existing immutable release evidence. It does not require rebuilding,
retargeting immutable tags or renewed artifact approval solely because a Git
container changed. A different source commit is never reported as recorded when
the generated view still names an older one, even if their payloads are equal.

### integrity-without-container-coupling

Content digests, authenticated source binding, package policy and exact approved
locks verify the bytes and authority they describe. They are not substitutes for
source-release identity. A claimed development marker alone never admits altered
bytes. Exact operation identities still protect stale-writer races and atomic
publication without redefining which source owns the release.

### independently-repairable-control

Trusted release bootstrap and bounded recovery do not depend on successfully
installing or validating an unrelated broken product graph. One accountable
writer carries an authorized recovery through qualification; overlapping agents
do not multiply the same exception into repeated approval requests.

### visible-hourly-freshness

Release freshness measures qualified source advancement while development changes
are pending. No-op workflows, queued requests and unqualified snapshots do not
count as deliveries. A normal release completes under thirty minutes including
queueing. Missed cadence names its blocker and owner rather than hiding behind
a green wrapper, a larger deadline or an ever-growing test gate.

### unit-only-release-test-gate

Promotion runs eligible unit tests and collects coverage once. Conditional PR
testing uses that bounded unit portfolio. General integration does not rejoin the
gate through a baseline, preflight or generated-consumer dependency. The separate
configured-feed construction/launch qualification below is the sole bounded
exception; it does not change unit eligibility or credit integration coverage as
unit proof.

### configured-feed-construction-and-first-launch

Every independently constructed Python environment in the release has an
authoritative owner, declared platform/profile, actual interpreter architecture
and ABI, and complete bootstrap, build and runtime dependency closure.
Contribution CI and promotion prove fresh
construction through the selected governed feeds with the applicable age and
security policy intact, then launch the installed entry points from the
constructed environments. A declaration-age audit, a first-party wheel build,
an import from the source checkout or an already-populated environment cannot
substitute for that proof.

Qualification covers the complete inventory, including standalone and selected
optional runtime environments. Unknown owners, missing platform/profile results,
unavailable feeds and failed or skipped construction are visible release
refusals, not green omissions. Evidence binds the admitted source and composed
candidate, environment inventory, platform/profile and package-policy inputs.
Provider coordinates remain adopter configuration; no public-index fallback or
policy exception is inferred from a successful build on another host.

### periodic-integration-and-replacement

Integration assurance remains available through periodic containerized
validation with explicit failure ownership. Stable delivery comes first;
subsequent portfolio improvement replaces lesser-value tests with more valuable
eligible units within the same or smaller budget, instead of accumulating them.

## Non-Goals / Boundaries

- No generated-main commit as a second durable release or artifact identity.
- No fake or omitted admitted-unit evidence, or removal of byte integrity,
  signing, exact locks, source governance or publication authority.
- No general end-to-end, actual-Git or multi-process integration-test portfolio
  in promotion; only the bounded configured-feed construction/launch exception.
- No implicit authorization for a main repair, stale overwrite, forced tag
  movement or change to independently governed control preservation.
- No second release channel, competing publisher or separate client provenance
  authority.
- No format, tag spelling, schema version or workflow wiring prescribed here.

## See Also

- Parent vision: [Visions](../README.md)
- Related: [Main-Owned Workflow Control](../main-owned-workflow-control/README.md)
- Related: [Runtime Assembly & Artifact Admission](../plugin-services/runtime-assembly/README.md)
- Related: [Test Portfolio Effectiveness](../test-portfolio/README.md)
- Child visions: none (leaf)
- Reality docs: [`docs/pipelines.md`](../../docs/pipelines.md) ·
  [`docs/patterns/dev-main-promotion-pipeline.md`](../../docs/patterns/dev-main-promotion-pipeline.md)
- Realization: [Dev/Main Release Pipeline](../../efforts/active/dev-branch-release-pipeline/README.md)
