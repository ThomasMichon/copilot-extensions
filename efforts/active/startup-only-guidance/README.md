# Startup-Only Guidance

- **Slug:** `startup-only-guidance`
- **Repo:** copilot-extensions
- **Branch(es):** Sequential proposal and implementation PRs against `dev`
- **Created:** 2026-10-29
- **Status:** Active
- **Vision:** [Harness guidance](../../../visions/harness-guidance/README.md):
  `authoritative-ownership`, `simple-local-static-delivery`,
  `resilient-safety-boundary`
- **Umbrella issue:** #5993 (bounded follow-up; existing whole-issue ownership retained)

## Guiding Intent

Make creation/startup projection the normal automatic delivery of plugin-owned
instruction bodies. Keep periodically synchronized offline fallbacks, but never
give a content-bearing fallback a filename containing `.instructions.md` or
automatically load it alongside the generated current body. Preserve canonical
plugin sources and repository-authored rules rather than maintaining a second
automatically discovered policy copy.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| guidance-owner | Proposal, source implementation, qualification and reviewed completion | #5993, scoped `startup-only-guidance` follow-up |

## Coordination

- **Topology:** Reviewed proposal, then focused implementation and migration PRs.
- **Host (owns PRs):** guidance-owner.
- **Delegates:** Bounded read-only lifecycle, maintenance and adopter inventories;
  implementation and completion judgment remain with the host.
- **Handoff:** This README records the current slice and outstanding gates.
- **Related owners:** Preserve #5993's earlier native/consumer/rollout obligations,
  #9097's separate repair owner, and #2173/#2256's broader composition work.
  This effort neither rewrites their contracts nor closes them by association.
- **Release and rollout:** Normal promotion owns release. A selected adopter's
  existing maintenance/installation owner owns separately authorized rollout.
  No live service or installed payload is changed by the proposal.

## Context

The completed [direct propagation effort](../../2026/10/27%20direct-instruction-propagation/README.md)
delivered fresh raw copying and managed-copy-first lookup in #9336/#9350.
It intentionally retained checked-in fallback maintenance. The existing
selector form already placed full fallback bodies in non-discovered `.md`
files, but inline declarations and cache-free control-floor rules previously
produced automatically discovered full checked-in bodies.

The [worktree-scoped guidance pattern](../../../docs/patterns/worktree-scoped-dynamic-guidance.md)
owns delivery mechanics. Reuse its declaration-driven publisher, generated
namespace, independent compatibility path and periodic maintenance worker.
Do not replace them with a new scheduler, renderer service, content attestation
or prepared-generation gate. Repository-local policy and source templates are
not redundant generated artifacts.

## Request

Operator request, verbatim:

> We should stop having instruction copies, in favor of using only the on-the-fly projection during worktree creation or session startup

The operator confirmed the effort slug `startup-only-guidance`, then clarified:

> Note that the fallbacks only need to be sycned periodically; we also need to make that fallbacks containing content don't have `.instructions.md` in their names, or else that would cause Copilot to auto-load them alongside the projected copies

The clarification preserves periodic fallback synchronization; it does not
authorize decommissioning that workflow. The implementation and validation
details below are **agent-recommended**, not additional operator requests.

## Plan

### Phase 1 - Review the refined delivery contract

- [x] Dedupe the public trackers and claim a bounded #5993 follow-up without
  taking its separate whole-issue acceptance or #9097 ownership.
- [x] Capture both requests, the confirmed slug and the periodic-fallback
  clarification; reconcile the vision's inline-fallback exception.
- [x] Review and merge this proposal before changing implementation or adopters.

### Phase 2 - Make fallback authoring non-discoverable

- [x] Extend the existing renderer, declaration handling, reader and scanner
  together so every maintained full fallback is a non-discovered `.md` body,
  including former inline/control-floor declarations. Thin recovery locators
  may remain automatically discoverable; they must not reproduce policy bodies.
