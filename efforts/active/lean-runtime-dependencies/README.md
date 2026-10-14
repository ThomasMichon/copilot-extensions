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
- [x] Refresh declaration/import and installation-profile evidence against the
  execution snapshot; record direct/transitive, active/retained and logical/physical distinctions.
- [ ] Coordinate overlapping index and daemon work with existing owners.

### Phase 2 - Worktrees UI dependency removal
- [x] Remove Textual from agent-worktrees while retaining Worktree Manager's UI.
- [x] Preserve version comparison and Manager process dispatch without relying
  accidentally on removed transitive packages.
- [x] Add dependency-minimal regression evidence, update directly related docs
  and ship the slice with a changefile.

### Phase 3 - Index client and hosted-runtime separation
- [x] Complete lightweight client import/install boundaries and route hosted
  controller, indexing workers and foreground commands through their owning runtime.
- [x] Eliminate duplicated store/server installs only after replacing the
  primary-runtime fallback with a supported explicit outcome.
- [ ] Preserve native and standalone-service contracts, configuration, queue,
  corpus state and the separately managed warm embedding engine.
- [x] Isolate legacy stdio MCP in its own opt-in extra without removing
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

### 2026-10-12 - Textual slice merged
- #6293 merged to `dev` as `cd7f0f939` after a current-head approving review
  and successful CI run `38250963792`, including Linux guard execution and the
  Windows launch lane. Version comparison and the actual Manager process boundary
  remain intact without Textual. Release promotion is tracked separately; a
  `dev` merge is not a production rollout or proof of publication to `main`.
- Review added normalized-distribution guards for equivalent UI package spellings.
  The final eight focused tests pass. Dependency comments reflect required
  `packaging` while retaining existing defensive damaged-runtime behavior.
- The docstring correction briefly exceeded the shrink-only module ceiling;
  it was condensed rather than widening the baseline or bypassing CI.

### 2026-10-12 - Index dependency boundary implementation
- Implemented the native base-only primary CLI and required healthy host sibling
  without duplicating complete service-cell or explicit-source native runtimes.
  Hosted query/index/worker commands preserve original arguments and exit codes;
  missing host dependencies are explicit errors. No production activation occurred.
- Full contained Windows suite passed: 789 tests, 97 platform/explicit-tier skips.
  Subsequent headless-command and lexical-path normalization checks passed:
  43 tests, one POSIX-only symlink skip. Both installer parsers, focused Python
  lint, headless-launch and installation-contract guards pass.
- Real fresh base installation contains no store/server/model packages and
  refuses hosted startup honestly. The actual PowerShell sibling provisioner
  installed a complete, origin-checked host environment beside it without
  starting a daemon, registering supervision or publishing an active version.
- RECORD-accounted external logical files measured 2.46 MiB for the unseeded
  development client profile and 508.85 MiB for the signed native host sibling.
  The latter includes pip/setuptools; versions and seeding differ, so these are
  profile evidence, not physical reclaim or a universal before/after estimate.
  LanceDB 0.39.0 contributes 314.31 MiB, chiefly its 312.02 MiB native extension.
- Docker clean-room and local Linux/macOS execution remain unavailable.
  Available Linux validation belongs to PR CI; existing live canary/warm-engine
  rollout remains with the standalone-service owner and was not performed.
- The first Linux CI run exposed eight stale POSIX test-harness expectations:
  a missing dropin fixture, an optional-server success assertion and an overly
  broad procutil extraction. Corrected those harnesses to exercise the required
  host package failure before activation; Linux execution of the corrections
  remains a gate rather than being inferred from Windows skips.

### 2026-10-14 - Merge reconciliation and next slice
- #6317 merged to `dev` as `a0869fbae` on 2026-10-13 after the POSIX fixture
  corrections. Its final Linux index suite, Windows checks and required PR gate
  all passed. The PR description now records the graceful-cutover assessment.
- Marked the delivered client/host boundary and duplicate-install removal items
  complete. The broader native/state-preservation validation remains open:
  passing contained checks are not authorized live rollout or macOS evidence.
