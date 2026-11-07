# Mux Reliability and Recovery

- **Slug:** `mux-reliability-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed plan and implementation PRs
- **Created:** 2026-11-06
- **Status:** Active
- **Vision:** [Session Hosting](../../../visions/session-hosting/README.md), [Worktree Lifetime](../../../visions/plugins/agent-worktrees/README.md), [Picker](../../../visions/picker/README.md)
- **Umbrella issue:** #11255
- **Related owners:** #7212, #4564, #10627, #8669

## Guiding Intent

Preserve durable work and running mux-backed sessions when discovery is
unavailable. Observation failures must remain visible uncertainty rather than
turning into a false death verdict, a misleading plain-process badge, or
authority to replace or retire a session.

This closes existing vision intent: provider-owned execution identity and
capability-honest observation, durable agency independent of presentation, and
explicit uncertainty. It does not create another hosting provider, process
registry, maintenance daemon or deployment authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Reviewed plan, implementation, focused validation and PR stewardship | Managed source worktree; public issue #11255 |
| Automated reviewer | Independent plan and exact-source review | Repository PR review |

## Coordination

- **Topology:** sequential plan and bounded implementation PRs.
- **Host (owns PRs):** Source driver.
- **Delegates:** none assigned.
- **Handoff:** retain the canonical README, current slice, tests, PR state and
  unresolved effects; never infer operational authority from this plan.
- Existing attachment isolation/native-process liveness (#7212), attached-client
  accounting (#4564), launch readiness (#10627), and installation-independent
  hosting (#8669) remain with their current owners. This campaign owns the
  discovery-failure preservation seam, not those broader programs.

## Context

Public upstream reports establish concrete failure classes:

- [psmux/psmux#622](https://github.com/psmux/psmux/issues/622): a failed
  `has-session` can delete a live server's port registration, after which
  another invocation can reap its server and children. The report's later
  controlled comparison verifies the destructive sequence on 3.3.8 and
  preservation on 3.4.0.
- [psmux/psmux#546](https://github.com/psmux/psmux/issues/546): a timestamp
  heuristic can unregister a live server before its PID anchor is checked.
- [psmux/psmux#650](https://github.com/psmux/psmux/issues/650): cross-logon
  access failures can be mistaken for process death and delete registration.
- [psmux/psmux#809](https://github.com/psmux/psmux/issues/809): an incomplete
  listing can look successful even on 3.4.0. A newer release alone is not
  proof of every observation guarantee.

These are reproducible public failure classes, not proof of any particular
incident's root cause. A surviving process or existing connection does not
establish that a new attachment can connect. A newer on-disk executable does
not upgrade already-running servers.

The implementation must respect the existing owner split: the hosting/Driver
boundary owns provider-specific execution and control; Manager presents and
composes it; Worktrees retains durable agency and bounded observations.
Existing compatibility adapters must not become a second hosting owner.
The [architecture principles](../../../docs/patterns/README.md) require
non-destructive observation, literal failures, economical evidence and
preserved ownership. Additive interfaces follow
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md).

## Request

The operator asked:

> Great. Help make an effort to add improvements and mitigations, and then start implementations

This follows the question:

> How can we mitigate or recover?

The phased source implementation and validation below are
**agent-recommended decomposition**, not an assertion that the operator
approved changes to running services, installations or sessions.

## Scope Approvals

- **Source:** the operator's verbatim request above, 2026-11-06.
- **Authorized interpretation:** create the reviewed effort and start
  task-relevant source improvements, tests, documentation and normal issue/PR
  delivery in this repository.
- **Excluded effects:** no live PSMux or harness upgrade, service/polling
  changes, process termination, terminal takeover, registry reconstruction,
  shared-host destructive reproduction, access changes or credential export.
- Review of this plan does not grant those effects. A later operational phase
  must obtain its own bounded authorization and preserve then-current owners.
- An unknown prior effect must be reconciled, not replayed. Routine in-scope
  code revisions and validation do not require a new blanket approval.

## Plan

### Phase 1 - Reviewed intent and owner map

- [x] Land this proposal through the repository's review and CI gate before
  implementation.
- [x] Trace the existing mux observation, classification and recovery consumers;
  select the smallest owner-correct change and retain related issue ownership.

### Phase 2 - Non-destructive observation

- [x] Remove hazardous PSMux CLI execution from the selected passive observation
  path. Reuse an existing bounded non-mutating host-owned read when available;
  otherwise expose the unavailable capability rather than probing by execution.
- [x] Distinguish complete confirmed absence from unavailable, partial,
  malformed, access-denied and timed-out evidence. Keep last-known hosting
  attributable and explicitly stale/uncertain; never promote it to live proof.
- [x] Preserve independent TMux behavior and supported legacy readers. No
  per-render subprocess, broad scan, process registry or parallel control path.

### Phase 3 - Truthful presentation and preservation

- [x] Carry degraded mux observation to the existing Picker consumer instead of
  presenting it as confidently non-mux `PROC`. Client count, hosting identity,
  process liveness and attachment reachability remain distinct.
- [x] Ensure uncertainty cannot authorize automatic mapping retirement, cleanup,
  duplicate launch, restart or attachment takeover in the changed consumers.
  Keep pre-existing ownership and destructive-action gates intact.

### Phase 4 - Recovery guidance and source delivery

- [x] Document preservation-first recovery: retain working attachments and
  durable work, distinguish detach from death, establish installed/running build
  evidence without unsafe version probes, and perform individually authorized
  recovery rather than global cleanup or guessed metadata repair.
- [ ] Land implementation through exact-source review and required CI; record
  source/release/rollout separately. No rollout is required or claimed here.
- [ ] Resolve every Plan/Validation item or transfer it to a named tracked
  objective, then mark Done and archive through the normal effort lifecycle.

## Validation Plan

- [x] Focused units prove unavailable/partial observations cannot become
  confirmed absence, stale positives stay uncertain, and ordinary live/dead
  and non-mux process cases retain their intended meaning.
- [x] A fake-provider or isolated real consumer boundary proves passive PSMux
  observation does not execute startup cleanup and preserves unrelated session
  state. Never run upstream destructive repro scripts against live sessions.
- [x] Focused mixed-version tests cover optional/additive evidence, legacy
  absence and rollback without silently weakening a required safety semantic.
- [x] Changed lifecycle consumers retain original ownership and reject
  uncertainty-driven retirement/replacement; a live replacement is not cleared.
- [x] Changed Picker rows distinguish reachable mux, detached mux, uncertain
  mux hosting and genuine process-only hosting without render-time probes.
- [ ] Run required source lint/type/install-contract gates and exact-source CI.
  Record native/clean-room lanes exercised and explicitly explain unavailable
  lanes; unit success is not a claim of live-host recovery.
- [ ] Re-read the canonical effort after each merge, update only the gates it
  actually closes, and verify final issue/effort disposition.

## Proposal

Implement a narrow preservation-first observation contract through the
existing owners, followed by its presentation and recovery documentation.
Prefer extending an existing result with optional bounded diagnostic evidence
over adding a mandatory preflight command, new daemon or duplicated registry.
Determine the exact adapter after source tracing; validate the real consumer,
not only its parser. Deployment and repairs to running sessions are separate.

## Journal

### 2026-11-06 - Proposal

- Claimed #11255 after searching current mux/discovery issues and open PRs.
- Verified public PSMux reports and preserved the distinction between known
  failure classes and unproven incident attribution.
- Allocated an isolated source worktree without launching a mux or new agent.
- Captured and re-read the Request, Guiding Intent and Plan against the
  operator's words; agent recommendations and operational exclusions are explicit.
- Implementation awaits the reviewed-plan gate.

### 2026-11-06 - Plan accepted; implementation opened

- Plan #11258 received approval at its published head, passed required CI and
  merged. The source worktree was pulled forward before implementation.
- Traced Worktrees' batched session observations, Manager's native discovery
  and client-count refresh, mapping retirement, and Picker classification.
  Several passive paths execute PSMux, and a successful incomplete listing can
  currently become a negative observation.
- The first implementation must preserve the Driver/Manager/Worktrees split.
  Do not add a second raw mux control implementation to the durable agency
  owner or execute a hazardous version query to discover support.

### 2026-11-06 - Preservation-first implementation

- Selected explicit unavailability for passive PSMux CLI observation: the
  current owners have no qualified non-mutating reader to reuse. Worktrees
  still publishes independent facts; Manager retains mappings and stored counts
  without refreshing them into live evidence. No second provider or registry
  was introduced.
- Strict listing validation rejects incomplete evidence. Legacy status
  validation and reclaim reachability retain uncertainty instead of retiring
  mappings or selecting a mux-homed process for teardown.
- Picker distinguishes last-known mux hosting, unknown hosting and independent
  process/ACP evidence without render-time probes. Existing current-roster
  versions retain their observation schema; optional `mux_last_known` is a
  separate bounded row object. Legacy reconcile also labels preserved counts.
- Explicit action-time presence checks remain separate from passive observation.
  Their existing query path is not qualified as harmless; a failed PSMux
  presence query cannot establish absence or admit a cold seed.
- Focused validation exposed an obsolete generation-identity fixture and a
  reply-versus-handler-settlement race in monitor tests. The fixtures now use
  the real identity format and synchronize completion without relaxing the
  asserted behavior or extending the request deadline.
- Source validation and exact-source review remain in progress. Native PSMux
  execution, live rollout and operational recovery have not been performed.

### 2026-11-06 - Focused validation

- Worktrees' six-file packet passed 408 tests; the POSIX real-TMux control was
  skipped on Windows. Manager's five-file packet passed 239 tests with three
  native opt-in skips; its only remaining failure was a supported-query fixture
  still selecting PSMux. After correcting that fixture, the complete discovery
  and Picker packet passed 81 tests, including normalized attachment refusal.
- Real registry/status consumers and both current-roster versions were
  exercised with synthetic provider boundaries. The monitor test proves an
  unavailable PSMux catalog cannot prune registration or stop independent
  publication. Reclaim tests prove unknown reachability cannot select teardown.
- A daemon-publication diagnostic correctly exposed the shared-host guard
  rejecting a test fixture's unscoped TMux query. The fixture now names a
  synthetic provider; no real shared mux query was allowed or run.
- Native PSMux/remote-host recovery was not exercised: it would touch live
  resources outside this source-only authorization. Provisioning and installer
  behavior did not change, so no clean-room installation lane was required.
- Source lint and the installation contract passed. Module-size validation
  required keeping legacy presence classification in the existing Manager
  discovery owner; no new provider, dependency or raised ceiling was added.
  Exact-source CI and review remain the delivery gate.

### 2026-11-06 - Required unit admission correction

- Required CI correctly rejected the changed mux parser's old source binding
  before running its sealed unit packet. Updated only that binding and the
  existing actor-free regression module's binding; no node, coverage target,
  effect restriction or budget was removed.
- The admitted presence control now requires unknown PSMux failure rather than
  silent negative evidence, and its strict-parser control covers malformed
  monitor catalogs. The selected actor-free packet passed 22 tests locally.
  The full sealed packet still requires exact-source CI qualification.

### 2026-11-07 - Review corrections and consumer validation

- Required CI passed at the previously published implementation head, including
  the sealed eligible-unit packet. The blocking review findings still required
  source corrections; green checks did not waive them.
- Cleanup now preserves unknown mux presence at initial selection, lock-held
  revalidation and final managed removal, even with expired/negative hints,
  a missing checkout or forced session cleanup. Complete catalogs still catch
  a newly appearing mux between selection and revalidation.
- Shared per-project publication retains the existing governance and cleanup
  custody handling. One failed PSMux-independent project publication cannot
  shut down unrelated roster refreshes.
- Passive status/list and legacy Picker readers return independent facts when
  mux observation is unavailable. Refresh does not clear cached mux positives.
  POSIX passive fallback errors use the same unavailable result; explicit
  strict action checks retain their original refusal.
- Current-feed publication failures and disconnects now refresh normalized
  badges and attachment/mux-live controls, not only raw availability. Fresh
  publication restores controls without changing retained Git/client facts.
- Consumer tests exposed old probe-based race fixtures, platform-implicit TMux
  cache fixtures, and a production-Picker test bootstrap requiring a host
  installation. The tests now use complete synthetic catalogs, explicit POSIX
  fixtures and the source-qualified engine, with environment restoration.
  Disabled cache warming returns before requiring an active project.
- Focused corrected cleanup/display tests passed; the cache packet passed 24
  tests, shared monitor/producer publication passed 62, and the actual current
  feed/deadline consumer passed 59. Earlier unaffected listing/monitor tests
  remain supporting evidence. Lint, module ceilings and installation contract
  are being refreshed before republishing; final exact-source review and CI
  remain required. No native PSMux, live recovery or rollout was performed.

### 2026-11-07 - Concurrent-source reconciliation

- Rebasing onto the current contribution branch preserved the new managed
  mapping input and message-activity projection. Supported complete
  observations still use fresh, project-matching managed counts; unavailable
  mux evidence cannot promote a cached count merely because its receipt is
  recent. Live, stale, wrong-project and cleanup-owner cases remain covered.
- The reconciled producer/projection/PSMux-publication selection passed 46
  tests. Exact conflict-file lint and changed-source module ceilings passed.
  All five original blocking threads have implementation replies and are
  resolved; a fresh review and CI for the rebased head remain outstanding.
