---
name: diagnosing-handoff-cutover
description: >
  Diagnose a worktree handoff-cutover mismatch between the authoritative
  head-session ledger and the resident status-monitor's current cutover target
  roster. Use when a stored handoff exists but the expected successor cutover
  does not start, starts in the wrong checkout, or a machine has a mix of
  active and dormant worktrees and one handoff appears stranded. Trigger
  phrases include:
  - 'handoff cutover mismatch'
  - 'head session misalignment'
  - 'check handoff heads'
  - 'handoff cutover stuck'
  - 'status-monitor handoff issue'
  - 'cutover targeted the wrong worktree'
  - 'pending handoff not picked up'
---

# Diagnosing handoff cutover

Use this when the handoff baton exists, but the expected cutover does not start
or appears to be targeting the wrong worktree.

## What the mismatch means

`agent-worktrees head-session --worktree <id> --json` is the authoritative <!-- marketplace-isolation: allow agent-worktrees-management -->
ledger replay for a worktree's current head and pending handoffs. A cutover
misalignment means that ledger still shows pending handoff state for one
worktree, but the resident status-monitor is currently sweeping a different
served-session roster.

The common shape is a machine with a mix of:

- **active worktrees** currently being served through live `wt-<id>` monitor
  registrations, and
- **dormant worktrees** whose records still carry pending handoff state even
  though they are not in the current served-session set.

## Detect it

Run the payload-local diagnostic CLI:

```bash
node "$CH" check-heads --json --cwd "$PWD"
```

PowerShell:

```powershell
node $ch check-heads --json --cwd $PWD
```

This command shells to `agent-worktrees head-session --json` for each known <!-- marketplace-isolation: allow agent-worktrees-management -->
worktree and compares that ledger view with the status-monitor's current
`wt-<id>` registry.

Look for:

- `pending-handoff-unregistered` — the ledger still has a pending handoff, but
  the monitor currently has no registered cutover target for that worktree.
- `pending-handoff-registry-path-mismatch` — the monitor has a `wt-<id>`
  registration, but it points at a different checkout path than the worktree
  inventory row being audited.

## Remediate it

1. **If you are still attached to the superseded predecessor session, run the
   safe retry command first:**

   ```bash
   node "$CH" retry-cutover --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
   ```

   PowerShell:

   ```powershell
   node $ch retry-cutover --session-id $env:COPILOT_AGENT_SESSION_ID --cwd $PWD
   ```

   This shells to `agent-worktrees handoff-cutover --retry --session-id <sid> --json`.
   It positively confirms whether the latest handoff already has a live
   successor pane. If one exists, it **refocuses** that pane instead of
   spawning anything new; only when no live successor exists does it fall back
   to a fresh spawn attempt.
2. **If the ledger cache is stale or the handoff is stranded after a failed
   successor,** run `agent-worktrees doctor --fix` to repair safe worktree <!-- marketplace-isolation: allow agent-worktrees-management -->
   state, including stale head cache and orphaned handoff cases.
3. **If you already know the exact predecessor and successor session ids,**
   repair the ground truth explicitly with `agent-worktrees conclude-session` <!-- marketplace-isolation: allow agent-worktrees-management -->
   or `agent-worktrees link-succession` rather than inventing a replacement <!-- marketplace-isolation: allow agent-worktrees-management -->
   head in another layer.

Do not patch the deployed plugin payload or try to force a cutover by editing
machine-local tracking files by hand. The ledger belongs to `agent-worktrees`;
diagnose with `check-heads`, repair with the worktree commands, then let a new
cutover advance the head.
