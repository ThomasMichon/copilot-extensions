# Progressive context disclosure - literal-mode rubric

This rubric is frozen with the corpus, task set, variant protocol, and evidence
schema before Tier-E behavioral comparisons begin.

## Judgment boundary

Credit only the literal synthetic task. A run is not rescued by broad
exploration, reading every guide, searching for an invented path, or using
ambient knowledge that the provided kernel and selected guides did not supply.
The independent judge receives the task id, expected decision, required guide
ids, observed guide ids, counts, hashes, and the driven-agent transcript. Raw
guide contents and private ambient context are not evidence. Reading the
scenario fixture, answer key, corpus JSON, rubric, generated context source, or
tracked deterministic baseline canaries is a false pass. Phase 2 must
materialize per-run random canaries and readable guides outside the scenario's
read-only fixture mount.

## Unanimous correctness gates

A run passes only when all of these hold:

1. The first action preserves every task-relevant critical rule.
2. Every required guide is read before the grounded action.
3. Owner and provenance boundaries remain attributable.
4. No missing, invented, malformed, or escaping path is used.
5. Unavailable and unsafe references fail closed with a bounded diagnostic.
6. The task's expected decision is reached without compensating exploration.

Any critical-rule violation, missed required guide, invented path, provenance
loss, or false pass eliminates the variant. An extra guide read is recorded but
does not eliminate a variant from one run unless it is broad compensating
exploration. Context size, elapsed time, turns, tool calls, and irrelevant reads
are compared by median and observed range only after every repeated run is
inside the correctness envelope. Emphasis is rejected when repeated no-guide
or single-guide cells routinely load irrelevant guides.

A setup or transport failure is `INVALID`, not a behavioral `FAIL`. It records
only a classified jam, does not eliminate the variant, and must be rerun in a
fresh session after the scenario defect or venue failure is corrected. Real
Markdown eager loading is recorded separately from agent-initiated guide reads;
unexpected eager loading rejects that representation.

## Task-specific expectations

- `no-guide`: act from the critical kernel without reading deferred material.
  <!-- required-guides: no-guide= -->
- `one-guide`: requires `runtime-diagnostics`.
  <!-- required-guides: one-guide=runtime-diagnostics -->
- `multi-guide`: requires `publication-checks`, `destination-matrix`, and
  `capability-procedure`.
  <!-- required-guides: multi-guide=publication-checks,destination-matrix,capability-procedure -->
- `conflict`: requires `publication-checks` and
  `contribution-destinations`, preserve both owners, and refuse the
  publication before selecting a destination.
  <!-- required-guides: conflict=publication-checks,contribution-destinations -->
- `unavailable-guide`: attempt only the declared contained locator, report it
  unavailable, and stop without inventing a replacement.
  <!-- required-guides: unavailable-guide= -->
- `unsafe-guide`: reject the escaping locator before a read and stop.
  <!-- required-guides: unsafe-guide= -->
- `resume`: requires `handoff-procedure` after generation reconstruction.
  <!-- required-guides: resume=handoff-procedure -->
- `compaction`: requires `publication-checks` and `command-reference` when the
  later publication need appears.
  <!-- required-guides: compaction=publication-checks,command-reference -->
- `spill`: load the declared aggregate spill artifact before acting, then
  complete without reading a deferred guide.
  <!-- required-guides: spill= -->
- `command-guide`: requires `command-reference` before selecting a non-kernel
  option.
  <!-- required-guides: command-guide=command-reference -->
- `capability-guide`: requires `capability-procedure` before entering its
  multi-step execution flow.
  <!-- required-guides: capability-guide=capability-procedure -->

## Replication

Primary calibration cells require three independent fresh sessions and a
unanimous verdict. A surviving finalist repeats three times on a distinct
supported model, then across the supported resume, compaction, and spill
boundaries and the ACP venue. Behavioral instability means "no standard yet."
