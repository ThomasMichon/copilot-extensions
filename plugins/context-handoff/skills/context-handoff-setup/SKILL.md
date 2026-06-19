---
name: context-handoff-setup
description: >
  Install or update the context-handoff Copilot CLI extension runtime --
  deploy extension.mjs to ~/.copilot/extensions/, ensure the experimental
  flag, and verify it loads. Use this skill when the context-handoff
  extension is missing, stale, or not loading, or after a
  `copilot plugin update context-handoff`. Trigger phrases include:
  - 'install context-handoff'
  - 'set up context-handoff'
  - 'context-handoff not loading'
  - 'context-handoff extension missing'
  - 'handoff extension not working'
  - 'no handoff reminders'
  - 'generate_handoff_prompt missing'
  - 'update context-handoff'
  - 'deploy the handoff extension'
---

# Context Handoff Setup

Install / update the **context-handoff extension** runtime. The extension is
the live context-window monitor (token tracking + 55%/70% nudges +
`generate_handoff_prompt` / `save_handoff_prompt` tools). For the `/handoff`
authoring workflow itself, see the **context-handoff** skill.

## Why a separate install step

`copilot plugin update context-handoff@copilot-extensions` only refreshes the
plugin **payload** under
`~/.copilot/installed-plugins/copilot-extensions/context-handoff`. It does
**not** deploy the extension to the load path. The runtime install is a
second step: run the plugin's `scripts/install.*` from the source dir.

## Install / Update

Run the installer from the **source dir** -- the marketplace plugin dir, or a
local checkout of `copilot-extensions`:

```powershell
# Windows -- from the context-handoff plugin source dir
pwsh -File scripts/install.ps1 update
```

```bash
# Linux/WSL -- from the context-handoff plugin source dir
bash scripts/install.sh update
```

The marketplace source dir is:

```
~/.copilot/installed-plugins/copilot-extensions/context-handoff
```

> **Windows caveat.** If the extension is loaded in the running session,
> `copilot plugin update` can fail with `EBUSY` (the live CLI holds handles in
> the installed-plugins dir). Prefer running `scripts/install.* update` from a
> **local checkout** of `copilot-extensions` in that case.

## What the installer does

1. Copies `extension/context-handoff/extension.mjs` to
   `~/.copilot/extensions/context-handoff/extension.mjs`.
2. Ensures `experimental: true` in `~/.copilot/settings.json` (extensions are
   gated behind it -- the env var alone is insufficient).
3. Writes a `schema_version` 3 deploy manifest to
   `~/.context-handoff/deploy-manifest.json`.

## Verify

The extension activates on the **next** session (the CLI scans
`~/.copilot/extensions/` at startup). After restarting:

```bash
scripts/install.ps1 status      # or install.sh status
```

A loaded extension logs `[Context Handoff] Session started ...` and exposes
the `generate_handoff_prompt` / `save_handoff_prompt` tools. If it does not
load, confirm `experimental: true` in `~/.copilot/settings.json` and that
`~/.copilot/extensions/context-handoff/extension.mjs` exists.

## Uninstall

```bash
scripts/install.ps1 uninstall   # or install.sh uninstall
```

Removes the deployed extension and the deploy manifest. Leaves
`experimental: true` untouched (other extensions may need it).
