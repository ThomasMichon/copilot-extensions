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
