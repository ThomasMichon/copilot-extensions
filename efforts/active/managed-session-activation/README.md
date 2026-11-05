# Managed Session Activation

- **Slug:** `managed-session-activation`
- **Repo:** copilot-extensions
- **Branch(es):** independent review-first plan and implementation PRs against `dev`
- **Created:** 2026-11-04
- **Status:** Draft
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
The reported edit denial still needs an isolated reproducer.

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

- [ ] Land this proposal after the required automated review and CI before implementation.

### Phase 2 - Prove and implement managed-session admission

- [ ] Inventory automatic hook/extension consumers and reproduce the unrelated-repository failure using synthetic owned fixtures.
- [ ] Implement shared admission from the selected installation's project ownership before policy, lifecycle or extension effects; repository registration/classification alone never admits a session.
- [ ] Cache positively established session/workspace membership with appropriate ownership-input invalidation; preserve current policy evaluation, first-use discovery and independent sessions.
- [ ] Apply equivalent admission to command/resident, lifecycle and fallback paths without disabling intended protections inside managed anchors/worktrees.

### Phase 3 - Validate and land the complete source fix

- [ ] Run focused unit and real consumer checks for unmanaged inertness, managed behavior and cache isolation/invalidation; record platform coverage and native measurements honestly.
- [ ] Update authoritative behavior documentation, add the plugin changefile and pass applicable lint, typing, install-contract and required publication checks.
- [ ] Land the complete implementation after review and required CI, preserving release-versus-installed-state distinctions.

### Phase 4 - Complete the canonical effort and source obligations

- [ ] Re-read this README after merge, resolve every Plan/Validation item, and publish the Done/archive delta through the normal PR flow.
- [ ] Settle the coordination issue and owned source-worktree/PR obligations; complete the driver's agenda without claiming installed rollout.

## Validation Plan

- [ ] Unregistered and registry-only reference/worktree-class repositories produce no agent-worktrees policy, lifecycle, pulse or disposition effects through actual automatic entry points.
- [ ] Owned managed anchors and worktrees still reach existing policy and lifecycle behavior, including nested working directories and the selected installation.
- [ ] Positive associations are reused, cannot leak across session/workspace/installation boundaries, and invalidate when relevant ownership inputs change; a prior unmanaged result does not permanently suppress later legitimate admission.
- [ ] Command and resident pre-tool paths, session start/end, post-tool behavior and compatibility fallbacks agree on membership; absence or failure of admission does not fabricate a lifecycle outcome.
- [ ] Lightweight real-process consumer checks and a fresh temporary profile exercise the first-touch and warm paths without changing the operator's installation; native Windows and available POSIX coverage are distinguished.
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
