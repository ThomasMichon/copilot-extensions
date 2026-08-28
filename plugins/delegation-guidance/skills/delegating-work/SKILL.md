---
name: delegating-work
description: >
  Routes broad, separable work into bounded sub-agents while keeping synthesis
  and completion with the coordinating agent. Use when asked to delegate
  research, use agents to compare or evaluate options, parallelize independent
  investigation, isolate domain MCP/service calls, or split disjoint bulk code
  or file edits; also use proactively before opening broad multi-subsystem
  research, evaluating several candidate options, or editing many files across
  disjoint components. Not for choosing a named domain agent or authoring an
  agent definition.
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
| Domain MCP or service-tool calls with verbose catalogs or payloads | Route to the domain-owning sub-agent |
| Disjoint bulk edits with clear file ownership | Delegate by non-overlapping file or component scope |
| Cohesive implementation with tightly coupled decisions | Keep coordinator-owned by default |
| Required independent review roles | Run each role once on an unchanged artifact |

Do not split work merely to maximize agent count. Delegation should reduce
coordinator context pressure or elapsed time enough to repay coordination cost.

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
