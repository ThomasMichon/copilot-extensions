# Installed Manager lifecycle before promotion

Parent and completion gate: [Main-Owned Workflow Control](README.md).
Tracked under #6237, extending the existing [installed Manager qualification](../installed-manager-tty-qualification/README.md)
(#11377), [release control](../dev-branch-release-pipeline/README.md) (#3336)
and [configured-feed construction](../feed-construction-release-gate/README.md)
(#7529). Standing intent:
[installed lifecycle before publication](../../../visions/release-pipeline/README.md#installed-manager-lifecycle-before-publication).

## Requested outcome

The full operator wording is retained in the parent's
[Request](README.md#request). Installation must yield a usable Worktree
Manager, not just a successful installer. The requested user flows are:

1. Set up a representative harness, install the suite, launch Manager and use
   its controls to create a worktree and start an actual Copilot session.
2. Exit Copilot normally, open Manager again and Resume that worktree with its
   original conversation continuity.
3. While a session remains active, open another Manager and Open the same
   session without replacing or duplicating it.

The operator permits mocking Copilot's endpoint to remove token dependence.
That is not permission to replace the CLI, Manager, worktree owner or mux with
successful callbacks.

## Existing evidence and missing boundary

- #11377's installed-TTY witness performs real candidate Manager/engine
  installation, registers a generic harness, invokes generated CMD and
  PowerShell project binstubs and observes ConPTY rendering, input and exit.
  Its negative control rejects install-success/launch-failure. It does not
  create a worktree or operate a session from the Manager UI.
- `tests/native/test_resume_pane_startup.py` in Manager qualifies concurrent
  production Resume launch plans and real private PSMux attachment. Its
  private engine resolution and finite Copilot setup fixtures do not establish
  installed suite operation or an actual CLI conversation.
- Worktrees' `tests/native/test_headed_worktrees_copilot.py` already runs the
  actual Copilot executable against a loopback deterministic provider and
  observes fresh/resumed history through real hosting boundaries. Reuse its
  endpoint and continuity techniques rather than adding a second model rig.
  Its launch-owner cases are not Manager-driven Create/Resume/Open acceptance.

These are complementary witnesses, not three proofs of the requested end-to-end
flow. Preserve their existing assertions and do not count a source test as
installed or frozen-candidate release evidence.

## Ownership and scope

- Campaign driver owns this CI/promotion requirement, plan, review and the new
  `worktree-manager/tests/native/test_manager_installed_lifecycle.py` witness.
  It consumes existing qualified helpers without taking their edit ownership.
- The accepted required-readiness contributor owns only the new
  `worktree-manager/tests/native/manager_installed_readiness_profile.py` and
  `worktree-manager/tests/test_installed_readiness_profile.py` input/control
  modules. Actual runtime discovery, installation and execution remain in the
  shared lifecycle witness, not a second fixture or promotion job.
- The existing #11377 qualification coordinator retains native installation,
  TTY fixture source and its open Main-activation PR. Agree actual overlapping
  helper/runner/result edits before touching those files; independent new-file
  work does not wait for that entire PR to merge. This does not supersede it.
- #3336 retains the promotion transaction and independently governed Main
  controller; #7529 retains configured-feed construction and its wider matrix.
- Existing launcher, path resolver, launch-admission, Copilot driver and
  PSMux/session owners retain fixes discovered by the witness. Report a precise
  failing boundary instead of waiving its policy inside the fixture.

The request authorizes source qualification work through normal review. It does
not authorize an operator installation, shared mux/service repair, new runner
capacity, credential transfer, promotion retry, control-pin change or Main
activation. A private fixture backend is not the operator's PSMux namespace.
No new daemon, scheduler, promotion writer or competing qualification effort
is introduced. Plan approval does not imply acceptance of a source handoff.

## Plan

The following implementation decomposition is **agent-recommended**.

### Phase 1 - Review and accept existing-owner seams

- [x] Review the release-vision extension and this linked parent amendment
  before implementation. #11739 merged after current-head Copilot approval,
  correction of the exception-list finding and required green CI.
- [ ] Agree actual overlapping native-helper and release-result integration
  seams with #11377 and #3336; preserve their active PRs and source custody.
  Proceed independently on the assigned new witness/profile files rather than
  make Main-PR completion or a blanket acknowledgement a prerequisite.
- [ ] Select the representative generic harness and its complete declared suite
  installation recipe from canonical owners. Enumerate what is installed and
  enabled; a manually assembled Manager/engine pair cannot be called a suite
  installation without proving the selected suite closure.

### Phase 2 - Extend the authentic installed consumer

- [ ] Reuse the canonical candidate installation, provenance and native
  terminal owners in fresh private HOME, config, registry and mux state.
  Keep configured feed/security policy; do not borrow selected host runtimes.
- [ ] Invoke generated project entry points, observe the installed Manager,
  and use normal UI controls for Create. Prove an actual Git worktree,
  installed launch resolution, real Copilot readiness and a deterministic
  request/response through the local inference endpoint.
- [ ] Exit Copilot through its normal user flow, close and reopen Manager,
  select Resume, and prove the same worktree and durable conversation with a
  further endpoint-backed turn. Do not repair tracking or rewrite session IDs.
- [ ] Keep a CLI session active while starting a second Manager, select Open
  and prove attachment to that exact native process incarnation/session/pane.
  Closing Manager or detaching its view preserves the original owner and its
  ability to complete another turn; no duplicate Copilot is admitted.
- [ ] Exercise the relevant cold and retained/warm backend states without
  allowing accidental reuse of a host server to supply success.

### Phase 3 - Bind the complete result before publication

- [ ] Extend the existing qualification result and refusal rules with explicit
  installed, Create/session, exit/Resume and active-Open outcomes. Bind the
  frozen candidate, installation closure, CLI/mux versions and hashes, harness
  configuration, fixture/endpoint inputs and producing run/attempt.
- [ ] Keep source CI's fast unit/guard contract separate from native execution.
  Use the existing explicit native lane for exact-source development proof;
  require the complete bounded candidate witness before artifact publication
  or promotion admission. No optional/manual result may masquerade as the
  mandatory candidate result.
- [ ] Missing, skipped, failed, timed-out, incomplete, duplicate, stale,
  wrong-candidate or wrong-input evidence refuses admission. Reuse requires
  independent verification of complete equivalent inputs and original success;
  a prior installed-TTY-only receipt cannot satisfy the extended contract.
- [ ] Preserve independent unit, artifact, integrity, signing, review and
  publication gates. Record timings within the existing release objective;
  do not manufacture a pass by increasing deadlines or dropping witnesses.

### Phase 4 - Prove enforcement and record acceptance

- [ ] Earn real isolated native positive and falsifying results through the
  supported execution lane, with diagnostics and verified owned cleanup.
- [ ] Through the existing release owner, prove an actual frozen candidate
  cannot publish with a failing lifecycle result and can proceed with complete
  successful evidence. Source wiring alone is not enforcement.
- [ ] Land any required Main workflow adoption through its separate reviewed
  control path. Do not infer activation from a Dev merge or steal the
  existing #11377 activation PR.
- [ ] Update TESTING/pipeline documentation and the parent journal with exact
  source/native/candidate/activation evidence, remaining platform limits and
  named repair ownership. Resolve the matrix before marking this slice done.

## Validation Plan

- [ ] Fresh representative harness and authentic selected-suite installation:
  no source imports, preexisting completion state, hand-injected launch plan,
  direct low-level Create/Resume replacement or production-policy bypass.
- [ ] Native Windows entry points traverse installed path resolution, actual
  admission, generated launch assets, real terminal and private PSMux. A Linux
  scenario alone cannot qualify the reported Windows failure classes.
- [ ] Real Manager Create yields the expected Git worktree and real Copilot
  conversation. The endpoint records an actual request and deterministic
  response; UI rendering, ready files or process existence are insufficient.
- [ ] Normal Copilot exit and a fresh Manager invocation reach Resume through
  real discovery. The original conversation survives and another turn completes
  without duplicate history, stale-path repair or manual session-state edits.
- [ ] A second Manager's Open attaches to the exact active owner rather than
  creating a replacement. Original process birth, session and pane identity
  remain consistent; detach/Manager exit leaves that session usable.
- [ ] Install-success/entry-launch failure, launch/path-resolution refusal and
  fixture-scoped mux connection failure each fail the appropriate lifecycle
  phase. Expected-negative classification requires its specific observed
  refusal; generic fixture, infrastructure or cleanup failure is not credit.
- [ ] All credentials and inference traffic remain fixture-local. Real CLI
  startup/hooks are retained; a network or authentication dependency must be
  diagnosed, not silently satisfied from the operator's login or settings.
- [ ] Deadlines, bounded output and retained diagnostics cover normal exit,
  timeout, cancellation and failure. Settle only owned native descendants and
  private mux resources. Unconfirmed cleanup retains exact custody and fails.
- [ ] Gate controls reject partial phase success, old TTY-only evidence and
  every absent/non-success/mismatched identity without publication authority.
- [ ] Actual candidate and activation evidence proves the gate is required;
  green source/unit checks, callback receipts and an informational native job
  are not promotion protection. Record each unexecuted tier explicitly.

## Journal

### 2026-11-09 - Requested lifecycle mapped onto existing qualification

The Campaign driver captured the full request in the parent and inspected
the existing installed-TTY, native Resume, real-CLI loopback-provider and
frozen-candidate gate seams. Coordination was posted on #6237 and #11377.
No native or workflow implementation is claimed; exact extension ownership
and reviewed intent precede those edits. Existing TTY activation remains
with its current coordinator, and all broader campaign gates remain open.

### 2026-11-09 - Reviewed lifecycle contract accepted

#11739 merged at `6c5be356d7a66bd10c9895d1270b607e312cff09`.
The [dated acceptance record](journal/2026/11.09-installed-manager-lifecycle.md)
retains the review, qualification limits and next existing-owner seam.
Only the reviewed-intent item closes; native lifecycle implementation,
suite closure and actual candidate enforcement remain unproved.

### 2026-11-09 - Independent file split instead of whole-PR blocking

Following the operator's coordination direction, the Campaign driver traced
the owned Main PR's publishing controller across machines and posted the exact
source split on its PR thread. Direct peer delivery was not acknowledged;
the existing session and control transaction were preserved.
The required-readiness contributor accepted the two new companion/control
paths above. The Campaign driver reserved the distinct lifecycle witness.
Shared helper/runner/result edits still require concrete overlap coordination;
neither this split nor the open Main PR establishes executed lifecycle proof.

### 2026-11-10 - Isolated the deferred Windows argument boundary

The second disposable native run (42766907684, candidate
`5dbe28a88ea64d482c7513f748a551a65887a605`) passed the repaired quoted-process
census and created the actual private PSMux session, then the pinned CLI
rejected its command before inference. Direct tests against that exact pinned
CLI accepted the configured flags and both directory spellings; those inputs
were not removed or weakened.

A separate private argv witness reproduced PowerShell's `-File` conversion
of `-C=<drive>:\...` into two arguments. The ordinary startup manifest and
full pane-manifest route preserved it. The deferred seed owner can reach a
later canonical setup spawn after mux serialization, leaving that final
boundary unprotected. The fix reuses the existing copied shell and one-shot
manifest at that spawn, retaining the original admission and seed ownership.
No provider mock, policy, budget or native-session identity was changed.

Focused source validation passed 254 shell/seed controls with 16 platform
skips, followed by both native final-spawn success/failure variants. Lint,
install-contract and module-size checks passed. These controls are not the
installed Manager lifecycle result: draft #11864 remains unqualified until
the exact updated source completes the disposable native witness. Authentic
selected-suite installation, ordinary upgrade and frozen-candidate mandatory
enforcement remain open under this parent.
