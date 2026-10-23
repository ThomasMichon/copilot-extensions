# Namespace Discovery Latency

- **Slug:** `namespace-discovery-latency`
- **Repo:** copilot-extensions
- **Branch(es):** Independent, sequential plan and implementation PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** [agent-bridge](../../../visions/plugins/agent-bridge/README.md), ownership clarity and honest cross-provider discovery
- **Umbrella issue:** #5761 (bounded latency slice; broader diagnostic acceptance retained)

## Guiding Intent

Make registered namespace discovery usable within its existing foreground
deadline by fixing the measured provider/startup bottleneck, not by increasing
timeouts or masking incomplete results. Preserve independent provider operation,
actual process ownership and compatibility with callers' total budgets.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Discovery repair owner | Plan, profiling, implementation, review and qualification | Managed worktree `rattling-circuit`; coordination on #5761 |
| Release-blocker repair owner | Bounded source-publication repair, not live control activation | Managed worktree `tinkling-toolbox`; coordination on #8033 |

## Coordination

- **Topology:** Sequential plan and implementation PRs; no shared feature branch.
- **Host (owns PRs):** Discovery repair owner.
- **Delegates:** None.
- **Handoff:** Journal the current slice and remaining qualification before any
  ownership transfer. Existing #3963 reader-lifetime and #5559 registry owners
  retain their scopes; this effort does not replace those campaigns.

## Context

The registered provider command can return a valid roster when invoked directly,
yet take roughly nine to eleven seconds on Windows. Bridge's default namespace
list deadline is eight seconds. That bound currently reaches both the outer
await and the native provider execution, so repeated executions can time out
and engage reader admission's circuit.

Current implementations already retain incomplete namespace information and
protect streaming consumers against unconfirmed removals. Credit those
protections: this is not a claim that every caller still receives an
unqualified empty roster. #5761 also requests distinct unavailable-provider,
timeout, confirmed-absent-target and unregistered-namespace outcomes. A
latency-only repair must not close that broader issue.

The plan below is **agent-recommended implementation and validation detail**,
not an operator request to expand deadlines, restart a shared service, update
installed runtimes, or change provider policy.

## Request

Operator selection, captured verbatim:

> repair-bridge

**Selection summary:** Fix the underlying Bridge/provider issue through the
normal source/review flow first. The confirmed effort slug is
`namespace-discovery-latency`. This does not authorize a foreground deadline
waiver or an ad hoc installed-payload fix.

Subsequent operator selection, captured verbatim:

> complete-within-eight

**Selection summary:** Continue optimizing startup and metadata reads; retain
complete cold discovery within eight seconds rather than substituting later
background completion.

Subsequent operator selection:

> repair-release-gate

**Selection summary:** Diagnose and repair the release blocker through the
normal reviewed source flow. Live Main-control changes, guard bypasses and
installed rollout remain separately authorized.

Source-publication clarification, captured verbatim:

> Wait... we don't care about published artifacts, though, we only care about what is in main; update and consumption is based on what main says, and then we check for matching published artifacts for main's tagged dev source commit.

The bounded repair follows the existing source-owned release identity. It does
not introduce version reservation, move publication after merge, or enable the
separately owned installer artifact-consumption lane.

After comparison with the independently merged reservation repair, the
operator explicitly selected:

> Great. Switch to the source-owned model

**Selection summary:** Remove the now-unneeded tag-reservation dependency
introduced by #8110 while retaining ordinary version bumps from landed Main,
the complete source-keyed artifact set and all current-source verification.
This supersedes the reservation approach for this repair; it does not
authorize live Main-control activation or installed rollout.

## Plan

### Phase 1 - Review the bounded repair plan

- [x] Land this proposal through the repository's automated review and required
  CI gates before implementation.
- [x] Bind the repair owner to the canonical effort and retain the #5761 claim.

### Phase 2 - Profile and repair the measured bottleneck

- [x] Profile the real registered command boundary on Windows, separating shim,
  interpreter/import, account enumeration and native backend time; compare cold
  and warm runs without printing credentials or private roster data.
