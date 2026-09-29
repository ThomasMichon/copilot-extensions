---
applyTo: "**"
---

# Context Handoff -- this mechanism exists

This worktree may have the `context-handoff` plugin active. It lets a session
compose a continuation brief and hand it to a successor, whether or not this
session began from one, so long-running or context-pressured work survives any
one session's window. Context pressure is never a reason to truncate
diligence, rush, or leave work unfinished -- only a reason to hand off. A
worktree may chain many handoffs until the objective is done.

If your first user turn looks like `<lead> | Resume: /consume-handoff to take
over | Recovery: context-handoff <kind>:<id>`, it is a **handoff seed** (a
"handoff prompt"): a locator, never the brief. Run `/consume-handoff` to load
the real continuation before anything else. You may also be asked to prepare
one near the end of a session.

Whether a handoff cuts a successor over live or only stages a brief for a
human is one switch: `mode` (`auto`, `manual-only`, or `off`) in
`.context-handoff/config.yaml` (or `~/.context-handoff/config.yaml`). Check
it, or ask, before assuming either.

## The commands, if the extension is loaded

- `/consume-handoff` -- load this worktree's pending handoff.
- `generate_handoff_prompt` / `save_handoff_prompt` / `trigger_handoff` --
  compose, durably store, and (mode-permitting) signal pickup.

## The same commands as plain CLI, extension loaded or not

The plugin's bundled CLI needs only `node`. Every command accepts `--json`
for machine-readable output; `help` prints the full option reference.

| Command | Purpose |
|---|---|
| `save` | Store a handoff without requesting pickup yet |
| `trigger` | Store + signal pickup (arms live-cutover only under `mode: auto`) |
| `consume --locator "<kind>:<id>"` | Claim and load a stored handoff exactly once |
| `facts` | Basic extension-free handoff facts for this worktree |
| `check-heads` | Audit pending-handoff head alignment across worktrees |
| `retry-cutover` | Refocus a live successor or respawn a stuck cutover |
| `sync-worktree` | Shared lock/rebase-safe worktree sync (same helper the force tier uses) |
| `list-sessions` | This worktree's sessions + recorded handoff chain (needs `agent-worktrees`) |
| `get-previous-session` | This (or a named) session's recorded predecessor (needs `agent-worktrees`) |
| `abort --locator "<kind>:<id>"` | Cancel a pending handoff before anyone consumes it |

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" trigger --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" consume --locator "<kind>:<id>" --cwd "$PWD"
node "$CH" list-sessions --json --cwd "$PWD"
node "$CH" get-previous-session --json --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" abort --locator "<kind>:<id>" --reason "<why>" --cwd "$PWD"
```
PowerShell: the same default under
`$HOME\.copilot\installed-plugins\copilot-extensions\context-handoff`, then
`node $CH <verb> ...`. The fallback guide below lists every verb and rung.

No global `context-handoff` command exists on `PATH` -- this plugin
deliberately ships **no binstub, install step, or runtime** (see the plugin's
own README § "How the extension is delivered"); always resolve `$CH`/`$ch` by
the exact verified path above before invoking it.

## The handoff prompt ("seed") format

A single-line locator, never the brief:
`<task lead> | Resume: /consume-handoff to take over | Recovery: context-handoff <kind>:<id>`.
`<kind>` is `task` (an agent-dispatch task id) or `file` (a worktree-state file
id). Copy the whole line as the successor's first prompt; don't paraphrase it.

## If the extension failed to load, on EITHER end

Read `instructions/context-handoff/handoff-fallback.instructions.md`
(projected into this directory): preparing or consuming a brief without the
extension, resolving the plugin's path without any tool, and writing the brief
to a file by hand as a last resort.