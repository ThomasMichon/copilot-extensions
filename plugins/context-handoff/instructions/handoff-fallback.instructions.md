---
applyTo: "**"
---

# Context Handoff -- durable fallback guidance

Loads without `context-handoff`; needs only a shell.

## Preparing a brief

Never end a turn with outstanding work and no handoff; before triggering or
once agreed, sync: resolve `$CH` (*CLI fallback*), run `node "$CH"
sync-worktree --json --cwd "$PWD"` (never `git rebase`/`agent-worktrees git
sync`, which bypass the guard); non-`synced` isn't fatal; note why.
**Self-audit first:** re-scan turns for open-ended self-flags; confirm each
resolved -- empty means checked, not assumed. Compose **Original
Request/Continuing Objective/Progress/Successor Work Roster/Outstanding
Background Flows & External State/Completion Gates/Re-Handoff Instructions**
(or if effort-backed, **Active Effort/Next Slice/Immediate Session Delta**) --
route an open self-audit hit into Next Slice, or the active effort if outside
this leg. Never drop an open flow/owned state (PR, claim) -- name it. Prefer
`generate_handoff_prompt` -> compose -> `save_handoff_prompt` ->
`trigger_handoff`; else the CLI below.

## Consuming a brief + recording head

Prefer `/consume-handoff`; a claimed-handoff response always names the
claimant session -- **state that id to the user**, never "nothing to do." A
disconnect isn't an answer: retry once, then the CLI. **Verify, don't trust:**
spot-check (bounded) predecessor history for open-ended phrases before
"nothing outstanding"; disclose if you couldn't check. Consume also names the
worktree (if `agent-worktrees` available) -- pull `agent-worktrees
worktree-status-bundle --worktree <id> --json` <!-- marketplace-isolation: allow diagnostic-tooling -->
with it (pruned ledger; title/summary is a theme, not proof). Recording head is `agent-worktrees`'s job; if `sessionStart`
missed it, run `agent-worktrees bind-session --worktree-dir "$PWD"`.

## CLI fallback (tools unavailable)

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" check-heads --json --cwd "$PWD"
node "$CH" sync-worktree --json --cwd "$PWD"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```
PowerShell: same resolution (`trigger`/`consume` share it), using the
`COPILOT_PLUGIN_ROOT` variable (default
`$HOME\.copilot\installed-plugins\copilot-extensions\context-handoff`), plus
`extensions\context-handoff\handoff-cli.mjs`; run `node $ch <verb> ...`.

## If the plugin failed to load: find it yourself, no tools required

1. Under session folder, read
   `instructions/context-handoff/session-guidance.instructions.md` if present;
   always scan session state for `files/handoff-*.md`, resume newest by mtime.
2. `agent-worktrees head-session --worktree "<id>" --json` / `agent-worktrees
   handoffs-check --worktree-id "<id>" --json` report a pending seed.
3. CLI fallback above.
4. After consuming, `agent-worktrees bind-session`. Never hand-terminate a
   predecessor pane -- run `agent-worktrees handoffs-check --worktree-id
   "<id>" --execute --json` first; if stuck, escalate: human or
   `agent-worktrees doctor --fix`.

No store, no `node`? Write the brief to `handoff-<slug>.md` under state folder
`files/` (create it first -- not guaranteed to exist); state the path; tell
the user `/clear`, then "Read <path> and resume the objective."

## Rules

Never claim auto-pickup; the seed is a locator, not full markdown; consuming
is setup, not completion -- keep driving the objective.
