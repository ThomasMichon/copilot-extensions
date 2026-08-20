---
name: diagnosing-copilot-extensions
description: >
  Diagnose problems with deployed copilot-extensions plugins -- a plugin update
  that "succeeds" but changes nothing, a missing binstub or command-not-found, a
  skill that won't load, the agent-bridge service not responding, MCP tools
  unavailable in a sub-agent, or a stale runtime. Symptom -> cause -> action, the
  key paths and diagnostic commands, and the baseline-reset escape hatch. Use
  when something is wrong with an installed plugin or its runtime.
  Trigger phrases include:
  - 'agent-worktrees not found'
  - 'agent-bridge not responding'
  - 'plugin update did nothing'
  - 'already at latest but stale'
  - 'skill not loading'
  - 'binstub missing'
  - 'command not found'
  - 'mcp tools unavailable'
  - 'diagnose copilot-extensions'
  - 'reset copilot extensions'
---

# Diagnosing copilot-extensions

Something's wrong with a deployed plugin. **Diagnose before remediating** — an
error names a symptom, not a root cause. Read the literal error, form a
hypothesis, gather evidence, and only then act. For an idempotent step a single
retry is a fine first move; never force-deploy or kill a process on a hunch.

## Where things live

| What | Path |
|------|------|
| Installed plugin payloads | `~/.copilot/installed-plugins/copilot-extensions/<plugin>/` |
| Runtime roots | `~/.agent-*` (for example `~/.agent-worktrees/`, `~/.agent-bridge/`, `~/.agent-codespaces/`, `~/.agent-containers/`, `~/.agent-mcp/`, `~/.agent-logger/`, `~/.agent-dispatch/`, `~/.agent-index/`, `~/.agent-vault/`) |
| Versioned slots | Python runtimes build immutable slots under `~/.agent-<name>/versions/<version>/`, publish `current-version`, and stamp `deploy-manifest.json` / completion markers |
| Binstubs | `~/.local/bin/agent-*` (`.ps1` primary + `.cmd` fallback on Windows) |
| Enablement | `~/.copilot/settings.json` (`experimental: true`) + repo `.github/copilot/settings.json` (`enabledPlugins` / `extraKnownMarketplaces`) |
| Catalog | `.github/plugin/marketplace.json` in the repo |

## Symptom → cause → action

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `copilot plugin update` says **"already at latest"** but the code is stale | Version not bumped before merge (marketplace compares versions) | Check the plugin's `plugins[N].version` in the repo vs the deployed `plugin.json`; the fix is a version bump on the *source* side (see `contributing-to-copilot-extensions`). |
| `plugin update` **succeeded** but the runtime behaves unchanged | Payload refreshed, **runtime not redeployed** — the CLI's "updated" message is payload-only | Use the unified deploy path: `<repo> update` (normally `agent-worktrees update`) on the machine. If the payload/runtime have the same version but content drift is suspected, use `<repo> update --force`. Per-plugin `install.*` / `init.*` is only a local-testing or recovery path. |
| `agent-worktrees` / `agent-bridge` **command not found** | Runtime not installed, `~/.local/bin` not on PATH, or an earlier PATH entry shadows the binstub | Check `Get-Command agent-worktrees -All` / `which -a agent-worktrees`; ensure `~/.local/bin` wins; run `<repo> update` to reconcile missing runtime/binstubs. |
| A **skill won't load** in a session | `experimental` off, plugin not enabled, or session not restarted (plugins scan at startup) | Confirm `experimental: true` in `~/.copilot/settings.json`; confirm the plugin in `enabledPlugins`; **restart the session**. |
| **agent-bridge not responding** | Service not running, stale routing table, or client assuming an old fixed port | `agent-bridge status` (it resolves the live dynamic port); use `<repo> update` / `agent-bridge status` evidence before restarting. On POSIX check the user service; on Windows current service lifecycle may be user-mode, with legacy scheduled-task artifacts only if installed earlier. |
| Bridge runs but a **remote send fails** | SSH transport, not the bridge | Test the SSH alias directly; check topology with `agent-bridge machines` / `agent-bridge agents`; fix the alias/key before touching the service. |
| Windows: **two `python.exe` daemons**, or one running from `C:\Program Files\Python3XX\python.exe`, looks rogue | **Normal** stdlib-venv shape, not a bug: the versioned-slot `Scripts\python.exe` is a `venvlauncher.exe` **supervisor** that re-execs the **base** interpreter as its **worker** (the worker is what binds the port). It's still the slot's code. | Confirm one daemon: worker's **PPID = the slot-path supervisor**, same start time, `active.json` names the canonical pid; slot `pyvenv.cfg` `home` = the base. Don't `Stop-Process` the `Program Files` worker as "global/rogue". See `agent-bridge-troubleshooting` § split-brain. |
| **MCP tools unavailable** in a sub-agent | agent-mcp bridge not wired / not ready | Verify the agent's `mcp-servers` entry and the `agent-mcp` bridge config; honor the MCP-readiness pattern (report unavailability, fall back to CLI). |
| Runtime seems **half-upgraded / corrupt** | Interrupted install, incomplete versioned slot, or same-version drift | Run `<repo> update --force`; if still broken, use the runtime's own uninstall/reinstall path or the baseline reset scope below. |

## Diagnostic commands

```bash
copilot plugin list                          # what's installed + enabled
agent-worktrees update --force               # unified payload + runtime reconcile, forced
agent-worktrees --version && agent-worktrees status
agent-bridge version && agent-bridge status  # service health
agent-codespaces version                     # if adopted
agent-mcp status                             # if installed
```

Compare a deployed `plugin.json` version against the repo's
`marketplace.json` `plugins[N].version` to confirm whether a machine is actually
behind or the source was never bumped.

## Baseline reset (escape hatch)

When the core runtimes are wedged and you want a clean baseline, the repo ships
an idempotent reset that works even when the CLIs are broken. **Current reset
scope is the original core trio**: `agent-worktrees`, `agent-bridge`, and
`agent-codespaces`; it removes their `~/.agent-*` runtime roots, bridge/relay
service artifacts, project binstubs, and (optionally) marketplace plugins /
per-project configs. Verify `~/.local/bin` afterward, especially on Windows
where current runtimes deploy `.ps1` + `.cmd` binstub siblings. The reset does
**not** claim to reset every newer runtime root (for example agent-mcp,
agent-dispatch, agent-index, agent-vault); use that plugin's uninstall/reinstall
path or remove its `~/.agent-*` root only after verifying it is not running.

```powershell
pwsh -File tools\reset.ps1                       # prompts; -Yes to skip
pwsh -File tools\reset.ps1 -Yes -RemovePlugins   # also copilot plugin uninstall
```
```bash
bash tools/reset.sh                              # prompts; --yes to skip
bash tools/reset.sh --yes --remove-plugins
```

Your source repos and their `.worktrees` are never touched.

## Reference

`docs/architecture.md` (runtimes, ports, the payload/runtime split),
`docs/install-contract.md` (the runtime-plugin contract), and each plugin's own
`docs/getting-started.md`. To land a fix once you've found the cause, use
`contributing-to-copilot-extensions`.
