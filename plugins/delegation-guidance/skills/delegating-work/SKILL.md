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
6. **Budget:** relevant limits on breadth, turns, files, or output size.
7. **Authority:** whether it may edit, run tools, or only report.
8. **Recursion:** execute directly; do not create child agents unless the
   coordinator explicitly authorizes nested delegation.

Assign non-overlapping edit ownership. Two agents should not modify the same
file or coupled surface concurrently.

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
