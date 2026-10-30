# Configurable AI Attribution

- **Slug:** `configurable-ai-attribution`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal, implementation and closure PRs
- **Created:** 2026-10-30
- **Status:** Done
- **Vision:** [Harness Guidance](../../../../visions/harness-guidance/README.md#configurable-attribution-signatures)
- **Umbrella issue:** #9633
- **Sub-issues:** none; #2965 retains its separate owner

## Guiding Intent

Make `ai-attribution` the single reusable authority for disclosure policy and
configurable signatures. An adopting harness should express its goals through
configuration of that owner, not a competing plugin or copied decision matrix.
Shared defaults and trusted personal overrides should support concise or formal
markers without requiring every publication workflow to invent its own wording.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary driver | Proposal, canonical provider, integration and publication | Managed source worktree |
| Guidance auditors | Bounded read-only suite and selected-adopter inventories | Parent-controlled evidence tasks |
| Adopter driver | Configuration defaults and consumer reconciliation | Adopter's normal contribution flow |

## Coordination

- **Topology:** serial proposal/implementation PRs; dependent adopter changes
  follow the reviewed upstream contract.
- **Host (owns PRs):** Primary driver.
- **Delegates:** evidence only until the proposal clears review; subsequent edits
  have non-overlapping ownership and no independent git/publication authority.
- **Handoff:** return bounded evidence or diffs to the primary driver; do not
  transfer the objective merely because a phase or PR finishes.
- Existing #2965 custody and #9627 instruction-delivery work remain separate.
  Reconcile actual content overlap rather than superseding their owners.

## Context

- [#9633](https://github.com/ThomasMichon/copilot-extensions/issues/9633) is the
  public coordination token for this bounded enhancement.
- The provider already has operator configuration, a publication-safety kernel
  and exact-session guidance. Its current configuration does not express a
  sparse matrix of signature templates and placement.
- #5121 deliberately established the newer contribution-audience policy.
  #2965's older repository-audience proposal is not an instruction to restore
  blanket private-repository or ownership exemptions.
- Runtime source/commit attribution, sending approval and disclosure are
  distinct responsibilities. Required generated/offline policy delivery is not
  an independent policy fork.
- The [guidance inventory](guidance-inventory.md) records the source sweep,
  retained references/fallbacks and generic adopter reconciliation boundary.

## Request

**Public-safe summary of the operator's multi-round request:** enhance this
plugin with configurable attribution signatures, and sweep this suite and a
selected adopting harness for duplicate guidance. Reconcile the systems so the
adopter's goals are configuration of the upstream owner.

The requested model is a sparse **domain x visibility x activity** grid, with
per-item header/footer slots, intelligent defaults and interpolation. Domain
means a service/surface family. Shared templates are overridable through
trusted personal configuration. Emit the effective configuration as lightly
formatted Markdown in session guidance, leaving agents latitude to apply a
marker to the actual circumstance. The same data should support an optional
pure programmatic decorator with deduplication.

The confirmed initial scope includes the resolver, session guidance and pure
decorator, **not automatic publication/sending integration**. Adopt the upstream
decision authority rather than retaining an adopter policy fork.

Private identifiers and adopter-specific context are intentionally absent.

## Plan

### Phase 1 - Review the bounded contract
- [x] Establish the public issue and visible claim without taking over #2965.
- [x] Reconcile the guidance vision, current provider and duplicate inventories.
- [x] Land this proposal through normal review before implementation.

### Phase 2 - Implement the canonical profile
- [x] Add sparse surface/visibility/activity selection and header/footer
  templates, shared defaults, trusted overrides and bounded interpolation.
- [x] Render the same effective profile into concise exact-session Markdown,
  retaining the current disclosure kernel and attributable error behavior.
- [x] Provide the optional pure resolver/decorator consumer with idempotent
  marker insertion and no publishing, authentication or approval side effects.
- [x] Document configuration, rendering, inheritance, supported context and
  failure behavior; preserve legacy configuration compatibility.

### Phase 3 - Reconcile consumers and duplicate guidance
- [x] Complete the source-owned attribution inventory and classify real forks,
  fixed wording, canonical references and intentional safety/provenance copies.
- [x] Replace genuine suite duplicates with canonical-owner references or data.
- [x] Supply the selected adopter's defaults/override seam and retire its
  competing provider through the adopter's own reviewed flow.
- [x] Preserve independent sending authority, human-review truth and required
  static/offline delivery; cooperate with externally owned changes.

### Phase 4 - Validate and close
- [x] Run focused unit, hook/CLI consumer and applicable native parity checks.
- [x] Land source and consumer changes through their required review/CI gates.
- [x] Record release versus installed/running state separately; transfer any
  unapproved rollout to a named objective rather than claiming deployment.
- [x] Resolve every plan/validation item, journal outcomes and archive Done.

## Validation Plan

The following implementation/test mechanics are **agent-recommended** realization
of the confirmed scope, not additional operator-requested features.

- [x] Exercise sparse inheritance, wildcard specificity, deterministic conflict
  handling, header/footer independence and explicit empty versus missing slots.
- [x] Cover interpolation, output escaping, invalid/oversized data and review
  statements derived from actual context rather than presumed human review.
- [x] Prove guidance and programmatic output use the same effective profile;
  configuration changes invalidate any reused session-guidance computation.
- [x] Prove repeated decoration is idempotent, recognizes owned marker variants
  at intended slots and does not remove ordinary quoted content or another
  author's disclosure.
- [x] Check unchanged unconfigured/legacy behavior and operator-versus-target
  configuration authority; a presentation profile must not silently waive a
  required disclosure or authorize a send.
- [x] Exercise the actual entrypoints in disposable configuration/session roots,
  including cold/warm guidance and available native shell paths. Record what
  was exercised and any unavailable lane explicitly.
- [x] Audit both inventories after migration: no competing selected provider,
  no new policy copies, no lost offline floor or provenance metadata.
- [x] Pass touched Python lint, repository documentation/effort/vision guards,
  changefile checks and required exact-head CI/review. Add no exhaustive native
  matrix to the required unit-only portfolio.

## Proposal

**Agent-recommended shape:** keep the existing policy authority and legacy
configuration. Add one bounded, versioned data model and shared resolver with a
JSON-first, dependency-light consumption path. Define slot inheritance and
selector ambiguity explicitly; do not rely on config key ordering. Templates
are data, never executable expressions or additional agent instructions.

The guidance renderer and optional decorator consume that same model. Keep the
startup kernel small; a large profile must not displace required safety
guidance. The decorator changes only the supplied draft, before final
preview/approval, and never modifies a published item or grants permission.
Review-specific wording requires actual review context. Adopter defaults remain
data owned by the adopter; personal overrides remain private.

Resolve concrete configuration/entrypoint details against the existing
dependency-free emitters and guidance cache before coding. Do not add a daemon,
network dependency, parallel policy registry or mandatory sibling plugin.

## Journal

### 2026-10-30 - Kickoff and ownership reconciliation
- Operator confirmed the slug, service/surface domain axis, resolver/guidance/
  optional-helper scope and upstream decision authority.
- Created and claimed #9633. Read #2965 and retained its ownership and newer
  policy reconciliation rather than executing superseded checkboxes.
- Read-only audits are recorded in the linked inventory. No implementation or
  rollout has begun.

### 2026-10-30 - Proposal reviewed; canonical-profile implementation admitted
- #9637 merged after an approving Copilot review and required green checks.
  The worktree was pulled forward onto the merged proposal.
- Phase 1 is complete. The next slice is Phase 2, implementing the shared
  profile/resolver and its two consumers before adopter reconciliation.

### 2026-10-30 - Shared implementation and focused consumer validation
- Implemented JSON profile discovery/selection, header/footer inheritance,
  literal interpolation, truthful review context and pure idempotent draft
  decoration in one standard-library module. The existing policy authority,
  legacy configuration and static safety floor remain unchanged.
- Exact-session guidance consumes the same profile. Profile-enabled launches
  bypass native warm-cache shortcuts and load current data after the cached
  policy kernel; unconfigured native warm paths remain intact.
- Contained suite passed 137 tests with 6 platform skips. A focused rerun after
  profile diagnostics and byte-budget fixes passed 20 tests, including the
  actual JSON helper and Windows wrapper with add/edit/remove configuration.
  New effectful probes are explicit T3 tests, not required unit-only CI growth.
- Python high-signal lint, documentation consistency and module-size checks
  passed. Design review, exact-head CI/review, publication and dependent adopter
  reconciliation remain open. No runtime rollout or human sending occurred.

### 2026-10-30 - Freshness/performance refinement and review
- Independent design review found no blocking defects. Its redundant root
  lookup observation was fixed: cache/profile consumers reuse one lookup per
  writer invocation, pinned by a work-count test.
- An initial native measurement exposed avoidable configured-warm Python
  startup. Native wrappers now reuse separately cached rendered profiles only
  after validating the complete ordered input roster and bounded file digests.
  Add/edit/remove/reorder inputs and warm operation without Python are covered.
- Eight isolated Windows wrapper samples completed: cold unconfigured 4.096s;
  warm unconfigured 1.0362/0.9829/1.1261s; first profile 1.8791s; warm profile
  1.526/1.3017/1.3342s. Warm medians were 1.0362s and 1.3342s respectively.
  These small samples do not qualify tails or composed session startup, and
  the cold sample does not meet the two-second composed-hook target.
- The final broad run passed 142 tests with 6 skips, but revealed a Windows
  pytest environment-length setup/teardown error from an oversized parameter
  ID. Explicit short IDs fixed it; the final affected packet passed 41 tests,
  including the formerly blocked oversized-file case, native cache freshness
  and real helper subprocesses. High-signal lint passed.
- A second independent review scoped to the cache refinement found no issues.
  Disposable fresh HOME/session fixtures cover first touch; no installer or
  external deployment changed. Native Linux/macOS execution and whole-session
  composition were not exercised locally; publication does not claim them or
  authorize a rollout. The new effectful probes remain explicit-only.
- Final full native-inclusive rerun after the short-ID fixture correction:
  **143 passed, 6 platform skips**. This supersedes the earlier failing attempt.

### 2026-10-30 - Implementation PR and source-guard correction
- Opened #9646 against `dev`. The source guard caught the refactored Git
  subprocess without Windows console suppression; both writer child-process
  paths now use the standard guarded headless flag.
- The repository headless guard passed and the final affected consumer packet
  passed 42 tests, including a direct process-kwargs regression. High-signal
  lint passed. Exact-head CI and Copilot review still gate merge.

### 2026-10-30 - Automated review findings corrected
- Preserved expanded lexical instruction-directory paths for safety checks,
  resolving copies only for target containment. Symlinked paths are rejected
  before loading, including missing-file paths through unsafe ancestors.
- Restricted templates to visible text/Markdown presentation, rejecting raw
  HTML, hidden/active markup and invisible formatting. Punctuation-only slots
  retain the visible disclosure baseline.
- Current markers take precedence over overlapping historical variants;
  replacement is at most once per nonempty slot, preserving draft content and
  idempotence even when a slot is suppressed.
- Native cache reuse now validates environment identity and present/missing
  probes produced by the single Python discovery owner, rather than repeating
  its normalization, deduplication and containment logic. The cache format
  migration invalidates older rendered-profile records.
- All four reported reproductions are covered. The final affected packet
  passed 54 tests with 3 platform skips, including warm operation without
  Python under duplicate/target-contained directories. Lint, documentation
  consistency, module-size and Bash syntax checks passed.
- The actual adopter profile fits the canonical session budget: 2,675 bytes
  including the real policy kernel and profile, below 4,096 bytes.

### 2026-10-30 - Conservative budget fallback and assembled-data validation
- The second review exposed a tightening loss when valid profile guidance did
  not fit. A compact disclose-all header now overrides baseline exemptions until
  the profile is resolved, including when no diagnostic notice fits. Cache
  format 3 preserves that mode in both native wrappers.
- Rejected shortcut/reference image openers, Unicode control/format characters
  in literal variables, and review claims assembled across template/variable
  boundaries, including historical variants. Both consumers share the latter
  validation without treating escaped names as template markup.
- Deep JSON is rejected explicitly instead of dropping the safety kernel.
  CLI/cache readers handle the same recursion failure class.
- Focused validation passed 68 tests with 3 platform skips, including both
  budget cases and real native warm-cache reuse with Python absent. High-signal
  Python lint passed. Fresh review and exact-head CI still gate merge.

### 2026-10-30 - Interpolation and discovery boundary review
- Applied markup checks to assembled slots and historical variants in both
  consumers, retaining escaped literal names and actual review-status text.
  Escapes are interpreted across interpolation boundaries rather than allowing
  an empty variable to assemble a hidden image marker.
- Matched the native policy owner's 128-entry/65,536-character discovery
  limits before probing. Overflow and missing-user expansion failures become
  bounded diagnostics; they cannot suppress the policy kernel.
- Cached discovery records the exact native home-environment representation,
  avoiding Python/reader normalization disagreement on trailing separators.
- Focused validation passed 87 tests with 3 platform skips, including assembled
  markup in all four slot/variant fields, discovery failures, and native
  warm reuse without Python under plain and trailing-separator home values.
  High-signal lint passed; fresh review and exact-head CI remain required.

### 2026-10-30 - Incomplete cache cannot bypass failed discovery
- Removed legacy base-only native reuse. Only a completed Python-owned
  discovery snapshot is eligible, including for an unconfigured launch.
  Consecutive launches with 129 missing absolute directories now both preserve
  the kernel, diagnostic and conservative disclosure header.
- Focused validation passed 87 tests with 3 platform skips. Warm no-Python
  fixtures now originate in the real Python owner rather than synthetic
  base-only cache records. Lint passed.

### 2026-10-30 - Boundary overlap and file-kind safety
- Determined both owned draft boundaries before inserting either slot.
  Overlapping removals and removals crossing the opposite current marker are
  preserved, preventing cross-slot deletion and retaining idempotence.
- Native missing probes now check lexical ancestors too, detecting a new
  symlink/junction even when the target still lacks the candidate file.
- Profile loading rejects non-regular candidates before opening and verifies
  the opened descriptor; nonblocking/no-follow flags protect supported systems.
- The affected packet passed 91 tests with 4 platform skips. The real Windows
  missing-parent junction regression passed for both HOME forms. A portable
  pre-open guard test passed; the real FIFO regression is POSIX-only and was
  skipped locally. High-signal lint passed.

### 2026-10-30 - Excluded-path dependencies and precedence
- Containment exclusions now retain lexical safety probes, and unsafe paths
  are rejected instead of being silently excluded. Native reuse cannot miss a
  directory changing into or out of a symlink/junction.
- Rejected unbalanced bracket fragments that could form hidden markup across
  slots/draft content. Duplicate profile paths retain their last occurrence,
  preserving final personal override precedence.
- Focused validation passed 93 tests with 4 platform skips, including real
  native exclusion/junction transitions in both HOME forms. High-signal lint
  passed. The current review gate remains open until the corrected revision
  receives its verdict and required checks.

### 2026-10-30 - Batched native probe parsing
- Removed Bash's three JSON-parser children per probe and child process per
  ancestor. One NUL-delimited parser pass handles all probes; shell builtins
  walk ancestors without losing path whitespace/control boundaries.
- Added an actual native work-count regression comparing one and 128 missing
  instruction directories with Python absent. It requires equal bounded
  parser counts and zero ancestor-tool launches.
- Local affected validation passed 93 tests with 5 platform/tool skips; Bash
  syntax and high-signal lint passed. The new POSIX work-count case is skipped
  on this Windows host, so no native Linux count/latency result is claimed.
  The existing periodic lane does not enroll this explicit-tier family.

### 2026-10-30 - Canonical source merged; adopter integration next
- #9646 merged as `6e06e9fb9a9b1973d8a0acacb73e9ca937585d5c` after the
  exact-head Copilot Comment had no open issues and all required checks passed.
  Every concrete review finding is corrected and its thread resolved.
- The controlled source worktree is on the squashed `dev` result; its provider
  and effort contents were verified identical before dropping replay of the
  already-merged commits.
- Source implementation is complete. The continuing objective is release-backed
  adopter configuration/offline delivery, reviewed adopter publication, and
  final effort closure. Release is not installation or activation; unapproved
  rollout remains with the adopter's named operational follow-up.

### 2026-10-30 - Release and isolated adopter validation
- Release `0.1.5-dev1` is available on regenerated `main` commit
  `68046a625657022a250bfcce9cbe25976c6596d2`. The shared provider and both
  native readers were verified byte-for-byte against the merged source,
  rather than inferring inclusion from ancestry.
- The selected adopter generated canonical selectors/fallbacks through the
  official scoped projection API; unrelated lock entries were unchanged.
  Offline scanning has no blocking findings; aggregate-budget/overlap
  advisories are reported rather than promoted into a new publication gate.
- Eight actual released-helper cases passed for default/private overrides,
  slots, truthful review context, exemption and idempotence. The real Windows
  hook passed cold and warm-without-Python checks with the adopter profile and
  a synthetic private override: 2,782/4,096 bytes.
- No installation, runtime activation or human send was performed. The
  adopter-owned canonical-provider activation objective retains unapproved
  rollout. Adopter review/CI/publication and final archive remain open.

### 2026-10-30 - Complete and archive
- The selected adopter's migration merged after a clean current-head review,
  required full validation and provenance readback. Its committed selection
  now has one upstream owner; presentation defaults/private overrides are
  data, not a local decision matrix or wording injector.
- Reconciled remaining toolset skill prerequisites and declared canonical
  dependencies. The post-migration inventory preserves intentional unattended
  fallbacks, offline delivery, source/commit provenance, exact-draft review
  truth, independent approvals and externally owned work.
- Source #9646 and the reviewed adopter change are merged; `0.1.5-dev1` is
  released. Every Plan and Validation Plan item is resolved. Native
  Linux/macOS qualification remains explicitly unclaimed; installation and
  activation are outside this source scope and retained by the named
  adopter-owned canonical-provider activation objective.
- No human send, replay or runtime rollout was performed. Archive this
  completed effort; #2965 and #9627 retain their independent ownership.
