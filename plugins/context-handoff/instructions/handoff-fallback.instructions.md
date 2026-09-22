---
applyTo: "**"
---

# Context Handoff -- durable fallback guidance

Reflected into the repo so it loads even if `context-handoff`'s own tools
never registered this session. Everything below needs only a shell.

## Preparing a brief

Never end a turn with outstanding work and no handoff. Before triggering
(context-pressure path) or once the user agrees (turn-end path), sync the
worktree. Resolve the CLI path first (same resolution as the "CLI
fallback" section below):
`CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"; CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"`.
Then run `node "$CH" sync-worktree --json --cwd "$PWD"` (never a bare
`git rebase`/`agent-worktrees git sync` -- this shares the same lock and
rebase guard as the automatic force-tier path). A non-`synced` result is
not a blocker -- note the reason in the brief and continue. Compose:
**Original Request / Continuing Objective / Progress / Successor Work
Roster / Outstanding Background Flows & External State / Completion Gates
/ Re-Handoff Instructions** (or **Active Effort / Next Slice / Immediate
Session Delta** when an effort backs the work). Never drop an open
background flow or owned external state (PR, claim) -- name it explicitly.
Prefer `generate_handoff_prompt` -> compose -> `save_handoff_prompt` ->
`trigger_handoff` when available; otherwise use the CLI below.

## Consuming a brief + recording yourself as head

Prefer `/consume-handoff`. A claimed-handoff response always names the
claimant session -- state it, never treat it as "nothing to do." A
transport disconnect mid-call is not a semantic answer: retry once, then
fall back to the CLI. Recording head is `agent-worktrees`' job -- if
`sessionStart` didn't auto-claim it, run
`agent-worktrees bind-session --worktree-dir "$PWD"` directly.

## CLI fallback (tools unavailable)

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" check-heads --json --cwd "$PWD"
node "$CH" sync-worktree --json --cwd "$PWD"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```
`trigger`/`consume` share that same `--session-id`/`--cwd` shape.

PowerShell: build `$ch` the same way -- PowerShell's own `COPILOT_PLUGIN_ROOT`
variable (or `$HOME\.copilot\installed-plugins\copilot-extensions\context-handoff`
if unset) plus `extensions\context-handoff\handoff-cli.mjs` -- then run
`node $ch <verb> ...`.

## If the plugin failed to load: find it yourself, no tools required

1. Read `instructions/context-handoff/session-guidance.instructions.md` in
   your session folder, if present -- it may already name a handoff.
2. List each session's disclosed session-state folder for a
   `files/handoff-*.md` entry (a predecessor's last-resort write); pick the
   newest by mtime, read it, and resume it if found.
3. `agent-worktrees head-session --worktree "<id>" --json` and
   `agent-worktrees handoffs-check --worktree-id "<id>" --json` (a separate
   plugin) report any pending handoff + seed.
4. The CLI fallback above, once `node` and this plugin's files are found.

After consuming: bind head with `agent-worktrees bind-session` (above), then
**never terminate a predecessor pane by hand** -- run
`agent-worktrees handoffs-check --worktree-id "<id>" --execute --json`, which
confirms genuine staleness first. Nothing to retire but the symptom
persists? Escalate to a human or `agent-worktrees doctor --fix`.

## Last resort: write the file yourself

No reachable store, no `node`? Write the brief to a `handoff-<slug>.md`
file under your disclosed session-state folder's `files/` directory
(create it first), state the absolute path, and give the user:
`/clear` then "Read <path> and resume the objective it describes." No
auto-pickup, no claim tracking -- last resort only.

## Rules

- Never claim auto-pickup; a handoff never loads automatically on restart.
- The seed is a locator, never the full markdown inline.
- Consuming is setup, not completion -- keep driving the objective.
