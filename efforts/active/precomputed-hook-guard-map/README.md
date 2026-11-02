# Precomputed Hook Guard Map

- **Slug:** `precomputed-hook-guard-map`
- **Repo:** copilot-extensions
- **Branch(es):** managed source worktree
- **Created:** 2026-11-01
- **Status:** Active
- **Umbrella issue:** #9911
- **Sub-issues:** None selected.
- **Vision:** [Worktree lifetime and agency authority](../../../visions/plugins/agent-worktrees/README.md)

## Guiding Intent

The existing Worktrees resident prepares a small map of local repository
anchors and their guard facts. Hooks resolve the relevant repository through
bounded local Git metadata and consult that map. No hook event starts topology,
plugin, registry or configuration acquisition, including on a cache miss.
If the map does not exist, the cross-repo guard fails open.

Precompute facts, not permission decisions. Preserve supported write routing,
current controls and recovery access without adding a daemon or requiring a
healthy resident merely to inspect or repair an installation.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Own this bounded projection/consumer change, validation and source PRs | Managed source worktree `steady-blueprint` |
| Existing hook maintainers | Retain unrelated hook, lifecycle and installer work | Existing repository issues and PRs |

## Coordination

- **Topology:** serial, coherent PRs against `dev`.
- **Host (owns PRs):** Source driver.
- **Delegates:** none assigned.
- **Handoff:** this effort and its public issue carry source progress and gates.
- Land the plan before implementation. Preserve existing source owners outside
  this bounded slice. Installation or live activation requires its own scope;
  merge and release are not evidence of deployment.
- The reconciled publication model is **managed push, without watchers**:
  reuse managed related/registry mutation and session preparation/startup
  boundaries, with explicit refresh for out-of-band edits. Do not introduce
  watcher frontiers, TTL expiration or periodic topology recomputation.

## Context

`scripts/cross_repo_guard.py` currently discovers guarded roots through the
`related` CLI on a missing or expired sidecar. The strict command-hook path
can therefore acquire the full plugin/related graph within a short event
deadline. The resident `_ResidentHookPolicy.guarded_roots()` also computes
on a request-time cache miss, while holding the hook request's shared lock.
Switching between those implementations does not remove discovery.

Earlier #7968 bounded/cached acquisition and #9534 batched registry paths into
`related list`. This effort removes hook-triggered acquisition rather than
adding another timeout allowance.

Reuse `RepositoryMapRuntime`'s producer-owned snapshot, generation and
admission pattern and the existing registry/related authorities. The vision
already assigns repository resolution to the producer, not request handlers.
This closes that intent; the specific JSON and Git keys are below its altitude.
Apply the existing circumstantial-hook and service-contract-evolution patterns.

## Request

Operator architecture direction, verbatim:

> Right. Identify what changes we need to make. My vision/architecture recommendation is that we figure out what we can compute ahead of time to preoduce a "quick lookup" for the hook. The agent-worktrees daemon could assist in keeping some fast-access JSON file updated with some dict/keys that contains some key computable by the hook for a set membership test.

Selected simplification and failure behavior, verbatim:

> Great. Make an effort to drive this update. Fortunately, there will never be that many local repos checked out most of the time, so a quick list can be made of every repo anchor and whether or not it needs the guard; the hook can resolve the anchor from git for any arbitrary folder, then check the map. If the map doesn't exist, fail-open

Execution direction, verbatim:

> Great. Help drive and build this, so we can have a fixed hook locally

The bounded source slice is assigned to the Source driver. Bounded local Git
identity is allowed; an earlier zero-Git design suggestion is not a requirement.

## Plan

The decomposition and implementation details below are **agent-recommended**.

### Phase 1 - Reviewed contract and coordination

- [x] Identify both request-time discovery paths and existing producer reuse.
- [x] Establish a deduplicated public issue and land this plan.
- [x] Specify repository/common-directory identity, caller/installation scope,
  complete local-repo coverage and unavailable-state behavior.

### Phase 2 - Producer-owned facts

- [x] Build and atomically publish the small guard map from the existing
  resident lifecycle, outside hook handling and the shared hook lock.
- [x] Serialize managed source mutation and fresh acquisition before publication;
  bind the observation to caller/registry identity and its publication generation.
  Do not promote incomplete acquisition observations into stable-input proof.
- [x] Publish through existing managed mutation/preparation boundaries and
  expose explicit refresh for out-of-band edits; no watchers or periodic scan.
- [x] Preserve complete-empty versus unavailable observations and surface
  publication failures without making reads refresh or start a service.

### Phase 3 - Lookup-only hook consumers

