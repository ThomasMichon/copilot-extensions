# Effort Agenda Companion - Inception Exchange

This is the historical inception record for the
[canonical effort](README.md), not a second plan or a runtime instruction.
The final operator direction below governs ownership and scope where it differs
from the initial investigation proposal.

Publication treatment: personal relationship attribution in the initial request
is replaced with an explicit omission marker, and nontechnical persona reactions
are omitted from the agent proposal. Substantive requirements and proposal text
are otherwise preserved. Formatting is normalized for this public document.

## Initial operator request

> [Personal relationship attribution omitted.] GraphCode:
> https://github.com/scgopi/GraphCode/
>
> The core essense it that it provides a way to descrbe agentic workflows as loopable graphs. I envision it kind of like an old-school board-game board: each player (agent) is on a square, and to move to the next square they perform the task on the square. Given the outcome, they advance advance a square, go down one of the branch-points of the square, or get reset to another location.
>
> When we allocate a worktree, there comes a point where we want the agent to pick up an agenda, and adhere to it. We allow agents to plan efforts, answer questions, and do experiments, but we need a way to make long-running agents "stick to the plan". While handoffs and context management, combined with worktree effort binding and claims management, give an agent *some* "rails" to focus on, a lot of times agents will "spiral" down odd paths. I'd love a way that we can formalize an effort into a companion/sidecar data structure, and then use a system to let the agent follow a state machine to keep them on a given task until they meet its exit condition, and then advance/branch/reset based on allowed options. I don't want to be as exacting as GraphCode, but we do need a way where a Worktree can have its runtime status guided a bit more, and refresh the next agenda item based on the backing effort and overall sequencing. A key scenario is "effort-nesting": when an agent detours to fix and issue and drafts a mini-effort, once it's done we need the agent to resume the main effort. This is handled somewhat with claims and binds, but the direction is minimal. Investigate applying GraphCode-style principles to efforts, providing a companion "runtime framework" agents can set up and follow for themselves in a worktree (voluntarily) that keeps them on-task.

## Investigation proposal

The following is agent-originated inception text. In particular, its suggestion
to place the runtime with the worktree owner is historical, not the current
design decision; the later operator request places the companion in efforts.

### Recommendation

Build an **opt-in effort agenda**: a coarse graph tied to the canonical effort,
with a durable worktree cursor and a **continuation stack for nested detours**.

Borrow GraphCode's explicit transitions and bounded loops - not its process/session
runtime. The agent remains free to reason, experiment, and choose implementation
details **within its current square**; changing squares becomes an explicit,
validated operation.

### What GraphCode contributes

