# Knowledge Projection Preparation

- **Slug:** `knowledge-projection-preparation`
- **Repo:** copilot-extensions
- **Branch(es):** Independent proposal, implementation, and completion PRs to `dev`
- **Created:** 2026-10-30
- **Status:** Active
- **Vision:** `harness-guidance`: `simple-local-static-delivery` and `generated-output-is-disposable-source-is-not`
- **Umbrella issue:** #9712
- **Sub-issues:** None initially

## Guiding Intent

Preparing a paired knowledge worktree should establish the local Git-ignore
prerequisite for disposable generated instructions. Operators should not need
to discover a skipped projection and append an exclude rule manually.
Reuse the native publisher's ownership and safety checks rather than creating
a competing ignore writer or changing committed repository policy.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Proposal, implementation, validation, and PR stewardship | Managed source worktree; public updates on #9712 |

## Coordination

- **Topology:** Independent per-phase PRs.
- **Host (owns PRs):** Source driver.
- **Delegates:** None.
- **Handoff:** Journal evidence and any unfinished acceptance here; retain the
  issue until all source-delivery gates are resolved.

## Context

The native publisher in
`plugins/agent-worktrees/src/agent_worktrees/static_instructions.py` already
resolves the repository's real Git exclude file, preserves its contents, adds
the reserved generated namespace, and verifies effective ignore coverage.
`worktree_creation.py` carves the companion and `knowledge_plugins.py` composes
the harness overlay, but companion preparation does not establish that
prerequisite. The compatibility renderer consequently warns
`projection-local-cache-ignore` and skips files in a fresh companion checkout
without checked-in ignore coverage.

The relevant existing contract is
`docs/patterns/worktree-scoped-dynamic-guidance.md`: generated copies are
disposable, Git-ignored output; checked-in projections and reviewed fallbacks
remain independent. This effort closes that existing intent rather than
extending policy. Existing source-resolution and startup-delivery efforts retain
their broader ownership; this is only paired companion ignore preparation.

## Request

The operator clarified:

> I mean, do we need to modify agent-worktrees code that preps the knowledge repo pairing to perform that work?

Then requested:

> Make an effort to drive that

The operator confirmed the slug `knowledge-projection-preparation`.

The referenced work is automatic local Git-ignore setup for generated
instructions during paired knowledge-worktree preparation. No plugin retirement,
message sending, runtime deployment, global registration cleanup, or new
standalone knowledge session is included.

## Plan

### Phase 1 - Review the bounded proposal

- [x] Confirm existing issue/effort ownership and trace the native publisher,
  companion creation, composition, and supported repair/resume entry points.
- [x] Land this proposal through the repository's required review flow before
  implementing the change.

### Phase 2 - Prepare the companion through the existing owner

- [x] Reuse or narrowly extract the publisher-owned local-ignore operation and
  call it at the validated paired knowledge-worktree preparation boundary.
- [x] Preserve existing exclude contents and effective ignore rules, linked
  worktree/common-Git-directory semantics, tracked output protection, and
  symlink/reparse and bounded-file safeguards.
- [x] Keep failures visible and preparation retryable without changing
  repository-tracked files, plugin activation, trust, or pair/session custody.
- [x] _(agent-recommended)_ Document the prerequisite at the existing owner and
  include the mandatory plugin changefile; avoid a second policy/config surface.

### Phase 3 - Qualify and close source delivery

- [ ] Complete the Validation Plan and land the reviewed implementation with
  required CI green.
- [ ] Record release status accurately without installing or activating a
  runtime; any operational rollout requires separate authorization.
- [ ] Resolve all plan items, archive this effort as Done, close the coordination
  issue, and finalize its controlled source worktree.

## Validation Plan

The following specific test coverage is _(agent-recommended)_ to demonstrate
the requested automatic preparation rather than merely testing a helper.

- [x] Regression: a fresh paired knowledge worktree without committed ignore
  coverage gets the reserved local exclude through its real preparation path,
  then produces its declared projection instead of skipping it.
- [x] Repeat preparation without duplicate rules, lost existing exclusions, or
  tracked working-tree changes; exercise an actual linked/bare-backed worktree.
- [x] Cover existing ignore coverage, invalid/unwritable or indirect exclude
  paths, tracked generated destinations, and a visible retryable preparation
  failure without weakening native safety checks.
