# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin ships two cooperating pieces:

| Piece | Type | Role |
|-------|------|------|
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; injects `additionalContext` nudges at 55% / 70% utilization; provides `generate_handoff_prompt` + `save_handoff_prompt` tools. `save_handoff_prompt` sits **on top of agent-dispatch**: when a coordinator is reachable it stores the handoff as a `proposed`/`handoff` **task** (payload = the markdown, pinned to the worktree, no session file); otherwise it falls back to a session-folder file |
| **context-handoff skill** | Skill | The `/handoff` workflow -- composes the continuation prompt from the extension's structured facts and the agent's live context -- and **`/resume-handoff`**, which consumes this worktree's pending handoff task in the foreground next session |

## Why an extension (and not a pure plugin)

The live monitor is **only** possible as a session extension. The Copilot CLI
hook surface a plugin normally uses cannot replicate it:

- **No hook input carries token counts.** `session.usage_info` (current /
  limit tokens) is delivered only to the extension SDK via
  `session.on("session.usage_info", ...)`. No `sessionStart` / `postToolUse`
  hook input exposes it.
- **`postToolUse` hook output is ignored.** The extension's nudge works by
  returning `additionalContext` from `onPostToolUse`, which the model reads.
  Command-hook output is discarded (only `preToolUse` can *deny* a tool call,
  not inject a message).

So the capability requires the extension payload.

## How the extension is delivered (no install step)

This is a **plugin-contributed extension**. The Copilot CLI discovers
extensions contributed by **enabled** installed plugins directly from the
plugin's `extensions/` directory (the `plugin` extension source) -- it scans
each `extensions/<name>/` subdir holding an `extension.{mjs,cjs,js}` file. This
plugin ships exactly one:

```
plugins/context-handoff/extensions/context-handoff/extension.mjs
```

There is **no** copy to `~/.copilot/extensions/`, no deploy manifest, and no
`scripts/install.*`. Installing/updating the plugin via the marketplace
(`copilot plugin update context-handoff@copilot-extensions`, or repo-level
`enabledPlugins` auto-install) is the whole deploy. The extension activates on
the **next** Copilot CLI session (extensions are scanned at startup).

## Requirements

Two conditions must hold for the extension to load -- both are handled outside
this plugin:

1. **The plugin must be enabled.** `context-handoff@copilot-extensions: true`
   in `enabledPlugins` (user `~/.copilot/settings.json`, or a repo's
   `.github/copilot/settings.json`). A marketplace plugin's `extensions/` dir is
   only scanned when the plugin is enabled.
2. **`experimental: true`** in `~/.copilot/settings.json` -- the CLI gates *all*
   extension loading behind it. This is ensured by the **agent-worktrees**
   installer (`Ensure-CopilotExperimental`, run on `agent-worktrees
   install`/`update`), the session-lifecycle owner present on every machine.
   This plugin does not set it.

## Verify

A loaded extension logs `[Context Handoff] Session started ...` and exposes the
`generate_handoff_prompt` / `save_handoff_prompt` tools. `/extensions` lists it
with source **plugin**. If it does not load, confirm both requirements above and
start a fresh session (the context-handoff-setup skill walks through this).

## Thresholds

| Utilization | Behavior |
|-------------|----------|
| 55% | Gentle reminder: "consider generating a handoff soon" |
| 70% | Urgent reminder: "generate NOW, compaction at ~80%" |

Reminder state resets after a successful compaction.
