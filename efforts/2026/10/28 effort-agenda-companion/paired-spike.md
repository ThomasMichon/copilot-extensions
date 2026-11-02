# Paired Guidance Spike

This is the **agent-recommended protocol** for Phase 1S of the
[canonical effort](README.md), derived from the operator's request for two
parallel worktree attempts at the same task, one with the guidance framework
and one without it. It is a proposed experiment, not a result or a launch order.

## Question and readiness

Does the first usable agenda guidance help an agent finish a parent task,
handle a prerequisite detour and return to the parent, compared with the
ordinary effort process?

Run only after a reviewed initial guidance candidate and a small manual agenda
are usable. The candidate can be a scoped guidance overlay; do not wait for a
helper CLI, service, full plugin rollout or complete Phase 1 implementation.
Claim the experiment slice on the umbrella issue and agree the fixture,
configuration and finite run budgets before launching either agent.

This is a behavioral spike, not an installation/bootstrap test. Verify setup
before the measured attempts. Do not apply a brittle-witness instruction to stop
at the first task defect: repairing the authorized prerequisite is part of the
behavior being tested. An infrastructure/setup failure is reported separately,
not silently repaired for one arm.

## Matched arms

| Property | Control | Treatment |
|----------|---------|-----------|
| Workspace | Fresh worktree and independent session | Separate fresh worktree and independent session |
| Starting code | Same pinned fixture revision | Same pinned fixture revision |
| Task | Same parent request, facts and acceptance criteria | Identical task input |
| Normal guidance | Ordinary efforts, planning, binding and handoff capabilities | Same ordinary capabilities |
| Extra layer | No candidate agenda framework | Pinned candidate guidance and its companion agenda |
| Agent and limits | Same model/version, reasoning, context/tool permissions and finite budgets | Matched configuration |

Use an eligible disposable adopter/fixture repository, not a source repository
that prohibits managed authoring sessions. These two fixture worktrees are
experimental outputs, not two competing implementations of a live issue.

Present identical task facts, solution hints and exit criteria in both arms;
the treatment's extra information is process structure, not an answer key.
Record the precise common prompt and treatment-only delta. Keep existing
baseline planning aids intact. A control that independently adopts comparable
structure is an observed crossover, not a reason to forbid normal planning or
quietly replace that run.

Do not install the candidate globally or let its files/instructions reach the
control through shared plugin settings, parent context or an effort loaded by
both agents. Keep trial instructions separate from this research plan.
Verify which guidance was actually made model-visible; file presence alone is
not proof that the treatment received it.

The arms must not read each other's outputs or state. Worktrees share Git
metadata, so forbid shared stash/ref manipulation, peer-branch inspection and
pushes/PRs during the attempts. Use separate run-local cursors, logs and
session/runtime profiles where needed. Neither arm changes global plugin state,
shared services or live data.

## Task shape

Use a small, synthetic code task with an observable end state:

1. A parent change with explicit externally checkable behavior.
2. A real prerequisite defect in the fixture that must be repaired to finish it.
3. Completion of that detour followed by renewed work and validation of the
   original parent change.

For example, add an option to a small record transformer whose existing parser
has a relevant edge-case defect. Both agents get the same code, existing tests,
task boundaries and permitted scope. The candidate agenda may organize the
detour as a child effort; scoring uses the same semantic milestones in either
arm, not whether the control happens to author a cursor or child-frame syntax.

Ordinary investigation needed to find and repair the prerequisite is allowed;
it is not automatically classified as drift. Holdout checks may remain outside
the editable worktrees, but required behavior is stated equally to both agents.
Have the evaluator run acceptance checks against each captured result rather
than trusting an agent's own completion claim or rewritten tests.

Start with one prerequisite detour and no artificial context interruption.
If the basic experiment is informative, a later matched pair can add nested
detours or a pause/resume at the same semantic milestone in both arms.

## Conduct

- Start both agents in parallel with matched resource availability; record
  contention or infrastructure asymmetry rather than calling it a guidance effect.
- Agree finite time/turn/cost limits before starting. Limit exhaustion is an
  observed outcome, never permission to mark the task complete.
- Do not coach only one arm. Any necessary operator intervention is recorded;
  a material asymmetric intervention makes the affected pair unsuitable for a
  clean paired comparison.
- Capture each attempt's task result, diffs, validation results, progress and
  detour/return trajectory independently. Retain raw run artifacts in the
  established machine-local results location, not the public source tree.
- Verify task fixtures and the intended presence/absence of candidate guidance.
  Mark a setup-contaminated pair invalid; report the reason without selectively
  rerunning only the worse-looking result.
- Agents stop at their task outcome or agreed limit without publishing a PR.
  The experiment owner captures results and settles the two worktrees through
  their normal lifecycle and applicable cleanup approvals.

## Scoring and interpretation

Predeclare task-level scoring before either launch:

| Measure | Evidence and interpretation |
|---------|-----------------------------|
| Parent correctness | Same independent acceptance checks on both outputs |
| Detour and return | Prerequisite fixed; original task resumed and validated, rather than abandoned after the repair |
| Completion honesty | Claimed completion agrees with actual parent and prerequisite outcomes |
| Scope adherence | Unnecessary off-task changes/actions, distinguished from justified discovery and task work |
| Assistance | Operator nudges, interventions or unplanned restarts needed to reach the outcome |
| Cost and overhead | Available elapsed time, turns, tool calls and account-attributed usage; unavailable token/cost data stays unknown |

Do not award success merely for following the graph while producing an
incorrect result. Likewise, do not penalize the control for lacking
treatment-specific artifacts. Include agenda overhead in the treatment's cost.
No-progress loops and false completion are outcomes worth recording even if
both agents eventually pass the functional tests.

Evaluate functional outputs under neutral arm labels before examining
process traces when practical. Keep objective task checks separate from the
necessarily interpretive trajectory review, and do not claim perfect blinding
when the guidance is visible in those traces.

One pair is a feasibility observation, not a statistically established benefit.
Record the actual configuration, exposure, outcomes, validity limitations and
guidance revision. Use the result to revise the candidate or propose bounded
fresh-session replications, including changing arm/venue assignment where
practical. Do not cherry-pick a favorable pair or add a new automated workflow
engine to run this experiment.

## See Also

- [Canonical effort, Plan and Validation Plan](README.md)
- [Operator request and inception](inception-transcript.md#operator-direction-on-a-paired-spike)