- [x] Inspect existing provider caches and refresh contracts. Select the
  smallest source-owned repair supported by the profile; do not introduce a
  second registry, a new resident service or a competing refresh owner.
- [x] Implement the repair with focused regression coverage. Preserve the
  eight-second default foreground deadline, native execution/CPU accounting,
  actual-holder lifetime, coalescing, cancellation isolation, shutdown/provider
  replacement tracking and admission/circuit protections.
- [x] If a cache is involved, keep its provenance, freshness and completeness
  explicit. Never turn a stale, failed, incomplete or unavailable enumeration
  into confirmed target absence or a success-shaped empty roster.
  No persistent cache was introduced; existing per-process account memoization
  and per-call marketplace reads retain their scope.
- [x] Update directly affected provider/Bridge documentation and add changefiles
  for each changed plugin. Keep the remaining typed-outcome acceptance on #5761
  explicit instead of declaring the entire issue resolved.

### Phase 3 - Qualify, publish and return to the consumer

- [x] Exercise real command and Bridge discovery boundaries on Windows with
  cross-account enumeration, cold and warm paths, and representative failure
  cases, within unchanged caller budgets.
- [x] Land the implementation through review and required CI. #7998 merged to
  `dev`; that merge is not a qualified Main release.
- [ ] Repair the blocking source-publication failure on #8033 through normal
  Dev review. Use the current source's complete signed artifact set rather
  than another source's per-plugin version aliases; preserve source binding,
  immutable bytes, consumer readback and legacy publication compatibility.
  Main-control activation remains with its separately authorized owner.
- [ ] Confirm qualified promotion to `main` separately from the `dev` merge.
- [ ] Retain release/rollout responsibility explicitly. Apply a local rollout
  only through the unified updater with its required authorization; verify the
  actual running generation and complete target discovery before claiming live
  readiness. No shared restart or rollout is implied by this proposal.
- [ ] Record the repaired latency slice and any remaining #5761 obligations,
  resolve all Plan/Validation items or transfer them to a named tracked owner,
  then archive the completed effort.

## Validation Plan

All items are agent-recommended means of proving the requested repair.

- [x] Unit/regression tests cover the measured cause, complete and incomplete
  rosters, provider failure/timeout, cancellation/coalescing, retained native
  holders and admission/circuit behavior without weakening existing coverage.
- [x] A real shim/subprocess check measures at least five cold-process and five
  warm runs; each successful registered provider enumeration completes within
  the unchanged eight-second budget. Report per-run wall time and completeness,
  not just an average or a mocked function's speed.
- [x] A fresh-state check exercises the no-cache/first-touch path. If remote
  backend latency cannot satisfy the contract, expose that limitation explicitly
  and amend the reviewed design rather than silently increasing the deadline.
- [x] Bridge-level integration proves the intended registered target is
  discoverable, healthy peer providers remain usable, and failed/incomplete
  namespaces never become confirmed removals. Include downstream total-budget
  compatibility, not only direct CLI success.
- [ ] Windows cross-account native qualification runs against the candidate
  source without changing shared installed payloads; Linux compatibility runs
  through the existing isolated runner. Record explicit reasons for any
  genuinely unavailable lane and retain its unresolved gate.
- [x] Required source tests, lint and install-contract checks pass for the final
  diff. Documentation-impact review distinguishes plan-only changes from later
  runtime changes.
- [ ] Promotion and authorized running-state proof are distinct receipts; an
  installed selector version alone is not evidence of a running repaired daemon.

## Proposal

Review the plan first. Profiling chooses the concrete repair; neither a larger
constant nor environment-only mitigation is an accepted substitute. This
closes a bounded reliability delta within the existing Bridge vision and
namespace-provider pattern, without changing architectural ownership.

## Journal

### 2026-10-23 - Single verified source-set readback

- Review found the new schema-2 CLI still entered independent helper paths
  repeatedly, causing five candidate validations and three complete source-set
  transfers despite performing no publication writes.
- The canonical readback now returns its validated index after candidate
  validation, source-tag/metadata checks and every exact raw-byte read. The
  schema-2 candidate CLI reuses that result for its publication receipt after
  qualification and all signatures pass. Direct helper callers and the
  historical landing/schema-1 flow retain their independent checks.
