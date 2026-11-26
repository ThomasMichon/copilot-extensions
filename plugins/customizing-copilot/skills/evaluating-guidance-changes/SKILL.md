---
name: evaluating-guidance-changes
description: >
  Runs a controlled A/B evaluation to show that a change to agent guidance
  (an instruction file, AGENTS.md, a skill, a hook's injected context, or an
  agent definition) does not make the agent worse: fixed, sandboxed scenarios
  with observable pass criteria, a baseline run on the unchanged guidance, a
  rerun on the changed guidance with everything else held constant, and an
  outcome-based comparison with repetitions, variance, cost limits, and
  recorded provenance. Use when asked to prove, measure, A/B test, regression
  test, or benchmark a guidance or skill change, to design scenarios for one,
  or to judge whether a before/after result is a real difference. Not for
  unit-testing code, static linting of skill files (see authoring-skills and
  reviewing-customizations), model or vendor benchmarking, or any evaluation
  that would have to run against live, consequential systems.
---

# Evaluating Guidance Changes

Guidance changes are behavior changes. A new instruction line that fixes one
failure can quietly break three others -- an agent becomes more cautious and
stops finishing work, stops raising a real problem, or starts reading files it
no longer needs. Reviewing the diff does not reveal this; running the agent
does. This skill is the method for a small, honest, controlled before/after
comparison.

It is harness-neutral: the same method applies whether the agent is driven by
a CLI in non-interactive mode, an SDK, or a hosted runner. It is also
stateless: the scenarios and the runner live in the operator's own harness or
knowledge repository, and so do the results -- this skill defines neither.

## When to evaluate

- **Do** evaluate a change to always-on guidance (instructions loaded every
  session), a skill whose trigger or procedure changed materially, a change
  that adds a new prohibition or a new mandatory step, or a change prompted by
  a real incident that you want to prove fixed.
- **Usually skip** a typo, a link fix, or a reword that does not change any
  instruction the agent acts on -- note "no behavioral change" instead.
- **Never** evaluate by pointing the agent at live systems where its actions
  have consequences. If a scenario cannot be sandboxed, it is not an eval
  scenario (see *Safety*).

## The method at a glance

1. Define a small fixed **scenario set** with observable pass criteria --
   before touching the guidance.
2. Run the **baseline**: the unchanged guidance revision, N repetitions per
   scenario.
3. Apply the change.
4. Run the **candidate**: the changed revision, same scenarios, same model,
   same settings, same N.
5. **Compare outcomes** -- pass rates and failure modes -- not self-reports.
6. **Record provenance** and decide: ship, revise, or reject.

## 1. Scenarios

A scenario is a fixed prompt, a fixed sandboxed fixture, and checks that a
machine can decide from what the agent actually did.

**Each scenario has:**

- **Prompt** -- exactly what the agent receives, frozen for the comparison.
- **Fixture** -- the files, fake service data, or stub tools the agent can
  see, copied fresh into an isolated working directory for every repetition.
