---
applyTo: "**"
---

# Context Handoff -- durable fallback guidance

Loads without `context-handoff`; needs only a shell.

## Preparing a brief

Never end a turn with work outstanding, no handoff. Trigger
on context-pressure (no confirmation), or once agreed on turn-end. Sync:
resolve `$CH` (*CLI fallback*), run `node "$CH" sync-worktree --json
--cwd "$PWD"` (never `git rebase`/`agent-worktrees git sync`, which
bypass the guard); non-`synced` isn't fatal, note why. Self-audit first:
re-scan turns for open-ended self-flags, confirm each resolved --
empty means checked, not assumed. Compose **Original Request/Continuing
Objective/Progress/Successor Work Roster/Outstanding Background Flows &
External State/Completion Gates/Re-Handoff Instructions** (or if
effort-backed, **Active Effort/Next Slice/Immediate Session Delta**) --
route an open self-audit hit into Next Slice, or the active effort if
outside this leg. Never drop an open flow/owned state -- name it. Prefer
`generate_handoff_prompt` -> compose -> `save_handoff_prompt` ->
`trigger_handoff`; else the CLI below.

## Consuming a brief + recording head

Prefer `/consume-handoff`; a claimed-handoff names the claimant
session -- state that id to the user, never "nothing to do." A
disconnect isn't an answer: retry once, then the CLI. Verify,
don't trust: spot-check predecessor history (bounded) for open-ended phrases
before "nothing outstanding", disclose if unchecked. Consume also names
the worktree if available -- pull `agent-worktrees
worktree-status-bundle --worktree <id> --json` <!-- marketplace-isolation: allow diagnostic-tooling -->
for lineage/activity (pruned; title/summary is a theme, not
proof);
recording head is `agent-worktrees`'s job; if missed, run
`agent-worktrees bind-session --worktree-dir "$PWD"`.

## CLI fallback (tools unavailable)

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" check-heads --json --cwd "$PWD"
node "$CH" sync-worktree --json --cwd "$PWD"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```
`trigger`/`consume`/`list-sessions`/`get-previous-session`/`abort` share
that shape. `node "$CH" help` prints every verb and option. PowerShell:
same, using `COPILOT_PLUGIN_ROOT` (default
`$HOME\.copilot\installed-plugins\copilot-extensions\context-handoff`),
run `node $ch <verb> ...`

## If the plugin failed to load: find it yourself, no tools required

1. Read (session folder)
   `instructions/context-handoff/session-guidance.instructions.md` if
   present; also scan every session's state for `files/handoff-*.md`,
   resume newest by mtime.
2. `agent-worktrees head-session --worktree "<id>" --json` /
   `agent-worktrees handoffs-check --worktree-id "<id>" --json` report a pending seed.
3. CLI fallback above.
4. After consuming, `agent-worktrees bind-session`. Never hand-kill
   a pane -- run `agent-worktrees handoffs-check
   --worktree-id "<id>" --execute --json`; if stuck, a human or
   `agent-worktrees doctor --fix`.

No store, no `node`? Write the brief to `handoff-<slug>.md` under state
folder `files/` (create first, not guaranteed to exist); state absolute
path; tell the user `/clear`, then "Read <path> and resume the
objective." No auto-pickup, claim tracking, supersession.

## Rules

Never claim auto-pickup; seed is locator, not markdown; consuming
is setup not completion, keep driving
