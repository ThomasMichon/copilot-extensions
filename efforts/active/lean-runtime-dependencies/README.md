# Lean Runtime Dependencies

- **Slug:** `lean-runtime-dependencies`
- **Repo:** copilot-extensions
- **Branch(es):** independent sequential slice PRs against `dev`
- **Created:** 2026-10-11
- **Status:** Active
- **Vision:** [Plugin services](../../../visions/plugin-services/README.md):
  `capability-sized-runtime-dependencies`, `compatible-helper-runtime-reuse`;
  [agent-index](../../../visions/plugins/agent-index/README.md):
  `lightweight-client-and-declared-host-service`
- **Umbrella issue:** #6234
- **Related issues:** #5768, #5873, #4876, #6143

## Guiding Intent

Reduce repeated installation, package footprint and cold-start work without
removing useful capabilities or weakening independently installable services.
The first wins are removing the unused worktrees UI dependency and completing
the index client/service dependency boundary. Compatible separate-process helper
reuse is a first-class investigation, not a preselected mandatory global broker.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Proposal, integration, sequential PRs and completion | Managed upstream worktree |
| Helper investigator | Bounded shared-runtime and ZDD evidence | Read-only delegated investigation |

## Coordination

- **Topology:** independent sequential slice PRs; one implementation writer per slice.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Helper investigator returns evidence and a compatibility proposal;
  no concurrent edits or publication from the coordinator's worktree.
- **Handoff:** this README owns the completion gate; each successor reads the
  current slice and journal. No phase completion closes the campaign.
