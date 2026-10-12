# Mux Child Window Suppression

- **Slug:** `mux-child-window-suppression`
- **Repo:** copilot-extensions
- **Branch(es):** Sequential proposal, implementation and completion PRs against `dev`.
- **Created:** 2026-10-10
- **Status:** Done
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

Additional request, with the private host identifier redacted:

> I'm still seeing tons of windows on [affected Windows workstation], so we can stay focused here. But do also sweep test flows for failure to ensure processes are always headless

Completion direction, with the private target identifier redacted:

> Transfer [outbound-only target] rollout to a tracked follow-up and close out the completed work

The remaining on-device adopter rollout is owned by the operator's downstream
work queue as **On-device adopter rollout verification**. No deployment to that
target is claimed by this effort.

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
- [x] Merge the implementation after review and required checks.
- [x] Sweep test-flow launch boundaries and suppress unintended test-runner
      windows, preserving explicitly interactive fixtures and containment.

### Phase 3 - Promote, deploy and close
- [x] Verify the promotion pipeline publishes the fixed snapshot to `main`.
- [x] Deferred to `On-device adopter rollout verification`: Deploy through the
      supported unified update on the remaining outbound-only target; the
      primary Windows and second Linux target rollouts are complete.
- [x] Record live launch behavior and deployed versions, then archive this
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
- [x] The promoted `main` snapshot contains the fix and release metadata.
- [x] Deferred to `On-device adopter rollout verification`: Capture the
      remaining target's deployed runtime evidence. Primary Windows desktop
      evidence establishes the fixed behavior without private identifiers.
- [x] Shared test-runner validation covers nested Python, PowerShell and Git
      launches, stdio, timeout cleanup, POSIX parity and native desktop window
      observation. _(agent-recommended)_

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
- Extended the scope to the operator-requested test-flow sweep. The shared
  containment worker and its command launch lacked window suppression;
  bounded execution alone does not establish a headless process contract.
- Added canonical no-window flags at both runner boundaries with explicit
  Windows standard streams. Intentional interactive test launches are not
  globally rewritten. Bounded runner validation: 35 passed, one Linux-only
  check skipped; native desktop observation remains outstanding.
- The same two nested-launch and timeout cases passed in the interactive
  Windows task (2 passed, 20 deselected): Python, PowerShell and Git retain
  streams and exit codes, while fixture console-handle checks remain empty.
  Full external-window/focus baseline observation remains a separate gate.
- Added release changefiles for every canonical process-helper consumer as
  required by the shared-library changefile gate; vendored copies are in sync.
- Corrected the four native test fixtures tracked in #6062: task selection now
  follows actual focused-row keyboard interaction; tarball extraction creates
  and verifies a real native directory symlink. All four targeted checks passed
  independently, without skips or production behavior changes.
- Implementation review approved the strict containment and runner fixes.
  Further fixture updates were published to the same PR. Current `dev` has
  conflicting changes: supported sync attempted a rebase and aborted cleanly.
  Resolve the conflict and obtain current-head review/checks before merge;
  promotion and deployment are not complete.
- Reconciled the implementation with current `dev`, preserving other active
  effort entries and the complete local validation journal.
- The combined native desktop run passed 1,887 checks with 15 capability or
  platform skips. The nested runner's success and timeout cases now observe
  external terminal hosts, visible windows, foreground transitions and process
  cleanup against a pre-launch baseline. Both completed without a surfaced
  window or focus acquisition.
- Restored the symlink fixture's established capability probe for accounts
  without native symlink privilege. Stabilized an intermittent actions-menu
  fixture by selecting the actual focused native row before opening its menu.
- A recurring legacy container namespace lookup still launched the worktrees
  state-root command without suppression. Applied the existing peer-launch
  no-window primitive to both legacy and same-cell probes; 59 targeted config
  and peer-boundary tests passed. Capture, timeout, encoding and optional-peer
  behavior remain unchanged.
