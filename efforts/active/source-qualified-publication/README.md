# Source-Qualified Publication

- **Slug:** `source-qualified-publication`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Blocked
- **F1 source gate:** Recorded contended-tier results accepted; source approval
  hold released. Ordinary current-head review/CI/merge remain required.
- **Vision:** `visions/plugins/agent-worktrees/README.md`, resident authoritative live-state database; `visions/picker/README.md`, consumer presentation
- **Umbrella issue:** #7937
- **Sub-issues:** #7936 · #7946

## Guiding Intent

Refresh and successful maintenance must converge on the selected producer's
authoritative publication without regressing a newer row, confusing sources,
or rebuilding a fleet in the caller. Observation-only publications must not
become semantic row writes or changes.

This closes existing authority and bounded-consumer intent; it introduces no
new service, alternate authority, or operator-specific integration.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Plan author | Source-grounded proposal and plan PR only | This effort and #7936/#7937/#7946 |
| Projection implementer | F1 semantic storage and negotiated observation protocol only | Existing isolated source worktree; #7946 |
| Receipt implementer (unassigned) | F2 targeted owner observation and receipts | Separate managed source worktree after F1 contract |
| Manager implementer (unassigned) | F3 source store and F4 maintenance convergence | Separate managed source worktree after contract admission |
| Existing owners | #7938, #7974, #7788 and publisher/recovery work | Read-only issue/PR/task state; ownership retained |

## Coordination

- **Topology:** plan-only PR, then the explicit staged graph in
  [implementation.md](implementation.md); no functional edits in the planning PR.
- **Host (owns PRs):** plan author owns only the plan PR. Each admitted
  implementation participant owns its own PR and gates.
- **Delegates:** none assigned to functional work by this document.
- **Handoff:** coordinator admits exact nonoverlapping files before execution;
  report collisions to that coordinator without taking another owner's work.
- Do not commandeer, contact, inspect sessions of, or collaborate with
  pre-existing Bridge agents. Do not use agent-chat or dispatch remotely.
  Only effort-owned native sub-agents, or newly created owned local Bridge
  agents if separately needed and authorized, may execute this effort.
  Forge/task metadata is the collision-detection surface. If review requires
  an existing Bridge agent, record a blocker rather than contacting it.
- Preserve #3761/#6748/#2619/#6086 and existing publisher fixes. This is
  neither whole-daemon completion nor authority to recover deployed services.
- No shared deployment, installation, force reset, or destructive live test.
- The proposal is discoverable through its three issue claims. Avoid a
  concurrent rewrite of the shared effort index in this isolated plan PR.

## Context

The inspected source baseline is `22fa78f06922d48a2bd91e1feb834b535b0defee`.
The bounded trace and proposed contracts are in
[implementation.md](implementation.md). The test obligations and command-level
lanes are in [validation.md](validation.md).

Existing #6595 established bounded resident reads and atomic held feeds.
The rollback recovery in #7933 preserves election/generation fencing and
truthful publication failure. Retain those fixes rather than replacing them.
#6748 remains the publisher-stall recovery owner; targeted acknowledgement is
not permission to restart or replace it. #2619 owns broader event-driven
production and #6086 owns workload/spawn-budget qualification.

## Request

Public-safe capture of the requested scope (private coordination identifiers
and paths intentionally omitted):

> “source-qualified monotonic Refresh”, “owner publication receipt”,
> “semantic-only row deltas”.
>
> “Do NOT edit any functional code in this planning turn”.
>
> “Produce source mini-effort detailed implementation plan with explicit
> producer/consumer API schema, source-qualified identity, generation/revision
> ordering, pending/degraded receipt semantics, atomic publication/heartbeat
> separation, bounded effect waits/no caller fleet scan, test paths and
> command-level validation.”
>
> “Need exact file ownership and staged PR graph.”

The required regression cases are preserved individually in the Validation
Plan and its linked matrix. Proposed API names, bounds and implementation
partitioning below are **agent-recommended**, not claims of existing support
or additional execution authorization.

## Plan

