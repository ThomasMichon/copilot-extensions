# Owned reader CPU observations

Next bounded Continuation C slice of [Process Slot Ownership](README.md).
The operator approved psutil-backed sampling of the reader-owned root and
descendants through shared procutil. This is not a host-wide observer or
a competing process registry.

## Placement and dependency boundary

- Add an optional `agent-procutil[metrics]` capability backed by psutil.
  Preserve the existing stdlib-only default and lazy imports for callers
  that do not request metrics.
- First land the reusable read-only observation contract and its native
  tests. Reader lifecycle wiring, aggregation/export and enforced CPU
  budgets are subsequent items, not implied by a library API existing.
- Reuse existing lifecycle/telemetry emission and registry presentation
  seams when wiring consumers. Do not build a second resident sampler.
- These decomposition and initial caps are agent-recommended refinements;
  the operator selected the owned psutil boundary, not numeric CPU limits.

## Observation contract

- The caller supplies an owned root PID plus its captured process creation
  time. Sampling revalidates identity; mismatch or missing identity never
  permits attaching a replacement PID.
- Discover descendants only through that verified root or an already
  verified descendant. Pin each observed PID/creation-time pair; retain
  its ownership history if it later reparents.
- Read user+system CPU seconds, not host utilization or a busy-percent
  proxy. Preserve each identity's observed cumulative maximum without
  double counting repeated observations.
- Publish totals only as observed lower bounds, with coverage/uncertainty
  alongside them. Polling can miss short-lived descendants and their
  final CPU interval; never claim an exhaustive process-tree total.
- Missing processes, access denial, PID reuse, truncated discovery,
  counter regression and unavailable psutil remain explicit. Unknown
  observations are not zero CPU, dead ownership, successful cleanup, or
  affirmative admission/reclamation evidence.
- Initial storage and per-call traversal cap: 128 identities. Exhaustion
  yields an explicit truncated/partial observation, not unbounded growth.
- Consumer cadence is at most once per second and uses an off-event-loop
  single-flight sample holder. A timeout does not release that holder.
  psutil may internally inspect process tables for descendant discovery;
  the identity cap does not prove constant host-independent scan cost.
  Measure discovery overhead before enforcing or widening this policy.
- No process termination, service restart, elevation, or global process
  scan/collection interface is introduced.

## Validation

- [ ] Root PID reuse never reads or adopts the replacement's CPU.
- [ ] Descendant identities are verified; repeated samples do not double count.
- [ ] Reparented observed descendants remain identity-pinned.
- [ ] Access denial and disappearing processes preserve explicit uncertainty.
- [ ] Discovery/storage cap reports partial coverage with bounded retained data.
- [ ] CPU counter regression cannot produce negative or invented usage.
- [ ] Optional metrics absence leaves ordinary procutil imports working and
      yields an explicit unavailable result for a measurement request.
- [ ] Native root/child fixture demonstrates increasing CPU observations and
      graceful cleanup without relying on mocks alone.
- [ ] Relevant Windows and Linux tests plus dependency/install-contract
      checks pass; unexecuted platform proof remains marked unverified.

## Remaining gates

Reader CPU observation wiring, role-level aggregation/export, measured
overhead, CPU budget selection/enforcement and no-progress/process-tree
reclamation remain open. A measured overrun may suppress new admission;
it does not authorize killing a progressing holder.