The inspected [GraphCode implementation](https://github.com/scgopi/GraphCode/blob/4088986d3e6d8ab4a938caae0d75f6b9f7e7d938/GraphcodeKit/Sources/GraphStore.swift#L3490-L3519)
has real graph execution, including handoff, message, and spawn edges. Its
[cycle logic](https://github.com/scgopi/GraphCode/blob/4088986d3e6d8ab4a938caae0d75f6b9f7e7d938/GraphcodeKit/Sources/GraphStore.swift#L3755-L3790)
evaluates stopping predicates and no-improvement conditions before re-firing.

The important distinction: **GraphCode's nesting is not the effort-nesting
mechanism you need.** It persists child subgraphs and
[rolls their terminal state up into the composite node](https://github.com/scgopi/GraphCode/blob/4088986d3e6d8ab4a938caae0d75f6b9f7e7d938/GraphcodeKit/Sources/GraphStore.swift#L1624-L1636).
It does not save an arbitrary parent execution point, pursue a detour, then
restore that continuation.

That call/return contract would be our principal addition.

### The smallest useful model

Keep three distinct authorities:

| Artifact | Owns |
|----------|------|
| Effort `README.md` | Intent, Plan, Validation Plan, Journal, and completion |
| Companion `agenda.yaml` | Permitted sequencing, branches, loops, and exit requirements |
| Managed worktree runtime state | Current position, attempts, evidence references, and suspended parent frames |

The companion should **reference plan items**, not reproduce their text or
maintain another completion checklist.

Each square needs only a stable ID, linked plan/validation items, its immediate
objective, exit criteria, and named outcomes. For example:

```text
Implement -> Validate -> Review/Merge -> Reconcile effort
    ^           |              |
    +-- failed -+              +-- changes requested -> Implement
```

These are broad work packages - not individual commands. Validation could encompass
several experiments and fixes without requiring a graph edit.

The runtime's agenda response should answer:

> **You are here. This is the current objective. These conditions permit leaving.
> These are the allowed next moves. This parent objective remains outstanding.**

An agent could voluntarily derive the companion from an effort and attach a run
to its worktree. Efforts without one would behave exactly as today.

### Make nesting a first-class operation

A detour must be a **call**, not merely another effort binding:

```text
Main effort / Validate
  call: Fix prerequisite bug
    call: Investigate underlying defect
    return: Validate prerequisite fix
  return: Re-run main effort validation
```

Before activating the child, persist the parent effort, node, definition revision,
reason for detour, and return target. Preserve the root objective throughout.

When the child reaches its actual completion gate:

1. Record its result and supporting evidence.
2. Restore the saved parent position exactly once.
3. Refresh the parent agenda against current reality.

Returning should usually mean **revalidate**, not blindly repeat the interrupted
action: the child may have changed code, dependencies, or external state.

A blocked child leaves the continuation visible. A finalized child worktree or
merged child PR does not, by itself, prove the child effort complete. Likewise,
completing the child must never permit finalizing while the root effort remains
open.

Parallel delegation is different: it creates a dependency and existing resource
obligations, not a blocking continuation frame.

### Fit it into the existing machinery

There is substantial substrate already:

- **Efforts** provide the canonical contract and completion gate.
- **Worktrees** provide active-effort binding, rightful-head identity, status, and
  cleanup protection.
- **Claims** represent resource custody - not "where to resume."
- **Dispatch** already provides [loop recipes](https://github.com/ThomasMichon/copilot-extensions/blob/1603cbbc0ac1f5a8838154d724e90eb4fa9f2724/plugins/agent-dispatch/src/agent_dispatch/recipes/registry.py),
  suspension, and resumption.

I would initially put the small agenda runtime **with the worktree owner**, while
keeping effort authoring conventions in `efforts`. Dispatch should remain the
generic execution/wake substrate, not acquire effort-specific node states. No
new daemon or queue is necessary for a pilot.

The execution cycle becomes:

```text
Read agenda -> Perform a work unit -> Submit outcome/evidence
            -> Validate transition -> Persist -> Read refreshed agenda
```

Startup, resume, and handoff should refresh that same durable run - not reconstruct
its position from conversational summaries.

There is one deliberate compatibility change: the
[current efforts vision](https://github.com/ThomasMichon/copilot-extensions/blob/1603cbbc0ac1f5a8838154d724e90eb4fa9f2724/visions/plugins/efforts/README.md#L45-L49)
derives the current slice from the first unchecked item. Preserve that default;
explicitly extend it for opted-in execution cursors. **The cursor answers "where
execution resumes," not "what is complete."**

### Guardrails that matter

Transitions should require the current owner, an expected runtime revision, a
permitted edge, and appropriate evidence. Tests and reviews should identify the
revision they validate; exploratory conclusions can use explicitly attributed
judgment rather than pretending everything is mechanically provable.

Repeated failure or no-progress loops should reach a declared replan/steering
state - not silently wander elsewhere. Graph edits need explicit reconciliation
with an existing cursor. Ordinary Journal additions should not invalidate the
graph.

Recovery must reconcile uncertain external effects before retrying them. An
idempotent cursor transition does **not** make a merge, deployment, or other
action exactly-once. A graph "reset" must never imply resetting Git, restarting
processes, or discarding obligations.

Most importantly, **this is cooperative control, not a cage**. It can enforce
recorded transitions and repeatedly expose the correct agenda. An agent that
ignores the runtime entirely can still drift. Stronger enforcement would require
an external evaluator or tool mediation; a prompt hook alone cannot provide that
guarantee.

### Suggested first slice

Pilot one worktree with a coarse agenda, evidence-gated transitions, and
depth-first child calls. Leave out visual editing, automatic effort splitting,
arbitrary shell predicates, and general parallel graph scheduling.

The decisive validation is **A -> B -> C -> B -> A**, including a crash during
return, a duplicate completion signal, and a stale predecessor attempting
advancement. Also verify that missing evidence prevents movement and child
completion never satisfies the root completion gate.

That would test the missing capability directly: **not merely keeping an agent
alive, but reliably restoring what it was supposed to be doing.**

Investigation only; no repository or deployment changes were made.

## Operator direction after the investigation

> In general I like the nuanced direction you propose. Help land an extension/companion for efforts (in the efforts plugin) that hels give agents this structured way through an effort. Note that I intend this to be a process aide, not something built into agent-dispatch. We could consider a helper CLI toolset to scaffold out one of these, and help maintain/identify a "cursor" in the agenda, of we want more objective driving through these. Take what you wrote so far and set it as the inception text for an effort to build this idea out.

The operator subsequently confirmed the slug `effort-agenda-companion`.

## Operator direction on a paired spike

> Great. We should figure out how to do a spike test, once we have some initial guidance. We can create a worktree, add in this extra guidance layer, and drive an agent to do a task, in parallel with driving an agent in a worktree without the guidance framework to attempt the same task.

The [paired-spike protocol](paired-spike.md) is agent-recommended experimental
design derived from this request, not additional verbatim operator requirements.

## See Also

- [Canonical effort and current proposal](README.md)
