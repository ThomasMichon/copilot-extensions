---
applyTo: "**"
---

# Agent Worktrees -- durable head-claim / stuck-cutover fallback guidance

This file is reflected directly into the consuming repo's own source tree, so
it loads on every session regardless of whether `agent-worktrees` (or any
other plugin, such as `context-handoff`) registered correctly this session
(see the paired `session-guidance.instructions.md` pointer for the dynamic
per-session file).

## If you don't seem to be this worktree's head session

An ordinary `sessionStart` registration normally claims head automatically:
either no predecessor exists, or the predecessor has yielded (opened a
handoff) or concluded. If it did not -- for example a resumed session lands
back on a stale predecessor instead of the most recent successor -- do not
guess or silently proceed as a second, uncoordinated session. Self-identify
explicitly instead:

```bash
agent-worktrees bind-session --worktree-dir "$PWD"
```

(PowerShell: identical invocation.) This declares your own session against
the worktree directly, without depending on any handoff plumbing having run
first.

## If your outbound claim is blocking finalize, or you need to release one

`finalize`'s obligation gate blocks on **unsettled outbound resource
obligations** -- a claim this worktree holds on a child worktree, CodeSpace,
container, bridge session, or an out-of-band `pr`/`workdir` you journaled by
hand. Never force-release one; resolve it:

```bash
agent-worktrees claims show
agent-worktrees claims sweep          # dry-run: provably-gone-and-safe reclaims
agent-worktrees claims sweep --apply  # only after reviewing the dry-run
```

The full itemized procedure per obligation kind (cross-repo worktree,
borrowed CodeSpace, bridge session, crashed holder, the affirmative-handoff
path when you genuinely cannot close a child yourself) lives in the
`worktree` skill's own `references/obligations.md` -- read it before
choosing `--handoff-to` or a selective `claims cleanup`. Tracing *whose*
claim something is (walking back to a root owner, or checking whether that
owner is still live) is the separate **`tracing-claimant-graphs`** skill.

## If a handoff/cutover trigger appears to have failed

A stuck cutover (a successor spawned but never confirmed, or a predecessor
pane that should have retired but is still running) is diagnosed, never
guessed at or manually killed:

```bash
agent-worktrees handoffs-check --worktree-id "<id>" --json
agent-worktrees handoffs-check --worktree-id "<id>" --execute --json
```

The first call is read-only and reports what it finds; `--execute` actually
retires a confirmed-stale predecessor. **Never terminate a pane by hand** --
a pane that looks idle may still be a legitimate live successor mid
cold-start (loading MCP servers/skills before its own `sessionStart` hook
even runs is routinely slower than a quick manual check). If
`handoffs-check` finds nothing to retire but the symptom persists, escalate
to a human or `agent-worktrees doctor --fix` rather than acting further on a
guess.

## Rules

- Never manually kill a pane or process to "fix" a stuck worktree -- diagnose
  with `handoffs-check`/`doctor` first.
- A session that cannot confirm it is head should not act as though it is;
  `bind-session` is the explicit, safe way to resolve that ambiguity.
- Never force-release or force-settle an outbound claim; resolve it via
  `claims show`/`sweep` and `references/obligations.md`, or the affirmative
  `--handoff-to` path -- never bypass the obligation gate.
