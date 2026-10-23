# Actions Dialog Isolation

- **Slug:** `actions-dialog-isolation`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-10-23
- **Status:** Active
- **Vision:** [Picker](../../../visions/picker/README.md), responsive-by-budget
- **Umbrella issue:** #7939
- **Sub-issues:** #7940 (shared configuration dependency; not absorbed)

## Guiding Intent

Opening Actions must render published row data without waiting for configuration
discovery. Background refinement belongs to exactly the dialog that requested
it; another source or newly opened dialog must never inherit its result.
Preserve Actions under one second and input acknowledgement under 100ms as
native acceptance targets, not claims inferred from helper tests.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Actions consumer owner | Plan, consumer implementation, regression tests and PRs | source `nimble-rivet`; #7939 |
| Startup owner | Existing bootstrap, loading and scalar binding contracts | #7938 |
| Configuration owner | Explicit freshness contract and shared cache integration | #7940 |
| Native measurement owner | Actual frame/input qualification | #7935 |

## Coordination

- **Topology:** independent per-slice PRs.
- **Host (owns PRs):** Actions consumer owner.
- **Delegates:** none; existing owners retain their separate scopes.
- **Handoff:** reviewed source changes and functional evidence remain distinct
  from native measurement, release and authorized rollout.
- Only `engine_worktree_actions.py`, `engine_dialogs.py` and their focused tests
  are admitted for functional changes here. No startup composition, scalar
  binding, telemetry reporter, authoritative row-feed or protocol changes.
- `project_config.py` changes require an explicit shared-seam agreement with
  the startup integration owner before implementation. #7940 remains open;
  this effort does not choose manual-only semantics or invent another cache.

## Context

#7939 identifies three connected defects: a tracking-path existence check
before modal push, cold `LOCAL` access while computing actions/modifiers, and
completion refreshing whichever submenu is currently open. The existing
`refresh_actions` updates options/footer but not the header.

#7940 separately identifies a discarded TTL in `ConfigCacheSession`. Its
expiry/single-flight/supplier-input acceptance must not be reported complete
by fixing Actions. Shared file-snapshot freshness must preserve the startup
owner's accepted engine binding and epoch behavior.

This closes the existing Picker responsive-by-budget intent. It follows
[performance measurement](../../../docs/patterns/performance-measurement.md):
no new polling, service, cache authority, network work or discovery on input.

## Request

Public-safe excerpt of the requested scope:

> Actions first-open cold work removal and configuration-dialog async/source
> identity correctness. Preserve under 1s first Actions, under 100ms input,
> dialog instance plus source/epoch-bound results, explicit cache expiry and
> unknown handling. No extra synchronous Git/network/config reads on the UI
> thread; no new polling/service/cache authority. Shared configuration changes
> require exact seam approval before edit. Keep native qualification separate
> from unit/headless evidence.

Private coordination identifiers and paths are intentionally omitted. This
effort is the finite Actions consumer slice, not completion of a wider campaign.

## Plan

### Phase 1 — Review the consumer contract

- [x] Review and merge this source plan before functional implementation.
- [x] Deferred to `#7940`: Confirm the shared configuration seam with its
  integration owner before any #7940 functional change; otherwise retain #7940
  as a dependency.

### Phase 2 — Immediate opening and exact completion admission

- [x] Push `SubMenuScreen` using published row fields before any path/provider
  lookup. Move record existence and eligibility refinement into the existing
  background request; keep fixture/no-record behavior compatible.
- [x] Remove cold `LOCAL` resolution from menu construction. Unknown locality
  must not offer local-only AHP actions; prefer explicit published locality or
  already-prepared source identity, never discovery.
- [x] Bind completion to the originating modal object, monotonically increasing
  open epoch and source-qualified row identity. Capture the request's project
  and source inputs; reject close/reopen, source replacement and out-of-order
  results before mutating a row, callback map or spinner.
- [x] Refresh header, options, extension/worker dispatch maps and loading state
  from one accepted revision. Preserve applicable launch toggles and navigation.
- [x] Preserve missing/error results as unverified cached data, not a newly
  authoritative empty or healthy result.

### Phase 3 — Review, land and retain qualification obligations

- [x] Update the directly related runtime documentation and mandatory changefile.
- [x] Complete focused functional tests, lint and required source checks.
- [ ] Obtain independent required review, resolve findings and verify actual merge.
- [x] Deferred to `#7939`: Record release/rollout and native qualification separately; do not change
  installed payloads, services or production UI as part of this source slice.

## Validation Plan

- [x] First-open work-count test: block provider/config/path lookup and show
  that modal push occurs first, with zero such work on the input thread.
- [x] Cold locality test: a raising lazy `LOCAL` accessor cannot be touched by
  modal construction; unknown locality does not offer local-only operations.
- [x] Deterministic schedules: close A/open B/finish A, reopen A, source change,
  same identifier on different source, cancellation, error and missing record.
  Rejected completions change no record, header, dispatch map or spinner.