- The actual CLI regression now counts exactly one candidate validation and
  one download per index/bundle per invocation. Two pure cases cover receipt
  reuse and readback failure without a success receipt; no network, timing or
  effect-observer exception is introduced.
- The focused publication/source-identity selection passed 100 cases and the
  readiness-core selection passed 32. Its other 23 native/workflow cases were
  outside this focused selection, not credited as passing. Canonical unit
  execution and new-head review/CI remain pending for this correction.

### 2026-10-23 - Source-owned composition selected

- While this repair was under review, #8110 independently merged a
  tag-aware allocator and its regression cohort. The operator explicitly
  selected the source-owned model after comparing that additional dependency
  with Main-selected Dev-source artifacts.
- Rebased onto current Dev, preserving the peer's unrelated control mirror,
  effort state and other changes. Removed only the reservation hook/helper
  and its twelve selector-specific admitted units. Existing composition
  integration tests now require unlanded aliases to have no effect, with
  normal version advancement after Main actually lands.
- Source publication now only verifies its already prepared complete source
  set: it cannot repair a missing set during a final binding. Legacy version-1
  publication and historical alias-validator refusals remain intact.
- The reconciled publication/source-identity selection passed all 98 cases;
  eight real-Git composition cases passed, including unchanged Main versions
  across abandoned aliases and normal advancement after Main lands. The
  twelve historical alias-validator/source-set integration cases also passed.
  Exact unit admission retained every unrelated identity and the unchanged
  300-second bound; lint, module ceilings and documentation guards passed.
- New-head review and required CI are pending. Live helper selection and
  installed acceptance remain separate.

### 2026-10-23 - Coupled alias-regression migration

- Review identified the retained real-Git alias suite as a directly coupled
  consumer of schema-2 version aliases. Migrated its publication assertions to
  the complete Dev-keyed sets, with both failed-Main-merge and squash-landing
  histories. Source publication no longer reads another source's aliases.
- Kept invalid prior-source provenance, signature, composition and payload
  refusals by exercising the retained alias validators directly. The cache
  regression now explicitly authenticates its input before testing reuse;
  corrupt current-source raw bytes still refuse.
- All twelve cases passed in their existing contained integration tier on
  Windows. They were not added to required unit-only CI. This is native Git
  fixture evidence, not successful historical hosted-asset verification or
  trusted Main-control activation.

### 2026-10-22 - Source-keyed publication blocker

- #7998 merged to Dev as `958d3e2046bb395d54c7fe8d63280a5bd68e848b`.
  Promotion `39884763835` passed preceding qualification but failed final
  publication; Main did not advance and no installed rollout was performed.
- #8033 was claimed before source edits. Its immutable artifact index and
  bundle digests were read back. An older unmerged candidate already owned
  Bridge `0.9.116-dev1` and Dispatch `0.18.64-dev1` aliases; the newer source
  adds `process-telemetry` under those same plugin versions. The trusted
  validator correctly rejects that unequal closure when asked to compare it
  against the older alias.
- The defect is that version-2 publication still depends on those aliases
  despite already publishing the complete signed `python-artifacts-<dev-sha>`
  set. The bounded source repair uses that canonical set for publication and
  raw-byte readback, retaining current-candidate validation and all legacy
  version-1 checks. Existing tags and assets remain untouched.
- Source support, reviewed helper activation, actual qualified Main promotion
  and authorized installed proof remain distinct gates. #3336/#6237 retain
  their wider release/control ownership.
- The changed publication and qualification selection passed all 88 focused
  cases, including two Dev sources with different bytes and the same plugin
  version. Four new pure units passed the canonical effect observer and
  single-collection coverage runner. Source hashes, module ceilings and lint
  passed without an observer exemption or widened baseline.
- A broader historical selection was not green: old materializer fixtures
  require Main-only state in a Dev checkout, and workflow fixtures expect
  retired CI jobs. Keep that non-release portfolio with #1303, not as a
  passing full-suite claim. The directly coupled raw-readback assertion now
  expects the canonical helper's actual digest error. A separate focused run
  passed 287 cases before one newly added fixture-construction failure; the
  corrected fixture is included in the final 88-case result.
