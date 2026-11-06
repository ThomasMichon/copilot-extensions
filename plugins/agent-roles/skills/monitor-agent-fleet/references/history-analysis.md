# History analysis and time accounting

Back to [Monitor agent fleet](../SKILL.md).

## Preserve the denominator

Choose an observation cutoff and period before collection. Read only authorized
histories for the selected owners and their relevant predecessors. A partial
sync, deferred live transcript or database snapshot without its current journal
is historical evidence, not a current roster.

Use the source's documented event/turn reader. Preserve timestamps, identities,
user scope changes, tool starts/completions, arguments relevant to the
experiment, results, explicit waits, approvals and acceptance. Omit streaming
text deltas and repeated boilerplate from the analysis copy without discarding
their source range. Redact sensitive payloads; retain source pointers instead
of copying credentials or unrelated conversation.
Distinguish original operator messages from embedded delegated prompts,
scheduled instructions and peer envelopes; their shared `user.message` event
type does not make them new human approval.

Partition chronologically with non-overlapping accounting intervals. Give each
reader enough boundary context to understand an in-flight tool or prerequisite,
but assign the overlap to exactly one interval. Each chunk returns:

```text
Source / session / event range / UTC interval / completeness
Root and any genuine operator scope changes
Work episodes: criterion, action, changed inputs, result, category, confidence
Explicit waits: condition, responsible owner, start and observed end
Repeated attempts: comparable signature, material changes, information gained
Open tools and unknown gaps; parent return and latest remaining acceptance
```

Unreviewed chunks stay visible. Do not extrapolate a keyword sample or latest
page to the entire lifetime.

## Classification

| Category | Evidence needed |
|----------|-----------------|
| Goal delivery | Implementation, validation or accepted output closes or informs a root criterion |
| Necessary prerequisite | A specific dependency blocks that criterion; its exit and saved parent return are known |
| Likely avoidable repetition/detour | Comparable attempts produce no new relevant evidence, or work has no demonstrated dependency/assignment |
| Operator-authority wait | A specific unresolved decision/permission is explicitly requested or held |
| Upstream review/CI wait | A named required external gate is pending; separate active response/fix time |
| Infrastructure/contention | Transport, admission, environment or capacity evidence prevents the intended experiment |
| Coordination/bookkeeping | Necessary handoff, plan reconciliation or status work; assess repeated overhead separately |
| Unknown | Missing history, ambiguous activity or a gap with no defensible attribution |

These are explanations, not automatic blame. An authorized detour can still
be inefficient; an unexpected failing experiment can be highly informative.
Keep confidence and counterexamples alongside the diagnosis.

## Quantification

- **Elapsed age:** first-to-last observation. Includes sleep, disconnections,
  external waits and unknown gaps. Never call it active work.
- **Observed tool time:** pair actual starts/completions. Union overlapping
  spans for elapsed-time accounting; summed child runtimes are a separate
  resource measure, not additional parent wall time.
  Exclude synthetic missing-result completions across restarts; they do not
  establish that a tool ran throughout the disconnected interval. Keep
  ingestion timestamps and a result's own elapsed measurement distinct.
- **Observed agent activity:** use native turn/activity intervals when provided.
  Do not treat an arbitrary gap between messages as continuous thinking.
  If estimating from event-adjacent windows, state the window rule and give a
  sensitivity range, not falsely exact minutes.
- **Wait time:** use an explicit condition and observed boundaries. An open wait
  is right-censored at the cutoff; it may overlap productive parallel work.
  Show that overlap instead of forcing both into an exclusive time pie.
- **Counts:** test attempts, tool calls, scheduled prompts and review rounds are
  useful frequency measures. Their percentages describe counts, not time.
- **Usage:** tokens/credits only from attributable telemetry. They measure
  resource consumption, not useful progress or an agent's merit.

Only compute a classified-time percentage when the denominator and interval
assignment are defensible. State coverage, unknown share, overlap handling and
whether necessary prerequisites are included in on-goal work. If not, report
episode counts and measured lower bounds instead.

## Detect loops with causal context

Compare repeated attempts by the affected source/revision, selected tests,
phase that failed, failure signature, hypothesis, environment/profile and
observed result. A new timestamp, output path, receipt or paraphrase alone
does not make an experiment new. A changed hypothesis needs a corresponding
measurement that can discriminate it.

Do not mistake a shell tool returning "still running" after its initial wait
for a killed or timed-out test. Read the existing command's eventual result.
Distinguish setup, collection, test body, native child, cleanup, outer
containment and observer deadlines. Native qualification is not one universal
failure class.

For service charters, also measure event capture-to-consumption and
consumption-to-first-action latency, backlog age and unchanged-thread silence.
A timer firing regularly does not prove the agent consumed or acted on intake.