- [ ] Run focused owner/lifecycle tests, Python lint, install-contract checks,
  and required repository CI for the final candidate.
- [x] Run a disposable first-touch consumer check using real Git and publication;
  record platform limits and the reason for any unavailable external lane.
- [x] Review final docs and public artifacts for accurate ownership, generic
  examples, and absence of private identifiers or operational rollout claims.

## Proposal

Prefer the smallest integration at the existing validated paired preparation
boundary. The native publisher remains the authority for local generated-output
handling. Do not solve this by invoking the checked-in projection sync,
duplicating a renderer, or unconditionally publishing the companion's plugins
into the harness.

## Journal

### 2026-10-30 - Kickoff

- Confirmed target effort adoption and the operator-selected slug.
- Searched open issues and PRs; no matching companion-ignore repair was found.
- Opened #9712 and reserved this bounded work.
- Source inspection found that the native publisher already owns exclude
  preparation; the companion lifecycle lacks the prerequisite.
- Proposal only. Implementation awaits review and merge.

### 2026-10-30 - Proposal accepted

- #9713 merged after exact-head Copilot approval with no open findings and
  required CI green.
- Pulled the source worktree forward. Phase 2 is the next owned slice;
  no runtime rollout is authorized.

### 2026-10-30 - Companion preparation implementation

- The validated worktree-pair composition path now prepares the companion
  inside the existing overlay transaction, after marking the harness pending.
  Successful preparation/composition clears that marker; failure preserves it.
  Disabled plugin composition still prepares disposable output, without
  enabling plugins; anchor companions remain untouched.
- Extracted namespace/index/exclude preparation from the native publisher;
  both callers share its only append/check-ignore owner and publication lock.
  Companion preparation never discovers or publishes a companion source set.
  Exclude reads reject indirection/nonregular files and are bounded to 2 MiB.
- Updated the existing dynamic-guidance pattern and generated an agent-worktrees
  patch changefile through `tools/changefile.py`; no hand version edits.
- Regression proof: the initial contained fresh-companion case failed at
  real `git check-ignore` after successful composition, before implementation.
  The bare-linked fixture initially needed explicit Git-directory selection;
  both fresh and bare-linked cases subsequently passed.
- Focused first-touch/safety qualification:
  `py -3.11 tools/run-plugin-tests.py agent-worktrees --test-file tests/test_local_cache_refresh.py -k 'companion or real_knowledge_only or changed_git_protection_link_target' --timeout 180 --plugin-timeout 240`
  passed 15 tests. It exercises real Git, repeated composition, retained
  excludes, unchanged tracked state, both compatibility/native publication,
  unsafe/unwritable/oversized excludes, tracked outputs, negation, retries,
  disabled composition, and anchor preservation.
- Expanded qualification across those owners plus creation repair reached its
  600-second runner bound. Separate diagnosis found two failures in untouched
  `test_creation_tail_repair.py`:
  `test_creation_repair_preserves_only_proven_local_parent_refs[relative-False]`
  and `[relative-True]` call `os.path.relpath` across different Windows drives,
  raising before composition. The focused reproduction reported 2 failed,
  131 deselected. This is not a clean expanded result; no unrelated repair
  or tracked exception has been claimed.
- Install contract: `py -3.11 tools/check-install-contract.py` passed for all
  13 plugins. Touched Python lint (`ruff check --select F,E9`) passed for the
  three source modules and two test files. Ruff was unavailable locally and
  offline, so its one-shot dependency was resolved with a seven-day cutoff
  into disposable tooling caches, then removed.
- Final changed-owner qualification:
  `py -3.11 tools/run-plugin-tests.py agent-worktrees --test-file tests/test_local_cache_refresh.py --test-file tests/test_knowledge_plugins.py --test-file tests/test_static_instructions_standalone.py --test-file tests/test_creation_pair_completion.py --timeout 600 --plugin-timeout 720`
  passed **211 tests**, with **10 platform skips** (4 POSIX FIFO and 6
  Linux-native process cleanup cases), in 310.92 seconds of pytest execution.