- Read-only native verification of the historical published set remains
  incomplete: local bundle verification could not initialize its public
  verifier, and this platform's candidate checkout did not reproduce the
  hosted payload hash. Neither failure was waived or normalized away. Those
  attempts are not release qualification. Source review and required CI,
  separately authorized helper activation and actual hosted publication
  remain open.

### 2026-10-22 - Required peer-map metadata and retained Linux scope

- The latest review identified a successful peer response lacking `account_map`.
  That required response field is now validated instead of defaulting to an
  empty map. The legacy account-list shape with an explicit empty map remains
  supported. The focused CLI/account/peer run passed all 136 cases.
- Retained hosted execution receipts confirm Linux CodeSpaces default-full
  qualification at `81065d74a03477c0e7e91ce7775c94d5f514d909`: 1,984 passes
  and 98 explicit skips across all four batches. This includes the new native
  owner-exit and overlap regressions; skips are not credited as passes.
- The Worktrees snapshot regression file belongs to its first successful
  batch. The full Worktrees invocation later stopped in batch five of 24 on
  two bootstrap/installer assertions, so it is only partial suite evidence.
- The completed inspection had seven failing plugin jobs. Their failing tests
  and named implementation paths are unchanged from the candidate's base;
  no baseline-control run was performed, so this is not proof of a fully
  green or regression-free portfolio. Failures cover Worktrees bootstrap,
  container probe structure, Vault service-output/publication, Logger source
  redirection, MCP generated-resolver/manifest fixtures, Bridge ordinary-task
  fixture identity, and Dispatch process-flag fixture typing.
- The workflow declares Test Portfolio Rationalization #1303 as the failure
  owner and marks this portfolio non-release-dependent. Preserve those
  findings for that owner rather than modifying unrelated installer/contracts
  in the namespace repair. Later parser/configuration-seam corrections have
  focused Windows evidence, not a new same-head Linux full-suite receipt.

### 2026-10-22 - Existing configuration seam and explicit individual refusal

- Source CI refused four additional lines in the grandfathered configuration
  module. Removed the new loader parameter and reused its existing
  `provider_reports` seam to supply the opt-in activation scan. The unchanged
  module ceiling now passes; no baseline was widened or assertion removed.
- Preserved explicit-context decoding refusal for individual account lookups
  too. They raise `ContextRefused`, rather than returning no preference and
  permitting ambient credentials. A regression exercises `env_for_repo`.
- The focused consumer/config/authentication run passed 310 cases with six
  existing skips. Requalification through the actual default-budget Bridge
  consumer completed five fresh scans in 6.078, 5.906, 5.985, 5.641 and 5.844
  seconds, each finding the target and serving five warm reads. The malformed
  map remained incomplete in 7.078 seconds; exact restoration recovered in
  6.218 seconds. Native and sampling holders settled.
- The existing periodic Linux inspection is pinned to candidate
  `81065d74a03477c0e7e91ce7775c94d5f514d909` (run `39881444618`).
  Its CodeSpaces suite passed, but several other jobs failed and the run was
  still active when inspected. Their logs and the exact exercised coverage
  remain owed; this is not an all-green Linux qualification claim.

### 2026-10-22 - Namespace-only overlap and bounded owner compatibility

- The next review summary identified interpreter-shutdown retention in the
  existing bounded credential-owner helper. Its daemon thread must not acquire
  executor workers whose shutdown hooks extend the caller's process lifetime.
- Narrowed overlap to explicit namespace enumeration. Ordinary listing,
  account-candidate and configuration reads retain their original serial
  defaults; namespace enumeration opts into all three overlap layers.
  No daemon executor, detached worker, larger budget or new shutdown owner
  was introduced.
- A real child-process regression blocks an owner listing for twenty seconds
  while the bounded helper returns unknown; ordinary interpreter shutdown must
  still finish within the five-second test bound. Additional cases prove
  serial default execution, namespace opt-in and activation-root opt-in.