- [x] Same-row success refreshes LOCK/header and verbs together while retaining
  valid modifiers and ordinary dispatch behavior.
- [x] Deferred to `#7940`: Configuration dependency must prove expiry with a fake clock,
  atomic replacement, concurrent single-flight reads, supplier/template changes,
  manual invalidation and honest missing/error behavior before it closes.
- [x] Deferred to `#7935`: Native qualification on a coordinated quiet lane must measure real first
  Actions and Escape/input boundaries cold/warm with source/serving identities,
  cardinalities, samples and errors. Headless tests do not pass this gate.
- [x] Keep clean-room/native/external tiers explicit. No installer behavior is
  changed; first-open functional fixtures cover unprepared consumer state,
  not fresh-machine installation or deployed behavior.

## Proposal

The [configuration-cache seam proposal](configuration-cache-seam.md) names the
exact #7940 methods, preserved startup contracts, dependency limitations and
test matrix. It is not implementation approval and does not expand the
Actions-only ownership boundary.

The exact modal token and accepted-revision checks are agent-recommended
implementation mechanics for the requested lifetime contract. Keep them local
to this consumer, using existing source identity and background execution
interfaces. Do not create a general request broker or shared polling layer.

## Journal

### 2026-10-23 — Bounded ownership and source trace

- Fresh issue and open-PR checks found no competing Actions implementation.
  Claimed #7939 with the public source handle; #7940 retains its own acceptance.
- Confirmed the pre-modal tracking lookup, lazy locality access, unbound
  completion and missing header refresh in the source consumer.
- Functional implementation awaits reviewed plan. Shared-cache edits await
  exact integration approval; native budgets remain unqualified.
- Managed proposal publication was attempted twice; both attempts refused with
  `Timed out waiting for PR rewrite authority; retry after the active publication.`
  No PR was opened. Preserve this source worktree and retry its managed
  publication after the current publisher releases authority; do not bypass
  the publication gate or start functional implementation.

### 2026-10-23 — Reviewed plan and isolated consumer implementation

- Plan #8142 received an approving Copilot review and green checks, then merged
  as `c3acde020b24eb56fd89004976b0c10e0fdae8d9`. Implementation started only
  after managed reconciliation onto that merge.
- The modal now owns a row snapshot and dispatch maps. Path/locality work moved
  behind modal push, with exact lifetime/source/setup/project completion checks
  and a visibly unverified fallback. No shared config-cache or startup code changed.
- Contained Windows functional run: 40 Actions/menu tests passed, including
  blocked tracking lookup plus Escape, repeated out-of-order completions and a
  real headless modal's LOCK/header/verb refresh. Corrected the regression
  fixture's noncanonical source ID and removed unrelated body-focus timing from
  the direct-open test before the passing run.
- Touched Python lint and the 13-plugin install-contract check passed. Isolated
  test dependencies were prepared only after the missing-environment error;
  no installed runtime, production UI, service or benchmark was changed/run.
- Native timing, release/rollout and #7940's separate configuration/supplier
  contract remain open. Source review and actual implementation merge are next.
- Required CI caught the Actions mixin crossing its 1,000-line source cap.
  Dialog lifetime, dispatch-map ownership and worker-only verification helpers
  were moved into the existing `SubMenuScreen` component, within the admitted
  two-file consumer boundary. The guard is retained unchanged.

### 2026-10-23 — Source slice merged; acceptance owners retained

- Implementation #8150 merged as `dd21e7509342ac1e5d9f22ac6a171cafb0837d19`
  after required checks passed. Its
  earlier alias-locality finding was fixed by calling the source's canonical
  `is_local` method on the worker, with the tuple fallback retained for old
  sources. The final contained Actions/menu packet passed 41 tests; lint,
  module-size and install-contract guards passed.
- The finite source effort is separate from the entire responsiveness campaign.
  #7939 remains open for release/installed acceptance; #7935 retains actual
  cold/warm native Actions/input qualification; #7940 retains all configuration
  freshness and supplier/consumer integration work. No native budget or live
  deployment claim follows from the source merge.
- Shared-cache proposal remains reviewed intent only. No `project_config.py`,
  startup binding, pending view, scalar API, telemetry reporter or authoritative
  row-feed code was edited.

### 2026-10-23 — Corrected review assessment and pre-mount warning repair

- The final #8150 review body contained a Medium pre-mount warning-loss finding
  despite its "No open issues" heading and the absence of an unresolved inline
  thread. The earlier clean-review/completion assessment was incorrect.
- Reopened the finite source completion gate. The follow-up stores verification
  status on `SubMenuScreen` before widget lookup and renders that stored value
  during composition. Tests cover unbound, error, missing and successful results
  arriving before the modal mounts. This is source repair, not native/cache
  acceptance deferred elsewhere.
- Source completion awaits review and actual merge of this repair. Inspect full
  review bodies as well as threads before deciding that the review gate passed.
- The corrected contained packet passes 46 tests, including a result delivered
  after composition but before mount. Touched Python lint passes; native
  qualification and installed rollout remain unperformed.
