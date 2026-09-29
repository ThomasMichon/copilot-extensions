---
applyTo: "**"
---

# Context Handoff -- durable fallback guidance

Reflected into the repo so it loads even if `context-handoff` never
registered. Needs only a shell.

## Preparing a brief

Never end a turn with outstanding work and no handoff. Before triggering
(context-pressure) or once the user agrees (turn-end), sync the worktree:
resolve `$CH` as in *CLI fallback* below, run
`node "$CH" sync-worktree --json --cwd "$PWD"` (never a bare `git rebase`/
`agent-worktrees git sync` -- both bypass the force-tier lock/rebase
guard). A non-`synced` result isn't a blocker -- note the reason and
continue. **Self-audit first:** re-scan your turns for self-flagged
open-ended language ("still open," "follow-up," "deferred") and confirm
each was resolved -- an empty roster is checked, not assumed. Compose the
standard shape (Original Request/Continuing Objective/Progress/Successor
Work Roster/Background Flows & External State/Completion Gates/Re-Handoff
Instructions), or effort-backed if bound. Never drop an open flow or owned
state (PR, claim) -- name it. Prefer `generate_handoff_prompt` -> compose
-> `save_handoff_prompt` -> `trigger_handoff`; else the CLI below.

## Consuming a brief + recording yourself as head

Prefer `/consume-handoff`. A claimed-handoff response always names the
claimant session -- state it, never "nothing to do." A disconnect mid-call
isn't a semantic answer: retry once, then fall back to the CLI. **Verify,
don't just trust:** spot-check the predecessor's history for open-ended
phrases before "nothing outstanding." When `agent-worktrees` is available,
pull `agent-worktrees worktree-status-bundle --worktree <id> --json` <!-- marketplace-isolation: allow diagnostic-tooling -->
first -- its ledger is pruned, so a clean bounds report never proves
nothing older exists. Recording head is `agent-worktrees`'s job -- if
`sessionStart` missed it, run `agent-worktrees bind-session
--worktree-dir "$PWD"`.

## CLI fallback (tools unavailable)

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" check-heads --json --cwd "$PWD"
node "$CH" sync-worktree --json --cwd "$PWD"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```
`trigger`/`consume` share that shape. PowerShell: same default root, then
`node $ch <verb> ...`.

## If the plugin failed to load: find it yourself

1. Read `session-guidance.instructions.md` in your session folder if
   present, else find the newest `files/handoff-*.md` by mtime.
2. `agent-worktrees head-session --worktree "<id>" --json` /
   `handoffs-check --worktree-id "<id>" --json` report a pending seed.
3. The CLI fallback above, once `node` and this plugin's files are found.
4. After consuming, `agent-worktrees bind-session` (above). **Never
   terminate a predecessor pane by hand** -- run `handoffs-check
   --worktree-id "<id>" --execute --json` first; still stuck, escalate to
   `agent-worktrees doctor --fix`.

No store, no `node`? Write the brief to `handoff-<slug>.md` under your
state folder's `files/` dir, state the path, tell the user: `/clear` then
"Read <path> and resume the objective it describes." No auto-pickup, no
claim tracking.

## Rules

Never claim auto-pickup (a handoff never auto-loads on restart); the seed
is a locator, never the full markdown; consuming is setup, not completion
-- keep driving the objective.