- The focused consumer/config/authentication suite passed 309 cases with six
  existing platform/optional skips. Five fresh actual Bridge scans completed
  in 6.266, 5.812, 6.422, 5.750 and 5.766 seconds under the unchanged default
  eight-second deadline, each followed by five warm reads. Malformed stored
  mapping remained incomplete in 6.547 seconds; exact restoration recovered
  complete discovery in 6.688 seconds. Native and sampling holders settled.
- The existing manual periodic Linux workflow can inspect an immutable
  candidate ref through the canonical hardened container runner. That hosted
  lane is distinct from unavailable local Docker and the unit-only PR packet;
  its candidate execution remains pending.

### 2026-10-22 - Undecodable peer-output refusal

- The next review had no remaining formal threads but identified undecodable
  peer output in its summary. Both legacy and same-cell snapshot calls now
  translate decoding errors into `AccountInventoryError`; the optional wrapper
  cannot turn those errors into a successful empty namespace.
- Added legacy snapshot and explicit-context namespace CLI decoding regressions.
  The focused CLI/account/peer suite passed all 134 cases. No deadline,
  success-path worker, activation-library source or effect-observer rule changed.

### 2026-10-22 - Explicit-context failure preservation

- The next #7998 review identified a separate boundary: same-cell peer startup,
  timeout and receipt failures raise `ContextRefused`, not `AccountInventoryError`.
  The optional namespace wrapper now preserves both exception types.
- Three CLI regressions exercise the real same-cell peer adapter with startup,
  timeout and exit-126 failures. Each produces a nonzero namespace-list exit
  without a successful JSON roster. The focused CLI/account/peer run passed
  132 cases.
- The carried strict-mapping finding was already corrected by `16ef16a14d`:
  the strict reader rejects malformed stored mappings, seven real-storage
  bulk-command cases passed, and native malformed-map qualification retained
  incompleteness. This is an addressed finding, not another required source fix.

### 2026-10-22 - Routing review corrections and native requalification

- Addressed both High findings and both newly reported Medium findings on
  #7998. Every nonzero account-snapshot exit and process startup/timeout
  failure now raises an inventory error; strict storage and peer decoding
  reject malformed account mappings instead of confirming an empty map.
- Routing is read once per discovery call, never cached across calls. A
  regression adds an alias between two refreshes and observes its newly
  configured account with exactly one bulk read per refresh.
- A per-snapshot index feeds the existing canonical account helpers. Regression
  cases preserve first-match registry precedence, exact alias casing and
  owner policy, while 200/400/800-repository cases require at most two slug
  parses per repository rather than quadratic scans.
- Focused contained suites passed: CodeSpaces 137 and Worktrees 157. Lint and
  the changed-source module-size gate passed without raising ceilings.
- Rebuilt only the contained Worktrees developer runtime and exercised the
  actual Bridge scan/default eight-second deadline in five fresh consumer
  processes: 7.844, 6.313, 6.235, 5.672 and 5.906 seconds. Each found the
  target and then served five warm reads. Native and sampling holders settled.
- A malformed stored account map produced an incomplete namespace in 7.078
  seconds, not a complete empty roster; restoring the exact fixture bytes
  restored complete discovery in 5.875 seconds. No shared runtime changed.
- The refreshed unit-source admission passed required Linux source CI before
  these routing corrections. A subsequent review and required CI on the new
  head remain necessary. Focused Linux threaded/I/O qualification and rollout
  are still explicit open gates.

### 2026-10-22 - Code review and unit-admission reconciliation

- #7998 opened for implementation review. The publication rebase conflicted
  only in the effort index; all peer effort rows were retained. The activation
  compatibility and bounded-overlap suite passed all 88 cases after rebasing.
- Upstream enrolled the activation import closure in the unit-only packet while
  implementation was in progress. The first source CI correctly refused the
  changed `_memo.py` and `resolver.py` bytes. Their exact hashes were refreshed
  after reviewing the existing adapter and original preservation tests: the
  default path remains serial, and no thread-enabled test or observer exemption
  was admitted. All other source hashes and packet nodes remain unchanged.
