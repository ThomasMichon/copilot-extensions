---
applyTo: "**"
---

# Agent Worktrees -- durable head-claim / stuck-cutover fallback guidance

This file is reflected into the consuming repo's tree, so it loads every
session even if `agent-worktrees` (or a peer plugin like `context-handoff`)
didn't register (see the paired `session-guidance.instructions.md`).

## If you don't seem to be this worktree's head session

`sessionStart` normally claims head automatically (no predecessor, or the
predecessor yielded/concluded). If not -- e.g. a resumed session lands on a
stale predecessor -- don't guess or proceed as a second, uncoordinated
session. Self-identify instead:

```bash
agent-worktrees bind-session --worktree-dir "$PWD"
```

(PowerShell: identical.) This declares your session against the worktree
directly, without depending on handoff plumbing.

## If your outbound claim is blocking finalize, or you need to release it

`finalize`'s obligation gate blocks on **unsettled outbound resource
obligations** -- a claim on a child worktree, CodeSpace, container, bridge
session, or an out-of-band `pr`/`workdir` journaled by hand. Never
force-release; resolve it:

```bash
agent-worktrees claims show
agent-worktrees claims sweep          # dry-run
agent-worktrees claims sweep --apply  # after review
```

Per-obligation-kind detail is in `worktree` skill's
`references/obligations.md` -- read before `--handoff-to` or a selective
`claims cleanup`. Tracing *whose* claim it is is the
`tracing-claimant-graphs` skill.

## If a handoff/cutover trigger appears to have failed

A stuck cutover (a successor never confirmed, or a predecessor pane still
running past when it should retire) is diagnosed, never guessed at or
manually killed:

```bash
agent-worktrees handoffs-check --worktree-id "<id>" --json
agent-worktrees handoffs-check --worktree-id "<id>" --execute --json
```

First call is read-only; `--execute` retires a confirmed-stale
predecessor. **Never terminate a pane by hand** -- an idle pane may be a
live successor mid cold-start. Nothing to retire but the symptom
persists? Escalate to a human or `agent-worktrees doctor --fix`.

## If you opened a pull request, drive it through to merge

`create-pr` claims the PR on this worktree's outbound-resource ledger once
it's genuinely open (`pr-merge-obligation-gate` defense 2) -- a live
obligation like a child worktree/CodeSpace/bridge session, **regardless of
`pr.strategy`**: `finalize`'s gate reads the local claim ledger only.
Merging clears it on its own; anything else needs explicit operator action
(see below).

- Drive every PR you (or a delegate) opened through a real merge -- opening
  and stopping, or reporting "landed" pre-merge, isn't the end state. See
  `worktree` skill's `references/pr-workflow.md` § "Default conduct" for
  the per-flow waiting policy.
- The claim auto-releases the instant any PR command (`create-pr`,
  `pr-ready`, `pr-status`, `pr-merge`, `finalize`, Picker's sweep) observes
  the PR **merged**.
- **Only the operator** may direct releasing the claim, resetting HEAD off
  the PR's unmerged commits, or abandoning it -- never your own judgment:
  `finalize --abandon --handoff-to <recipient>`, on explicit instruction
  only, never inferred from "stuck" or "CI's red".
