# context-handoff

Context window monitoring and session handoff for GitHub Copilot CLI.

This plugin ships two cooperating pieces:

| Piece | Type | Role |
|-------|------|------|
| **context-handoff extension** | Copilot CLI session extension (`extension.mjs`) | Monitors `session.usage_info` for exact token counts; injects `additionalContext` nudges at 55% / 70% utilization; provides `generate_handoff_prompt` + `save_handoff_prompt` tools |
| **context-handoff skill** | Skill | The `/handoff` workflow -- composes the continuation prompt from the extension's structured facts and the agent's live context |

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

So the capability requires the extension payload. This plugin's job is to
**deliver that payload** to the user-space extension load path
(`~/.copilot/extensions/`) and ship the accompanying skill.

## Install

This is a **payload runtime** plugin (a non-Python runtime: a JavaScript
extension deployed outside the plugin cache). Per the
[install contract](../../docs/install-contract.md), a payload update is two
steps -- refresh the marketplace cache, then run the installer from the
source dir:

```bash
copilot plugin update context-handoff@copilot-extensions
# then, from the updated source (the context-handoff-setup skill drives this):
#   Windows: pwsh -File scripts/install.ps1 update
#   Linux:   bash scripts/install.sh update
```

The installer:

1. Copies `extension/context-handoff/extension.mjs` to
   `~/.copilot/extensions/context-handoff/extension.mjs`.
2. Ensures `experimental: true` in `~/.copilot/settings.json` (extensions are
   gated behind it).
3. Writes a `schema_version` 3 deploy manifest to `~/.context-handoff/`.

The extension activates on the **next** Copilot CLI session (the CLI scans
`~/.copilot/extensions/` at startup, before installers run).

## Status / uninstall

```bash
scripts/install.ps1 status      # or install.sh status
scripts/install.ps1 uninstall   # removes the deployed extension + manifest
```

Uninstall removes the deployed extension and manifest. It intentionally
leaves `experimental: true` in `settings.json` alone -- other extensions may
rely on it.

## Thresholds

| Utilization | Behavior |
|-------------|----------|
| 55% | Gentle reminder: "consider generating a handoff soon" |
| 70% | Urgent reminder: "generate NOW, compaction at ~80%" |

Reminder state resets after a successful compaction.
