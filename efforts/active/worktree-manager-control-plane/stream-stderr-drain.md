# Bounded registered-stream stderr draining

[Canonical Manager effort](README.md#finite-registered-stream-stderr-drain)

- **Slice status:** Proposed; functional edits wait for plan review.
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

- [ ] S0: Review and merge this finite plan before functional source edits.
- [ ] S1: Capture fail-first real-child controls for noisy list and progress
  providers, retaining quiet-provider positives.
- [ ] S2: Implement concurrent bounded stderr draining and owned settlement
  in the existing list/progress consumers.
- [ ] S3: Pass focused consumers and source guards, obtain exact-head review
  and required CI, merge and reconcile this finite source lane.

## Validation Plan

- [ ] SV1: Real children emit more than a pipe buffer of stderr before valid
  list rows and progress frames; both deliver without a silence timeout.
- [ ] SV2: Preserve nonzero-exit stderr diagnostics, explicit error frames,
  older-provider fallback and valid empty/held subscriptions.
- [ ] SV3: Exercise stderr overflow, reader-start/read failure, cancellation
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

### 2026-11-11 - Reproduction and bounded proposal

The actual installed consumer passes the quiet inert child in 0.135 seconds;
330,000 stderr bytes before the same valid frames cause a false initial-read
timeout in 2.041 seconds under a two-second diagnostic deadline. Both children
are bounded and settled. Current open-PR paths and complete truncated
residuals show no overlapping registered-stream consumer hunk. Functional
source is unchanged while this plan is proposed.