- [x] Replace standalone discovery and resident lazy acquisition with the same
  bounded map reader and local Git repository-key lookup.
- [x] Preserve current modes, kill switches, expiring grants, target extraction,
  delegation nudges and installation isolation.
- [x] Update deployment/reconciliation inventories and compatibility behavior;
  remove the hook's obsolete discovery/runtime-resolution dependencies.

### Phase 4 - Qualification and delivery

- [ ] Complete the Validation Plan and document actual behavior.
- [ ] Land source through required review/CI, then verify release inclusion.
- [ ] Resolve every Plan/Validation item before marking this effort Done.
  Record installed acceptance separately; do not infer it from source delivery.

## Validation Plan

- [x] A missing map fails open without topology/CLI discovery, refresh,
  installation, daemon start or an infrastructure-shaped deny.
- [x] Both native and resident consumers fail a test if topology, plugin
  activation, registry acquisition or full config resolution is invoked.
  Git identity uses bounded metadata reads, not a subprocess or RPC.
- [x] Positive, negative, complete-empty, malformed, incomplete and wrong-scope
  views retain distinct truthful outcomes. Managed mutations withdraw old views
  before committing; out-of-band changes require explicit refresh/preparation.
- [ ] Cover ordinary subdirectories, new files, relative/absolute and multiple
  targets, linked worktrees, bare anchors, nested repositories/submodules,
  aliases, UNC paths and platform case/normalization semantics.
- [x] Verify live mode/kill-switch/grant-expiry changes and existing decisions.
- [x] Verify serialized current acquisition, atomic concurrent reads,
  failure withdrawal before source mutation and retained retry scopes.
- [ ] Exercise related add/remove/primary and registry mutation publication,
  preparation/startup publication and explicit refresh; prove no watcher,
  TTL-driven or periodic topology acquisition is installed.
- [x] Exercise the actual cold/warm/missing-map native entry, bounded Git and
  cleanup; preserve one implementation per event and supported ACP behavior.
- [x] Verify the actual installer-owned fresh wrapper copy and its reader
  dependency closure in a disposable root.

## Proposal

Use a bounded, versioned JSON projection owned by the existing resident.
Resolve source and target repository identity locally; distinguish a worktree
root from its common Git directory. Keep caller-specific related policy and
installation scope explicit rather than using a global union of guard rules.

No map, unsupported schema or unusable observations may cause a foreground
rebuild. Current controls and the current operation still determine the result.
Unknown facts are not a complete negative inventory; recovery-capable degraded
evaluation is distinct from authorization or successful lifecycle mutation.

## Journal

### 2026-11-01 - Source slice opened

- Read existing source contracts, resolved the producer/consumer seams and
  checked open guard-map issues and pre-tool PRs.
- Notified the existing hook owner of the bounded source assignment without
  acquiring its checkout or unrelated work.
- Captured the bounded-Git and missing-map fail-open decisions for plan review.
- Reconciled the existing #9911 owner and accepted its managed-push/no-watchers
  design refinement. Closed the subsequently discovered duplicate #9920;
  source work continues against #9911 without acquiring the earlier checkout.
- Planning merged in #9923 after approval and required green checks.
- Implemented the shared file-only reader, managed producer, existing mutation/
  preparation integration, explicit refresh, deployment closure and documentation.
  Preparation updates only its caller; it does not rebuild every prepared
  worktree. Managed mutations and explicit refresh cover prepared views.
- Integrated Windows share-delete reads after a real concurrent publication
  failure, withdrew old views before source mutation, and retained unavailable
  caller scopes after a partially failed writer. Plugin contribution writes
  refresh existing views without treating their containing checkout as a caller.
- The directly affected contained packet passed 515 tests with four
  symlink-capability skips. PowerShell 5/7 entry controls passed eight selected
  cases with the matching prepared source SDK. The older selected SDK lacks
  `agent_procutil.owned_budget`; its separate six admission failures do not
  qualify mixed-version operation and were not hidden by changing the adapter.
- The actual Python installer wrapper-copy owner and cold/warm/missing-reader
  consumer passed in a disposable root. These results do not establish installed
  activation, full runtime construction or unexecuted platform/native lanes.
- Final producer/reader controls passed 21 cases, including caller-only
  preparation and explicit completed-source/incomplete-publication outcomes.
  The existing create/resume lifecycle packet passed 45 cases. Complete
  complementary instruction-preparation selections passed 144 cases with
  13 platform/capability skips under unchanged deadlines.
- Corrected the five native nonregular-file fixtures to model both `stat` and
  `lstat` explicitly. A full preparation group exceeded its existing bound;
  smaller complementary selections, not a larger timeout, qualified the file.
