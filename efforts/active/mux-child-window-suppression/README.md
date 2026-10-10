# Mux Child Window Suppression

- **Slug:** `mux-child-window-suppression`
- **Repo:** copilot-extensions
- **Branch(es):** Sequential proposal, implementation and completion PRs against `dev`.
- **Created:** 2026-10-10
- **Status:** Draft
- **Vision:** Below-altitude regression repair; restore the existing Windows background-process launch contract.
- **Umbrella issue:** #6040

## Guiding Intent

Eliminate unintended terminal windows and focus theft from recurring managed
mux operations. Preserve direct interpreter PID ownership, daemon readiness,
captured output, exit status, and supported lifecycle behavior.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Fix driver | Proposal, implementation, release and verification | Issue #6040 and its PRs |

## Coordination

- **Topology:** Sequential proposal, implementation and completion PRs.
- **Host (owns PRs):** Fix driver.
- **Delegates:** None.
- **Handoff:** Resume from this README and the owning issue.

## Context

PR #5823 made ordinary and passive mux-daemon starts use a direct GUI-subsystem
Python interpreter. The recurring `set-option` and `has-session` subprocess
calls still lack explicit window suppression. The earlier correction in #4153
relied on the daemon's console launch shape; changing that shape exposed the
unguarded children again.

The authoritative launch-kind matrix is
`docs/patterns/windows-background-process-launch.md`. It requires a
console-subsystem root for recurring console descendants and explicit
suppression for captured children. Preserve the direct PID fix without assuming
that a GUI-subsystem parent suppresses its descendants.

## Request

> Great. Drive necessary fixes, and get the PRs merged, dev promoted to main, and it deployed out

The operator confirmed the slug and rollout to the declared deployment targets.
Target identities and private diagnostic details remain in the downstream
control repository, not this public effort.

## Plan

### Phase 1 - Review the repair contract
- [ ] Merge this proposal through the normal review and check gates.

### Phase 2 - Implement and verify suppression
- [ ] Apply the existing shared suppression primitive to recurring PSMux
      children. _(agent-recommended implementation)_
- [ ] Preserve direct interpreter PID ownership while conforming the daemon
      launch shape to the existing recurring-child contract; verify the actual
      Windows parent/child behavior rather than flags alone.
      _(agent-recommended implementation)_
- [ ] Add focused regression coverage, update directly affected documentation,
      and add the required release changefile. _(agent-recommended)_
- [ ] Merge the implementation after review and required checks.

### Phase 3 - Promote, deploy and close
- [ ] Verify the promotion pipeline publishes the fixed snapshot to `main`.
- [ ] Deploy through the supported unified update on the declared targets.
- [ ] Record live launch behavior and deployed versions, then archive this
      completed effort through review.

## Validation Plan

- [ ] Focused bounded tests cover ordinary/passive direct PID and environment
      preservation, repeated child calls, output, failures and timeouts.
      _(agent-recommended)_
- [ ] Native Windows observation covers repeated real PSMux operations from the
      production parent shape, with no unintended terminal windows or focus
      changes; fixture descendants remain contained. _(agent-recommended)_
- [ ] Applicable lint, install-contract, headless-launch and documentation-impact
      gates pass. _(agent-recommended)_
- [ ] The promoted `main` snapshot contains the fix and release metadata.
- [ ] Deployed runtime evidence establishes the fixed behavior, without private
      identifiers in public artifacts.

## Proposal

Use existing process utilities rather than a new launch abstraction. Fix the
child boundary and retain the direct-interpreter ownership guarantee. Follow
the existing Windows launch-kind matrix if a consoleless daemon cannot reliably
contain terminal-multiplexer descendants.

## Journal

### 2026-10-10 - Proposal
- Claimed #6040 after searching related interpreter and console issues.
- Captured the operator's completion gate and confirmed effort slug.
- Identified that flag-only tests are insufficient for the recurring-child
  launch boundary; native observation is part of completion.