- The Textual slice includes the dependency-minimal regression, docs and patch
  changefile in merged #6293. Its remaining "ship" checkbox awaits release
  attribution rather than treating a `dev` merge as a release.
- Prepared the bounded two-consumer helper experiment for review. It does not
  introduce a shared runtime or close the compatibility/lifecycle design gate.
  Next implementation slice: isolate legacy stdio MCP dependencies, retaining
  the existing CLI bridge and explicit opt-in legacy entry point.

### 2026-10-14 - Helper evidence merged
- #6643 merged to `dev` as `9015d09d8` after approval and green CI.
  Review corrected assertion-elision under optimized Python; four process
  contract tests now pass under `-O`. This closes publication of the bounded
  probe, not installation deduplication, auth isolation or shared lifecycle work.
- The full Phase 5 design gate remains open. Proceeding with the already
  planned optional dependency reduction before choosing a reuse mechanism.

### 2026-10-14 - Explicit legacy stdio MCP profile
- Moved the MCP SDK requirement out of `[store]` into `[stdio-mcp]`; native
  `[store,server]` installers do not request it. The normal agent read CLI is
  unchanged. The legacy adapter is HTTP-client-only and runs in the explicitly
  opted-in invoking runtime, not a host sibling that no longer installs its SDK.
- Full contained Windows validation passed: 857 tests, 180 explicit/platform
  skips. The suite includes a real `agent-index mcp` initialize/tools-list
  handshake, plus role-parametrized no-host-dispatch and manifest checks.
- A fresh 12-package base profile has no MCP SDK, NumPy or FastAPI and refuses
  `mcp` with exit 2 and actionable opt-in guidance. Adding only `[stdio-mcp]`
  exposes all five real SDK tools and completes the real stdio handshake without
  NumPy or FastAPI. The SDK has its own transitive HTTP dependencies; the extra
  is not described as dependency-free.
- Focused Python lint and the 13-plugin installer contract pass. Linux behavior
  remains subject to PR CI; no local macOS lane or Docker daemon is available.
  No managed runtime slot, service, model or production endpoint was changed.

### 2026-10-14 - Optional MCP slice merged
- #6658 merged to `dev` as `0976e89aa` after a current-head approval and green
  Linux/Windows checks. Review also removed MCP from both launcher provisioning
  allowlists, aligned the solo clean-room witness with honest SDK absence, and
  declared the Python 3.10 test backport.
- Linux CI exposed premature EOF in a pipelined protocol fixture. The final
  test uses the actual SDK client to await initialize and list-tools responses
  before closing its caller-owned transport. No sleep or retry hides the race.
- This completes the explicit optional-MCP implementation item. Release/rollout
  and broader native-state validation remain separate outstanding obligations.

### 2026-10-14 - Bounded statistics implementation
- Confirmed Textual removal in released `main` snapshot `f327cf447` (#6642):
  required `packaging`, absent Textual and the normalized UI dependency guard
  are all present. Phase 2 publication is complete; this does not prove local
  installation or authorize runtime rollout.
- Replaced full-table pandas materialization with a source/language-only Arrow
  scan requesting 1,024-row batches. Histograms omit nulls, retain empty strings
  and preserve the existing empty-store shape. Count and mid-scan failures now
  propagate rather than manufacturing zero or partial success.
- A real LanceDB fixture with 2,502 rows proves complete counts beyond the
  default query limit and across multiple batches without `to_pandas`.
  Structural coverage pins projection and batch size; failure tests reject both
  count errors and partial scans.
- The full contained Windows suite passed: 858 tests, 180 platform/explicit-tier
  skips. Focused content/statistics checks passed 16 tests. The implementation
  stays within the existing shrink-only module ceiling; no baseline was widened.
- This changes status-query execution, not storage layout or service ownership.
  No fresh installer, production endpoint, embedding model or live deployment
  was exercised. Linux validation remains a PR CI obligation.