- [x] Keep periodic sync, consent/ownership protections, atomic lock updates,
  worker/reconciler integration and complete fallback accounting. Do not require
  fallback synchronization at creation/startup or on every agent read.
- [x] Enforce safe fallback filenames, including embedded or repeated
  `.instructions.md` forms and other native instruction-discovery names.
  Validate the whole candidate path as well as its final suffix.
- [x] Preserve managed-copy-first raw lookup and independent publication.
  Do not restore hash, fingerprint, version or receipt admission for generated
  content, or let fallback age block usable current guidance.
- [x] Implement provider-first compatibility before changing declarations or
  adopted artifacts. Preserve existing CLI operations and strict argument
  rejection; describe supported old/new maintenance readers, rollback and
  truthful incompatible-state errors rather than assuming lockstep updates.
- [x] Update owning patterns, setup/review guidance and focused tests; add a
  changefile for every changed plugin without hand-editing versions.

### Phase 3 - Migrate declarations and adopted copies

- [x] Migrate suite declarations and control-floor/catch-all instructions to
  the reviewed contract without deleting canonical templates or inclusion
  declarations. Preserve repository-authored invariants and exact-session writers.
- [ ] Inventory each selected adopter's tracked full bodies, locators, fallback
  files and maintenance configuration. Obtain itemized move/delete approval
  before retiring any tracked files; do not silently remove authored content.
- [ ] Migrate approved artifacts through the adopter's reviewed workflow.
  Keep its existing periodic worker, regenerate non-discovered fallback bodies,
  update references and prove old auto-loaded policy bodies are gone.
- [ ] Qualify creation/startup and recovery on the supported selected paths.
  Keep missing-preparation and genuinely hookless behavior explicit: report
  unavailable current guidance and use on-demand offline recovery, never claim
  a successful refresh or introduce another always-loaded full fallback.

### Phase 4 - Land and account for delivery

- [ ] Assess every current-head review finding and required CI gate, merge the
  implementation/migration changes, and journal their actual scope.
- [ ] Confirm release separately from source merge; complete authorized selected
  adoption or explicitly transfer its remaining qualification to an accepted
  named owner under #5993 without claiming installed behavior.
- [ ] Resolve every Plan/Validation Plan item, mark Done and archive this effort.
  Keep the broader #5993 and other owners' unresolved outcomes open.

## Validation Plan

- [x] Fake native instruction discovery sees a single generated full body per
  enabled source, not a second checked-in policy body. Thin locator presence
  must not cause unconditional fallback reads beside an available projection.
- [x] Every maintained fallback filename excludes `.instructions.md` anywhere,
  and fallback paths avoid automatic discovery, including native special names,
  case variants, nested/repeated suffixes and malformed declaration controls.
- [x] Both former inline/control-floor and existing selector sources produce
  usable on-demand fallback bodies; periodic sync updates them without
  overwriting authored files or requiring per-launch maintenance.
- [ ] Cold creation, launch/resume, pairing, standalone/compatibility publication
  and bounded SessionStart backup still copy current enabled templates. Include
  disabled sources, partial discovery and overlapping refresh controls.
- [x] Missing generated files select a reachable offline fallback; a healthy
  generated body wins regardless of fallback freshness. Missing or failed
  preparation stays visible without fabricated guidance admission.
- [x] Provider-first mixed-version reader/writer, legacy declaration/lock and
  rollback controls cover the changed maintenance boundary. No upgrade relies
  on a new command being accepted by an older installed runtime.
- [x] Retain containment, symlink/reparse, Git-index, foreign-file, ambiguity,
  pairing-pending, concurrency and incomplete-discovery retention controls.
- [ ] Prove approved adopter migration removes all redundant auto-discovered full
  policy bodies while preserving repo-owned AGENTS rules, skills, local plugin
  sources, exact-session guidance and periodic maintenance.
- [ ] Run focused bounded tests and applicable lint/docs/structure checks.
  Use the existing portfolio for native launch/discovery qualification; copying
  bytes and fake discovery do not prove model admission or installed rollout.

## Proposal

