# Mux Child Window Suppression

- **Slug:** `mux-child-window-suppression`
- **Repo:** copilot-extensions
- **Branch(es):** Sequential proposal, implementation and completion PRs against `dev`.
- **Created:** 2026-10-10
- **Status:** Active
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
- [x] Merge this proposal through the normal review and check gates.

### Phase 2 - Implement and verify suppression
- [x] Apply the existing shared suppression primitive to recurring PSMux
      children. _(agent-recommended implementation)_
- [x] Preserve direct interpreter PID ownership while conforming the daemon
      launch shape to the existing recurring-child contract; verify the actual
      Windows parent/child behavior rather than flags alone.
      _(agent-recommended implementation)_
- [x] Add focused regression coverage, update directly affected documentation,
      and add the required release changefile. _(agent-recommended)_
- [ ] Merge the implementation after review and required checks.

### Phase 3 - Promote, deploy and close
- [ ] Verify the promotion pipeline publishes the fixed snapshot to `main`.
- [ ] Deploy through the supported unified update on the declared targets.
- [ ] Record live launch behavior and deployed versions, then archive this
      completed effort through review.

## Validation Plan

- [x] Focused bounded tests cover ordinary/passive direct PID and environment
      preservation, repeated child calls, output, failures and timeouts.
      _(agent-recommended)_
- [x] Native Windows observation covers repeated real PSMux operations from the
      production parent shape, with no unintended terminal windows or focus
      changes; fixture descendants remain contained. _(agent-recommended)_
- [x] For both ordinary and passive starts, exercise the production launch seam
      from a kill-on-close Job, close the caller Job after successful readiness,
      and independently confirm daemon health. A bounded control child must
      exit with the caller Job. _(agent-recommended lifecycle requirement)_
- [x] Applicable lint, install-contract, headless-launch and documentation-impact
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

### 2026-10-10 - Proposal reviewed
- Proposal PR #6042 merged after review, including explicit ordinary/passive
  caller-Job survival validation.
- Implementation follows the existing launch-kind matrix: direct base console
  Python with the shared windowless daemon flags, plus explicit suppression at
  captured PSMux child boundaries.

### 2026-10-10 - Implementation evidence
- Direct base console Python retains the original venv environment and runtime
  PID ownership; ordinary and passive launch seams passed real Windows
  kill-on-close Job survival tests, including a contained control child.
- Explicit captured-child suppression covers status writes and liveness
  probes. The adjacent attachment probe already used the shared primitive.
- Targeted bounded mux/cutover suite: 133 passed, one opt-in desktop test skipped.
  The separate interactive-desktop lane passed all eight tests, including two
  successful real PSMux status cycles, no owned visible windows or focus
  acquisition, and no fixture descendant leaks.
- Touched Python lint, install-contract and headless-launch guards passed.
- Documentation impact: updated the canonical manager README to explain direct
  console Python plus windowless daemon and captured-child launch flags. The
  existing shared launch-kind pattern remains accurate and unchanged.
- Added the required patch changefile. Review, promotion and deployment remain
  outstanding.
- Implementation review requested explicit real timeout coverage. The desktop
  lane now passes nine checks, including a real PSMux `run-shell` timeout with
  two Python descendants and verified caller-Job cleanup of the complete tree.
- Synchronized the active effort index with this README.
- Further review identified that a fixture-owned timeout Job did not prove
  production timeout ownership. Status writes and liveness probes now use an
  explicit shared Job-backed captured-child runner on Windows. Their actual
  failure paths passed real PSMux timeout tests with two descendants.
- Desktop observation now also detects newly surfaced Default Terminal hosts
  outside the fixture's ancestry and terminal foreground changes against a
  pre-launch baseline. Ten desktop checks passed; focused orchestration
  coverage remained 133 passed with three opt-in checks skipped.
- The next review identified the shared spawn helper's best-effort Job fallback:
  without a Job, it resumed the child before the caller could refuse it.
  Added an optional strict synchronous contract that kills a still-suspended
  child on failed Job assignment before any descendant can execute; the mux
  runner opts in. Default behavior for existing consumers is unchanged.
- Native full-suite validation exposed four unrelated failures now tracked in
  #6062 (three pivot-modal tests and one tarball-symlink fixture). They remain
  unresolved validation blockers, not accepted flakes. A fifth failure caused
  by the removed `subprocess` import was corrected at its test's actual seam.
- Strict pre-resume failure, captured-child and corrected credential test
  selection: 11 passed. Shared vendoring, release changefiles, complete strict
  contract/native revalidation and reviewer approval remain outstanding.
