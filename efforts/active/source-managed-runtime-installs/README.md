# Source-Managed Runtime Installs

- **Slug:** `source-managed-runtime-installs`
- **Repo:** copilot-extensions
- **Branch(es):** independent, serial per-slice PRs against `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** [Plugin Service Model](../../../visions/plugin-services/README.md#managed-runtime-deployment), [Runtime Assembly](../../../visions/plugin-services/runtime-assembly/README.md#selected-development-source-assembly), [Installer](../../../visions/installer/README.md#independent-runtime-source-selection), [Clean-room Validation](../../../visions/clean-room-validation/README.md#independent-source-installation)
- **Umbrella issue:** #7028
- **Sub-issues:** #3905 (existing source-materialization gap), #7029 (source selection and immutable generations), #7030 (deployment boundary and adapters)
- **Future objective:** #7031 (exclusive local plugin-content sourcing; outside this runtime-only campaign)

## Guiding Intent

Separate Copilot plugin delivery from service-runtime deployment without
disabling ordinary hook behavior. An opted-in installation can deploy clean,
merged development source directly, retain a stable release or exact pin, and
iterate through immutable generations without waiting for marketplace payload
updates or rewriting a live environment.

Prove the source install on a disposable container before attempting a native
Windows runtime-only pilot. Ordinary plugin self-provisioning remains the
default; managed deployment is a local choice, not adoption of another product
or a change to service-supervision ownership.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation coordinator | Owns the plan, integration, public PRs and completion gate | Managed source worktree |
| Linux clean-room runner | Reproduces and verifies actual source installation and activation | Existing Docker clean-room rig; WSL is a supported host |
| Windows validation runner | Verifies native installer, resolver and non-deployment hook behavior | Isolated Windows fixtures, then an explicitly authorized runtime-only pilot |
| Independent reviewer | Reviews each coherent proposal or implementation change | Repository PR review |

## Coordination

- **Topology:** serial, coherent PRs against `dev`; this plan clears review before implementation.
- **Host (owns PRs):** Implementation coordinator.
- **Delegates:** bounded evidence, implementation or validation scopes only; no shared mutable git ownership.
- **Handoff:** the next owner reads this README and the current slice evidence before continuing. A merged PR resolves only its actual Plan and Validation Plan items.
- **Existing owners:** preserve #3654 (vendor pointers), #5472 (immutability), #4876 (artifact admission), #5851 (source isolation), and #6812/#6820 (update outcomes/sequencing). Reuse their mechanisms; do not fork their campaigns.

## Context

The current Manager has self-update source configuration, but that is not a
suite-wide runtime-source policy. Plugin bootstrap paths still publish runtime
content and installed command redirects from the enabled payload.

Promotion composes versions and expands development vendor pointers into a
self-contained `main` tree. Raw-dev non-editable installs have an existing
tracked gap (#3905). Canonical runtime helpers already provide immutable
completion, activation, retention and claim-owned editable development
generations. This campaign extends those building blocks, rather than treating
editable development claims as clean source deployments.

The [source-owned release vision](../../../visions/release-pipeline/README.md)
keeps the verified development commit as the release identity. Generated main
commits, materialized payload digests, package versions, profiles and dependency
locks describe different facts; no new competing artifact authority is needed.

## Request

**Settled request summary:** add a local gate only around service payload
publication and installed runtime redirect updates; retain all other hook
behavior and ordinary Copilot marketplace delivery. Add an independent managed
runtime source supporting main, stable/exact older pins and merged dev.
Materialize dev dependencies and install immutable, hash-qualified,
non-editable generations from clean merged source, never worktrees or dirty
checkouts. Keep adapters thin and version-skew compatible. Begin with a
clean-room container on WSL, then prove a native Windows runtime-only pilot.

The operator confirmed the slug and explicitly selected **runtime-only** for
the Windows pilot. Local plugin-content sourcing is long-term intent tracked
separately in #7031, with exclusive source replacement rather than shadowing.

The [inception record](inception-transcript.md) retains the operator's words,
with private host identifiers redacted. Implementation choices below marked
**agent-recommended** are not additional operator requirements.

## Plan

### Phase 0 - Review the intent and plan
- [x] Land the vision amendments and this canonical proposal through repository review.
- [x] Confirm issue/slice ownership and preserve the existing dependency campaigns.

### Phase 1 - Reproduce direct-dev installation in the clean room
- [ ] Extend the existing Tier-P rig with a credential-free, source-install scenario using a recorded clean merged dev commit.
- [ ] Reproduce #3905 through an actual non-editable installer path and record the first classified failure.
- [ ] Establish observable source identity, runtime generation, installed command and source-checkout isolation assertions.

### Phase 2 - Assemble immutable merged-source generations
- [ ] Reuse canonical source materialization for the complete declared vendor closure without modifying the input checkout.
- [ ] Add source-qualified non-editable generation identity while preserving package versions, profiles, dependency policy and completion evidence.
- [ ] Make installer, runtime resolver and installed command redirects agree on the exact selected generation on Linux and Windows.
- [ ] Prove identical reuse, distinct same-package-version commits, source removal, interrupted build and failed-candidate preservation.

### Phase 3 - Separate plugin-triggered service deployment
- [ ] Add a local managed-deployment policy at the shared publication boundary, retaining standalone defaults and installation scope.
- [ ] Cover session bootstrap and indirect first-use publication without disabling other hook or extension behavior.
- [ ] Keep adapters thin and enforce drift-compatible service interaction without implicit runtime replacement.

### Phase 4 - Integrate Manager-owned runtime source selection
- [ ] Maintain a separate installation source with main, stable/exact pin and opt-in merged-dev selection resolved once per run.
- [ ] Keep service-source selection independent of Manager self-update and Copilot plugin-content selection.
- [ ] Route the selected source through the same assembly, readiness, activation and observable update-outcome path.
- [ ] Preserve exact selection across retries and source failures; never fall back to an ambient checkout or marketplace payload.

### Phase 5 - Prove assembled use and native Windows readiness
- [ ] Run the assembled runtime path in a fresh WSL-hosted clean-room container from a clean merged dev commit.
- [ ] Exercise real commands and service interaction after source-checkout removal, including marketplace/runtime skew and non-deployment hooks.
- [ ] Prove native Windows source installation, generation selection and hook-deployment separation before a consented runtime-only pilot.
- [ ] Update owner documentation and record actual serving-generation evidence, not only successful copies or install markers.

### Completion and scope control
- [ ] Resolve all Plan and Validation Plan items; record or transfer remaining discrete work to a named tracked objective before marking Done.
- [x] Deferred to `#7031`: implement exclusive local-checkout sourcing of Copilot plugin content after the runtime-only campaign.

