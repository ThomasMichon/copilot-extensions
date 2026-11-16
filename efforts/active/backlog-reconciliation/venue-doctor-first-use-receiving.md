# First-use CodeSpace doctor: proposed receiving contract

Back to [Backlog Reconciliation](README.md).

## Intent and exact scope

This agent-recommended proposal would retain #9281's complete first-use
`agent-codespaces doctor <name> --fix` contract directly in the existing
#954 venue-parity canonical. Both issues remain OPEN. The issue-only study
establishes a feasible receiving route, not accepted issue content, a
technical fix, implementation ownership or permission to provision a venue.

The reported starting state is an existing Ubuntu 24.04 CodeSpace with
Copilot CLI, node, python3 and uv, but no tmux, no `copilot-extensions`
marketplace and no agent-worktrees runtime. Manual recovery required an apt
list refresh after a fetch/404 failure, marketplace registration, and runtime
installation from the already installed plugin. Only after those three
steps did the reported doctor invocation return `[READY]`.

The request retains two independent obligations: include the failing
command's stderr in every `[FAIL]` result, and cover **each** first-use case
by remediation **or** its exact next command. For the apt remediation
alternative, refresh the lists **at least once after a fetch failure**.
That is not a one-attempt ceiling. A plugin installation followed by a
status-only installer invocation is not the requested runtime bootstrap.

#954 already owns equivalent-quality venue preparation and shared failure
semantics. Its #2042 intake requires bounded actionable provider
doctor/readiness diagnostics and prohibits competing doctor authority or
silent provisioning during advertised read-only inspection. Those clauses
do not directly retain these three triggers, the per-case alternative or
failing-command stderr. A link to #9281 would not close that gap.

