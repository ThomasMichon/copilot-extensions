---
applyTo: "**"
---

# Context Handoff -- this mechanism exists

This worktree may have the `context-handoff` plugin active. It lets one
session compose a continuation brief and hand it to a successor session,
whether or not this particular session began from a handoff -- so
long-running or context-pressured work survives past any one session's
window instead of being lost. Context-window pressure is never a reason
to truncate diligence, rush a task, or leave work unfinished; it is only
a reason to hand off. A worktree may chain many handoffs in succession
until the overall objective is done.

If your very first user turn looks like `<lead> | Resume: /consume-handoff
to take over | Recovery: context-handoff <kind>:<id>`, that is a
**handoff seed** (sometimes called a "handoff prompt"): it names a
locator, never the brief itself. Run `/consume-handoff` to load the real
continuation before doing anything else. You may also be asked, near the
end of a session, to prepare one of these for whoever picks up next.

Whether this actually cuts a successor over live, or only ever stages a
brief for a human to open manually, is a single config switch --
`.context-handoff/config.yaml`'s (or `~/.context-handoff/config.yaml`'s)
`mode` key: `auto`, `manual-only`, or `off`. This note does not say which
one is active here -- check the file, or ask, before assuming either way.

## The commands, if the extension is loaded

- `/consume-handoff` -- load this worktree's pending handoff into the
  current session (predecessor task, or newest matching file).
- The `generate_handoff_prompt` / `save_handoff_prompt` / `trigger_handoff`
  tools -- compose, durably store, and (mode-permitting) signal pickup.

## The same commands as plain CLI, extension loaded or not

Resolve this plugin's own bundled CLI first -- it needs only `node`, never
a tool call:

```bash
CH_ROOT="${COPILOT_PLUGIN_ROOT:-$HOME/.copilot/installed-plugins/copilot-extensions/context-handoff}"
CH="$CH_ROOT/extensions/context-handoff/handoff-cli.mjs"
node "$CH" save --title "<t>" --prompt-file "<f.md>" --session-id "$COPILOT_AGENT_SESSION_ID" --cwd "$PWD"
node "$CH" trigger --cwd "$PWD"          # arm pickup (mode-permitting)
node "$CH" consume --locator "<kind>:<id>" --cwd "$PWD"
```
PowerShell: same `$CH_ROOT`/`$CH` resolution (default
`$HOME\.copilot\installed-plugins\copilot-extensions\context-handoff`),
then `node $CH <verb> ...`. Full verb list and every fallback rung below
this (down to a bare state-folder file write) live in the guide linked
below.

## The handoff prompt ("seed") format

A handoff prompt is never the brief itself -- it is a short, single-line
locator string, always shaped:

```
<task lead> | Resume: /consume-handoff to take over | Recovery: context-handoff <kind>:<id>
```

`<kind>` is `task` (an agent-dispatch task id) or `file` (a worktree-state
file id). That trailing `Recovery: context-handoff <kind>:<id>` clause is
the only part that matters mechanically -- copy the whole line as the
first prompt in the successor session; do not paraphrase or shorten it.

## If the extension failed to load, on EITHER end

Nothing above the CLI section needs the extension at all. Read
`instructions/context-handoff/handoff-fallback.instructions.md`
(projected into this same directory) for the full runbook: preparing a
brief, consuming one, resolving the plugin's own path without any tool,
and what to do if you can't find the plugin at all. It covers both sides
-- a predecessor with no way to save, and a successor with no way to
consume -- down to writing the brief to a file by hand as a last resort.