- A local Windows packet attempt reached 839 passes and one platform skip
  before the unchanged preservation reader attempted `ctypes.dlopen`; the
  effect observer correctly refused it. This is not a passing packet receipt
  and no native-loader exception was added. Required Linux source CI remains
  the packet acceptance gate.
- The focused Linux threaded/I/O lane remains unexecuted: this Windows host
  has neither Docker nor the devcontainer CLI required by the existing isolated
  runner. Installing another venue or weakening the runner is not implied.

### 2026-10-22 - Windows source qualification

- Final five real payload-command cold processes completed in 5.8888, 5.8586,
  6.3526, 5.6877 and 5.6699 seconds under an actual eight-second process bound.
  Each returned the known target. Earlier failed attempts remain failures,
  not qualification.
- A fresh-process, state-reset control removed prior account bindings and had
  no roster cache: declared sources alone discovered the target in 7.424 seconds.
  The isolated runtimes were preprovisioned through their canonical developer
  installers; this is not a virgin-machine or unprovisioned-install latency claim.
- The actual Bridge CLI resolver/CPU observer completed a native scan in
  6.0 seconds, then five warm cache reads. A refused strict account snapshot
  remained an incomplete namespace while an unrelated static agent survived;
  restoring the fixture restored complete discovery. Native and sampling
  holders settled.
- Focused source runs passed: activation 88; Worktrees account compatibility
  152; Bridge timeout/cancellation/native ownership 58; CodeSpaces/config
  coverage 276 with seven repository skips, followed by 83 focused cases after
  final casing/precedence corrections.
- Windows is qualified locally; a focused Linux threaded/I/O lane has not run
  from this Windows owner. Ordinary Ubuntu source CI uses the unit-only packet
  and is not a substitute for that lane. Its validation item remains open.
- No shared installed runtime or service changed. Code publication, required CI,
  qualified release and authorized current-host readiness remain pending.

### 2026-10-22 - First profiling and overlap candidate

- Profiling the released provider measured about 10.3 seconds inside Python,
  including about 6.9 seconds of serialized account-source discovery and
  2.5 seconds of serialized account listings. The real payload launcher adds
  roughly two seconds before Python.
- A source candidate overlaps three independent metadata reads and at most four
  native account listings, preserving deterministic final merge precedence.
  The contained focused run passed 135 tests with one existing skip.
- The latest real-backend candidate process still takes about six seconds
  before launcher overhead. This is improvement, not proof of the eight-second
  complete-discovery requirement. No shared runtime was updated and the
  foreground deadline remains unchanged.
- Qualification is still open. Do not publish this candidate as a completed
  repair or treat a sum of separately measured timings as an end-to-end pass.

### 2026-10-22 - Retained deadline and additional startup headroom

- The operator explicitly retained complete discovery within eight seconds.
  A five-process native check passed its first four runs but timed out on the
  fifth; the candidate is not qualified.
- Remaining profiling identified serialized activation-root metadata reads.
  An in-memory bounded-overlap experiment reduced activation resolution from
  roughly three seconds to roughly 1.6 seconds while retaining the roster.
- The source repair therefore adds an opt-in, bounded local-root overlap mode
  to `plugin-activation`, with synchronized per-call marketplace reads and
  deterministic result merging. CodeSpaces opts in; other consumers remain
  serial by default. All vendored consumers must receive changefiles.
- No deadline, native policy, shared runtime or daemon has been changed.

### 2026-10-22 - Reviewed plan

- #7930 merged after Copilot approval with no findings and successful required
  CI. The implementation worktree was pulled forward onto the reviewed plan.
- Phase 1 is complete. Phase 2 starts with profiling the registered command
  boundary; no timeout, runtime or installed-payload change has been made.

### 2026-10-22 - Kickoff

- Claimed the bounded latency slice on #5761 as `rattling-circuit`.
- Recorded the direct-command/deadline discrepancy and preserved the broader
  diagnostic acceptance and existing reader-lifetime ownership.
- Authored the proposal before implementation; no runtime or guidance change
  is part of this planning delta.