- Rebased onto current `dev`, preserving checkout profile preparation and
  Windows Git observation behavior. The pre-tool no-project-acquisition
  assertion now covers both observation modes. The rebased monitor packet
  passed 188 cases and deployment closure passed seven.
- Routed actual map reads through the existing share-delete opener. A focused
  overlapping reader/real atomic writer test preserves a whole snapshot and
  permits bounded writer completion. Windows replacement can still encounter
  a transient open-target conflict; the existing writer retries it rather than
  requiring another rename implementation. The final map/profile-preparation
  packet passed 24 cases.
- Opened implementation #9938. Its first CI run identified two stale
  admitted-unit source hashes and module-size growth. Updated only the changed
  admission inputs, kept registry dispatch concise, and moved initial resident
  preparation diagnostics into the guard-map owner without widening a baseline.
  The final focused guard-map packet passed 23 cases, including initial resident
  publication and its visible fail-open error path.
- Addressed all four findings from the first implementation review: atomically
  retain prepared caller scopes during withdrawal/interruption, consistently
  acquire registry authority before publication locks, retire automatically
  rediscovered callers removed from registration, and reuse each target identity
  only within its current event. The focused packet passed 29 cases, including
  actual concurrent registry-owned work and interruption/retry recovery.
- Corrected the incremental review's legacy `AGENT_HOME` mismatch. Map and
  live-grant reads now use the same selected root, without borrowing real-home
  state. The root-isolation, native, guard and registry packet passed its
  other 95 cases with one capability skip; the resident live-grant fixture
  needed an explicit matching `AGENT_HOME` and its corrected case passed.
- The current-head re-review identified FIFO/nonregular inputs in the hook and
  both producer reads. All three now share bounded regular-file validation,
  nonblocking POSIX opens and post-open checks. The focused packet passed 33
  cases with one native-FIFO capability skip; synthetic FIFO-type rejection
  runs for all three readers on every platform.
- Addressed the next overview's transient-identity warning even though it had
  no inline open issue. Unreadable checkout or registered-anchor metadata no
  longer retires a prepared caller. Two regressions prove later refresh restores
  the complete inventory. The focused packet passed 36 cases with one native
  FIFO capability skip.
- Retained the caller's registration name through complete snapshots and every
  tombstone stage. This distinguishes a removed caller from an unrelated
  unavailable checkout without relaxing caller-identity failure handling.
  The combined removal/outage regression and interruption binding assertions
  passed in the focused packet: 37 cases, one native FIFO capability skip.
- Addressed the next overview's map-store indirection and unregistered-source
  mutation warnings. The actual Windows junction control proves neither reads
  nor publication can enter another store. Related-index mutations still work
  in unregistered checkouts while refreshing prepared callers; new implicit
  caller registration uncertainty is advisory, not an after-write failure.
  This packet passed 40 cases with one native FIFO capability skip.
- Applied the same unavailable-versus-empty distinction to unreadable declared
  guarded checkouts. The focused unreadable-target and unregistered-write
  controls passed three cases; no incomplete observation became an empty view.
- Captured the configuration owner's suppressed binding/YAML failures and
  isolated each publication from an outer warm configuration cache. Activation
  now uses the same `AGENT_HOME` as configuration. Optional presence ambiguity
  is refined through the existing source probe; appeared or unreadable inputs
  refuse publication. The focused packet passed 46 cases with one native FIFO
  capability skip, including malformed binding/recovery and cache controls.
- Added the real producer wiring regression requested in the next review:
  actual source selection, plugin contribution discovery and YAML grafting over
  plugin/base/caller-specific knowledge/global machine/exact project layers.
  Only native acquisition is substituted. Managed knowledge and exact-layer
  removals republish the two callers without staleness or context crossover.
  The same focused selection verifies lossless Git pointer spaces, including
  actual Windows extended-path directories. The wider run passed 47 cases with
  one native FIFO capability skip; a normal Win32 trailing-space fixture needed
  extended paths, and its corrected focused selection passed both cases.
- Replaced prepared-view globbing with failure-visible directory enumeration.
  Refresh and managed-write regressions prove an unreadable inventory cannot
  report empty success, change source bytes or leave old facts paired with a
  newly changed policy. The focused listing/concurrency/real-graft selection
  passed four cases.
- Preserved retry scope when an existing map has a transient file-read error.
  Withdrawal and prepared-caller scans now distinguish unavailable I/O from
  missing or malformed maps. An unreadable map refuses a pre-mutation write;
  post-mutation errors retain unavailable tombstones and report incomplete
  publication. Seven phase-specific controls prove a later refresh recovers
  every caller and its original guard. The complete map packet passed 57 cases
  with one native FIFO capability skip.
