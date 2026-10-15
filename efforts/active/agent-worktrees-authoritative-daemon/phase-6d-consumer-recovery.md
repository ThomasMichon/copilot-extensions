# Phase 6d.3a - Consumer startup and transient feed recovery

Parent: [authoritative-daemon effort](README.md), Phase 6d.3.
Issue: #6832. Participant: Streaming recovery owner.

## Request

Operator request, verbatim for this upstream repair: "Yes, identify whether
we have an existing bug, come up with a plan, and drive a fix."

The operator wants streaming, not a permanent one-shot workaround. This is
a bounded acceptance repair under the already-reviewed
[Phase 6d plan](phase-6d-authority.md), not a new daemon or polling authority.

## Established evidence

The actual Windows Picker resolves its canonical machine as local, but its
held-feed loader starts a two-second watchdog immediately after spawning
the engine. Concurrent UI startup can consume that interval before the
engine emits its first protocol frame. The same selected source returns
valid resident-owned rows through a one-shot current-roster read.

The watchdog also marks transport expiry non-retryable. A transient startup
or heartbeat failure therefore permanently stops this source, unlike the
existing bounded-backoff handling of an abrupt disconnect.

Existing resident reports #6739 and #6748 are related symptoms with different
reported triggers. They remain separately tracked; no assumption that a
client fix repairs every resident failure or explains every platform.

## Plan

The following decomposition is agent-recommended.

- [ ] Separate the existing bounded source-startup budget from snapshot/update
      transaction and heartbeat deadlines. Include process and SSH startup
      in the source budget; do not weaken atomic protocol admission.
- [ ] Retry transient transport expiry through the existing cancellable
      reconnect/backoff owner, retaining visible failure or stale last-good
      state. Preserve terminal malformed, authentication and incompatible
      peer failures; do not introduce caller computation or polling fallback.
- [ ] Add real child-process regressions for delayed initial publication,
      startup timeout followed by recovery, interrupted publication, missed
      heartbeat followed by recovery, and cancellation cleanup.
- [ ] Update the consumer contract and add a Manager changefile, then drive
      implementation review, required CI, merge and normal promotion.
- [ ] Apply an explicitly authorized unified rollout. Exercise default
      streaming through the installed real Picker before removing any
      temporary opt-out; verify subsequent updates and daemon availability.

## Validation plan

- [ ] Demonstrate the delayed-start and transient-expiry regressions fail
      against the old loader and pass with the fix, with measured budget
      boundaries and unchanged atomic snapshot/update shapes.
- [ ] Run the contained Manager feed/current-roster/SSH-loader selection,
      touched Python lint, and applicable repository guards.
- [ ] Exercise the real Windows Picker with the changed source against the
      selected installed engine, then repeat with the promoted installed
      Manager. Record source/runtime identity and actual rendered rows.
- [ ] Confirm client cancellation reaps its own child and reconnect work.
      No test or probe may stop a shared resident or alter user settings.
- [ ] Account explicitly for Linux/macOS native coverage and any unavailable
      live platform. This client repair does not certify untested resident
      lifecycle behavior.

## Completion and exclusions

This slice is complete only after reviewed implementation, promotion and
authorized installed streaming acceptance, or after an explicit tracked
blocker/transfer. The remaining Phase 6d and schema-bounded parent acceptance
stay with #3761. Singleton replacement/restart changes, resident publication
bugs, fleet authorization and unrelated update sequencing are not silently
included in this client repair.
