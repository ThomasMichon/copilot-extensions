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
the real continuation before anything else.

Whether a handoff cuts a successor over live or only stages a brief for a
human: `mode` (`auto`/`manual-only`/`off`) in `.context-handoff/config.yaml`
(or `~/.context-handoff/config.yaml`). Check it or ask first.

## The commands, if the extension is loaded

- `/consume-handoff` -- load this worktree's pending handoff.
- `generate_handoff_prompt` / `save_handoff_prompt` / `trigger_handoff` --
  compose, durably store, and (mode-permitting) signal pickup.

## The same commands as plain CLI, extension loaded or not

Bundled CLI, needs only `node`. Every command except `help` accepts `--json`.

| Command | Purpose |
|---|---|
| `save` | Store a handoff without requesting pickup |
| `trigger` | Store + signal pickup (arms live-cutover only under `mode: auto`) |
| `consume --locator "<kind>:<id>"` | Claim and load a stored handoff exactly once |
| `facts` | Extension-free handoff facts for this worktree |
| `check-heads` | Audit pending-handoff head alignment across worktrees |
| `retry-cutover` | Refocus a live successor or respawn a stuck cutover |
| `sync-worktree` | Shared lock/rebase-safe sync (same as the force tier) |
| `list-sessions` | Recorded handoff chain (needs `agent-worktrees`) |
| `get-previous-session` | Recorded predecessor (needs `agent-worktrees`) |
| `abort --locator "<kind>:<id>"` | Cancel before consumption (needs `agent-worktrees`; `agent-dispatch` for a task) |

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
```
Other verbs share that shape; add `--json` for machine output (table above).
PowerShell: same default
(`$HOME\.copilot\installed-plugins\copilot-extensions\context-handoff`), run
`node $CH <verb> ...`.

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