Reuse the current startup publisher and the existing selector/non-discovered
fallback pair. Remove the special case that keeps full checked-in control
kernels automatically discoverable, rather than retiring offline fallbacks.
Treat startup copying, periodic fallback authoring and actual model loading as
three distinct events. Maintain compatibility at the owning authoring/reader
boundary before migrating plugin declarations or adopter artifacts.

No file deletion, service restart, machine setting, installed update or new
schedule is authorized by this proposal. Necessary effects retain their
existing review, custody and explicit approval gates.

## Journal

### 2026-10-29 - Refined intent captured

- Inspected separate lifecycle, maintenance and adopter evidence tracks.
  Existing startup copying is reused; no implementation was changed.
- The operator clarified periodic offline fallback retention before the
  originally considered maintenance-retirement plan was written or executed.
- Re-read the request capture against both literal inputs. The no-duplicate
  automatic bodies and non-`.instructions.md` fallback names are firm intent;
  the phased compatibility and validation mechanisms are agent-recommended.
- Claimed the bounded follow-up in #5993 without closing or taking its retained
  whole-outcome acceptance. Proposal review remains the next execution gate.

### 2026-10-29 - Proposal merged; provider implementation admitted

- #9610 merged after current-head Copilot approval, complete review assessment
  and required green checks. The proposal changed documentation only.
- Re-read the complete effort after merge. Phase 2 now owns provider-side
  fallback naming, authoring and discovery behavior before declaration or
  adopter migration. Periodic maintenance remains part of the target.
- No installed payload, service, tracked instruction artifact or machine
  setting changed. Source merge is not release or adoption.

### 2026-10-29 - Provider slice implemented and qualified

- Maintenance authoring normalizes legacy inline, omitted-mode and selector
  declarations to thin selectors plus ordinary non-discovered fallback bodies.
  Complete canonical templates and the periodic worker remain intact.
- Whole derived fallback paths reject embedded/repeated `.instructions.md`
  forms and case variants; native instruction basenames are also refused.
  Managed-copy-first runtime reading remains free of content-admission gates.
- Updated legacy fixtures preserve actual inline lock/preimage migration,
  foreign-file refusal, concurrency, rollback and old oversize-file controls.
  The isolated, network-disconnected full customization suite passes 679 tests
  with two existing skips. Documentation, install-contract and customization
  checks pass their blocking gates.
- Qualified the actual prior selector-capable provider in both reader/writer
  directions, including rollback maintenance after explicit selector inputs.
  An older writer with an inline declaration remains outside the startup-only
  guarantee; the owning pattern documents the required input migration.
- Independent review found no code defect and one stale pattern-index summary,
  now corrected. Added the provider's release changefile.
- This is implementation and qualification, not a merged or released provider.
  Declarations, adopter artifacts, installed payloads and native delivery
  qualification remain unchanged; Phase 3 waits for this provider slice to land.

### 2026-10-29 - Dependent policy consumers qualified during review

- Current-head review identified four conduct-guidance tests retaining inline
  output assumptions. Reproduced all four failures, then adapted the consumers
  to assert thin selectors and complete fallback policy bodies instead.
- Preserved and strengthened offline recovery after payload removal: both
  artifacts remain byte-identical, the fallback retains a complete envelope,
  and offline source lookup selects the owned fallback.
- The focused consumer packet passes all 24 cases. Its full isolated,
  network-disconnected plugin suite passes 103 tests with 41 existing skips.
  Added that plugin's patch changefile; no policy template or declaration changed.
- The implementation PR remains review-gated until the updated head is assessed.

### 2026-10-29 - Provider merged; declaration migration implemented

- #9615 merged after current-head Copilot approval, the addressed consumer-test
  thread was resolved, and all required checks passed. Re-read the complete
  effort after that merge; source completion does not close adoption or native
  delivery qualification.
- Phase 3 now makes all twelve former inline declarations explicitly select
  non-discovered fallbacks, across six owning plugins. Removed the catch-all's
  obsolete cache-free flag. Canonical templates, inclusion declarations,
  exact-session writers and policy prose are unchanged.
