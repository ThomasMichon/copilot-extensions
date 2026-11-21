# Unified Bridge inception

This is the inception record for the [canonical effort](README.md), not another
plan. The example repository binstub is generalized to `example`; no other
requirement is removed. The later correction supersedes the earlier sentence
about new worktrees resuming a session.

## Original operator request

> We need to propose some cleanup and normalization here:
> - We need one, unified sub-command to be able to enumerate all *available* bridges scoped by-default to the repo/project used under CWD or via explicit repo binstub (e.g. `example bridge list`).
>   This command needs to make it clear which bridges are running, what types they are (Mux, ACP, HTTP/WS), where they are (machine, worktree, anchor/dir), who "owns" them (creator/caller worktree) and ideally include Title/Disposition, or any other cached-at-hand information providing useful differentiating information. These will all be bridges which have at least one session, whether that session is live in-process or not. This will include *all* worktrees in agent-worktrees' roster, so long as they have at least one session made, whether active or not.
> - We likely want quick at-hand filters for bridges by target machine, target project, and namespace (codespace, container, etc.), and we need agents to know those additional source exist so they don't give up.
> - We don't care about "sessions" vs "live-sessions". Eliminate that distinction. We can handle ACP and Mux equivalently at this point, so we should align how we refer to them across all sub-commands. Active/live can just be a filter, as well as an always-emitted status.
> - *Live* anchor sessions should enumerate as valid bridges, but dormant ones should not; there would be too many. We could consider a session-archive list flow if we really needed to recover a worktree-anchor session. This does align with how we work with codespaces and containers, so I guess we'll need this.
> - We still need a sub-command which enumerates all potential "targets" for bridges: session-less existing worktrees, containers, codespaces, ACP hosts, worktree-compatible anchor roots. Callers will need to explictly "create" bridges in those spaces causing default allocation mechanics to kick in. Creating a bridge in an anchor should enforce creation in a worktree unless --anchor is explicitly requested, and this should require a "break-glass". Similarly, creating a bridge in slot which already has an active bridge or "last assigned bridge" should block unless the prior bridge is explicitly ended. Bridges in new worktrees should always be forced to resume the previous session unless a legacy handoff is detected. agent-bridge should try to enforce one bridge per anchor session unless a break-glass is used.

## Approved agent proposal

This section summarizes the agent proposal that the operator approved; it is
not additional operator wording.

- A bridge is a persistent conversation endpoint; a target is a potential
  placement. One project-scoped bridge list composes existing owners rather
  than creating a competing registry.
- Session-backed worktrees remain visible while dormant. Live anchors are
  included; empty worktrees are targets and dormant anchor history is explicit.
- Hosting kind and transport are independent. Rows distinguish lifecycle,
  process liveness and control availability, retain creator/controller and
  useful cached metadata, and report missing or partial sources honestly.
- Common selectors and lifecycle commands replace the public managed/live
  session split, with an explicit compatibility transition.
- Listing is read-only. Creation composes normal allocation; stop preserves
  assignment and explicit end releases it without deleting history.
- Admission is atomic. Occupied and last-assigned placements refuse a new
  bridge until explicitly ended. Direct or additional anchor use requires
  explicit scoped break-glass, without bypassing hosting or ownership policy.
- Review intent first, then deliver truthful listing, common selectors and
  targets, followed by guarded creation/continuity. Source delivery does not
  authorize live installation, cleanup or rollout.

## Operator correction and implementation assignment

> "new worktrees resume the previous session" was probably supposed to be "resuming worktrees routes to the previous session in that worktree". Creating a new worktree should create a new session within it.
> Agree with your plan. Update agent-brige's vision, make an effort captureing this intent/spark, and get to work implementing.

## Operator charter reconciliation

> Update this worktree's charter to be clear about this direction; reconcile against what we had this worktree focusing on

The driving charter must name this current finite assignment while preserving
earlier unfinished objectives and their independent owners. This public effort
does not absorb private operational state or declare those objectives complete.
