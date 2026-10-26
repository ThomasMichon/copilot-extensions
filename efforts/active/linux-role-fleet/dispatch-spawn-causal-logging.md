# Dispatch spawn causal logging: first source slice

Back to the [logging integration plan](logging-conventions.md) and
[Linux role fleet effort](README.md). Tracker: [#8377](https://github.com/ThomasMichon/copilot-extensions/issues/8377).

**Status: proposed bounded implementation slice.** This adds causal records
through existing Dispatch logging owners, not a new collector, allocation
ledger, delivery protocol or complete fleet-logging qualification.

## Request and source boundary

Operator direction, public-safe excerpt:

> I would prioritize adding missing, non-redacted logging.

The requested outcome is a reconstructable sequence for each attempted task
spawn. The implementation decomposition and tests below are agent-recommended.
Preserve useful operation identities, stage names, elapsed time and causal
errors rather than replacing them with only hashes or generic categories.
Credentials never belong in a diagnostic record.

The existing application handler owns UTC/process/runtime attribution,
cross-process append custody, discovery and retention. Ordinary Bridge CLI
tracing is separately opt-in and cannot account for work blocked before its
subprocess boundary. `Supervisor.poll_once()` performs reconciliation before
new task selection; a live lane can therefore stop advancing before a spawn
reservation exists.

This first slice covers Dispatch-owned lane cycles, scheduling decisions,
reservation, workspace preparation and the ordinary Bridge caller boundary.
Bridge's internal workspace/native/first-prompt stages, native output capture,
CLI-mode creation convergence and full timeline export remain subsequent
owner-specific work. No source record claims those unobserved stages completed.

## Implementation plan

- [ ] Verify that the actual worker-lane entry point has a configured existing
      application sink, not only its supervising daemon. Reuse that handler
      and its discovery; no new permanent log directory or service.
- [ ] Emit a cycle/stage entry before each potentially blocking reconciliation,
      selection, capacity, reservation, preparation, backend and bookkeeping
      call, then a result/error with elapsed monotonic time. A missing end is
      outstanding observation, not confirmed absence.
- [ ] Correlate existing supervisor, task/generation and reservation/creation
      identities as they become available. A cycle/call identifier is diagnostic
      grouping only; it never supplies execution or retry authority.
- [ ] Explain bounded scheduling outcomes: exhausted retry budget, local
      concurrency limit, no fleet capacity, governance refusal and reservation
      contention. Do not dump the whole task or environment or log an
      ever-growing candidate corpus each tick.
- [ ] At ordinary Bridge calls record the operation, existing reservation,
      entry, exit/timeout and useful causal diagnostics. Preserve original
      arguments, prompts, results, exception behavior and launch count.
      Use targeted credential removal, not blanket suppression of benign
      operation identities and errors. Do not add raw prompt/envelope capture
      to general application logs.
- [ ] Keep the large supervisor module within its existing size guard. If
      extraction is needed, move only the cohesive cycle orchestration behind
      the existing public method; do not redesign lifecycle or allocation.
- [ ] Update the existing diagnostic/spawn guides with event coverage and
      remaining gaps. Default application records and opt-in raw-call
      observation must not be described as the same facility.

The existing sink's degraded-write reporting and primary-result behavior remain
unchanged in this slice. It does not certify complete trace retention when
storage fails. A later isolated-lab policy may require a retained pre-effect
record before allowing its synthetic launch; that policy is not silently
imposed on ordinary callers here.

## Validation plan

- [ ] Actual configured application output contains parseable, correlated
      entry/result records with existing timestamp/process/runtime attribution.
- [ ] A blocked fake stage has an entry visible before it is released.
      A failing stage has a causal error and no invented successful end.
- [ ] Capacity/refusal/contended reservation controls explain why no backend
      was called; an admitted synthetic task still spawns and records once.
- [ ] Existing original-key replay, retained/pending creation, startup
      completion, bookkeeping failure and allocation tests preserve behavior.
- [ ] Known credential values remain absent while synthetic benign IDs and
      causal messages remain useful; malformed/full task envelopes are not
      copied into application output.
- [ ] Existing handler-degradation behavior stays visible and does not change
      the primary operation result. No new remote collector or retries.
- [ ] Required CI uses focused synthetic/unit contracts. Real child/crash,
      persistent-container and native CLI qualification remain in their bounded
      isolated lanes, not a new required promotion matrix.
- [ ] Independent source review and required checks pass before merge.
      Release, authorized installation and newly observed running records
      remain separate acceptance gates.

## Completion and integration

Resolve only this slice's source gates when its implementation merges.
The parent logging inventory, native/installer/container adapters and running
qualification remain open. Do not change updater, service, credential, queue,
registrar or production configuration to demonstrate these records.
