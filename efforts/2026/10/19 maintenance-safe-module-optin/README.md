# Maintenance-Safe Module Opt-In

- **Slug:** `maintenance-safe-module-optin`
- **Repo:** copilot-extensions
- **Branch(es):** sequential proposal, implementation and completion PRs against `dev`
- **Created:** 2026-10-18
- **Status:** Done
- **Vision:** installer `legible-and-consent-driven`; machine-fleet existing service authorities
- **Umbrella issue:** #7213

## Guiding Intent

Allow an adopter to deliberately include individually trusted repo-local modules
in the existing maintenance-safe restore without weakening its default-off
boundary or treating arbitrary commands as inherently safe.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation owner | Proposal, implementation, validation and PR stewardship | #7213 |

## Coordination

- **Topology:** One writer, sequential PRs against `dev`.
- **Host (owns PRs):** Implementation owner.
- **Delegates:** None.
- **Handoff:** Preserve this effort and #7213 if a concrete blocker prevents completion.

## Context

Resources already declare an optional `maintenance_safe` boolean. Modules have
no corresponding opt-in, so blanket maintenance-safe restore skips every module.
The current implementation explicitly anticipates a future per-module opt-in.
This closes a narrow consent-controlled reconciliation delta, consistent with
the installer vision's `legible-and-consent-driven` behavior and the
machine-fleet vision's existing reconciliation authority. No new service,
scheduler, dependency or authority is introduced.

The patterns' right-sized surface, cross-platform parity and fail-loud principles
apply. Existing resource semantics and trusted source admission remain unchanged.

## Request

Public-safe capture of the requested capability:

> Add deliberate validated per-module `maintenance_safe: true` support to existing
> `restore --maintenance-safe`. Omitted/false and malformed flags must NOT gain
> unattended execution. Existing resource behavior, normal restore, explicit
> selection semantics, dry-run guarantees, platform/machine gates, environment
> isolation, failure propagation, conflict handling, and trusted source boundaries
> must remain intact. No new scheduler, daemon, arbitrary implicit opt-in, or
> automatic opt-in of existing modules.

This is declaration-controlled selected trusted code, not a claim that arbitrary
scripts are inherently safe. Runtime rollout and adopter configuration changes
are outside this implementation objective.

## Plan

### Phase 1 — Reviewed contract
- [x] Land this proposal through the normal PR review gate before implementation.

### Phase 2 — Implementation
- [x] Validate the optional module flag as a strict boolean; explicit null and
  other nonbooleans are errors.
- [x] Execute only opted-in applicable modules during blanket maintenance-safe
  restore, reusing existing execution and dry-run paths; preserve explicit selection.
- [x] Update owning schema/documentation/skill surfaces and add a plugin changefile.
- [x] Add focused regression coverage and land the implementation through review.

### Phase 3 — Completion
- [x] Record merged implementation and validation outcomes; close the implementation
  issue and effort, retaining automatic promotion and authorized rollout as
  separate release/adopter responsibilities.
- [x] Deferred to `#7247`: investigate unexplained historical native Windows stamp
  item-limit expirations without weakening native coverage or original bounds.

## Validation Plan

- [x] Cover omitted, false, true, null and other malformed module flags.
- [x] Cover normal restore, dry-run with/without preview support, mixed safe/unsafe
  modules and explicit selection without executing unselected commands.
- [x] Preserve machine/platform gates, conflicts, failure propagation and
  invocation/environment behavior with focused and full agent-machines suites.
- [x] Stage new tests before contained execution; run the documented bounded
  runner, relevant lint, documentation guards and install-contract gate.
- [x] Verify generic public artifacts, passing required checks and real Copilot
  review before each merge. Do not claim release or rollout from a `dev` merge.

## Proposal

The flag is optional and defaults off. Only the actual boolean `true` admits a
module to blanket maintenance-safe execution. Parsing rejects every explicitly
declared nonboolean. The existing executor retains command construction, source,
machine/platform, dry-run, timeout and error behavior. Explicitly named modules
retain the operator-directed exception. No existing module gains an opt-in.

## Journal

### 2026-10-18 — Proposal
- Claimed #7213; inspected module exclusion and resource flag prior art.
- Reconciled consent-driven behavior and existing reconciliation authority.
- Implementation has not started; proposal review is the next gate.

### 2026-10-18 — Reviewed plan
- Proposal #7214 merged as `262ec7ab94df714512dfdad64f5bd7a233b04554` after
  Copilot approval and passing required checks; execution builds on that plan.
- Inspection found module `--only` filtering happened after command execution.
  Move selection ahead of execution as part of preserving the stated contract;
  regression tests must prove unselected commands never run.

### 2026-10-18 — Implementation and validation blocker
- Implemented strict boolean module consent, default-off maintenance execution
  through the existing executor, and pre-execution explicit module selection.
- Updated the parser contract, CLI help, module documentation, maintenance
  documentation, setup/restore skills and plugin changefile.
- The contained focused modules/reconcile-resources/authority/resources lane
  passed: 265 tests. Python `ruff check --select F,E9` passed; install-contract
  passed for all 13 runtime plugins; the documentation-consistency guard passed.
