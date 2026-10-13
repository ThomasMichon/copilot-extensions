# Reader sample provenance and collector cost

An agent-recommended next increment of [Process Slot Ownership](README.md)'s
reader cost/provenance item, following the [landed reader wiring](reader-cpu-wiring.md).
This slice supplies measurement evidence before expected-cost policy or numeric
CPU admission limits; it does not authorize either enforcement or termination.

## Contract

- Extend optional procutil metrics without adding default runtime dependencies
  or automatically collecting in other consumers.
- Give each actual native sample bounded instance/sequence provenance that
  distinguishes new work, cached reuse and a recreated sampler for the same
  owned root. Do not use private session/task identifiers.
- Record monotonic sample start/completion and elapsed sampling wall duration.
  Record CPU cost attributable to the sampling worker thread, separately from
  the observed owned process-tree CPU lower bound. Do not mix these totals.
- Unavailable, denied, regressed or invalid timing remains explicit unknown
  with uncertainty, never an invented zero. Timing instrumentation failure
  must not erase a valid process-tree observation.
- Retain completion/accounting evidence for failed samples as well as
  successful ones without converting backend exceptions into successful
  observations. Existing ordinary awaiters still receive those exceptions.
- Native completion, not a cancelled awaiter or bounded join timeout,
  establishes final cost. Pending work remains pending across cancellation,
  provider replacement and loop shutdown; do not overlap native holders.
- Cache reuse retains the same sample provenance and cost. Keep only bounded
  current/last evidence, not an ever-growing sample history.
- Export the bounded accounting evidence through Agent Bridge's existing
  content-free telemetry seam. Preserve valid enumeration, owned identity,
  completion-based cadence and bounded shutdown.
- Add no global observer, parallel process registry, resident helper, service
  restart, elevation or new process-termination behavior.

## Validation

- [ ] New native samples have distinct provenance; cached reuse does not
      manufacture a new sample or duplicate its reported collector cost.
- [ ] Recreated samplers for the same root cannot collide in sample identity.
- [ ] Deterministic clocks verify elapsed wall/thread CPU cost, invalid or
      unavailable clocks and instrumentation failure without lost CPU data.
- [ ] Failed/cancelled awaits retain actual native completion accounting and
      preserve exception semantics without unhandled detached failures.
- [ ] Retained evidence stays bounded under repeated sampling.
- [ ] Native Windows and Linux fixtures produce truthful collector cost
      separately from the reader-owned CPU lower bound.
- [ ] Existing optional-import, PID/creation, single-flight, cadence, native
      holder, fallback, circuit and telemetry privacy contracts remain green.

Cumulative role reduction, fan-out policy, numeric budgets, admission circuits,
no-progress/tree reclamation and authorized release/adoption remain parent gates.
