# Resilient Update Sequencing

- **Slug:** `resilient-update-sequencing`
- **Repo:** copilot-extensions
- **Branch(es):** sequential proposal and implementation PRs against `dev`
- **Created:** 2026-10-15
- **Status:** Draft
- **Vision:** [Installer and Configurator](../../../visions/installer/README.md):
  resilient update convergence, historical generated-command migration and
  change-driven local integration
- **Umbrella issue:** #6820
- **Related issues:** #6812, #1238, #1110, #6234, #4876, #6476

## Guiding Intent

Reach a verified good, current installation without recurring manual cleanup or
repeating expensive successful work. Discover cheap blockers first, prepare
expensive and failure-prone inputs early, activate healthy candidates through
short recoverable transitions, and finish with inexpensive local integration.
Ship the historical-command and Terminal profile improvements first rather than
waiting for a complete installer redesign.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Design, implementation, review, integration and authorized verification | Managed upstream contribution worktree |
| Observability owner | Timestamp framing, operation audit and measured work reduction | #6812 |
| Installer owners | Existing candidate construction, admission and activation contracts | #6476 and the vendored-installer-engine effort |

## Coordination

- **Topology:** sequential reviewed slices; the Coordinator owns this effort's
  implementation and completion gate.
- **Host (owns PRs):** Coordinator.
- **Delegates:** bounded non-overlapping implementation or evidence only;
  no child owns the entire campaign by completing one slice.
- **Handoff:** this README and [design](design.md) retain the parent completion
  gate. Record merged work, unresolved dependencies and exactly one next slice.
- #6812 retains telemetry, line framing and its independent audit. This effort
  owns historical command migration, the narrow profile-refresh integration and
  sequencing changes. Coordinate shared update entry points before publication.
- #1238 retains the broader transfer of project-command ownership to Manager.
  The first slice repairs the current owner's migration contract; it does not
  move ownership or rewrite agent invocation policy.
- #1110 retains explicit legacy-to-cell attribution. Same-lineage version
  migration never implicitly activates a cell or claims an unrelated owner.
- #6234 and #4876 retain dependency minimization, compatible reuse and artifact
  acquisition. #6476 and the existing installer-engine effort retain shared
  construction mechanics. Reuse their contracts; do not build another engine,
  artifact resolver, trust root or update scheduler.

## Context

The monitored no-version-change baseline in #6812 took 229.1 seconds, including
153.9 seconds of payload refresh, 21.7 seconds of profile refresh and 26.5
seconds of opaque runtime checks. No venv was built, so those measurements do
not establish changed-version or cold-build cost.

Project command ownership preservation can currently be followed by an
"in sync" message and a successful aggregate exit. The migration recognizer
covers only one old template family, not all attributable historical generated
commands. Profile refresh re-enters the installer self-stage path before
calling Manager, even though Manager already owns the pure fragment generator.

The [design](design.md) defines boundaries, execution order, migration cases,
incremental rollout and the validation matrix. These are proposed changes, not
a statement that prepare-only adapters or suite-wide atomic activation exist.

## Request

Operator direction, verbatim:

> The command-ownersihp refusals need to go away; the existing ones came from previous generations of the harness so cross-version migration was missed and we need automatic cleanup for them. It's nuts that is takes 20 seconds to refresh terminal profiles.
> I assume venv builds are the biggest hit; there should be an effort for that. We really need to sequence things so that the highest-risk, longest-pull activities occur first, so we can fail-fast and offer retry or mitigation. Then we should get more mechanical, quick, and reliable as we continue. So while the other agent builds more telemetry and auduting, help strategize a plan to re-sequence the update for better resilience.

> The binstubs/profiles stuff seems like low-hanging fruit we should knock out. Help sequence an effort and design here (for all the above), get it reviewed, and drive it.

The earlier stated completion requirement is:

> Its goal is always to *get to a good, current state*; giving up or skipping things is bad because an operator won't be around to see what's wrong.

The operator confirmed `resilient-update-sequencing`. Concrete algorithms,
slice boundaries and validation details below are agent-recommended means of
fulfilling that direction, not additional operator requirements.

## Plan