- Implementation PR #6055 merged as `ee739cb13260` after current-head review
  and all checks passed. The reviewer had no remaining Medium/High findings;
  the carried index-status finding was already fixed in the reviewed head.
  Native Windows and Linux CI runner checks passed. Release promotion and
  authorized runtime rollout remain the next gates.
- Promotion run `38099828902` passed every validation job except the bridge's
  frozen wrapper-authority corpus. Issue #6098 tracks the stale component
  digest after the shared process-helper change. Refreshed only the three
  authority bindings and their registry checksums, preserving wire vectors and
  refusal behavior. Targeted corpus/attestation validation passed 42 checks
  with six Linux-only skips; the contract guard passed both contracts and all
  35 fixtures. The local full runner was refused by an existing host admission
  lease after a bounded wait; no competing workload was stopped.
- Repair PR #6114 merged as `1bf681095776` after current-head approval,
  full bridge CI and all required checks passed. The next `dev` CI candidate
  contains both the launch repair and refreshed corpus bindings. Promotion,
  live runtime cutover and deployment observation remain outstanding.
- Watch-generation repair PR #6135 merged as `3972e45374a6` after approval
  and green checks, resolving the transient worker-count assertion in #6131.
  A separate portable installer timeout is tracked in #6138; the unchanged
  Linux reproduction passed in 8.17 seconds without increasing test budgets.
- At the operator's explicit request for immediate mitigation, applied a
  reversible local emergency overlay of the reviewed child suppression and
  direct-console launch changes. The existing graceful cutover completed with
  clean, unforced drain and relay adoption; the predecessor exited. Two real
  desktop cycles observed four PSMux clients with no owned visible windows or
  focus acquisitions. This mitigation is not a promoted release and does not
  close the release or unified deployment gates.
- Triggered the trusted `repository_dispatch` full validation/promotion path
  for current reviewed `dev`. It resolves and pins live `dev` through the
  existing gate and retains all full validation requirements.
- Promotion run `38109888269` passed all full validation jobs. Generated
  release PR #6154 merged as `887bb50826ec`, publishing the actual fixed
  `main` snapshot and Worktree Manager `0.6.22-dev1`.
- The primary Windows target completed the supported unified update. The
  promoted mux daemon replaced the emergency generation; the predecessor
  exited. Two real desktop cycles observed 13 PSMux clients, zero owned
  visible windows and zero focus acquisitions, with healthy released runtime
  responses in both cycles. The emergency overlay is no longer serving.
- A second, native Linux target completed its unified update to the same
  manager release and updated plugin runtimes with graceful daemon cutovers.
  The outbound-only target still requires on-device rollout confirmation;
  retain the deployment and final archival gates until that evidence exists.

### 2026-10-12 - Completed engineering and transferred final adopter rollout
- The operator explicitly transferred the remaining outbound-only rollout to
  **On-device adopter rollout verification**, tracked in the downstream work
  queue with supported update, graceful cutover, runtime-version and two-cycle
  desktop evidence requirements. The target remains unverified, not silently
  counted as deployed.
- The implementation, release fixes and deployment evidence landed through
  PRs #6055, #6114, #6135 and #6158. Published manager `0.6.22-dev1` completed
  the primary Windows cutover and second native Linux update; two Windows
  production cycles observed 13 PSMux clients with zero owned visible windows
  or focus acquisitions. Later local maintenance reports installed and active
  manager `0.6.25-dev1`.
- Promotion lifecycle repair #6172 is merged and published. Run `38175347474`
  waited for its exact generated PR to merge before tagging commit
  `3bd09511840d4ebb7d02ebd5d76879b4246d0d26`. A subsequent successful run
  `38212582333` again completed the waiter after its release PR merged and
  tagged afterward. The exclusive workflow lifecycle now spans delivery.
- Investigated stale CI tracker #6220 and verified the already-merged fixture
  correction #6221: the original non-final payload retained contradictory
  FINAL display metadata. The exact regression and all 29 descriptor-validation
  cases pass without weakening production validation; closed the stale tracker.
- All engineering, release and reachable-target obligations are complete.
  Remaining adopter verification has a named tracked owner, and this effort
  is archived rather than claiming that verification already happened.
