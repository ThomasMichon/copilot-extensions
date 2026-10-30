# Startup-Only Guidance

- **Slug:** `startup-only-guidance`
- **Repo:** copilot-extensions
- **Branch(es):** Sequential proposal and implementation PRs against `dev`
- **Created:** 2026-10-29
- **Status:** Draft
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
selector form already places full fallback bodies in non-discovered `.md`
files, but inline declarations and cache-free control-floor rules still
produce automatically discovered full checked-in bodies.

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
- [ ] Review and merge this proposal before changing implementation or adopters.

### Phase 2 - Make fallback authoring non-discoverable

- [ ] Extend the existing renderer, declaration handling, reader and scanner
  together so every maintained full fallback is a non-discovered `.md` body,
  including former inline/control-floor declarations. Thin recovery locators
  may remain automatically discoverable; they must not reproduce policy bodies.
- [ ] Keep periodic sync, consent/ownership protections, atomic lock updates,
  worker/reconciler integration and complete fallback accounting. Do not require
  fallback synchronization at creation/startup or on every agent read.
- [ ] Enforce safe fallback filenames, including embedded or repeated
  `.instructions.md` forms and other native instruction-discovery names.
  Validate the whole candidate path as well as its final suffix.
- [ ] Preserve managed-copy-first raw lookup and independent publication.
  Do not restore hash, fingerprint, version or receipt admission for generated
  content, or let fallback age block usable current guidance.
- [ ] Implement provider-first compatibility before changing declarations or
  adopted artifacts. Preserve existing CLI operations and strict argument
  rejection; describe supported old/new maintenance readers, rollback and
  truthful incompatible-state errors rather than assuming lockstep updates.
- [ ] Update owning patterns, setup/review guidance and focused tests; add a
  changefile for every changed plugin without hand-editing versions.

### Phase 3 - Migrate declarations and adopted copies

- [ ] Migrate suite declarations and control-floor/catch-all instructions to
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

- [ ] Fake native instruction discovery sees a single generated full body per
  enabled source, not a second checked-in policy body. Thin locator presence
  must not cause unconditional fallback reads beside an available projection.
- [ ] Every maintained fallback filename excludes `.instructions.md` anywhere,
  and fallback paths avoid automatic discovery, including native special names,
  case variants, nested/repeated suffixes and malformed declaration controls.
- [ ] Both former inline/control-floor and existing selector sources produce
  usable on-demand fallback bodies; periodic sync updates them without
  overwriting authored files or requiring per-launch maintenance.
- [ ] Cold creation, launch/resume, pairing, standalone/compatibility publication
  and bounded SessionStart backup still copy current enabled templates. Include
  disabled sources, partial discovery and overlapping refresh controls.
- [ ] Missing generated files select a reachable offline fallback; a healthy
  generated body wins regardless of fallback freshness. Missing or failed
  preparation stays visible without fabricated guidance admission.
- [ ] Provider-first mixed-version reader/writer, legacy declaration/lock and
  rollback controls cover the changed maintenance boundary. No upgrade relies
  on a new command being accepted by an older installed runtime.
- [ ] Retain containment, symlink/reparse, Git-index, foreign-file, ambiguity,
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
