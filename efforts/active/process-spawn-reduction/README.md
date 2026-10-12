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

### Phase 4 - Unit boundaries and release proof

- [ ] Coordinate #6087 with the test-portfolio owner: zero real Bash/WSL during
  ordinary collection; in-process policy mocks; native/integration opt-in.
- [ ] Define workload-specific cumulative-spawn budgets from measured results,
  separately from peak live process limits. _(agent-recommended)_
- [ ] Verify promoted releases and supported deployment, then repeat live
  hook/daemon and desktop observations across two periodic cycles.
- [ ] Resolve or explicitly transfer every Plan and Validation Plan item to a
  named tracked objective; journal completion and archive normally.

## Validation Plan

- [ ] First hook slice proves zero client/CLI child processes on daemon success,
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

## Journal

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
