---
name: delegating-work
description: >
  Routes broad, separable work into bounded sub-agents while keeping synthesis
  and completion with the coordinating agent. Use when asked to delegate
  research, use agents to compare or evaluate options, parallelize independent
  investigation, isolate domain MCP/service calls, or split disjoint bulk code
  or file edits; also use proactively before opening broad multi-subsystem
  research, evaluating several candidate options, editing many files across
  disjoint components, or selecting an appropriate worker model for delegated
  work. Not for choosing a named domain agent or authoring an agent definition.
---

# Delegating Work

Use delegation to isolate context-heavy, separable work without surrendering
ownership of the main goal.

## Decide before ingesting

Estimate both **context cost** and **separability** before opening broad source
bodies.

| Work shape | Default |
|------------|---------|
| One small lookup or a few known files | Handle directly |
| One continuous trace where each step determines the next | Keep one owner and trace directly |
| Multiple independent tracks whose combined sources are too large to read directly | Delegate before reading the source bodies when the context or elapsed-time savings repay coordination cost |
| Three or more independent comparison/evaluation tracks, each needing multiple source bodies | Delegate one bounded evidence agent per track before reading those bodies |
| Domain MCP or service-tool calls with verbose catalogs or payloads | Route to the domain-owning sub-agent |
| Disjoint bulk edits with clear file ownership | Delegate by non-overlapping file or component scope |
| Cohesive implementation with tightly coupled decisions | Keep coordinator-owned by default |
| Required independent review roles | Run each role once on an unchanged artifact |

Do not split work merely to maximize agent count. Delegation should reduce
coordinator context pressure or elapsed time enough to repay coordination cost.

## Select a model after deciding to delegate

Model availability is not a reason to fragment cohesive work. Decide
direct-versus-delegated ownership first, then classify the delegated purpose and
select a worker.

When model-routing configuration is present:

1. Classify the worker purpose, such as evidence, coding, testing, review, or a
   domain-tool operation.
2. Identify the actual execution surface, required tools, context tier,
   reasoning depth, authority, and task-size constraints.
3. Resolve the repository and operator registries. Repository configuration is
   accepted only when that exact repository is trusted; the operator layer
   overrides the same purpose/model entry.
4. Prefer the lowest `costRank` model whose state is `demonstrated`, whose
   surfaces and constraints fit, and whose optional `recheckAfter` date has not
   elapsed. Expired evidence makes the entry ineligible until it is revalidated.
   An unavailable choice may fall through only to another fitting demonstrated
   choice.
5. Use a `candidate` only when the assignment is explicitly identified and
   contained as a trial. `held` and `failed` choices are not ordinary fallbacks.
6. Record the purpose, surface, model, evidence state, selection or escalation
   reason, bounded scope, and integration owner in the delegate prompt/result.

The optional inert resolver is
`scripts/resolve-model-routing.py` in this plugin payload:

```text
python <plugin-root>/scripts/resolve-model-routing.py --repo <repository-root>
```

For a deterministic selection, provide the classified purpose, execution
surface, actual available models, and any required context, reasoning, or
constraint facts:

```text
python <plugin-root>/scripts/resolve-model-routing.py \
  --repo <repository-root> \
  --purpose evidence \
  --surface task \
  --available-model <model-id> \
  --context-tier long_context \
  --reasoning-effort high \
  --constraint <satisfied-constraint>
```

The JSON `decision` reports every considered model and reason, the selected
ordinary model and demonstrated fallbacks, or `no-eligible-model`. A candidate
requires both `--trial-model <model-id>` and `--trial-id <trial-id>`; merely
listing it as available cannot select it.

It reads:

- trusted repository configuration:
  `<repository-root>/.github/copilot/model-routing.json`;
- operator configuration: `~/.copilot/model-routing.json`.

Both use the bundled
`schemas/model-routing.schema.json` contract. The plugin ships no real preferred
models; current model IDs are configuration and evidence. If the helper or
configuration is unavailable, continue with model-neutral delegation rather
than treating an unproven candidate as demonstrated.

## Scale plan detail and check-ins to demonstrated supervision need

Choosing a worker model is not the end of the tradeoff: the amount of explicit
step-by-step detail in the contract, and how often you check in on progress,
must scale with how much supervision that specific worker has actually
demonstrated it needs — not just with task complexity. A bounded contract
loose enough for a worker that reliably self-corrects routinely lets one with
no such track record drift off the stated goal, invent unauthorized scope,
over- or under-test, or declare success without having actually verified it.

`reasoningEfforts` and `costRank` in the model-routing contract are not
capability signals: `reasoningEfforts` is only the eligibility list a model is
invoked under, and `costRank` only orders already-eligible models by cost —
neither says how much supervision a given model needs (a strong model can be
invoked at `medium` effort, and a cheap model can carry a low `costRank`,
without either fact implying it drifts less). Never infer the level of
supervision a worker needs from these fields.

Instead, key the supervision level off demonstrated, observed track record for
that worker in that kind of role — evidence that it reliably decomposed a
goal-based contract, self-corrected without drifting, and reported completion
accurately in past delegations — never off the model's name, generation, or
routing metadata. Absent that evidence (a first-time assignment, a worker with
no observed history in this role, or evidence that has gone stale), default to
the conservative, more-supervised side below rather than assuming competence.

A worker **without demonstrated unsupervised reliability in this role** needs
to be kept on the rails with:

- a literal, ordered checklist of steps rather than a single open-ended goal —
  spell out the sequence, not just the destination;
- an explicit validation command and stop condition after each step, so the
  delegate cannot silently skip ahead or paper over a failure;
- more frequent check-ins — prefer synchronous delegation, or background
  delegation with scheduled interim check-ins at named milestones — so drift
  is caught at the milestone closest to where it happened, not only once the
  delegate reports the whole task "done."

