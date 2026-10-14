# Continuous Worktree Reclamation - Vision

- **Subject:** safe, continuous reclamation of locally managed workspaces.
- **Scope:** leaf; child of agent-worktrees.
- **Status:** Active
- **Last revised:** 2026-10-13
- **Reality docs:** agent-worktrees cleanup, GC and resident lifecycle surfaces.

## Purpose & Intent

Workspaces should be collected as they become safely disposable, rather than
accumulating until an operator must run a large recovery sweep. Under normal
workload, reclamation keeps pace with allocation without making interactive
work slower or making deletion more aggressive.

## Concepts & Components

### One reclamation authority

agent-worktrees owns eligibility, final lifecycle admission, checkout removal and
durable archival. Continuous maintenance composes those same operations with the
existing resident authority; it does not create a second cleanup service or a
competing interpretation of safety.

### Allocation owners and disposal

An allocator remains accountable for its created resources and terminal
settlement. Completing a task or ending a session is not independent permission
to remove its worktree. Semantic conclusion of unfinished objectives remains
separate from mechanical collection of already-proven disposable space.

### Host-wide, authority-scoped coverage

Maintenance covers every locally registered worktree-class repository within
the resident's installation and adoption authority, including repositories with
no open user interface. Registration is discovery, not permission to cross
another installation, remote host or repository's contribution boundary.

## Features

### continuous-safe-reclamation

Eligible workspaces are collected periodically and after relevant lifecycle
changes, under each repository's retention and disposal policy. Bounded
rediscovery recovers missed notifications and restart gaps.

### fair-bounded-maintenance

Repositories and candidates receive fair progress. Large backlogs, a locked
record or an unreachable dependency cannot monopolize maintenance or delay
interactive reads, writes, launches and shutdown.

### inspectable-reclamation

Operators can distinguish eligible backlog, retention waits, safety holds,
contention, failures and completed removals. Dry-run, pause/disable and selective
inspection use the same policy as automatic maintenance. Durable identity,
lineage and completion evidence remain available after a checkout is gone.

### workload-proportionate-cost

Unchanged workspaces do not repeatedly incur full fleet discovery, process
startup or repository network refresh. Expensive evidence is shared where its
authority permits; mutation still obtains the fresh evidence it requires.
Steady-state throughput, foreground latency and recovery from bursts are
measurable rather than inferred from the existence of a timer.

## Behaviors

### safety-is-revalidated-not-cached

Age, visibility, an empty diff or a cached cleanup verdict never establishes
disposability alone. Live or unknown execution, unfinished objectives, dirty or
unmerged content, unresolved claims, open publication, pending handoffs and
uncertain ownership preserve the workspace. Paired workspaces are eligible only
when both responsibilities are safely settled and reciprocal identity is proven.

### responsibility-remains-with-its-owner

Collection cannot silently release another actor's claim, abandon its objective,
attest legacy ownership or convert unknown cleanup history into completion.
Historical and explicitly disposable allocations follow their own established
admission policy, not an inference from their naming or lack of conversation.

### contention-is-a-visible-deferral

Lock contention and unavailable evidence cause bounded deferral with attributable
reasons and retries that allow other work to proceed. Repeated failures become
operator-visible; neither timeout nor an old lock file authorizes forced removal.

### maintenance-obeys-resident-lifecycle

Admitted maintenance is included in the existing resident's demand, draining,
exclusive ownership and restart model. A successor reconciles interrupted work
before continuing. Intentional disable or shutdown does not resurrect maintenance
or leave an unaccounted destructive worker behind.

### allocation-overload-is-not-deletion-authority

Sustained allocation beyond collection capacity is visible as growing backlog
and eligible age. Containment belongs with the allocator and operator policy,
not shortened safety checks or indiscriminate parallel deletion.

## Non-Goals / Boundaries

- No independent GC daemon, scheduler or mandatory dispatch dependency.
- No deletion merely because a worktree is old, hidden, clean or inactive.
- No automatic semantic conclusion, forced claim release or responsibility transfer.
- No host-wide scan of arbitrary directories, reference checkouts or unmanaged repos.
- No silent retention-policy expansion, activation or rollout through a vision.

## See Also

- Parent: [agent-worktrees](../README.md).
- [Resident singleton](../status-monitor-singleton/README.md).
- [Worktree stewardship](../../../agent-fabric/agentic-factory/worktree-stewardship/README.md).
- [agent-dispatch](../../agent-dispatch/README.md) - allocator accountability.
- [Architecture patterns](../../../../docs/patterns/README.md).
- Reality: [agent-worktrees](../../../../plugins/agent-worktrees/README.md).
