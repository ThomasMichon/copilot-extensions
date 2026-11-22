# Worktree Dehydration - Public Request Capture

Back to the [canonical effort](README.md).

## Initial operator request

> Help design this idea for optimizing disk-space usage for agent-worktrees: when an agent is stopped, and the worktree is in CONVO, WIP, MERGED, or FINAL states (basically any state except DIRTY), delete the git worktree and checkout itself, preserving the branch and agent-worktrees tracking. Basically, provide the ability to "dehydrate" and "rehydrate" worktrees. We could consider explicitly leaving behind a marker file in the worktree folder for hydrated worktrees, specifying the target commit and sesion info, which would allow us to recover agent-worktrees' tracking state if we ever lost it.

## Operator clarification

> Can git handle this itself? Or do we need to tell git to delete the worktree?
> I would expect to be keeping the branch, which would be the local ref that preserves the commits against garbage collection. We don't necessarily scope all branches as `worktree/____`, but we could. There's also sometimes the chance that the driver agent puts a worktree onto a different branch; for those we may just want to block dehydration.
> My expectation is that when we "dehydrate", we would take the relationship snapshot and write the marker file at that time, and when we hydrate, we'll use the marker file to determine all recovery state and then delete the file once the worktree rehydrates cleanly.
> We can use "(D)" is something as a state-suffix for dehydrated worktrees; dehydration is a sub-state which can be applied to any worktrees for which all files are tracked by commits or explicitly ignored. Basically if `git status` reports no untracked files, modified files, or staged files, I consider it hydrate-able. Agents *should* be using agent-scratch for their temporary files, and build/test ephemeral artifacts can be reproduced on the next runs. Instruction projections and the like can also be reproduced during rehydration.
> I would ultimately want dehydration to be "automatic": when an agent stops, agent-worktrees should verify that the process locks is lifted and then perform dehydration. When a worktree needs to be resumed via mux or bridge, then agent-worktrees should rehydrate before resume. We could consider a manual "dehydration hold" which could be applied to worktrees, or we could support a config switch to alter auto/manual modes for hydration, and support a manual "Dehydrate" action in the Worktree Manager UX.

## Latest operator amendment

> We don't have to put the marker files *in* the worktree folders, but we should put them "nearby". We could just put a folder dedicated to "dehydrated worktrees" like a ".dehydrated/<wt-id>.json" catalog under the worktrees folder root for a repo. Then there wouldn't be issued with us making the marker file, then having git remove the worktree. We'll just need to be sure to account for that dehydration catalog when sweeping worktrees for roster rebuild periodically.
> Agree with using live git checks before dehydration attempts.

**Publication instruction, public-safety abstraction:** preserve the raw origin
separately; put the main version in copilot-extensions with tracking items, vision
updates and architectural design. Private provenance destinations are omitted.
This changes no behavioral requirement.

## Agenda placement amendment

> We need to move `.effort-agenda` out of the worktrees' actual footprints; it needs to be in ~/.<repo>/worktrees/<worktree-id>

> We'll need a "doctor" for that

The per-repository worktree data home is outside the actual checkout. Existing
lane migration and history-preservation qualification precede automatic
dehydration. The exact lane layout beneath that home and safe migration are
architectural details; this instruction does not authorize deleting old history
without preservation. The transition needs an explicit doctor; report-first
inspection and a separate repair path are the proposed interaction.

## Supersession and recommendations

### Execution assignment after the reviewed design

> Drive the PR, then make the fixes to the worktrees so that cleaning finalized worktrees won't clear the agendas. We'll want to ensure that the Agendas tool matches the expected storage location

The design PR #13788 was already merged. This assigns the agenda storage,
preservation and consumer-alignment prerequisite, including its doctor and
normal source delivery. Earlier planning-only scope remains historical evidence;
it no longer blocks this assigned source work. Live operator data migration,
automatic dehydration and rollout are not inferred.

- The nearby catalog replaces both a marker inside the checkout and the
  assistant's earlier proposal for a marker permanently present while hydrated.
- The retained local branch replaces the assistant's suggested mandatory extra
  retention ref. Branch namespace standardization is optional, not decided.
- The explicit ignored-output contract replaces the assistant's proposed
  per-artifact preservation policy.
- The Git remove/add approach is an assistant recommendation supported by
  [Git's documented worktree operations](https://git-scm.com/docs/git-worktree).
- Shared entry/removal fencing, interruption journaling, catalog schema details,
  configuration names and capability evolution are agent-recommended mechanics.
  They must preserve the operator's simpler content and branch contract.
- Automatic process-exit behavior is the intended end state, not authorization
  to activate deletion during this planning publication.

## Configured-feed dependency follow-up

The private feed name is generalized in these public-safe quotations.

> Fix the stub package version to fit within the seasoning; it should be something provided via [configured feed]

> Since this machine has to pull packages from the [configured feeds], help update the dependencies to use a set that can resolve from this machine

> This just means we have to work around with different dependencies.

The later assignment permits a compatible dependency-set change, rather than
repeating the earlier unsuccessful stub-pin candidates. The clarification
includes replacing a dependency and adapting its direct consumer while
preserving behavior and type checking. It does not relax seasoning or integrity
checks, replace the configured feeds, or authorize an agenda migration, doctor
workaround or fleet rollout.