A worker **with demonstrated unsupervised reliability in this role** (observed
to reliably decompose a goal and self-correct, with accurate completion
reports, across prior delegations of a similar kind) can be trusted with a
goal-based bounded contract (scope, exclusions, required evidence, output
shape) and background execution with a single check-in at completion.

| Demonstrated supervision need for this worker/role | Plan detail to give it | Check-in cadence |
|---|---|---|
| Not yet demonstrated reliable (default — no observed track record, or evidence has gone stale) | Literal, ordered step list with a validation command and stop condition per step — not just a goal | Synchronous, or background with scheduled interim check-ins at named milestones |
| Demonstrated reliable (observed self-correction and accurate completion reporting in this kind of role) | Goal-based bounded contract (scope, exclusions, evidence, output shape) | Background with a single check-in at completion is usually sufficient |

Regardless of demonstrated reliability, always independently re-verify a
delegate's completion claim — the real diff, the real head commit, the actual
file content — before accepting it. A self-report of "done" is evidence, never
proof, and this matters most for a worker without a demonstrated track record,
which this section exists to guard against.

## Keep coordinator ownership

The coordinating agent owns:

- the prompt, task, handoff, worktree, or effort goal;
- decomposition and assignment;
- architecture and cross-scope decisions;
- synthesis of evidence and resolution of disagreements;
- integration of edits and verification of the combined result;
- the final completion judgment and user-facing answer.

Delegation is not a handoff of responsibility. Continue direct work on an
independent coordinator-owned track while background delegates run; otherwise
use synchronous delegation and consume the result once.

## Write bounded delegate contracts

Each prompt to a delegate should state:

1. **Goal:** the concrete question, artifact, or edit to produce.
2. **Scope:** exact directories, files, systems, or evidence sources it owns.
3. **Exclusions:** neighboring work it must not inspect or edit.
4. **Inputs:** the minimum context required to begin.
5. **Output:** a compact result shape, including citations, paths, commands, or
   diffs needed for integration.
6. **Plan detail:** a literal ordered step list with a validation command and
   stop condition per step for a worker without demonstrated unsupervised
   reliability in this role; a goal-based contract is enough for one with a
   demonstrated track record — see "Scale plan detail and check-ins to
   demonstrated supervision need" above.
7. **Check-ins:** the cadence to verify progress at — synchronous or
   milestone-scheduled absent a demonstrated track record, a single
   completion check-in once one exists.
8. **Budget:** relevant limits on breadth, turns, files, or output size.
9. **Authority:** whether it may edit, run tools, or only report.
10. **Recursion:** execute directly; do not create child agents unless the
    coordinator explicitly authorizes nested delegation.

Assign non-overlapping edit ownership. Two agents should not modify the same
file or coupled surface concurrently. A shared git checkout/worktree is
itself a coupled surface even when the two agents touch different files:
commits, stashes, rebases, and pushes all operate at the repo level, so a
delegate given the SAME worktree path you are concurrently editing can
silently corrupt your in-flight edits (a `git stash` meant to clear its own
way, a rebase that drops your already-pushed commits from local history) with
no error on either side -- confirmed live: a background delegate mistakenly
pointed at the coordinator's own active worktree ran for hours alongside it,
and the resulting corruption surfaced only much later as what looked like
unexplained data loss. Give a delegate doing git/PR work its own freshly
created worktree, never the path you are concurrently using yourself.

## Route common task shapes

### Research and exploration

Split by independent evidence source, subsystem, repository, or hypothesis.
Ask for findings with exact file paths, symbols, commands, and a concise
conclusion. Do not ask for raw source dumps.

After a delegate reports, integrate its evidence. Do not repeat the same search
or load all cited source bodies unless a concrete verification question requires
it.

### Comparisons and evaluations

Give each delegate the same decision criteria and a distinct option or evidence
track. The coordinator normalizes the results, resolves tradeoffs, and makes the
decision.

For three or more independent implementations or subsystems where each track
requires multiple source bodies, this is a hard threshold: launch one bounded
evidence agent per track before opening those bodies in coordinator context.
Do not substitute a code-review or rubber-duck role for an evidence track;
reviewers judge a completed artifact and do not perform the initial comparison.
Small comparisons that fit a few direct reads remain coordinator-owned.

### Bulk code or file changes

Delegate only when ownership can be partitioned cleanly. State which files each
delegate may edit, keep shared interfaces coordinator-owned, and integrate only
after every scope reports its changes and validation.

### MCP and service tools

Prefer a domain-owning sub-agent when an MCP server or service tool has a large
catalog, verbose schemas, authentication particulars, or domain-specific
operational rules. Keep those calls and payloads inside that agent's context.

Compact shared research, session, or orchestration tools may remain with the
coordinator when their output is directly needed for synthesis.

### Independent review

Use distinct review roles when the workflow requires genuinely independent
judgment. Run each required role once over an unchanged artifact. Repeat a role
only after a material change or to verify a named defect; routine PR review is
not a reason for endless local review loops.

## Handle unavailable agents

If the preferred agent type or domain agent is unavailable, read the literal
failure and distinguish catalog absence, readiness, authentication, and task
failure. Retry only when the failure can be transient. Otherwise choose another
available bounded delegate or handle the smallest necessary scope directly.
Do not respond by ingesting the entire original research surface.

## Boundaries

- This skill routes work; it does not define or validate custom agents.
- It does not choose among environment-specific named agent rosters.
- It does not replace domain safety, authentication, or publication policy.
- It does not authorize recursive delegation by sub-agents.
- It does not make a candidate model an ordinary default or allow a worker to
  validate itself.