### P0 — Reviewed plan
- [x] Trace the three issues through actual producer, feed, loader and screen.
- [x] Specify interfaces, compatibility, ownership exclusions and staged graph.
- [x] Obtain plan review and merge through the source contribution gates (#8021).

### F1 — Semantic publications
- [x] Implement the negotiated observation/semantic split and changed-row
  persistence from [implementation.md](implementation.md).
- [x] Preserve atomic head, row, order, observation and deletion publication.
- [x] Accept the explicitly authorized contended-host tier for F1 source
  completion, retaining nominal performance work in #7935/#7936/#7946.
- [ ] Complete the scoped F1 source review/merge and transfer remaining
  consumer, effect and installed qualification to their existing owners.

### F2 — Owner receipts
- [ ] Implement bounded targeted owner observation and post-effect receipt
  publication; no caller scan or replay of destructive effects.
- [ ] Preserve partial success, contention, supersession and pending outcomes.

### F3 — Monotonic source store
- [ ] Route local/remote Refresh to the selected source and commit through Inbox
  to the same store consumed by live ticks.
- [ ] Keep point-row revision floors separate from complete-feed cursors.

### F4 — Maintenance convergence
- [ ] Distinguish completed process effects from confirmed roster publication.
- [ ] Converge through the existing feed without source reconnect or full sweep.

### F5 — Integrated qualification
- [ ] Run the exact regression matrix and producer-to-screen integration.
- [ ] Record source, release and any separately authorized rollout states
  independently. No live rollout is part of this plan-only turn.

## Validation Plan

- [ ] Locked revision 10 → point unlocked 11 → 20 unchanged feed ticks never
  regresses the row; exercise actual screen/loader/Inbox path.
- [ ] Local and remote same-ID fixtures remain isolated; remote Refresh makes
  zero local point reads.
- [ ] Delayed/out-of-order replies, reload epochs and owner-generation reset
  preserve authoritative ordering and UI-thread mutation.
- [ ] Gated reclaim publication remains pending; releasing publication
  converges without repeating reclaim or reconnecting.
- [ ] Contention, partial bulk success and concurrent new owner are explicit.
- [ ] Ten real-provenance publications of 1,000 unchanged rows produce zero
  semantic replacements/deltas after the initial baseline; one title change
  produces exactly one semantic replacement/delta.
- [ ] Freshness remains truthful; heartbeat is not reobservation; reconnect,
  deletion, order and generation replacement remain atomic.
- [ ] Complete [validation.md](validation.md), including compatibility and
  command-boundary lanes, before asserting implementation success.

## Proposal

**Reviewed in #8021; F1 execution admitted separately.** Keep one owner and one
source-qualified consumer store. Add an explicitly negotiated bounded
observation/receipt contract, not an optimistic row cache or second writer.
Separate fact revisions, observation freshness and transport heartbeat.
Detailed design: [implementation.md](implementation.md).

The bounded [current-feed entry amendment](startup-entry-proposal.md) addresses
the pre-control CLI startup gate while preserving existing command semantics
and exact shared-file owners. Its source review merged in #8139;
it does not expand F1 into launch, installer or owner-side effects.

## Journal

### 2026-10-24 — F1 source-tier acceptance; approval hold released
- The operator independently accepted exact candidate `0ef1505b`, current
  green CI `40064027710`, clean owner-authored Comment `5636917775` and
  all resolved code-finding threads. All 74 archived evidence members and
  the Manager/Worktrees JUnit/results were independently byte-verified.
- The accepted source tier is Manager 2x plus Worktrees module 2x and the
  single timing-only console/guarded 3x follow-up. This is not a nominal
  two-second pass or production SLA/configuration change.
- The F1 source approval hold is released; ordinary readiness, current-head
  review/required CI and configured managed merge are authorized. The
  owner-authored clean-Comment policy applies; no additional Approve label
  is required. New source/review findings or failing required CI still block.
- Nominal #7935/#7936/#7946, F2/F3/F4, integrated GUI/installed validation
  and overall effort completion remain open. This acceptance does not mark
  this whole effort Done or authorize a rollout.

### 2026-10-24 — Explicit contended-host test tier; nominal performance stays open
- Added only test-local opt-in factor 1/2/3, default 1. Production source and
  deadline/configuration bytes remain identical to `19f58003`; no fake clock,
  skipped assertion, removed sleep or new global runtime configuration.
- Under the expressly authorized contended tier, all five Manager controls
  pass at 2x with exact one/two-child counts and complete owned cleanup.
  Worktrees module passes its full 1,100-row/update/cutover case at 2x,
  initial completion 3.843 seconds.
- Console/guarded 2x misses were strictly the four-second observer admission
  deadline. Late complete snapshots validated atomically through engine and
  Manager; no semantic/cleanup defect was found. The one authorized 3x run
  selected only those two failures: full cases pass at 2.047/3.985 seconds.
- These are contended-tier passes, not nominal passes. Every nominal
  qualification field remains false; original 1x failures and raw 2x misses
  are retained. Nominal performance work remains in #7935/#7936/#7946.
- Exact result acceptance, fresh current-head review/required CI and the
  normal merge gate remain outstanding; no automatic readiness or merge.

### 2026-10-24 — Refreshed draft and reusable-generator correction
- Normal owned-branch sync retained `f3610e0f` and replayed the source on the
  merged #8392 owner repair. Its relevant HTTP47 bytes match, and the repair
  is an ancestor. No competing repair or history override was used.
- The push client timed out, but a fresh provider read independently confirmed
  actual draft head `81dedbf3` and green required CI `40053692459`.
- Fresh review `5636432797` identified a High in retained input aliases.
  Reproduced wrong v1 observation times and failure after a generator clears
  its reused dictionary. Each admitted row is now snapshotted by decoding its
  already-validated encoding, including nested fact values.
- Twenty-seven focused in-process wire/closure/generator cases pass.
  Correct row/observation IDs, live/advisory v1/v2 data, actual Manager feed
  admission and zero semantic writes/deltas on reobservation are covered.
- The prior Manager deadline failures and unchanged Worktrees native hold
  remain unresolved. No timing rerun, readiness, merge or rollout occurred.

### 2026-10-24 — Closure-observation review correction; qualification still blocked
- `6dce27dc` is actually published on draft #8217 with required green CI.
  Fresh review confirmed the complete-wire budget correction but identified
  stale optional closure timestamps in reconstructed v1 output.
- Reproduced removal failing while timestamp refresh passed. Reconstruction
  now starts from semantic fields before applying current observations; the
  stored semantic payload, row version and zero-write behavior remain intact.
- The affected Worktrees packet passes 194 cases. The same 92-case Manager
  selector reports 87 passes and five native startup/deadline failures.
  That selector includes native controls; earlier descriptions of it as
  purely in-process were inaccurate. No unchanged retry, relaxed deadline
  or loader change followed.
- Draft publication and fresh review/required CI remain required for this
  review correction. Native and Manager deadline qualification remain blocked;
  no readiness, merge or rollout is authorized.

### 2026-10-24 — Complete v2 wire-row budget correction
- Draft #8217 published `b5ab565a` with required green CI. Fresh review
  confirmed the two earlier findings resolved but found that engine admission
  omitted the serialized semantic revision from its row-byte accounting.
- Reproduced a real durable publication of a 65,536-byte legacy row becoming
  a 65,558-byte v2 row rejected by Manager. The admitted correction retains
  legacy schema/type and version checks, then checks the complete v2 encoding
  before the publication transaction, on reads and at both engine admission
  paths. No fixed reservation or lowered legacy capacity is used.
- Contained validation passes 192 Worktrees cases (including 23 new exact
  boundary/rollback/compatibility cases) and the existing 92-case Manager
  parser/feed selection. Actual JUnit files confirm both counts with zero
  failures/errors. Details remain in [validation.md](validation.md).
- No unchanged native timing case, deadline adjustment, ready transition,
  merge or rollout occurred. Fresh publication/review/CI of this correction
  and the independent native qualification hold remain separate gates.

### 2026-10-23 — Size-compliant entry and wire-admission corrections retained
- The admitted two-helper extraction is committed locally in `5a946fd6f`.
  The actual root module has 6,760 lines against its 6,761 ceiling, without
  changing the baseline, shared shutdown policy or public late-bound wrapper.
- The latest unchanged module/console/guarded native cases all failed their
  two-second gate under saturated host load. They do not establish a regression
  or unavoidable floor; earlier passes do not qualify the extracted candidate.
- Both #8217 Medium findings reproduce: negotiated v2 reads obscure shared v1
  failures, and receiver-added metadata makes valid near-limit rows fail at
  snapshot/update barriers. Local corrections preserve typed errors and raw
  UTF-8 wire limits, excluding only four local metadata fields after admission.
- Scoped Worktrees contracts pass 134 cases plus one final envelope control;
  Manager parser/feed contracts pass 92. Required source CI, fresh accepted
  review, unchanged native qualification and actual merge remain outstanding.
  Detailed commands, failures and exclusions are in [validation.md](validation.md).
- No timing retry or publication followed these corrections. A coordinated
  quiet lane is required before the next unchanged native invocation.

### 2026-10-23 — Plan merged; F1 admitted
- #8021 merged after current-head Copilot approval and required green CI.
- F1 alone is admitted: semantic storage/observation wire contract and Manager
  parsers, with no F2 effects, F3 loader/UI or reserved startup integration.
- Compatibility refinement: keep the existing durable schema and election
  usable by an older supported writer. Additive observation tables are
  generation/revision-qualified; stale extension data never overrides a
  complete publication from a compatible older elected writer. Wire version
  is not elected-writer incarnation or durable-schema version.
- Functional validation is in progress. Parent issues and all remaining
  source/consumer qualification obligations remain open; no rollout authorized.

### 2026-10-23 — F1 source qualification blocker
- Local F1 implementation retains schema-1 writer usability and exposes the
  negotiated v2 protocol without enabling loader/UI adoption.
- Contained Worktrees tests: 145 passed with the one separately failing native
  CLI startup test deselected; Manager parser/feed selection: 83 passed.
  Lint and the 13-plugin install-contract guard passed.
- The native CLI test fails even alone before any owner control request.
  Its two-second bound is unchanged. Source publication/merge is held pending
  a separately admitted startup diagnosis or a reviewed tracked disposition;
  there is no accepted exception and no claim of complete F1 qualification.
- Detailed evidence and preserved remaining gates: [validation.md](validation.md).

### 2026-10-23 — Admitted bounded startup diagnosis
- Compared the exact unchanged two-second native case against pre-F1 source:
  both fail before the owner control request. Source bytes were restored from
  the retained F1 commit after comparison.
- Recorded phase timing, command/interpreter identity, sampled host load and
  child-only import attribution in [validation.md](validation.md). Loaded-host
  observations are not a worst-case or causal attribution of run-to-run delta.
- The next meaningful CLI entry/import change overlaps exact-file live claims:
  `__main__.py` in #8045/#8031 and `update_stage.py` in #7788. Stopped that
  expansion; no agent contact, deadline waiver, startup fix or F1 source merge.

### 2026-10-23 — Exact entry proposal, awaiting admission
- Refreshed the live PR heads and actual entry hunks; the proposed pre-import
  insertion is hunk-disjoint, not globally unclaimed.
- [Startup-entry proposal](startup-entry-proposal.md) identifies the minimal
  selected-command entry, shared parser/context preservation, necessary console
  declaration and healthy-read monitor-import back-edge removal.
- Existing diagnosis was reused without benchmarks or suite reruns. No
  functional code changed; the implementation task remains suspended.

### 2026-10-23 — Amendment reviewed; bounded repair still misses native gate
- #8139 merged after current-head Copilot approval and required green checks.
  The documentation-only PR excluded the retained F1 implementation commits.
- Implemented only the admitted selector, console declaration, canonical
  parser/context extraction, lazy client recovery and conditional routing
  import. Existing launch/lifecycle/import-115, installer, server ownership,
  cutover/drain and shared-library owner scopes remain untouched.
- New targeted parity tests pass, including library-import/decline behavior,
  legitimate context overrides, invalid installation cell and vanished CWD.
  The actual local test-environment console wrapper uses the new declaration;
  no shared installed payload was modified.
- The unchanged two-second native gate still fails before first health:
  module 4.844 s, guarded module 3.828 s, console 3.953 s. The guarded child
  rejects broad dispatcher/recovery imports; it reached a complete snapshot
  without triggering those refusals. No further import/library expansion.
- The retained F1 packet and exact new evidence/gates are in
  [validation.md](validation.md). F1 remains unmerged and the effort Blocked.

### 2026-10-23 — Merged guidance-owner integration
- Rebased the retained candidate onto `dev` containing #8045 merge
  `419a12d054193e3253f91a8395444608116fa1e3`; the rebase was conflict-free.
  The original candidate remains on `backup/f1-entry-before-merged-e`.
- Fresh #8031 head `a3f34952706fe1020558c7181daacd0555d50f67` still changes
  import 115, launch and monitor-sweep hunks, not the admitted early selector.
  #7788 remains open at `afb2d47f7ab156c3e6ab9ba6d31624c46a91eda9`.
- Re-ran only the unchanged native module gate against this integration:
  first health 2.703 s, complete snapshot 2.907 s, still FAIL at two seconds.
  No deadline or scope expansion; source publication remains held.

### 2026-10-23 — Three inert controls; raw receipt retained
- Reused the existing phase/import evidence first. Ran exactly three serial,
  default-site inert-print controls through the same prepared interpreter and
  canonical containment/fixture construction; no full-roster retry.
- First stdout was 0.328/0.343/0.234 s, with zero exit codes and empty stderr.
  The different workload does not qualify the CLI or establish an unavoidable
  interpreter/scheduler floor. No timings were added or subtracted into a
  synthetic budget.
- Exported all original samples, environment/version metadata, load data,
  diagnostic source and lint logs before automatic sandbox cleanup. The
  complete raw bundle was attached to the owning task and read-back verified;
  the local artifact copy is retained. See [validation.md](validation.md).
- No specific sufficient F1-owned optimization is established. The retained
  eager `asyncio` dependency belongs to shared `agent-procutil`, outside the
  admitted scope. Await owner-scoped investigation or a coordinated quiet lane,
  not automatic retries or a deadline exception.

### 2026-10-23 — L/E/G3 integrated; real entries pass, guarded control remains red
- Integrated #8190 (`72be3f01`), #8045 (`419a12d0`) and #8163
  (`80b1b041`). The context-extraction conflict was resolved by retaining
  G3's exact recovery-report early return in the shared implementation:
  no project discovery or CWD mutation. Original commits remain backed up.
- Fresh complete main-file claim inventory found no claim on the `procs`
  binding. The permitted minimal binding change keeps `core.procs` the same
  module and preserves function/parameter names; canonical plugin Ruff
  configuration now passes without suppression.
- Targeted context/client/materialized-import checks passed 100 cases.
  The single unchanged actual module case then passed at 1.360 s initial
  completion. Actual generated-console qualification passed at 1.734 s.
  Different observed load prevents attributing the timing difference to L.
- Combined contracts passed 272 cases, with the already-run module case
  excluded. The instrumented guarded-module case alone missed the unchanged
  two-second initial deadline (health 1.938 s, complete 2.188 s). No retry,
  threshold change or additional scope followed.
- Full raw stdout, phases, counters, source/interpreter/load evidence and
  claim/lint results were exported before fixture cleanup and placed in
  verified durable evidence storage. The prior raw capsule is preserved.
  Source publication remains blocked; see [validation.md](validation.md).

### 2026-10-23 — Plain protocol guard qualifies; source review pending
- The documented meta-path protocol requires `find_spec`, not ABC inheritance.
  Removed only the guarded child's unused `importlib.abc` import/base.
  Five deliberate forbidden imports still fail before a downstream finder
  effect; an allowed import succeeds. The same native guard class is exercised.
- AST comparison against `764e90601` proves the native function is unchanged
  except that dependency: all predicates, invocation, 1,100 rows, timer
  start/stop, two-second assertion and completeness/error checks are retained.
- Six negative/allowed controls and 39 affected feed contracts pass.
  The guarded native case now passes at 1.469 s initial completion, including
  update, cutover, clean exit and joined readers. Load differed; this remains
  finite source-fixture evidence, not causal/worst-case/GUI/installed proof.
- All original raw evidence is durably retained and verified before cleanup;
  previous prototype and evidence carriers remain preserved. Product source
  did not change in this guard-only correction. Scoped source publication is
  now pending its normal review/required-CI gates; root goals remain open.

### 2026-10-23 — Required source guard blocks publication completion
- The managed source publication rebased/squashed the candidate. Required
  eligible-unit execution, attribution and workflow checks pass; the
  module-size guard fails: `__main__.py` has 6,773 lines versus its shrink-only
  ceiling of 6,761. No baseline increase, suppression or merge bypass was used.
- The current test-only admission cannot authorize the production extraction
  needed to remove that twelve-line growth. A bounded proposal is to move
  early-dispatch composition and the existing shutdown-wrapper implementation
  into `current_roster_entry`, retaining real-`__main__` gating and the public
  `__main__.console_entry` compatibility wrapper. It is not implemented.
- All raw required-CI logs and exact publication metadata remain retained.
  Source review/merge and finalization are still outstanding; finite native
  success does not satisfy those separate gates.

### 2026-10-23 — Exact size-compliant extraction proposed
- [Entry/shutdown proposal](entry-size-extraction.md) names two related moves
  into the existing owned entry module, preserving the root's public wrapper
  and late-bound callback/exit-policy behavior.
- Fresh file/hunk inventory is disjoint from current external main-file
  claims. The in-memory projection on `f7a057618` is 6,773 → 6,760 lines;
  no production file or baseline was changed.
- Identified the existing shutdown test's stale console-mapping assertion as
  a required coupled test update, without dropping its coverage.
- The task stays blocked pending product-hunk admission. Prior raw carriers
  remain proposed EVIDENCEONLY; no benchmark, queue, execution or wake occurred.

### 2026-10-22 — Bounded planning
- Read the three live issue bodies and their empty prior comment/assignment
  state; inspected open PR titles and task searches before scoped plan claims.
- Confirmed the overwrite and local-before-remote paths in source, the absent
  observation intent in current-read requests, and timestamp-driven full-row
  persistence/deltas. No latency or IOPS measurement is claimed.
- Preserved existing source owners and adopted the no-existing-agent-contact,
  no-agent-chat, no-remote-dispatch boundary.
- Captured request constraints and all specified regressions; API details are
  explicitly proposals. No functional source or installed state was edited.
