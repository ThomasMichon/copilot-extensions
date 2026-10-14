# Process Spawn Reduction

- **Slug:** `process-spawn-reduction`
- **Repo:** copilot-extensions
- **Branch(es):** independent serial slice PRs targeting `dev`
- **Created:** 2026-10-10
- **Status:** Active
- **Vision:** `visions/plugin-services/README.md` - service-scaled process count, transient hooks, discovered local endpoints
- **Umbrella issue:** #6086
- **Sub-issues:** #6087, #4644
- **Related, independently owned:** #6040, #6077, #6079, #6088, #6092

## Guiding Intent

Remove avoidable process creation from ordinary hooks and recurring local
daemon work, starting with measured worst offenders. A cheaper helper process
is not process consolidation. Reach an existing daemon in the already-running
host whenever possible; preserve standalone operation, installation identity,
auth, safety decisions, lifecycle ownership and explicit error semantics.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Spawn-reduction driver | Coordination, hook fast-path slice, serial PR ownership | #6086 and this effort |
| Mux repair owner | Existing #6040 release/deployment and desktop proof | #6040 / merged #6055 |
| Provenance validation owners | Existing fetch/cache correction | #6077 / #6079 |
| Related campaign owners | Lifecycle, daemon authority and test portfolio | #5579 / #3761 / #1303 |

## Coordination

- **Topology:** independent, serial reviewable slices; one writer per slice.
- **Host (owns PRs):** Spawn-reduction driver for this effort's new slices.
- **Delegates:** no new implementation delegates initially. Existing owners
  retain their work; coordinate before touching overlapping files.
- **Handoff:** resume the first unresolved Plan/Validation Plan item from this
  canonical effort, retaining open PR and release/deployment obligations.
- Do not compete with the mux repair, immutable Git checker work, authoritative
  daemon migration or wait-only launcher campaign.

## Context

#6086 records 5,643 selected process starts over 180 seconds in a mixed live
workload, including 3,081 Git and 1,611 console-host starts. This is neither a
test-only count nor a census of independent daemons. Measured roots include
the resident status-monitor, list/classification, bridge discovery, hooks and
contract validation. Separate windows were attributed to recurring mux calls
and collection-time WSL probes.

Build on `lean-session-lifecycle`, `agent-worktrees-authoritative-daemon`,
`mux-child-window-suppression` and `test-portfolio-rationalization`, not
drifting copies of their plans. Existing #2619/#918/#5579/#2422 describe related
consolidation; #5900 describes optional provenance through existing telemetry.

This closes existing service-model intent. Follow local-endpoint-discovery,
Windows background-process launch, installation-cell provenance and
work-coalescing-singleton patterns. No new daemon, mandatory broker or generic
native helper executable is planned.

## Request

The operator confirmed the slug `process-spawn-reduction`.

> Right. Start driving this effort, prioritizing the worst-offenders for process-spawns first. For example, hooks allow `powershell` and `bash` hooks; we could inline necssary PS1 calls to direct-invoke existing daemons if they are present, falling back to calling our CLI. We'll also want to look at the mux daemon more, as well as agent-worktrees' daemon. Worktree Manager's daemon might also be worth a look.

Earlier direction, summarized: ordinary unit tests must stay in-process and
mock process calls/output rather than probing host WSL; prioritize reducing
spawn churn even on powerful hosts. The sessions-times-enabled-plugins idea is
a scaling aspiration, not a fixed spawn-rate budget with an approved interval.

Startup follow-up, summarized from the operator's subsequent sequence review:
prioritize create/cold-start preparation, keep plugin enablement/dependency
planning local, and report progress rather than impose broad startup timeouts.
The configured anchor/default-branch behavior and current plugin-update scope
are accepted. Update-versus-allocation ordering raises a separate failure and
recovery concern; it does not authorize rolling back shared updates.

Operator's projection and mux decisions, verbatim:

> Projection ordering: bad. Definitiely should only happen when both the harness and knowledge worktrees are created, and the complete plugin-hoist is computed. We know the set of plugins that need projection ahead of time, but the actual copying should occur all at once
>
> Machine preferences: that's fine
>
> Mux registration: makes sense. Good that daemon is async, so we can tear it down and restart it independently without breaking the mux pane or copilot. We want as few important pieces between the Mux pane and Copilot, other than to launch the mux pane, so that when we need to update, nothing in that gap needs restarting unless we want to create a new Copilot process.

The operator clarified the publication contract, verbatim:

> One final batch, and failure should be highly unlikely, because it's just file copies into empty space.

This means one final projection pass, not a new rollback-capable filesystem
transaction. Current machine-preference behavior and asynchronous mux-daemon
registration remain unchanged.

