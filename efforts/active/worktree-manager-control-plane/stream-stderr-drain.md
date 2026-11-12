# Bounded registered-stream stderr draining

[Canonical Manager effort](README.md#finite-registered-stream-stderr-drain)

- **Slice status:** Active; reviewed plan merged, bounded source proof next.
- **Participant:** Managed source `patient-knob`, finite stream-consumer only.

## Goal and request

Correct a reproduced registered-pivot consumer failure: a provider's diagnostic
stderr burst must not block valid NDJSON stdout and become a false timeout.
The portable request is abstracted for public source publication. No provider
redesign, persistent channel, runtime rollout or other owner's work is included.

The actual installed consumer delivers one row with 11 diagnostic bytes but
times out with zero rows when the same inert provider writes 330,000 bytes
before its identical row/done frames. This establishes the pipe-blocking class,
not the cause of every live provider timeout.

## Design and boundaries

This closes the Picker vision's
[live-not-snapshot and render-derive-not-own](../../../visions/picker/README.md#live-not-snapshot)
intent while preserving existing provider authority. It follows existing
caller-owned process containment and right-sized ownership patterns; no new
process service, transport or state authority is proposed.

The current list reader pipes stdout and stderr but reads stdout alone until
EOF. The existing progress-action reader uses the same spawn and settlement
shape. Correct both directly affected consumers without replacing their
NDJSON parser, frame/run ownership, Inbox, cancellation or deadline policy.

Agent-recommended implementation:

- Start one bounded stderr reader immediately after spawning, before waiting
  for stdout frames. Keep stderr separate from NDJSON and retain diagnostics
  for existing failure/unsupported-stream handling.
- Reuse a suitable existing process-output primitive if it preserves streaming
  frame delivery and process custody. Otherwise use a small consumer-owned
  drain helper, not another process/lifecycle framework.
- Bound retained stderr to 1 MiB per invocation. Exceeding that limit is an
  explicit output failure, not provider silence or success. Retire only that
  owned child through the existing Job/process-group owner.
- Join the drain on success, provider error, timeout, cancellation, close and
  reader-start failure. A failed cleanup stays explicit; do not untrack an
  unsettled child or silently leave a reader behind.
- Preserve stdout streaming, existing summary/row/delta/done/error behavior,
  empty subscriptions, fallback for older unsupported-stream providers,
  no-replay action semantics and original process/silence clocks.

No persistent or wire contract changes: provider argv and NDJSON remain
unchanged. A provider with bounded stderr works with old/new consumers; an old
consumer still has this defect. Rollback restores the old reader. The finite
output limit is an explicit new consumer refusal, documented and tested rather
than treated as successful truncation. No package-version gate or new capability
preflight is needed.

## Plan

- [x] S0: Review and merge this finite plan before functional source edits.
- [x] S1: Capture fail-first real-child controls for noisy list and progress
  providers, retaining quiet-provider positives.
- [x] S2: Implement concurrent bounded stderr draining and owned settlement
  in the existing list/progress consumers.
- [ ] S3: Pass focused consumers and source guards, obtain exact-head review
  and required CI, merge and reconcile this finite source lane.

## Validation Plan

- [x] SV1: Real children emit more than a pipe buffer of stderr before valid
  list rows and progress frames; both deliver without a silence timeout.
- [x] SV2: Preserve nonzero-exit stderr diagnostics, explicit error frames,
  older-provider fallback and valid empty/held subscriptions.
- [x] SV3: Exercise stderr overflow, reader-start/read failure, cancellation
  while stdout is silent and application close. Verify finite return, exact
  owned child settlement and no surviving drain thread; never accept an
  output failure as terminal success.
- [ ] SV4: Run the affected stream/deadline and progress-modal regressions,
  lint/module/install/documentation guards and actual required CI.

## Completion and ownership

Only this finite consumer correction may become Done after source review,
tests, actual merge and canonical acceptance. Existing provider, renderer,
full-duplex channel, first-run and native/installed owners retain their scope.
No live provider action, machine repair, runtime update or general cleanup
is a test fixture. A source pass does not prove that every installed pivot is
healthy or that a productive configuration was changed.

## Journal

### 2026-11-11 - Review correction: invalidate failed reads before cleanup

The next review accepts the stdout-custody correction and identifies a second
valid High finding: a held read that already published rows can remain ready
after stderr overflow while stdout prevents final cleanup from returning.
A real held-provider/descendant control fails first on the missing immediate
error transition.

The list consumer now registers a captured-scope/generation failure callback,
including replay when diagnostics failed before registration. Notification
precedes potentially blocking retirement. It retains inspection rows but marks
the current read failed immediately, and a failure fence prevents queued frames
from restoring ready state. Superseded-generation failures and unrelated scopes
remain inert. Native process/reader custody stays retained until cleanup.
All 127 affected cases pass, including late frames, generation skew, fast
reader failure and retained cleanup. The corrected thread, fresh final review
and exact-head CI remain the publication gates.

### 2026-11-11 - Review correction: retain stdout-reader custody

Review #12141 identified a valid High finding: an exited provider and settled
stderr do not prove stdout or its communication reader settled. A real-child
control with a descendant retaining stdout fails first because the process was
removed from tracking after both communication attempts failed.

The correction gates untracking on successful stdout communication as well as
diagnostic settlement. Close waits for the consuming owner, serializes any
retry after that owner finishes, and requires the actual stdout pipe closed
and communication reader stopped. A refused cleanup remains tracked; other
children still receive retirement attempts. The regression also exercises
recovery through the normal close owner after releasing the inert fixture,
with independent finally cleanup. All 125 affected cases pass, followed by
the expanded real close-owner recovery control. The finding is corrected
locally; thread resolution, fresh final review and exact-head CI remain open.

### 2026-11-11 - Bounded source correction and consumer proof

The four original real-child controls passed with 11 diagnostic bytes and
failed first with 330,000 bytes for both list and progress consumers. The
correction drains stderr concurrently, separately from stdout, with a 1 MiB
capture cap and explicit output/setup/cleanup failures. Existing process
containment remains the retirement owner; failed custody is retained and
closing one refused child does not skip the remaining children.

The affected five-file packet reached 123 passing cases with one residual
progress-clock fixture failure: interpreter startup consumed its synthetic
half-second first-publication budget before a row could be produced. The
related progress-clock fixtures now wait for the real child's startup marker
before arming their unchanged silence/process clocks. All six finite-stream
residual controls then pass; there is no unresolved local failure. This is
123 passing cases plus a six-case corrected residual, not an inflated sum of
independent cases.
The new stderr controls cover overflow before/after terminal stdout, reader
construction/start/read errors, noisy legacy fallback, nonzero diagnostics,
silent stdout cancellation/close, unchanged publication deadlines and retained
cleanup refusal. Real child, pipe and reader settlement are asserted.

Lint, full module-size, install-contract, documentation and whitespace checks
pass. Documentation records the consumer limit and older-consumer behavior.
Source review, actual required CI, merge and canonical acceptance remain open.
No installer or fresh-machine provisioning is changed; contained fresh-profile
children provide the simple consumer boundary. A clean-room installation,
external venue and installed/provider operation are not claimed or authorized
by this finite source lane.

### 2026-11-11 - Reviewed proposal accepted

Plan #12121 merged as `3a886b38f5ef5705bf404052eb003e47c911492e`
after genuine exact-head approval and the actual PR CI and lockdown checks.
The initial stalled attempts were not treated as passing; a description update
recording the separate manual CI result triggered fresh passing PR checks.
The same source worktree is reconciled onto the merge. Next is fail-first
real-child list/progress proof, not runtime rollout or live-provider repair.

### 2026-11-11 - Reproduction and bounded proposal

The actual installed consumer passes the quiet inert child in 0.135 seconds;
330,000 stderr bytes before the same valid frames cause a false initial-read
timeout in 2.041 seconds under a two-second diagnostic deadline. Both children
are bounded and settled. Current open-PR paths and complete truncated
residuals show no overlapping registered-stream consumer hunk. Functional
source is unchanged while this plan is proposed.