- The contained full agent-machines suite timed out in the unchanged
  `tests/test_cell_lifecycle.py::test_adapters_repair_uninstall_preserve_isolate_replay`,
  while its adapter helper waited for a native subprocess at line 101, invoked
  from line 128. A focused contained retry reproduced that timeout. This is a
  validation blocker, not a demonstrated root cause or permission to relax bounds.
- Implementation publication is blocked. Next action: resolve the native
  lifecycle-test timeout within separately authorized diagnosis, rerun the full
  bounded suite, then publish the implementation PR and drive review/checks to
  merge. No implementation release or runtime rollout has occurred.

### 2026-10-18 — Native phase measurements and subsequent validation blocker
- Standard commit attribution trailers are intentional; no history rewrite is
  needed. Implementation and ordinary review remain authorized.
- Measured native lifecycle phases under unchanged bounds: repair 2.797s,
  healthy repair 2.588s, uninstall 11.484s and replay 2.499s. The original
  compound item accumulated fixture and multiple adapter costs; no individual
  native phase hung.
- Split the lifecycle contract into independent transition cases, retaining a
  real native subprocess for every transition and the receipt, peer-isolation,
  durable-state, runtime/snapshot removal and replay assertions. Healthy/uninstall
  setup uses the direct lifecycle engine. Replay starts from the equivalent
  empty owned-cell state verified by uninstall rather than paying for an entire
  prior lifecycle. No timeout, skip, containment or runtime policy changed.
- Full bounded validation passed the lifecycle prefix, then timed out in
  `test_installation_repair_installed_cli_native_first_touch` during its apply
  process wait. Its focused unchanged-budget retry passed (17.07s call).
- One complete retry using supported five-file groups passed group 1
  (185 passed, 2 skipped), including the corrected lifecycle cases, then hit a
  different item timeout:
  `test_installation_repair_admits_manifest_declared_readiness_path`,
  `test_installation_repair.py:325`, during `_readmit_owner` and
  `plugin_activation.resolver.resolve_active_plugins`, with the stack in Windows
  `Path.resolve` / `ntpath.realpath`. This does not establish a deadlock or a
  module-consent regression. No further broad retries or unrelated repairs.
- Focused lint and install-contract remain passing. Full validation and
  implementation PR publication remain blocked on native repair-portfolio
  diagnosis. Related harness tracker #5245 is not an accepted-flake waiver.
- Next action: obtain bounded diagnosis/repair ownership for this newly exposed
  native repair-portfolio failure, achieve a complete normal-budget validation,
  then publish and steward the implementation PR. Release and adopter rollout
  remain separate, with no runtime update performed.

### 2026-10-18 — Manifest-path scope alignment and distinct fixture failure
- One focused diagnostic measured the original manifest-path case at 9.76s,
  including nine real native actions and five owner re-admissions. No individual
  hang or fixture-isolation root cause was established.
- Deliberately aligned the path-specific test to an independent selected child
  using `configure(dependencies=())` before manifest relocation. Added exact
  selected-child outcome assertions to both plan and apply, retaining real native
  actions and status/readiness/attempt assertions. This is scope alignment, not
  a proven timeout root-cause fix.
- Focused bounded manifest-path plus dedicated native dependency coverage passed:
  2 passed, 66 deselected.
- The single authorized complete five-file-group run passed groups 1–4:
  185/185/23/110 passes respectively, with 2/22/8/0 existing skips. Group 5
  completed with 122 passes, 4 skips and four failures in
  `test_real_init_stages_and_watchdog_preserves_124`, covering both PowerShell
  hosts and success/watchdog variants. Each exited 1 with
  `CommandNotFoundException` in `init.ps1`, rather than expected 0 or 124.
  Groups 6–7 did not execute; this is not a complete pass.
- Source inspection identifies a concrete fixture gap: `init.ps1:46` requires
  `versioned_runtime_native.ps1` beside itself, but the isolated test payload at
  `test_payload_installer_lifecycle_powershell.py:245–264` writes `init.ps1`,
  its probe stub and plugin manifest without copying that required helper.
  No additional run or edit of that fixture was performed.
- Decision point: authorize a narrowly scoped fixture dependency copy and its
  validation, or separately route this blocker. Implementation publication
  remains blocked. No further broad retries, deadline changes, skips, test
  reductions, runtime edits or rollout.

### 2026-10-19 — Required helper copied; final group concurrency blocker
- Confirmed `versioned_runtime_native.ps1` is a self-contained adjacent helper
  with no dot-source dependencies. Copied only that real source file into the
  isolated native/watchdog fixture; no blanket scripts-tree copy or runtime edit.
- The four focused success/watchdog variants passed under their unchanged native
  limits: 4 passed, 22 deselected.
- Used the runner's supported explicit file selectors to run all 15 files from
  original groups 5–7, retaining settled unchanged groups 1–4. Original group 5
  passed (126 passed, 4 skipped); group 6 passed (270 passed, 22 skipped).
  Together with prior groups 1–4, settled coverage is 899 passed, 58 existing
  skips. That is not a complete portfolio pass.