### Phase 1 - Reviewed design and coordination
- [ ] Land this effort, design and standing intent through review before code.
- [ ] Establish complementary edit boundaries with #6812 and retain neighboring
  effort ownership explicitly.

### Phase 2 - Historical command migration and cheap local finishing
- [ ] Recognize attributable historical generated command/receipt generations,
  refresh same-lineage launchers and retire obsolete owned project commands
  automatically, preserving foreign, edited and ambiguous artifacts.
- [ ] Make command/receipt transitions recoverable and idempotent under the
  existing ownership locks; test Windows sibling files and POSIX launchers.
- [ ] Make unresolved required command reconciliation explicit in update status
  and exit behavior; never call a preserved conflict "in sync."
- [ ] Replace the profile install/stage detour with the Manager's narrow owning
  surface, preserving fragments, state/settings reconciliation and shortcuts.
- [ ] Run local finishing once after relevant update inputs stabilize; avoid
  unchanged writes and unnecessary repeated discovery.
- [ ] Publish measured no-change and migration/profile-repair results, with
  changefiles, review and source/release distinction intact.

### Phase 3 - Risk-first, resumable update execution
- [ ] Collect one attributable desired-state plan and front-load cheap
  deterministic blockers, costly acquisition and dependency admission.
- [ ] Compose existing prepare/validate/activate contracts with the installer
  owners. Do not label a combined build-and-activate installer prepare-only.
- [ ] Introduce a readiness barrier and dependency-aware recoverable activation
  for supported adapters, retaining prior healthy generations and explicit
  unsupported-adapter behavior.
- [ ] Preserve verified target progress and retry transient failures with
  backoff and fresh inspection; keep authority/policy/integrity blockers
  durable and actionable without inventing another scheduler.
- [ ] Adopt reuse and bounded independent preparation only where measured
  benefit and existing isolation/resource contracts justify them.

### Phase 4 - Release, running-state proof and conclusion
- [ ] Verify promoted source contents and perform the authorized local unified
  update; prove command migration, selected runtime health and profile latency.
- [ ] Record no-version-change, changed-version and fresh-path coverage
  separately; do not substitute footprint results for build timing.
- [ ] Resolve or explicitly transfer every implementation and validation item
  to an accepted named objective, update reality docs, archive and finalize.

## Validation Plan

- [ ] Historical receipt/template fixtures cover same-lineage payload relocation,
  known pre-receipt formats, missing/retired projects and knowledge-only commands.
- [ ] Foreign/cross-cell owners, edited bytes, corrupt receipts, ambiguous source
  lineage and case collisions remain protected and explicitly reported.
- [ ] Failure between command and receipt publication, concurrent reconcilers,
  partial Windows siblings, cancellation and retry preserve recoverability.
- [ ] Profile integration crosses the real Manager command boundary without
  installer/self-stage invocation; unchanged inputs preserve output bytes and
  timestamps, and changed selections retain stable GUID and shortcut behavior.
- [ ] Update failure propagation distinguishes intentional exclusions from
  unresolved required targets; a retry does not hide failure history or rebuild
  already-verified candidates.
- [ ] Preparation, readiness and activation fault injection proves incomplete
  slots are never selected, prior runtimes remain usable and input drift causes
  revalidation rather than stale publication.
- [ ] Native Windows and POSIX focused tests, simple end-to-end checks and
  applicable fresh-environment tests cover changed contracts; record any
  unavailable clean-room/live lane explicitly.
- [ ] Targeted lint, install-contract checks, required CI and review pass for
  every implementation slice. Measure profile stage and total no-change
  latency before/after with timestamped logs.
- [ ] Source merges, release promotion and actual running-state verification
  are separately recorded; no deployed-copy patches or direct default pushes.

## Proposal

Pending review. Start with Phase 2 after the proposal merges. The larger
preparation barrier requires actual installer adapter support; it is not
achieved by rearranging today's combined installer invocations.

## Journal

### 2026-10-15 - Proposal and execution authorization
- Captured the requested historical cleanup, profile latency and resilience
  direction without treating telemetry work as an implementation dependency.
- Claimed #6820 as the companion to #6812 and retained the existing build,
  artifact, ownership-transfer and transactional-installer campaigns.
- Implementation has not started; the reviewed proposal is the first gate.
