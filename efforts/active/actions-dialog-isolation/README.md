# Actions Dialog Isolation

- **Slug:** `actions-dialog-isolation`
- **Repo:** copilot-extensions
- **Branch(es):** independent plan and implementation PRs targeting `dev`
- **Created:** 2026-10-23
- **Status:** Draft
- **Vision:** [Picker](../../../visions/picker/README.md), responsive-by-budget
- **Umbrella issue:** #7939
- **Sub-issues:** #7940 (new configuration-freshness episode)

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
- Phases 1–3 own only `engine_worktree_actions.py`, `engine_dialogs.py` and their
  focused tests. That finite Actions source episode is complete.
- Phase 4 separately admits the exact file-store and derived-consumer hooks in
  the [configuration amendment](configuration-cache-seam.md#execution-amendment).
  Startup composition, scalar binding/API/epoch logic, pending binding,
  telemetry, authoritative row-feed and protocol ownership remain unchanged.
- Shared `harness_state` supplier extraction and the narrow locality/refusal
  seams now have explicit admission, subject to the additional source-plan
  review below. No other phase gains authority by implication.

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

The completed Actions episode remains recorded below. Phase 4 is a new #7940
episode requiring its own reviewed source amendment before implementation.

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
- [x] Obtain independent required review, resolve findings and verify actual merge.
- [x] Deferred to `#7939`: Record release/rollout and native qualification separately; do not change
  installed payloads, services or production UI as part of this source slice.

### Phase 4 — Bounded configuration freshness

- [ ] Review and merge the execution amendment before functional changes.
- [ ] Replace the three indefinite file memoizers with demand-driven scoped
  snapshots, real binding identity, TTL and single-flight changed-input reads.
- [ ] Add only the admitted derived-map/manual-reload/accepted-revision hooks;
  preserve unrelated selection and current setup/source generation.
- [ ] Qualify the complete amended matrix, including cancellation, error
  retry, manual clear/rebind races and zero-work warm reads.
- [ ] Obtain supplier-API admission before editing `harness_state`; then qualify
  the actual selected-supplier path without unrelated-project discovery.
- [ ] Review the supplier/locality amendment before those functional edits;
  keep the headed-launch bypass guarded or explicitly blocked pending admission.
- [ ] Pass review and required checks, merge source PRs and explicitly retain
  native/release/installed obligations. Do not close #7940 with a production
  supplier or derived-view gap.

## Validation Plan

Phase 4's additional matrix is in the
[configuration amendment](configuration-cache-seam.md#execution-amendment).

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

### 2026-10-23 — Verified source closure

- Follow-up #8162 merged as `339d029f2dbb73d62e08d579d54ad5058ea99c84`.
  The full current-head review approved the repair and explicitly marked the
  Medium warning-loss finding resolved. Its sole remaining Low metadata
  observation referred to an older PR description; the corrected live title/body
  were verified, explained and the thread resolved before merge. Required checks
  passed. Managed post-merge reconciliation succeeded.
- The finite Actions source effort is now Done. Every source item is resolved;
  release/installed acceptance remains #7939, native timing remains #7935 and
  the unimplemented configuration freshness seam remains #7940. No campaign,
  release, installed-runtime or native-budget completion is asserted.

### 2026-10-23 — Configuration episode admission

- Fresh issue/PR and task collision checks found no competing #7940 owner.
  The retained source worktree is clean and remains a managed workspace.
- The integration owner admitted the exact file-store surfaces and existing
  derived-consumer hooks in the amendment, not scalar/bootstrap changes.
- Public-safe requested constraints: preserve real binding identity and bounded
  obsolete scopes; no UI waits/I/O or stale-empty authority; preserve public
  value/exception compatibility; propose the owner-selected supplier API first;
  no invented template syntax, saturated-host benchmark or installed rollout.
- Shared supplier extraction remains unapproved. The source amendment is
  proposed, not already implemented.

### 2026-10-23 — Supplier and locality admission amendment

- Fresh issue/PR/task checks found no competing owner. Existing prototype and
  original failing-control evidence are preserved separately from this
  documentation-only amendment.
- Admitted: the exact `ProjectAnchorSelection`/`project_anchor_selection` owner
  API and extraction/reuse of existing registry path/anchor rules; plus
  `data_local.is_local` not-ready handling and immediate
  `engine_maintenance_actions._resume_decision`/`_decide` visible refusal.
- Known locality stays Boolean. Unavailable configuration is an explicit
  not-ready outcome, never a successful `False`. Internal deferred decisions
  retain a refusal marker even if Restore changes their action label; `_decide`
  reports the reason and returns without exit/launch. Existing known-local and
  known-remote paths keep their current results.
- One additional immediate consumer bypass was found:
  `headed_actions.open_worktree_cli_headed` builds a launch request without
  `_decide`. Its one refusal-marker guard needs explicit admission before that
  path is safe to publish. No generic action-engine rewrite is proposed.
- Source review additionally identified `_resume_embodied_worktree_cli`'s
  unconditional result mutation/success after `_decide`, and the direct
  locality checks in its synthetic path and `_embody_task_cli`. Their exact
  no-effect refusal controls are explicit additional admission/publication
  dependencies in the amended plan.