- Original group 7 hit an item timeout in
  `tests/test_stamp_binstub_two_stage.py::test_windows_concurrent_stamp_binstub_leaves_no_torn_binstub`.
  Its caller at line 254 was in `_assert_no_torn_reads_while_running` line 229,
  waiting in `proc.communicate(timeout=60)`; a concurrent poller was at line 224.
  The captured main stack was in the stdout reader join. This is not proof of a
  deadlock or authorization to raise the item limit.
- No further retry, reduction or concurrency-fixture edit followed. Next decision
  is bounded ownership/diagnosis of this distinct final-group blocker, retaining
  real concurrency and no-torn-read coverage. Implementation publication and
  release remain blocked; no runtime rollout occurred.

### 2026-10-19 — Bounded diagnostic and final-group retry decision
- One authorized temporary helper-only diagnostic retained all three native
  writers, the real observer and original item bound. Both host cases passed:
  Windows PowerShell roots completed by 14.711s with 2,164 observer reads;
  PowerShell 7 roots completed by 13.395s with 1,426 reads. Every root exited 0;
  there were zero torn-read violations. No exited-root/persistently-blocked-drainer
  state was observed. The internal native stage was not observable from the
  captured root metadata. This does not explain the original timeout.
- Removed the instrumentation and verified the clean original implementation
  head `e01f19e5134d0c6260c519379d28c64abd24e80e`.
- One authorized canonical bounded run selected all five original group-7 files,
  preserving groups 1–6 rather than rerunning them. It failed before reaching
  the concurrency case: `test_windows_stamp_binstub_leaves_a_usable_payload_dir_marker`
  expired at the item bound while its line-146 native `subprocess.run` awaited
  `init.ps1 -Action stamp-binstub`. The main stack was in the stdout reader join;
  the underlying subprocess timeout remained 60 seconds.
- No root cause, accepted-flake waiver or successful complete group is claimed.
  Settled coverage remains 899 passed, 58 existing skips across original
  groups 1–6; focused diagnostic passes are not a substitute for group 7.
- Stopped for an operator decision, with no additional remedy, retry, source
  change, release or rollout. The original concurrency timeout remains unresolved
  reliability evidence alongside this earlier marker-case timeout.

### 2026-10-19 — Stage evidence and complete bounded portfolio
- Additional authorized stage tracing measured the real init/probe/resolver
  chain through successful native completion, including process CPU time.
  Concurrent Windows PowerShell governance occupied about 6 seconds per root,
  versus 0.19–0.31 seconds in publication; PowerShell 7 governance occupied
  3.1–3.4 seconds versus 0.09–0.22 seconds in publication. No publication,
  inherited-pipe or resolver-compilation hang was reproduced.
- Removed every diagnostic modification and verified clean uninstrumented
  source before acceptance. Original group 7 passed: 79 passed, 5 skipped.
  All 35 original files now have complete bounded evidence across the seven
  supported five-file groups: 978 passed, 63 existing skips, with original
  item/group/plugin/process/memory limits and real native coverage retained.
- Historical timeouts remain unexplained, not silently fixed or declared
  accepted flakes. Exact-name tracker deduplication found only #7213; residual
  reliability is now separately tracked in #7247. This implementation does not
  change the stamp runtime or claim a speculative performance remedy.
- Rebased onto current `dev`, preserving both sides of effort-index-only
  conflicts. Incoming execution changes were additive peer-owner support and
  additive library APIs; existing process primitives and this capability's
  runtime/test logic were unchanged. Revalidated the affected discovery,
  identity, machine-alias and self-update consumers: 231 passed, 8 skipped.
- Implementation review/merge is the next gate. Automatic release promotion
  and separately authorized adopter rollout remain outside this coding slice.
- Final publication gates passed: touched-code F/E9 lint, install-contract
  (13 runtime plugins), version consistency and documentation consistency
  (24 plugins). Reviewed the exact runtime diff against the shared review rubric;
  no additional mutation authority or automatic opt-in was introduced.

### 2026-10-19 — Implementation merged and coding objective complete
- Implementation #7248 merged to `dev` as
  `d2f1f796101f019f60ccd4cdc85c3ee32fa0892c`; reviewed head was
  `2b716506c7ac335f3ff66d3334e77f7a6fb578a4`.
  Copilot approved that head, posted no findings, and required PR-gate,
  source-attribution and workflow-lockdown checks passed before merge.
- Closed #7213 as implemented. Re-read this effort and resolved its Plan and
  Validation Plan against the actual merged behavior and bounded test results.
  The coding objective is Done; the separate historical reliability investigation
  remains open in #7247, not hidden as an accepted flake or claimed source fix.
- Release remains pending automatic promotion owned by the existing release
  pipeline (#3336); adopter-specific rollout requires its own authorization and
  running-state proof. No runtime update, machine configuration or service
  change was performed. Archive this completed coding effort through normal
  review, then release its binding and finalize the child.
