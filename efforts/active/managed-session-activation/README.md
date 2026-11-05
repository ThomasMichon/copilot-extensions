# Managed Session Activation

- **Slug:** `managed-session-activation`
- **Repo:** copilot-extensions
- **Branch(es):** independent review-first plan and implementation PRs against `dev`
- **Created:** 2026-11-04
- **Status:** Active
- **Vision:** [Worktree agency and cooperative write guards](../../../visions/plugins/agent-worktrees/README.md#write-guards-are-hygiene-nudges-named-for-their-plain-mundane-reason); [bounded hook failures](../../../visions/harness-guidance/README.md#bounded-actionable-hook-failures)
- **Umbrella issue:** #10564
- **Sub-issues:** none

## Guiding Intent

Installing agent-worktrees globally makes its capabilities available; it must
not enroll unrelated Copilot sessions into managed enforcement or lifecycle.
Automatic behavior first establishes membership in a `projects.yaml`-managed
harness anchor or owned worktree. Positive session associations can be reused
without turning a cached ownership fact into cached permission for an edit.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source driver | Plan, admission implementation, tests and PR stewardship | Current isolated source branch |

## Coordination

- **Topology:** sequential plan and implementation PRs; one source owner.
- **Host (owns PRs):** Source driver.
- **Delegates:** none.
- **Handoff:** canonical README and the driver's explicitly adopted worktree agenda retain the parent completion gate and next action.

## Context

#10564 records global hooks and extension behavior applying outside an adopted
harness. Initial inspection identified command hooks, the Python hook client,
resident pre-tool policy and the session extension as relevant consumers.
Isolated event-replay controls now cover the reported scope failure and compare
registry-only no-ops with preserved managed-anchor denial.

This closes the existing vision's cooperative, applicable intervention intent.
It follows [circumstantial hook contracts](../../../docs/patterns/circumstantial-hook-contracts.md):
acquire reusable ownership facts first, evaluate current occurrences only in
scope, and preserve the existing managed policy rather than caching decisions.
It introduces no global adoption, daemon, network dependency or CLI-management
restriction.

## Request

Operator's initial report, verbatim:

> Bug report: since agent-worktrees gets installed globally for a user on a machine, its hooks and skills are always available in Copilot sessions in any local repo, whether registered with agent-worktrees or not. This means its hooks.json and extension kick in all the time. This can cause agent-worktrees to block edits to the user's repo due to the repo classification, such as being read-only or requring worktrees, even though the user isn't using an agent-worktrees managed harness. We need to be careful not to let the hooks or extensions "activate" unless the current session is running from a harness managed by agent-worktrees (via projects.yaml). So we need a way to check that the current session belongs to a managed worktree, before we even consider running the hook checks or other activations. Once we know a session id belongs to an owned worktree or anchor, we can then cache that for future checks.

Operator's execution request, verbatim:

> Help make an effort and drive the necessary fixes in copilot-extensions; use an agenda to track the work for this worktree

The phases and validation controls below are agent-recommended decomposition
of that accepted source objective, not additional operator requirements.

## Scope Approvals

The execution request above authorizes source research, an effort and worktree
agenda, necessary source fixes, bounded isolated validation, and normal issue/PR
publication and stewardship in copilot-extensions. It does not authorize an
installed runtime update, service restart, fleet rollout, new access, or changes
to another contributor's still-open work. Review, CI, existing ownership and
publication rules remain independent gates.

## Plan

### Phase 1 - Review the bounded activation contract

- [x] Land this proposal after the required automated review and CI before implementation.

### Phase 2 - Prove and implement managed-session admission

- [x] Inventory automatic hook/extension consumers and reproduce the unrelated-repository failure using synthetic owned fixtures.
- [x] Implement shared admission from the selected installation's project ownership before policy, lifecycle or extension effects; repository registration/classification alone never admits a session.
- [x] Cache positively established session/workspace membership with appropriate ownership-input invalidation; preserve current policy evaluation, first-use discovery and independent sessions.
- [x] Apply equivalent admission to command/resident, lifecycle and fallback paths without disabling intended protections inside managed anchors/worktrees.

### Phase 3 - Validate and land the complete source fix

- [x] Run focused unit and real consumer checks for unmanaged inertness, managed behavior and cache isolation/invalidation; record platform coverage and native measurements honestly.
- [x] Update authoritative behavior documentation, add the plugin changefile and pass applicable lint, typing, install-contract and required publication checks.
- [ ] Land the complete implementation after review and required CI, preserving release-versus-installed-state distinctions.

### Phase 4 - Complete the canonical effort and source obligations

- [ ] Re-read this README after merge, resolve every Plan/Validation item, and publish the Done/archive delta through the normal PR flow.
- [ ] Settle the coordination issue and owned source-worktree/PR obligations; complete the driver's agenda without claiming installed rollout.

## Validation Plan

- [x] Unregistered and registry-only reference/worktree-class repositories produce no agent-worktrees policy, lifecycle, pulse or disposition effects through actual automatic entry points.
- [x] Owned managed anchors and worktrees still reach existing policy and lifecycle behavior, including nested working directories and the selected installation.
- [x] Positive associations are reused, cannot leak across session/workspace/installation boundaries, and invalidate when relevant ownership inputs change; a prior unmanaged result does not permanently suppress later legitimate admission.
- [x] Command and resident pre-tool paths, session start/end, post-tool behavior and compatibility fallbacks agree on membership; absence or failure of admission does not fabricate a lifecycle outcome.
- [x] Lightweight real-process consumer checks and a fresh temporary profile exercise the first-touch and warm paths without changing the operator's installation; native Windows and available POSIX coverage are distinguished.
- [ ] Targeted tests and required source gates pass on the submitted revision; public documentation and reproducible validation steps describe the implemented behavior.
- [ ] Review and required CI pass, implementation and completion deltas merge, the effort is Done/archived, and source obligations settle. Installed rollout is explicitly excluded.

## Proposal

One ownership admission boundary precedes automatic behavior. Resolve the
session's actual workspace against the selected installation's adopted
projects and owned worktrees, not a general repository class or ambient
plugin registration. Keep reusable positive association facts scoped to their
inputs. Choose the simplest existing-compatible acquisition and reuse seams
after the consumer inventory; the implementation review validates mechanism,
failure behavior, cache invalidation and mixed payload/runtime paths.

## Journal

### 2026-11-04 - Kickoff

- Accepted the execution request and claimed #10564 with a public-safe source objective.
- Confirmed target effort adoption and allocated an isolated source worktree.
- Adopted an owner-managed context-qualified agenda for the driving worktree; it sequences plan review, implementation, validation/publication and canonical completion.
- No implementation, installed update or service mutation has occurred.

### 2026-11-04 - Reviewed plan accepted

- #10566 merged after an approving Copilot review with no findings and required green CI.
- Pulled the source branch forward to the merged plan; the coordination issue remains open.
- Proceeding to the Phase 2 consumer inventory and isolated reproducer. No installed rollout is authorized.

### 2026-11-04 - Admission implemented and locally qualified

- Added one passive project/Git-membership owner with source-bound positive session caching; command hooks, the resident receiver and SDK effects share that boundary. Legacy bootstrap fallback is gated too.
- Initial regression controls failed for all four unmanaged event kinds before admission was wired. Actual hook subprocesses now remain inert in unregistered and registry-only reference/worktree repositories; the same adopted anchor still denies protected edits.
- The six-file adjacent packet passed 378 cases with 3 platform skips at unchanged runner budgets. The final 35-case admission packet also passed, including incomplete-Git freshness, repeated use of the same session/workspace in real isolated installation cells with no legacy fallback, and refusal to execute WindowsApps interpreter aliases.
- Native PowerShell 7 and Windows PowerShell 5.1 controls preserve Unicode paths, deferred PR-observation frames and protected decisions. Two cycles per host observed actual Python descendants, zero visible windows and zero foreground transitions. Forced admission timeout retires the pinned actual interpreter behind the venv redirector before fixture cleanup.
- Retained protected-hook cold/warm samples were 2338.85/2056.21 ms for PowerShell 7 and 2014.52/1846.26 ms for Windows PowerShell. Earlier pairs were 2152.66/1844.50 and 1993.24/1777.37 ms. Inputs were one adopted synthetic anchor/project, a temporary profile and the source policy fallback, not an installed serving runtime. These are individual samples, not tail or two-second budget qualification; wider performance qualification remains with #7935.
- Install contract, version/docs consistency, focused Python lint, JavaScript syntax and all 40 admitted strict typing projects passed. The installer declaration generator changed only this plugin's human-output declaration.
- Fresh temporary-profile checks exercise first-touch behavior without installing anything. Git Bash consumers were exercised on Windows; native Linux/macOS, a live Copilot process and an external venue were not available in this local Windows source lane. SDK transport is an explicit test double around the actual extension and Python policy modules, not installed activation evidence.
- Implementation review, required CI and canonical completion/archival remain open. No runtime update, service restart or fleet rollout has occurred.

### 2026-11-04 - Required unit-source admission correction

- Required PR CI refused the changed status-monitor source before unit execution because its positively admitted digest still described the prior bytes.
- Audited the source cohort: only `status_monitor_cli.py` differed from the declared inputs. Refreshed that one binding; node inventory, coverage targets, effect restrictions and execution budgets are unchanged.
- Replaying the admitted packet in an isolated tooling environment before updating the implementation head. Local Windows-native results are separate from required Linux PR evidence.

### 2026-11-04 - Cache, runtime discovery and consumer qualification

- The initial admitted unit replay passed 4,136 tests locally under the unchanged effect restrictions and Windows-native budget. The required Linux PR run still needs the corrected source binding.
- Ownership parsing without PyYAML now ignores optional repository metadata, including inline tag lists. The shared Git reader reports the actual dependency frontier for successful and failed probes; admission retains all failed owners for negative reuse, and only the workspace plus matching owner for positive reuse. Anchor HEAD, shared objects/refs and worktree backlinks therefore invalidate both revocation and repair cases.
- PowerShell uses the canonical read-only runtime resolver before a legacy PATH fallback. The Windows extension can use the same owner without PATH Python and shares concurrent discovery. Explicit cells still validate before selecting their runtime and retain the canonical validator's PATH Python prerequisite.
- Policy-only fixtures now explicitly admit their events; real unmanaged controls remain intact. Disposable native profiles prove legacy runtime-only discovery, selected-cell isolation, concurrent/warm reuse and zero visible windows or foreground transitions. The Node runtime-discovery path exercises two cold cycles.
- The ten-file admission/native/adjacent packet passed 582 tests with four platform skips at unchanged item/group/plugin budgets. A final 59-case cache/resident packet passed after extending failed-probe/backlink coverage and reusing the hook client's admission function in the resident entrypoint.
- Required CI also identified the resident entry module exceeding the 1,000-line cap. Reusing the existing admission function removes the duplicate lookup and restores that cap without widening its baseline. Its unit-source binding is refreshed; the admitted packet's node inventory, coverage targets, effect restrictions and budgets are unchanged.
- Python lint, JavaScript syntax, install contract (13 plugins), version/docs consistency and all 40 enforced strict typing projects passed. The Hooks contract documents the ownership dependencies and interpreter prerequisites. No dependency, installed runtime, service or rollout changes were made.
- Implementation review, corrected required CI, merge and canonical completion remain open.
- The final exact-revision Windows-native admitted packet passed 4,136 tests in 226.718 seconds, with coverage recorded once and no widened effects or budgets. The module-size gate passes. This local qualification is not substituted for required Linux PR evidence.

### 2026-11-04 - Complete ownership and runtime-only consumer closure

- The original five findings are resolved. Further qualification exposed block-style optional metadata, explicit-cell ownership inputs and environment-only platform selection as additional admission failures. The stdlib reader now ignores both inline and block-style optional subtrees; cache freshness includes namespace/install receipts, payload manifest and owning roots, including canonical file identity.
- Extracted the unchanged kernel/container-aware classifier from the shared runtime into a stdlib-only module. Runtime callers retain the same export; `tools/sync-execution-platform.py` delivers its standalone admission copy. Release entries name the library and every physical/editable consumer; no dependency floor or version manifest was hand-edited.
- Existing SessionEnd consumer tests require runtime-only discovery for explicit cells as well as legacy profiles. Native owners now discover a candidate through the existing layout/non-link checks and read-only resolver, then run isolated no-site admission against the owning payload's canonical context validator before policy, lifecycle or slot-site effects. This preserves that contract without a new PATH Python prerequisite.
- The final six-file packet passed 362 tests with 22 platform skips in 501.23 seconds including preparation and cleanup. It covers registry metadata, warm cell-ownership revocation/repair, both classifier delivery forms, native runtime-only cells, actual Git Bash SessionEnd without PATH Python, helper deployment, cached kill-switch behavior and resident policy. Native POSIX-only extension coverage remains explicit and skipped on Windows; it is not claimed as live Linux acceptance.
- The expanded helper inventory reached the unchanged item deadline through the fixture's old per-file Git Bash publication loop. The fixture now exercises the installer's actual native inventory plus real Python copy settlement, retaining byte-equality and lifecycle/runtime loading assertions. Its focused five-case control passed before the final packet; no item, group or plugin budget was widened.
- Required Linux units passed on the preceding submitted head. Its source guard identified an unclassified bounded admission-worker launch; the caller-contained census now names it, and the complete source-guard family passes locally. Lint, generation audits, install contract and version/docs consistency also pass.
- The admitted source cohort now includes the extracted classifier and updated identity-module binding. Nodes, coverage targets, effect restrictions and budgets remain unchanged; exact-revision replay and fresh review/required CI precede merge. No installed update or service mutation is authorized or performed.
- The committed source replay passed 4,136 admitted tests with coverage in 326.109 seconds under the Windows-native profile. The 1,000-line module-cap check also passes; exact-head Linux PR evidence and the fresh verdict remain independent gates.

### 2026-11-04 - Native JSON dictionary compatibility

- Required CI passed on the submitted ownership/runtime head. A remaining decoder mismatch could reject otherwise valid event data: PSObject projection loses empty or case-distinct JSON keys before membership extraction, including opaque tool data and case-aliased ownership paths in the helper response.
- Native admission now uses dictionary decoding for both directions and extracts only exact workspace/session keys. PowerShell 5.1 retains its host's metadata resolver without PSObject projection or a new dependency; modern hosts use their native hashtable decoder.
- The affected admission/selection/post-tool packet passed 96 tests with one platform skip. Eight real-process regressions then passed on both native PowerShell hosts with nested case-distinct/empty keys, reserved metadata, a dictionary-member shadow key and case-aliased registry roots.
- The complete source-guard family, Python lint and unchanged admitted-unit source bindings pass. This native-only correction does not change the unit packet's admitted inputs or limits. Fresh review and exact-head required CI still precede merge; no installed mutation occurred.

### 2026-11-05 - Execution scope, anchor precedence and real cleanup witnesses

- An overview without inline findings identified three concrete follow-ups. Persistent positive reuse now includes the canonical execution platform before cache lookup, so the same session/workspace/registry cannot reuse a WSL fact in Linux execution.
- Admission preserves explicit per-project machine anchor precedence over the registry fallback. Namespaced project-state location, remote normalization and identity-receipt validation come from generated canonical runtime readers, not a second hand-maintained identity algorithm or a runtime/site import. Missing first-touch state does not provision anything.
- The reader snapshots the configuration inputs and fragment-directory frontier. Conditional fragment locations are not guessed or activated merely to acquire membership; admission requires ownership to hold across the possible locations unless an explicit higher-precedence machine setting resolves them. Nearby configuration still cannot enroll a project absent from the adoption registry.
- Lower-level policy cleanup fixtures now explicitly admit their events, retaining the separate real admission controls. Timeout and caller-exit cases reach actual workers and verify birth-qualified descendant retirement rather than succeeding through the new early no-op.
- The complete six-file packet passed 294 tests with 22 platform skips in 579.58 seconds including preparation and cleanup, at the original budgets. A final five-case packet passed after extending dependency-free JSON/YAML override and clearing coverage. All eight real native policy-lifetime/redirector cases are included.
- Source guards, generation/installation contracts, version/docs consistency and lint pass. The positively admitted unit-source inputs remain unchanged. No installed update, service action or rollout was performed; fresh review, required CI, merge and canonical archival remain open.

### 2026-11-05 - Null clearing and platform-exact registration

- The no-PyYAML scalar reader now maps unquoted YAML null forms, including empty values, to `None` while preserving quoted strings. Per-anchor clearing therefore follows the same registry fallback as canonical configuration and JSON null.
- Registry location selection uses only the requested platform, matching `RepoEntry.local_path()`. WSL does not inherit a Linux-only checkout; an explicit machine anchor remains authoritative.
- Thirteen targeted and adjacent cases passed, covering null spellings, JSON equivalence, clearing, strict WSL association, explicit override and persistent platform isolation. Lint passes; admitted-unit inputs are unchanged. Fresh review and exact-head CI still gate merge.

### 2026-11-05 - Canonical registry ownership only

- Removed the deprecated `projects.yaml.anchor` fallback, matching the canonical project migration and registry ownership model. Adoption remains required, but a stale project location cannot activate automatic behavior without the current platform's registry path or explicit machine override.
- Replaced anchor-only command, resident and lifecycle fixtures with canonical adoption plus repository registration. Six new cases cover stale locations on Windows, Linux and WSL with and without PyYAML, including canonical-path repair and cache invalidation after removal.
- The five-file admission and real-consumer packet passed 265 tests with 26 platform skips in 576.92 seconds including preparation and cleanup, at unchanged budgets. Lint, install contract, generated-reader synchronization and module-size checks pass; admitted-unit inputs are unchanged. Fresh review and required CI remain prerequisites to merge. No installed update or service mutation occurred.

### 2026-11-05 - Quoted ownership keys and mixed-version fallback

- A review overview without inline findings called out YAML parsing and older-client fallback. Bounded assessment found two reproducible cases: quoted YAML ownership keys were ignored without PyYAML, and SessionStart could select an older runtime client before admission when the payload client was missing.
- The stdlib reader now recognizes quoted ownership keys, including machine platform and anchor overrides. SessionStart admits the event through the selected payload before invoking an older client; unmanaged sessions and missing helpers remain inert.
- The adjacent two-file packet passed 91 tests with 11 skips. Eleven explicit controls then passed, including all nine real-process older-client cases across PowerShell 7, Windows PowerShell 5.1 and Git Bash; they prove both inert unmanaged/missing-helper cases and preserved managed fallback. Budgets were unchanged.
- Rebased onto current `dev`, reconciling only effort-index conflicts and preserving all tested implementation bytes. Required exact-head CI and a fresh review remain open; no installed mutation occurred.