- Replaced the inline allowlist with a complete shipped-declaration guard.
  A suite-wide fake-discovery fixture verifies thin selectors, byte-exact
  current copies, complete non-discovered fallback bodies and current-copy
  selection for every declared source. The full customization suite passes
  680 tests with two existing skips.
- The actual prior selector-capable writer also accepts the entire migrated
  declaration roster and authors pairs readable by the current offline reader.
  This qualifies supported maintenance rollback inputs, not an installed update.
- Selected adopter artifacts, their itemized mutation approvals, installed
  payloads and native creation/startup qualification remain separate open gates.
- The six-owner isolated qualification passes customization, attribution,
  efforts and harness suites. It exposed one additional elevation declaration
  assertion, now corrected and verified, plus four independent budget-suite
  failures: unavailable Node.js in isolation, a source-free uninstall fixture
  missing its payload binding, and two obsolete installer-banner assertions.
  The declaration PR is not opened while those failures await a scope/owner
  decision; their existence is not permission to waive validation or broaden
  unrelated installer changes silently.

### 2026-10-30 - Separate validation prerequisite merged

- After explicit scope approval, #9625 repaired the four budget validation
  failures in a separate reviewed PR. It merged after current-head Copilot
  approval, complete finding assessment and all required green checks.
- The isolation container now bootstraps a pinned, checksum-verified Node
  interpreter before offline test execution. Mirrored installer tests use
  provisioning statements rather than obsolete banners.
- The source-free uninstall fixture now supplies the existing product-owned
  manifest and verifies missing/foreign ownership remains refused. It preserves
  configuration, transaction-lock identity and staged bytes; production uninstall
  authority and process-inspection checks are unchanged.
- The final isolated budget suite passes 488 tests with 73 skips, and the
  isolation-wrapper unit suite passes 177 tests. These are prerequisite results,
  not completion of the declaration migration or native model admission.
- Re-read this effort and resumed the saved declaration work on the merged
  prerequisite. The complete six-owner isolated packet now passes: conduct
  103, attribution 118, budget 488, efforts 85, harness 278 and customization
  680 tests (1,752 passed with 142 platform/explicit skips in total).
- Declaration publication and review remain next. Selected adopter mutation
  approval, native delivery qualification and authorized rollout are still open.

### 2026-10-30 - Declaration review tightened qualification

- #9627 review found the renamed declaration guard still referenced by the
  optional macOS packet. Updated the workflow selector and its exact-packet
  expectation together, without changing job activation or CI scope.
- Strengthened every shipped-source check to prove policy text is absent from
  the selector and present in the fallback, then remove the test fixture's
  current copy and verify actual offline fallback selection.
- Running the workflow contract exposed an older fixture assumption that all
  manual routes used single-input expressions. The helper now supports the
  workflow's existing grouped alternatives and exclusion terms, with positive
  and negative controls; it still rejects unsupported syntax.
- The full isolated customization suite remains at 680 passes with two skips;
  the focused workflow contract passes all 26 tests. Current-head re-review
  and addressed-thread resolution remain required before merge.
- The next review identified the immutable-ref compatibility window: the
  optional packet must select either the prior or renamed guard when checking
  an older source commit. Its bounded selector now accepts both names and
  passes against the actual pre-rename source.
- Replaced the hard-coded expression example with checks of the real workflow's
  enabled job sets for each broker input and each owner exclusion. The combined
  workflow/delivery packet passes 160 tests; the pre-rename selector passes its
  one intended guard. Current-head review remains open.
- Kept the suite-wide publication/discovery test in the ordinary integration
  suite, not the fast guard packet. The declaration-only selector guard remains
  the lightweight check; no new per-source Git subprocess matrix is added to it.
- Retained exact single-input expressions for every non-broker route. Only the
  four broker jobs use the alternative/exclusion checks above; a mutation
  control proves adding an unrelated input to a native route is rejected.
  The workflow contract now passes all 29 cases.