- Real creation/repair entry points:
  `py -3.11 tools/run-plugin-tests.py agent-worktrees --test-file tests/test_creation_tail_repair.py -k 'test_creation_real_cli_first_touch_report_and_fix or test_creation_restores_native_pair_permissions_and_is_idempotent or test_creation_repairs_original_native_pair_after_optional_naming' --timeout 240 --plugin-timeout 300`
  passed **12 tests**, with 121 deselected, in 169.95 seconds. These supplement
  the direct validated-pair regression with real first-touch CLI and retained
  native-pair recovery paths.
- Final public-artifact inspection found no private identifiers, deployment
  claims, manual version edits, or tracked consumer-policy edits. The
  composition module remains below the 1,000-line source cap. Changes are
  implementation-ready, not published or shipped; the expanded cross-drive
  fixture failure remains explicitly reported above.
- Validation tiers: the regression cases are disposable first-touch consumer
  checks, not installed-runtime rollout. Linux devcontainer/native FIFO lanes
  are unavailable on this Windows worker. No external venue provisioning or
  operational rollout is authorized. Required CI, publication/review/merge,
  release-status recording, issue closure and effort archival remain open.

### 2026-10-30 - Coordinator verification

- Reviewed the final implementation and portable fixture correction.
- Independently reran the 15 focused companion/safety cases through the
  contained plugin runner: all passed.
- Ruff F/E9 passed for all six touched source/test modules, including the
  corrected creation-tail fixture. Install-contract checks passed for all
  13 plugins.
- Phase 2 is complete. Phase 3 owns the remaining review, required CI,
  source delivery, release-status recording, and archival gates.

### 2026-10-30 - Exclude append boundary correction

- Review identified that appending a rule could grow a valid exclude past its
  2 MiB read limit. The shared owner now checks the final UTF-8 byte size before
  effects and appends explicit platform-newline bytes, without text translation.
- Eight new regressions cover LF/CRLF, an exact-fit append and one-byte overflow,
  multibyte existing contents, both companion preparation and native publication,
  unchanged bytes on refusal, and a valid subsequent preparation.
- The changed-owner run passed 218 tests with 10 platform skips; its only
  failure was the permission fixture intercepting text append instead of the
  new binary append. The fixture now intercepts every append mode.
- All 14 boundary/denial cases subsequently passed through the contained runner.
  No unresolved test failure remains; touched Python lint and staged patch
  checks passed. Required CI and review must be refreshed for the new head.

### 2026-10-30 - Fix-forward for cross-drive fixture qualification

- Corrected the earlier stopping decision: an encountered, untracked fixture
  failure requires fix-forward, not documentation alone.
- Both relative-parent cases computed `os.path.relpath` against an unrelated
  runner working directory. On Windows, fixture and source drives can differ,
  so construction raised before exercising parent validation. The relative
  case now changes cwd to its own disposable `tmp_path` first. The same real
  relative topology, rejection assertions, both identity variants and Windows
  coverage remain intact; no skip or production-policy change was introduced.
- Reproductions:
  `py -3.11 tools/run-plugin-tests.py agent-worktrees --test-file tests/test_creation_tail_repair.py -k 'test_creation_repair_preserves_only_proven_local_parent_refs and relative' --timeout 180 --plugin-timeout 240`
  passed **2 tests**, with 131 deselected, in 4.36 seconds. These are the same
  two cases that previously failed before their behavioral assertions.
- The existing agent-worktrees patch changefile covers this coupled test
  correction; no additional plugin, version edit or duplicate changefile is
  needed. Prior implementation and proposal-acceptance edits remain preserved.
- Complete affected-file qualification:
  `py -3.11 tools/run-plugin-tests.py agent-worktrees --test-file tests/test_creation_tail_repair.py --timeout 900 --plugin-timeout 1080`
  passed **133 tests**, with **no skips**, in 539.39 seconds of pytest
  execution (557.73 seconds including runner preparation/cleanup). The larger
  explicit bound completed normally; no further failures were encountered.
- The fixture correction passed Python AST syntax validation and staged
  `git diff --cached --check`. Ruff is not available in the follow-up
  environment; the no-install instruction was respected rather than restoring
  it. Earlier five-file lint and install-contract results remain recorded
  above; the new one-line fixture change is covered by the complete file run.
- The previously encountered cross-drive failures are now resolved by this
  staged fix. Source publication, required CI, review/merge, release tracking,
  archival and operational rollout remain outside this implementation slice.