## Validation Plan

- [ ] **Source admission:** reject dirty, worktree-backed, unmerged or substituted source; preserve the exact resolved commit throughout a run.
- [ ] **Materialization:** actual installer imports the complete vendor closure from a non-editable install with no source-checkout dependency.
- [ ] **Identity:** identical inputs reuse unchanged completion bytes; different commits or assembly/dependency/profile inputs cannot overwrite a completed slot.
- [ ] **Selection:** installed binstubs and native resolvers execute the exact completed selected generation, including hash-qualified dev sources.
- [ ] **Failure:** incomplete/failed candidates remain unselected and preserve the previous healthy runtime and redirects.
- [ ] **Policy:** real hook and first-use paths do not publish runtime copies or redirects in managed mode; their non-deployment behavior still runs.
- [ ] **Skew:** newer-plugin/older-service and older-plugin/newer-service interactions work within declared capabilities or return precise readiness outcomes.
- [ ] **Source modes:** main tracking, stable/exact pins and dev tracking converge through one source-assembly path without moving midway through a run.
- [ ] **Clean-room:** a structured Tier-P report proves fresh Linux installation and real service use; report the exact source and serving identities.
- [ ] **Windows:** native installer/resolver fixtures pass; any installed pilot has explicit change consent and attributable running-state evidence.
- [ ] **Agent-recommended - robustness:** test concurrent preparation/publication, unavailable-source retry and state-aware rollback through existing lifecycle primitives.
- [ ] **Agent-recommended - rollout evidence:** retain a concise qualification/soak record before marking a development snapshot stable; do not infer stability from a clean install alone.
- [ ] **Contribution gates:** targeted bounded tests, required install-contract checks, appropriate clean-room evidence and reviewed merged PRs cover the final implementation.

## Proposal

Keep deployment policy, source selection and serving-generation selection
separate. Reuse the canonical materializer, runtime construction/retention and
admission paths. Runtime-source selection does not modify marketplace
definitions, introduce a second supervisor, or claim release promotion.

**Agent-recommended:** keep the Manager's own recovery/update source on a
known-good selection while canarying service runtimes; treat this as an
independent configuration choice rather than forcing the Manager and suite
onto one branch.

## Journal

### 2026-10-16 - Proposal and scope
- Captured the agreed runtime/plugin boundary and confirmed the effort slug.
- Opened #7028, #7029 and #7030; reused #3905 for raw-dev materialization.
- Tracked future exclusive plugin sourcing separately in #7031.
- Confirmed Docker is reachable on a WSL host; no native runtime policy, marketplace settings or running services were changed.
- Implementation remains gated on the proposal PR merging.

### 2026-10-16 - Reviewed proposal and clean-room slice
- Proposal PR #7053 received an exact-head Copilot approval with no findings and successful checks, then merged as `41c1e787ae`.
- Pulled the owning worktree forward and selected Phase 1. The effort remains open; proposal merge is not implementation completion.
- Began a credential-free Tier-P source-install scenario using the actual Agent Bridge installer from an exact merged dev commit.

### 2026-10-16 - First source-install witness
- The fresh container installed merged dev commit `41c1e787ae` through the real Agent Bridge installer without Copilot credentials.
- The first assertion failed because the selected generation was only `0.4.1-dev3`, without a source identity.
- Inspection found shared-library imports resolving into the checkout and generated metadata/cache files written there. Moving the source checkout made the installed import fail with `ModuleNotFoundError: credential_relay`.
- This narrows the current manifestation of #3905: editable canonical references can hide the dependency until the checkout is unavailable. The fix must materialize a non-editable closure outside the source, not merely make the initial installer exit successfully.
- Began canonical source preparation and a source-qualified runtime identity that preserves the original package version; native Windows unit/initializer coverage currently passes, while full source-install acceptance remains open.