- The index service distribution and rollout remain owned by
  [agent-index standalone service](../agent-index-standalone-service/README.md)
  (#5768). This effort owns dependency boundaries and measured footprint changes,
  not a competing index service implementation. Coordinate overlapping installer
  work with #5873 and open PRs before editing.
- Shared daemon ownership composes with #6143; artifact selection composes with
  #4876. Do not recreate either campaign or preempt another contributor's PR.

## Context

[Dependency audit](audit.md) preserves the measured motivation, package frequency,
source evidence, recommended reductions and limits of the measurements.
[Shared-runtime investigation](shared-runtime.md) defines the decision gate for
installer de-vendoring and candidate shared ZDD fronts.

Promotion already expands canonical shared-library references into independently
shippable payloads. That source-sharing mechanism does not itself avoid repeated
venv installation. Installed compatible helpers can potentially be reused over
efficient process boundaries while payloads remain self-contained.

## Request

Operator direction, with only private destination names abstracted:

> This is great analysis. Ensure this is documented as the motivation for an
> effort (and put a log/journal entry in [the private knowledge repository]), and
> then plan an effort to go down your priority list and tackle the biggest
> issues. Textual removal from agent-worktrees is an obvious win, as is finishing
> the server/client split in agent-index. I also think there's an opportunity to
> have our service installers explicitly "de-vendor" shared code, so that if
> installation of one service already put together a shared-lib venv then another
> install doesn't have to. This of course only works if we have some efficient
> separate-process helpers. ZDD fronts are a good example: we might get away with
> having only one instance of ZDD, for example.
>
> Use your analysis to drive vision updates, effort buildouts, and get started
> on work.

The operator confirmed the slug `lean-runtime-dependencies`.
The additional package/profile priorities below originate in the audit and are
marked agent-recommended; exploratory backend replacements are not commitments.

## Plan

### Phase 1 - Reviewed intent and dependency baseline
- [x] Land this proposal, the audit motivation and vision amendments through review (#6235).
- [ ] Refresh declaration/import and installation-profile evidence against the
  execution snapshot; record direct/transitive, active/retained and logical/physical distinctions.
- [ ] Coordinate overlapping index and daemon work with existing owners.

### Phase 2 - Worktrees UI dependency removal
- [ ] Remove Textual from agent-worktrees while retaining Worktree Manager's UI.
- [ ] Preserve version comparison and Manager process dispatch without relying
  accidentally on removed transitive packages.
- [ ] Add dependency-minimal regression evidence, update directly related docs
  and ship the slice with a changefile.

### Phase 3 - Index client and hosted-runtime separation
- [ ] Complete lightweight client import/install boundaries and route hosted
  controller, indexing workers and foreground commands through their owning runtime.
- [ ] Eliminate duplicated store/server installs only after replacing the
  primary-runtime fallback with a supported explicit outcome.
- [ ] Preserve native and standalone-service contracts, configuration, queue,
  corpus state and the separately managed warm embedding engine.
- [ ] Isolate legacy stdio MCP in its own opt-in extra without removing
  agent-facing tool capabilities. _(agent-recommended)_

### Phase 4 - Largest optional installation costs
- [ ] Govern explicit compatible CPU/GPU engine wheel profiles; eliminate
  accidental accelerator downloads and unsafe install-then-swap assumptions.
  _(agent-recommended; coordinate artifact ownership with #4876)_
- [ ] Make restricted-SSH Paramiko provisioning genuinely opt-in.
  _(agent-recommended)_
- [ ] Replace full-table pandas statistics materialization with projected
  bounded aggregation. _(agent-recommended)_
- [ ] Decide syntax grammar extras and unseeded installer profiles from measured
  benefit; retain supported fallback installation. _(agent-recommended)_

### Phase 5 - Installer de-vendoring and shared helpers
- [ ] Execute the [shared-runtime investigation](shared-runtime.md), including
  separate-process reuse and a shared ZDD front candidate.
- [ ] Review the chosen compatibility, lifecycle and installation-cell design
  before implementing a reuse mechanism.
- [ ] Implement the smallest accepted reuse slice, or explicitly transfer an
  accepted-but-deferred design to a named tracked objective. If evidence rejects
  reuse, retain per-service execution and close that implementation path with
  measured costs and the rejection decision; do not transfer rejected work.

### Phase 6 - Close the measured delta
- [ ] Publish before/after profile footprint and fresh-install evidence, with
  running-state proof for any authorized rollout.
- [ ] Resolve or explicitly transfer every implementation and validation item;
  experimental ONNX/storage replacements remain separately tracked decisions.
- [ ] Update reality docs and archive only after the campaign completion gate.

## Validation Plan

- [ ] Unit and dependency-contract tests cover each changed declaration,
  optional extra, import boundary and helper compatibility rule.
- [ ] Simple end-to-end checks exercise actual client/Manager/worker process
  boundaries in dependency-minimal environments, not only mocked imports.
- [ ] Clean-room first installation proves each affected solo plugin/profile,
  combined installations, reuse and missing/incompatible helper outcomes.
- [ ] Windows, Linux and macOS checks preserve supported installer and process
  behavior; unavailable platform lanes require an explicit documented safe
  fallback or feature exemption, not assumed parity. Checks preserve
  headlessness, caller-Job independence,
  graceful cutover, ownership and rollback; shared-runtime concurrency includes
  two installers and two independent marketplace installations.
- [ ] Index integration preserves authenticated routing, HTTP/SSE/MCP contracts,
  background jobs, retrieval semantics and durable state across upgrades.
- [ ] Every slice records applicable unit, integration, clean-room and available
  live-venue evidence, or a specific reason a tier is unavailable.
- [ ] Measurement separates compressed downloads, unpacked logical files,
  hardlink-aware physical allocation, model/cache data and retained version slots.
- [ ] Publication checks, changefiles, review, merge and release attribution are
  satisfied; rollout authorization and its completion owner are explicit.

## Proposal

Approved and merged in #6235. Implementation proceeds in reviewed slices.
Shared reuse is a design investigation; neither one global ZDD process nor one
shared mutable venv is presumed acceptable.

## Journal

### 2026-10-11 - Audit and operator direction
- Preserved the dependency audit as motivation and captured the operator's
  explicit Textual, index-split and installer de-vendoring priorities.
- Claimed #6234 after searching existing work; retained #5768, #5873, #4876 and
  #6143 as neighboring owners rather than creating duplicate implementations.
- Created the proposal and validation gate. Private deployment-specific
  motivation and the full operator request are recorded only downstream.

### 2026-10-12 - Proposal review clarification
- Corrected vision navigation and reconciled helper reuse with standalone
  runtime ownership: each consuming installer must secure a durable compatible
  dependency and its reference lifetime, not borrow a sibling's private venv.
- Reuse remains an unimplemented investigation; reality-pattern changes will
  accompany a separately reviewed design before any runtime migration.

### 2026-10-12 - Reviewed proposal and first implementation slice
- #6235 merged after approval and green CI; the worktree advanced onto the
  reviewed `dev` proposal before implementation.
- Began Phase 2: remove unused Textual and declare `packaging` directly to
  preserve monotonic version reconciliation, with an isolated no-UI import
  regression and dependency-contract check.
- Refreshed the execution census: 40 canonical pyprojects, 36 external base
  declarations, 16 optional declarations and 25 external names. The historic
  audit remains tied to its original snapshot rather than silently rewritten.
- Phase 2 targeted validation: 144 tests passed, including actual isolated CLI
  and Manager-process handoff with UI imports rejected; install contract reports
  zero violations and touched Python passes focused lint.
- The fresh managed test environment resolved without Textual/Rich; Pygments is
  present only through the test runner's pytest dependency. Docker clean-room
  execution is unavailable because no daemon is listening on this host.
  Native Windows evidence is local; Linux runs in PR CI. No macOS lane or live
  runtime rollout is claimed for this declaration-only slice.