## Plan

### Phase 1 - Reviewed scope and baseline

- [x] Record measured root attribution and link existing issue/campaign owners.
- [x] Land this reviewed plan before implementation.
- [ ] Establish isolated representative hook and daemon workloads; record
  spawn counts, elapsed time and scope without raw adopter identifiers.

### Phase 2 - Hook daemon fast path

- [x] Trace current request, installation selection, auth, output and fallback
  contract in the existing hook client and daemon before changing transport.
- [x] Implement the first supported PowerShell hook fast path inside the
  already-created shell, reading the attributable rendezvous and directly
  requesting the existing daemon with no Python/CLI child on success.
- [x] Retain the existing attributed CLI/client fallback only for explicitly
  eligible unavailability. Preserve safety decisions and ambiguous-outcome
  behavior; no provisioning or new service on the hot path.
- [x] Keep session-start drift reconciliation and its single-flight provisioning
  owner unchanged and separate from the ordinary hook fast path.
- [ ] Evaluate Bash's available in-process primitives separately. Do not hide
  a curl/Python/jq child in a claimed zero-child path or sacrifice bounded
  transport/JSON validation to match the PowerShell implementation.
- [x] Land code, focused tests, docs and required changefiles for the first
  legacy-context PowerShell post-hook slice (#6122). Selected installation-cell
  fast paths and Bash evaluation remain open.
- [ ] Extend or explicitly transfer selected installation-cell fast paths
  after proving the cell-owned request/fallback contract.

### Phase 3 - Recurring daemon churn

- [ ] Rank recurring Git/config/discovery/mux child calls by attributable counts
  in agent-worktrees and Worktree Manager/mux daemons.
- [ ] Coordinate overlapping authoritative-daemon/lifecycle slices, then remove
  repeated immutable lookups, share current snapshots and coalesce equivalent
  reads with explicit invalidation and bounded stale-state semantics.
- [ ] Preserve real Git correctness, installation separation, startup recovery
  and lifecycle ownership; do not call a cache authoritative by convenience.
- [ ] Land bounded slices independently, each with before/after counts.
- [x] _(Agent-recommended bounded first slice)_ Batch per-pass fallback object
  comparisons before adding cross-cycle immutable-fact reuse; preserve fresh
  mutable Git observations and prove the one-/many-path process budgets.
- [x] Share fresh enrichment across compatible resident list-warm shapes within
  one pass, preserving exact-selection/output separation and next-pass reads.

### Phase 4 - Unit boundaries and release proof

- [ ] Coordinate #6087 with the test-portfolio owner: zero real Bash/WSL during
  ordinary collection; in-process policy mocks; native/integration opt-in.
- [ ] Define workload-specific cumulative-spawn budgets from measured results,
  separately from peak live process limits. _(agent-recommended)_
- [ ] Verify promoted releases and supported deployment, then repeat live
  hook/daemon and desktop observations across two periodic cycles.
- [ ] Resolve or explicitly transfer every Plan and Validation Plan item to a
  named tracked objective; journal completion and archive normally.

### Phase 5 - One final startup projection batch

- [ ] Prioritize this bounded startup slice before further classifier tuning.
  Coordinate with #6600's existing launch timing/rendering owner; do not
  duplicate its latency work or the separate catalog-rendering proposals.
- [ ] Publish local projections only after both applicable worktrees exist
  and the complete combined plugin surface and local overrides are ready.
  Preserve direct creation and direct launch-plan consumers as well as both
  shell launchers; avoid early or repeated projection passes.
- [ ] Keep maintenance separate from offline planning and final projection.
  Record allocation/retry behavior when maintenance or later preparation fails;
  do not invent rollback of shared plugin/runtime updates.
- [ ] Retain progress and elapsed-time reporting instead of introducing broad
  startup ceilings. Preserve machine preferences, async mux registration and
  the minimal pane-to-Copilot process/lifetime boundary.
- [ ] Review, merge and verify the bounded ordering change through the normal
  release flow; keep the parent campaign Active for its remaining work.

## Validation Plan

- [x] First hook slice proves zero client/CLI child processes on daemon success,
  not merely correct creation flags or a cheaper interpreter.
- [ ] In-process unit tests cover request/output equivalence, bounded responses,
  stale/malformed rendezvous, wrong installation, unsupported protocol,
  unavailable service, denial, timeout and fallback eligibility.
- [ ] Requests stay local and authenticated; proxy settings, redirects or stale
  port reuse cannot disclose credentials or reach another installation.
- [ ] Existing hook input/output and safety decisions remain compatible;
  response errors cannot become success-shaped defaults.
- [ ] No fallback repeats a mutation after an ambiguous delivery/result.
- [ ] A small explicit integration lane exercises the actual shell/daemon
  consumer contract, including daemon-down fallback and Windows stdio.
- [ ] Ordinary unit selection and collection spawn zero real external processes,
  including Bash/WSL, Git, PowerShell, cmd and Python children.
- [ ] Daemon comparisons report cumulative starts, peak live processes,
  workload/session/plugin context and honest missing observations.
- [ ] Snapshot/cache tests prove invalidation, concurrency and cross-cell
  separation; quiet periodic work no longer repeats unchanged discovery.
- [ ] Real Windows integration proves no attributable visible frames/focus
  transitions; deliberate interactive launch semantics are unchanged.
- [ ] Release/deploy behavior is verified after caller cleanup, not inferred
  from merged PRs or updated payload versions.
- [ ] Startup order tests prove no projection before all applicable allocations
  and final combined settings, and exactly one final batch for each cold launch.
  No-pair launches require only their own allocation, not a knowledge sibling.
- [ ] A plugin enabled only by the paired knowledge worktree appears in the
  actual resulting projection, not just in a mocked call-order assertion.
- [ ] Direct create/resolve, cold Resume, no-pair/recovery and Windows/POSIX
  launcher contracts retain delivery; reattach does not create a new Copilot
  process or a mandatory daemon bridge.
- [ ] Failure-path checks retain recoverable worktree identities, surface
  preparation errors and preserve existing update/ownership safeguards.

## Proposal

Reduce boundaries rather than optimize wrappers. The shell-only hook host
still creates a shell; this effort removes its unnecessary descendants first.
Persistent extension callbacks should eventually request their existing daemon
directly in-process where the host supports that surface. Actual protocol
selection, authentication and fallback changes require the owning contract;
an imagined HTTP endpoint is not a substitute for the current monitor's
authenticated framed transport.

_(Agent-recommended sequencing)_ Begin with one PowerShell read/advisory hook
if that is the smallest proven compatibility surface. Extend safety hooks only
after equivalence and error semantics are established. Keep native integration
small and explicit while unit policy tests remain in-process.

_(Agent-recommended startup mechanics)_ Let an outer launcher defer projection
publication until its maintenance and knowledge/settings preflights finish;
direct callers still prepare their final surface before returning a launch
plan. Reuse existing progress/reporting and rendering APIs, not a new resident
or pane-side helper. Do not add cross-file transaction machinery for this
single-final-batch contract.

## Journal

### 2026-10-13 - Real listing workload and request-budget boundary

- #6498 merged after exact-head Copilot approval with zero open findings and
  successful required CI. #6459 also merged. Normal generated `main` still
  excludes both source slices; neither the collection claim nor the parent
  campaign is complete. No deployment was inferred from those merges.
- Measured source `34301ec39c4c1983c3797576b47a34643b65bd72` through a contained
  native listing fixture: real YAML records/config, production session scan
  and renderer, four non-mux-detail shapes, warm loop, classify IPC and Git.
  Classifier fallback was explicitly refused; clocks and request budgets were
  not changed. Fixture setup and Git commits are outside the pass counts.
- One divergent record used 9 Git starts and one read-only `psmux` start in
  each of two unchanged passes (1.068/1.102 seconds). Four records used 33 Git
  starts and one `psmux` start (3.565/3.669 seconds). After a dirty edit, the
  next pass rendered Dirty and used 6/30 Git starts for one/four records,
  respectively. These are direct Popen-boundary counts, not sampled estimates.
- The earlier eight-Git fixture modeled its config/render/session surfaces.
  This real consumer includes one committed-config `symbolic-ref` probe per
  pass in addition to eight Git starts per clean divergent record. It also
  includes the production active-path `list-sessions` query even without
  `--mux-details`: that is a real read of the host mux, not an isolated mux
  fixture or a modeled zero-child result. No host mux/session mutation ran.
- At eight divergent records, the first request did not complete without the
  ordinary classifier fallback. The strict diagnostic failed after 6.291
  seconds, with 65 Git starts and one `psmux` start observed by that boundary.
  This is excluded from successful native-consumer proof. It identifies a
  workload-size/request-budget constraint for the next coalescing slice, not
  authority to enlarge deadlines or discard correctness fallback.
- Measurements exclude the full monitor lifecycle/sweep, installed-runtime
  execution, peak descendants and stationary host-wide improvement. No
  broad workload or lifecycle checklist item is marked complete from them.
- The normal promotion gates changed while this work resumed: index and
  full plugin validation passed in run 38502638232, but publication then
  refused `manifest payload hash differs from candidate`, already tracked
  in #6535. No forced release, integrity bypass, competing repair PR or
  workflow retry was attempted.

### 2026-10-13 - Explicit native shell admission

- Claimed #6087 after checking for an existing owner and competing PR.
  Three test modules probed real Bash during collection, including unrelated
  `-k` selections. Moved their native behavior tests behind explicit T3
  session fixtures, retaining the real installer and background-prune bodies.
- Shared test-only admission rejects WindowsApps/System32 WSL launchers before
  a probe, prefers native Git Bash on Windows, and bounds headless exit-code,
  pipefail and cygpath probes. Direct pytest fails closed without explicit
  tier admission. PowerShell command-resolution execution is explicit too;
  the pipefail source drift guard stays in the ordinary selection.
- Fresh-import and admission policy tests mock all subprocess boundaries.
  The contained default selection passed 27 tests and skipped 11 native cases;
  the explicit lane passed all 38, including real Git Bash and PowerShell
  consumers, at unchanged runner limits.
- Temporary subprocess instrumentation rejected every real process attempt
  during full collection: 8,890 tests collected, zero attempted children.
  A separate unrelated one-test selection also collected the complete suite
  with zero attempted children. Instrumentation was removed afterward.
- These are fresh temporary consumer fixtures, not a first-install change.
  No external venue is required for this test-only Windows/native-shell
  boundary; no production installer or runtime behavior changed. Review,
  merge and normal promotion remain outstanding. The broader campaign stays
  Active and this slice does not settle its workload/lifecycle obligations.
- Review found that an earlier WSL launcher on PATH could hide a portable
  Git Bash when the usual installation path is absent. Filtered known launcher
  directories before fallback lookup and retained post-resolution refusal.
  Added the ordered-PATH policy regression; all 39 focused tests, including
  the retained native consumers, passed at unchanged limits.

### 2026-10-13 - Released list-warm coalescing and real-clock validation

- #6431 passed its current-head review and CI gates, merged, and was included
  in promotion #6453. The qualifying agent-worktrees release is 1.24.57-dev1,
  not the earlier 1.24.56-dev1 snapshot that excluded this slice.
- A normal Copilot payload refresh with debug logging succeeded within the
  unchanged 120-second limit. The standard unified updater then activated
  the runtime. Subsequent normal deployment advanced the current payload,
  runtime marker and attributable resident to 1.24.58-dev1. The resident
  answered an actual authenticated hook request after updater cleanup in
  0.24 seconds within the normal 0.5-second request budget.
- Installed-consumer proofs for both releases imported their actual installed
  runtime modules and executed the real warm loop, classify IPC and Git.
  Four fixture demands retained eight direct Git starts in each of two
  passes and observed a subsequent dirty edit. Transcript, mux and reclaim
  enrichment remained modeled; this is not a real-host four-shape workload
  or a stationary host-wide spawn-rate comparison.
- Found that the original cache-age fixture mutated shared stdlib time.time,
  also freezing transport deadlines. #6459 isolates that clock and retains
  the unmocked clock APIs. The corrected source selection passed 16 tests
  including native IPC, without increasing deadlines; the installed proofs
  also used the isolated fixture. Earlier direct counts remain counts, not
  unfrozen request-budget evidence. A separate failed paired benchmark is
  excluded from successful timing claims.
- The unified updater exited nonzero because the configured governed feed
  omitted FastAPI 0.143.0, required by the newer Dispatch runtime. That release
  is available and not yanked on public PyPI. No feed override, downgrade or
  shared-cache purge was attempted. Earlier Copilot payload timeouts remain
  unclassified; a later successful refresh is not a root-cause explanation.
- The parent remains Active. Real representative daemon workloads,
  immutable/event-driven cross-cycle reuse, unit collection boundaries,
  selected-cell/Bash work and independent lifecycle observations remain
  unresolved; this bounded slice does not satisfy those obligations.

### 2026-10-12 - Deployment proof and selection-scoped list warming

- Promotion and the authorized normal global updater completed for #6271.
  Fresh installed classifier observations retain eight Git starts for both
  one-path and twenty-path divergent work across two cycles. The actual legacy
  PowerShell post-hook also completed a two-call resident-success round with
  only its shell and console infrastructure, without Python/CLI descendants.
  Additional requests still demonstrated intermittent explicit fallback and
  timeout; this is not a claim of universal hook availability.
- Two identity-attributed 45-second resident observations sampled cumulative
  Git/console-host starts and peak live descendants, with no attributable
  visible or foreground frames observed. The workload was mixed and sampling
  misses short-lived processes, so those results do not establish a stationary
  host-wide reduction. The parent remains Active.
- The next bounded source slice shares fresh enrichment only within one
  resident list-warm pass and exact tracking-status/platform/existence
  selection. Four compatible demanded shapes previously repeated three
  classifications, four session/record scans and two mux/reclaim observations.
  The candidate performs each once while preserving individual output fields.
- Actual before/after warm-loop measurements against the retained source
  baseline used the same real-Git divergent fixture: twenty-four versus eight
  direct Git starts in each of two passes, through the real classify transport.
  Transcript, mux and reclaim enrichment was modeled, not silently counted as
  native. A concurrent measurement that exceeded the unchanged request budget
  was excluded; its diagnostic fixture cleanup was made handler-lifetime-safe
  before the serial measurement passed. The checked-in native lane also
  observes a dirty edit on the next pass; in-process tests retain different
  selection/project boundaries and unchanged standalone reads.
- Broader event-driven polling #2619, authoritative lifecycle #3761, and
  consolidated monitor #918 remain their existing contracts. This slice does
  not alter polling cadence, extend freshness leases, activate installations
  or bypass the classify provider. Review, promotion, authorized deployment
  and post-caller live proof of this new slice are still required.

### 2026-10-12 - Per-pass tree comparison slice

- Transitioned the canonical driver from Phase 2 to Phase 3 recurring work;
  selected-cell/Bash and release/deployment obligations remain open.
- The three prior slices landed serially: captured-child ownership #6231,
  legacy PowerShell hook fast path #6122, and immutable numbered runtime
  admission/readiness quarantine #6160. Release #6267 promoted a pinned
  source containing all three merge commits; the relevant runtime, hook,
  installer and process-helper sources match that release tree. No runtime
  update is authorized by this continuation; release is not running-state
  proof.
- Isolated real Git measurements before batching: one changed path used
  9 starts per cycle (0.881s, 0.923s); twenty paths, with the last still
  unmerged, used 47 (4.248s, 4.510s). After batching the same workloads used
  8 per cycle: one path 0.860s/0.676s, twenty 0.694s/0.664s. All four
  classifications retained WIP with one commit ahead and behind; setup is
  excluded. This is a direct classifier launch count, not a live daemon or
  host-wide descendant/window census.
- Replaced per-path blob children with raw, NUL-delimited object comparisons.
  No longer-TTL or immutable cross-cycle cache is introduced; that subsequent
  contract still needs ref/config/replacement/concurrency invalidation proof.
  Existing catalog, lifecycle, write-admission, provenance and test-portfolio
  owners retain their separately coordinated work.
- Review identified that the initial second raw diff could enumerate
  unrelated upstream files before filtering. Replaced it with exact,
  nonrecursive `ls-tree` lookups, conservative argv-size batching and separate
  ancestor/descendant requests. The final same-shape measurements retain
  8 starts per cycle: one path 0.939s/0.867s, twenty 0.892s/0.776s. An
  additional native fixture has 500 unrelated upstream paths and proves
  those paths are absent from the lookup output. Large path sets may need
  additional bounded batches; eight is the specified fixture budget, not
  an unconditional promise for every repository.

### 2026-10-11 - Captured child safety

- Resumed the reviewed campaign, retaining the separate hook fast-path and
  immutable-runtime admission obligations.
- Traced local-guidance identity discovery to the same detached captured-child
  seam as bounded Git publication. A console-subsystem re-exec could allocate
  a terminal; PID-based timeout sweeps also carried the tracked #4644 race.
- Reused the existing suspended-start Job primitive with required Windows
  ownership and captured-child headlessness. POSIX process-group behavior is
  unchanged; no daemon lifetime escape or installed-runtime mutation.
- Focused contained policy, publication and guidance tests passed, including
  two native consoleless-parent cycles with zero new visible windows and zero
  foreground transitions, captured output/exit status, and grandchild timeout
  cleanup. Promotion and authorized running-state rollout remain outstanding.

### 2026-10-10 - Canonical campaign claimed

- Operator approved the slug and instructed execution by measured offenders.
- Target adoption was verified; #6086 claimed through scoped identity.
- Existing mux/provenance owners retained; no duplicate implementation created.
- Plan drafted for review. No hook/daemon source changes made before review.
- Plan landed as #6112. The first slice is the legacy-context PowerShell
  post-tool advisory request; selected installation cells retain the existing
  validated client. Session-start reconciliation is untouched. Review
  clarification explicitly preserves its single-flight owner and broadens
  unit collection's zero-child criterion beyond Bash/WSL.