- **Gating checks** -- observable criteria that decide pass/fail:
  - **Forbidden actions** -- a tool or command that must not be attempted
    (e.g. any write outside the sandbox, any network call, a destructive
    command). Check the *attempt* in the tool-call log, not only its effect.
  - **Required actions** -- a read, lookup, or verification step the agent
    must perform (e.g. it must open the governing file before acting).
  - **Final-answer criteria** -- the final message must contain / must not
    contain specific facts or decisions that matter (e.g. it must name the
    real root cause; it must not claim success).
  - **End state** -- the expected files, outbox items, or fake-service
    records exist (or don't) after the run.
- **Non-gating observations** -- useful signals recorded but not used to
  pass/fail (extra reads, turn count, duration, cost).

**Design rules:**

- Write the scenarios and checks **before** writing the guidance change, so
  the checks measure the intent rather than the implementation.
- Cover the **target behavior** the change is meant to produce, and add
  **control scenarios** for behavior the change must not break -- especially
  cases where the agent must still stop, refuse, ask, or raise a problem. A
  change that makes agents bolder must be tested against a case where
  stopping is correct; a change that makes agents more careful must be tested
  against a case where proceeding is correct.
- Prefer checks on actions and end state over checks on wording. Wording
  checks are brittle; use them only for facts that must appear.
- Keep the set small (a handful of scenarios) and fast; a scenario that needs
  minutes of real work per run will not get enough repetitions.
- Version the scenario set. A changed scenario starts a new baseline.

**An LLM judge or rubric is optional and never gating.** If you use one,
report its score separately from pass/fail, keep the rubric fixed across
baseline and candidate, and treat disagreement between judge and checks as a
prompt to improve the checks.

## 2-4. Baseline, change, candidate

- The **baseline** is the guidance exactly as it is before the change (for a
  version-controlled harness, the committed revision). The **candidate** is
  the same tree with only the change applied.
- Hold everything else constant: model, reasoning effort, tool set, enabled
  plugins, scenario versions, fixture contents, runner version, and timeouts.
  If any of these must differ, the comparison is not A/B -- rerun the
  baseline under the new conditions.
- Run baseline and candidate **close in time** so provider-side changes do
  not masquerade as guidance effects; interleave them when practical.
- Isolate each repetition: a fresh fixture copy, a fresh session, no carried
  memory or cached state between repetitions unless the scenario is
  specifically about that state.

## 5. Comparing results

Compare what happened, not what the agent said about it.

- **Outcome, not self-score.** The agent's own claim of success, confidence,
  or compliance is not evidence. Use the gating checks.
- **Repetitions.** Agent behavior is stochastic. Use at least 3 repetitions
  per scenario per side for any claim that gates a decision; more for
  scenarios near a threshold. A single run (1/1) is anecdotal -- it can show
  that a failure is *possible*, not that a change fixed it.
- **Report rates with their denominators** (e.g. 5/6 vs. 2/6), per scenario,
  plus the failure modes behind each failure. Do not average away a scenario
  that went from 6/6 to 3/6 because another went up.
- **Variance.** When the baseline itself is flaky on a scenario, a candidate
  difference of one run is noise. Either add repetitions or make the
  scenario more deterministic before drawing conclusions.
- **Regression rule.** Any control scenario that drops is a regression to
  explain, even if the target scenario improved. "Better on average" does
  not license being worse on a safety or stop-correctly case.
- **Unknowns stay unknown.** If a metric (tokens, credits, a check) could not
  be measured, record it as unknown -- never as zero.
- **Re-scoring.** Save each repetition's raw outputs (tool-call log, final
  message, end state). When a check is found to be wrong, fix it and
  **re-score the saved outputs offline** for both sides rather than
  re-running the agent.
- **Test the checks.** Keep a few recorded outputs with known expected
  verdicts (including ones that must fail) and run the checks against them
  whenever the checks change. A check that never fails is not checking.

## Cost limits

- Set a per-repetition budget (time and, where available, credits or tokens)
  and stop the repetition when it is exceeded; record it as a timeout
  failure, not a pass.
- Estimate total cost before starting: scenarios x repetitions x 2 sides x
  per-repetition budget. Cut scenarios before cutting repetitions.
- Record measured cost per scenario so the next comparison can be budgeted.
- Run repetitions serially (or with a small fixed concurrency) and leave
  headroom for the operator's own work; respect any resource guard the
  harness already has.

## Safety

An eval runs an agent unattended. Treat it as untrusted automation.

- **Non-interactive.** No prompts to a human; disable or stub any
  ask-the-user tool so a scenario cannot stall waiting for an answer.
- **Least privilege.** Allow only the tools the scenario needs -- read-only
  file and shell access plus an allowlist of specific scripts or stubs.
- **Sandboxed side effects.** Stub tools read from fixtures and write to a
  sandbox outbox. No real messages, tickets, commits, pushes, payments,
  deployments, or deletions -- not even "test" ones on real systems.
- **No credentials.** Do not hand eval runs real secrets or production
  tokens; stubs do not need them.
- **No network** beyond what the agent runtime itself requires.
- **Protect the inputs.** Make the fixture and guidance read-only to the
  agent; any write outside the sandbox fails the repetition.
- **Clean up.** Terminate the agent's process tree at the end of every
  repetition. If anything is left running or the sandbox cannot be reset,
  fail the repetition and stop the batch rather than continuing on dirty
  state.

**Fidelity limits.** Stubs and fixtures can pass where reality fails (and
vice versa). State plainly what each scenario mocks, and treat a passing
eval as evidence about the scenarios -- not as proof about production
behavior.

## 6. Provenance and decision

Record, for every comparison, in the operator's own harness or knowledge
repository (not in a shipped plugin, and without secrets or personal data):

- run identifier and date;
- baseline revision and candidate revision (or the exact diff applied);
- model, reasoning effort, runner version, and relevant settings;
- scenario-set version and the list of scenarios run;
- per scenario and side: pass count / repetitions, failure modes, duration,
  and cost (or "unknown");
- any check changes and whether outputs were re-scored;
- the decision and its reason.

Decide:

- **Ship** when target scenarios improve (or hold) and no control scenario
  regresses beyond noise.
- **Revise** when the target improves but a control regresses, or the result
  is within noise -- change the guidance or the scenarios and run again.
- **Reject** when the target does not improve or a safety-relevant control
  regresses.

Link the recorded result from the change's description so reviewers can see
the evidence, and rerun the comparison when the model or runner changes
materially -- a past result is evidence about that configuration only.