This is tracking capture, not a new installer architecture. The
[authoritative runtime-replacement goal](../../../visions/installer/README.md#authoritative-runtime-replacement)
remains unchanged. The reported first-use commands do not introduce a
manual-update veto, authorize a runtime operation or require adoption of
the entire venue programme before this bounded repair can be delivered.

## Evidence and retained ownership

The study read both complete originals, #954's eighteen comments and
#9281's zero comments after complete authorship qualification. The eighteen
comments have one distinct author. Both issues were OPEN, unlocked and
unassigned; their metadata remained unchanged after the study's text reads.
Coordinator publication-input qualification at 2026-11-16 05:36 UTC again
found complete stable histories and two equal complete twelve-member
collaborator rosters. The narrow same-repository Copilot review
cross-reference was verified as metadata only; no bot-authored text was read.

No identified or genuinely uncertain held-source derivative was encountered
in the admitted pair. No linked issue/PR body, product source, installer,
ROOT effort, test or native operation was used to claim fulfillment.
Earlier bounded source research remains partial evidence, not a new
execution result or whole technical completion.

The existing CodeSpace doctor/provider-readiness, installer and venue
programme owners retain their responsibilities. Preserve every incumbent
#954 clause and discussion, P0-P5 and genuine venue differences, restricted
denial, P3 reservations, and the separate #2042, #2094, #1082, #2476 and
#5926 scopes. The independent #4227 affected-scope condition is not resolved
or expanded by adding this first-use CodeSpace case.

## Proposed direct issue intake

Publish this addition only after required plan review and current
qualification. Preserve the receiver's entire incumbent body, discussions
and check states. All new technical rows remain unchecked.

### Plan FD9281

- [ ] **FD9281-P1:** Retain the reported existing Ubuntu 24.04 CodeSpace
  starting state and all three missing prerequisites. For each case,
  `agent-codespaces doctor <name> --fix` must perform its bounded remediation
  OR print the exact next command; neither a generic failure label nor
  `[SKIP]` without that command satisfies the alternative.
- [ ] **FD9281-P2:** Cover stale apt lists causing tmux fetch/404 failure;
  for the remediation alternative, refresh lists at least once after a
  fetch failure. Cover a missing marketplace when installing agent-bridge,
  and an installed agent-worktrees plugin with no binstub/runtime. Preserve
  marketplace registration and the reported
  `bash <plugin>/scripts/install.sh install` runtime-bootstrap entry rather
  than a bare status-only invocation, using the supported installer and
  applicable permission boundaries.
- [ ] **FD9281-P3:** Include bounded, diagnostically useful failing-command
  stderr in each `[FAIL]` result. Preserve the successful `[READY]` result
  after the reported prerequisites are recovered; do not infer readiness
  merely from printing a command or successfully installing a plugin.
- [ ] **FD9281-P4:** Extend the existing provider doctor/readiness owner,
  not a competing authority. Preserve the incumbent prohibition on silent
  provisioning during advertised read-only inspection, existing reprobe,
  credential-exit, ready, report-only, lean and stale controls, and all
  actual installer, credential and venue-operation authorization.

### Validation FD9281

- [ ] **FD9281-V1:** Cover every stated starting-state prerequisite and
  failure independently and together through focused CLI/unit cases.
  Verify each remediation OR exact-command alternative, the post-fetch
  list-refresh condition, marketplace registration and explicit runtime
  install entry. Do not silently substitute a one-refresh maximum for
  the original at-least-once requirement.
- [ ] **FD9281-V2:** Verify failing-command stderr for each `[FAIL]`,
  diagnostically useful bounded output, the successful recovery/`[READY]`
  outcome, and preservation of the incumbent controls in P4. Printing a
  remedy, a safe refusal or a source merge is not a successful live repair.
  Keep the original focused outcome distinct from #954's independently
  owned native/installed portfolio; add no universal venue or host gate.
- [ ] **FD9281-V3:** Before any separate tracking retirement, qualify both
  current issues and read back the complete direct Plan, Validation and
  Coordination in the OPEN receiver, with every incumbent byte and check
  state preserved. Preserve #9281's original verbatim. Any eligible
  administrative retirement is `NOT_PLANNED`, not technical completion,
  venue readiness, P3 activation or resource release.

### Coordination FD9281

This addition assigns no implementer and changes no reservation, claim,
hold, source owner or resource custody. The admitted pair contains no
#9281-specific token-OPEN, same-claim release or receiver-consent condition;
that observation does not clear a contrary actual restriction elsewhere.
All #954 P3 and independent programme conditions retain their existing scope.

Keep #9281 OPEN until complete direct receiving acceptance is verified and
its separate disposition is justified. The bounded doctor outcome need not
wait for every other #954 objective; its eventual completion cannot settle
those objectives. No provisioning, elevation, credential change, native
execution, runtime update or cleanup is authorized by this tracking plan.

## Whole-source crosswalk

| #9281 obligation | Direct receiving clause | Preserved distinction |
|---|---|---|
| Existing Ubuntu 24.04 CodeSpace and stated installed/missing prerequisites | P1, V1 | Not a fresh-host or every-venue programme |
| tmux apt fetch/404 failure and at-least-once post-failure list refresh | P2, V1 | No invented one-attempt ceiling |
| Missing marketplace for agent-bridge plugin installation | P2, V1 | Registration or exact command, not a failure label |
| Installed agent-worktrees plugin but absent binstub/runtime | P2, V1 | Explicit runtime install, not status-only invocation |
| Remediation OR exact next command for each case | P1-P2, V1 | Three independently retained alternatives |
| Underlying failing-command stderr in every `[FAIL]` | P3, V2 | Bounded output must remain diagnostic |
| `[READY]` after the reported manual recovery | P3, V2 | A proposed command is not executed recovery |
| Existing doctor authority, controls and operation boundaries | P4, V2, Coordination | No new owner, provisioning grant or competing doctor |
| Complete accepted receiving before separate retirement | V3, Coordination | Planned tracking is not technical completion or claim release |

## Publication boundary

This proposal and its [dated journal](journal/2026/11.15-venue-doctor-first-use-receiving.md)
require normal review before issue mutation. A merged proposal alone is not
accepted receiving content. Actual delivery, complete readback and any
separate source retirement must be recorded afterward; the parent campaign
and its fixed whole-queue completion gates remain open.