- Replaced error-suppressing Git path predicates with bounded native metadata
  observations. Unreadable nested Git markers no longer fall through to a
  parent repository; unavailable or nonregular common-directory markers no
  longer invent a linked worktree's common identity. Three controls retain
  the unavailable result and recover the correct identity after repair.
  The combined map, standalone consumer and deployment-contract packet passed
  95 cases with one native FIFO capability skip.
- Made machine-wide related-policy selection failure-visible in the existing
  configuration acquisition report, reusing its source probe rather than an
  error-suppressing file predicate. The real producer regression now refuses
  an unreadable global layer, retains unavailable caller maps and restores
  both callers' effective guards after repair. The focused producer and
  configuration-source packet passed seven cases.
- Preserved the registered lexical alias alongside canonical Git identity.
  Shell target matching now recognizes both paths without resolving the whole
  inventory during an event. A real native junction control covers redirects,
  write cmdlets and `git -C` through both spellings, retaining read-only access.
  The combined packet passed 95 cases with one FIFO skip; its generated hook
  hash assertion required regeneration and then passed with the alias control.
- Re-admitted the changed configuration-source bytes in the existing unit
  manifest after CI rejected the old hash. Source verification passes without
  changing the unit inventory, coverage targets or required gate behavior.
- Qualified aliases through the actual registered-root owner, which deliberately
  returns a canonical path. Publication now retains the registry's lexical
  path only after that owner validates the checkout; the native alias control
  no longer substitutes past this resolution seam.
- Let the resident invoke the same cross-repo decision path as the native
  consumer, so relevance and live disabled modes run before Git/map lookup.
  Read-only shell commands and disabled write events fail a regression if they
  traverse Git metadata. The map and pre-tool relevance packet passed 121
  cases with four explicit native-capability skips.
- Removed inventory-wide `realpath` traversal from map validation. Canonical
  physical paths and the separately retained registered alias are validated as
  data; only the caller and relevant event targets need current filesystem
  identity. A 20-root control fails if inventory paths are probed.
  Common Git identity also requires its object/ref directories, with two
  missing-metadata and recovery controls. The combined map, consumer and
  deployment-contract packet passed 101 cases with one native FIFO skip.
- Corrected two existing denial fixtures after the new common-directory
  validation exposed incomplete synthetic Git metadata. Both installation
  scopes now include object/ref directories, and the resident fixture selects
  its map through the matching `AGENT_HOME`. The contained three-case packet
  passes without weakening metadata validation or borrowing another map.
- Moved SessionStart guard acquisition out of the resident shared lock into
  its existing checkout-local backup worker. Managed pre-launch and initial
  resident publication remain explicit producer boundaries. The lifecycle
  regression forbids synchronous guard preparation, and the worker regression
  proves acquisition runs after releasing its administrative lease. Failures
  remain visible alongside independently successful instruction delivery.
  The focused five-case packet and complete 26-case worker packet pass.
  Rebased onto current Dev while preserving the native stderr handling fix;
  regenerated only the combined installer's existing admission hash.
- Replaced literal `git -C` path-prefix detection with event-local Git
  identity matching. Relative and quoted directory arguments preserve linked
  checkout protection while independent nested repositories remain unguarded.
  Repeated directory options resolve relative to the preceding option.
  The full map, standalone consumer and deployment packet passed 108 cases
  with one native FIFO capability skip, including six directory-identity
  regressions and the existing 20-root identity-cost control.
- Restricted Git directory consumption to quoted literal global `-C`
  arguments before the write subcommand. Message text can no longer redirect
  the inferred repository or manufacture a guard hit. Four explicit/implicit
  directory-message controls pass, and relative path cases exercise repeated
  directory arguments. The full packet passed 112 cases with one native FIFO
  capability skip.
- Reused the existing anchor guard's pure Git invocation parser instead of
  maintaining a second partial global-option parser. Cross-repo matching
  retains its own write/delegation policy and event-local Git identity; it
  does not load anchor registrations or import the runtime. Ten controls cover
  literal-pathspec and configuration options before/after `-C`, including
  implicit cwd and unrelated-repository cases. The map, standalone consumer,
  deployment and relevance packet passed 180 cases with four native-capability
  skips. Existing message, linked/nested and identity-cost controls remain green.
- Addressed the next review overview's index-repository warning despite its
  absence of inline issues. A work-tree-only override changes file targets,
  not the repository owning Git's index; matching now retains the current
  repository alongside explicit work-tree targets unless Git's repository/cwd
  was explicitly overridden. Four work-tree controls and the complete focused
  Git identity/message/options packet passed 27 cases.
