---
applyTo: "**"
---

# Context Handoff -- durable fallback guidance

Reflected into the repo so it loads even if `context-handoff` never
registered. Everything below needs only a shell.

## Preparing a brief

Never end a turn with outstanding work and no handoff. Before triggering
(context-pressure path) or once the user agrees (turn-end path), sync the
worktree: resolve `$CH` as in *CLI fallback* below, then run
`node "$CH" sync-worktree --json --cwd "$PWD"` (never a bare `git rebase`/
`agent-worktrees git sync` -- both bypass the force-tier lock/rebase
guard). A non-`synced` result isn't a blocker -- note the reason and
continue. **Self-audit first:** re-scan your own turns for self-flagged
open-ended language ("still open," "follow-up," "deferred," "didn't
verify") and check whether each was resolved by a later turn before
composing -- an empty roster is a checked claim, not a default. Compose:
**Original Request/Continuing Objective/Progress/
Successor Work Roster/Outstanding Background Flows & External State/
Completion Gates/Re-Handoff Instructions** (or **Active Effort/Next
Slice/Immediate Session Delta** if effort-backed -- route any still-open
self-audit hit into Next Slice/the active effort here, since this shape has
no Successor Work Roster). Never drop an open
background flow or owned state (PR, claim) -- name it. Prefer
`generate_handoff_prompt` -> compose -> `save_handoff_prompt` ->
`trigger_handoff`; otherwise use the CLI below.

## Consuming a brief + recording yourself as head

Prefer `/consume-handoff`. A claimed-handoff response always names the
claimant session -- state it, never "nothing to do." A disconnect mid-call
isn't a semantic answer: retry once, then fall back to the CLI. **Verify,
don't just trust:** when you can read the predecessor session's own
history, spot-check it for the same open-ended phrases (bounded search, not
a full re-read) before reporting "nothing outstanding" -- disclose plainly
if that check couldn't happen. Recording
head is `agent-worktrees`' job -- if `sessionStart` didn't auto-claim it,
run `agent-worktrees bind-session --worktree-dir "$PWD"`.

## CLI fallback (tools unavailable)

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" check-heads --json --cwd "$PWD"
node "$CH" sync-worktree --json --cwd "$PWD"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```
`trigger`/`consume` share that shape. PowerShell: same resolution, using
the `COPILOT_PLUGIN_ROOT` variable (default
`$HOME\.copilot\installed-plugins\copilot-extensions\context-handoff`)
plus `extensions\context-handoff\handoff-cli.mjs`; run `node $ch <verb> ...`.

## If the plugin failed to load: find it yourself, no tools required

1. Read `instructions/context-handoff/session-guidance.instructions.md` in
   your session folder, if present -- it may already name a handoff.
2. List each session's state folder for `files/handoff-*.md`; resume the
   newest by mtime if found.
3. `agent-worktrees head-session --worktree "<id>" --json` and
   `agent-worktrees handoffs-check --worktree-id "<id>" --json` report any
   pending handoff/seed.
4. The CLI fallback above, once `node` and this plugin's files are found.
5. After consuming: bind head with `agent-worktrees bind-session` (above).
   **Never terminate a predecessor pane by hand** -- run
   `agent-worktrees handoffs-check --worktree-id "<id>" --execute --json`
   to confirm genuine staleness first; still stuck? Escalate to a human or
   `agent-worktrees doctor --fix`.

## Last resort: write the file yourself

No store, no `node`? Write the brief to a `handoff-<slug>.md`
under your state folder's `files/` directory (create it first), state the
absolute path, and tell the user: `/clear` then "Read <path> and resume
the objective it describes." No auto-pickup, no claim tracking.

## Rules

- Never claim auto-pickup; a handoff never auto-loads on restart.
- The seed is a locator, never the full markdown inline.
- Consuming is setup, not completion -- keep driving the objective.
