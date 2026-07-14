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
| Runtime venvs | `~/.agent-worktrees/`, `~/.agent-bridge/`, `~/.agent-codespaces/`, `~/.agent-containers/`, `~/.agent-mcp/`, `~/.agent-logger/`, `~/.agent-dispatch/`, `~/.agent-vault/` |
| Binstubs | `~/.local/bin/agent-*` |
| Enablement | `~/.copilot/settings.json` (`experimental: true`) + repo `.github/copilot/settings.json` (`enabledPlugins`) |
| Catalog | `.github/plugin/marketplace.json` in the repo |

## Symptom → cause → action

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `copilot plugin update` says **"already at latest"** but the code is stale | Version not bumped in the push (marketplace compares versions) | Check the plugin's `plugins[N].version` in the repo vs the deployed `plugin.json`; the fix is a version bump on the *source* side (see `contributing-to-copilot-extensions`). |
| `plugin update` **succeeded** but the runtime behaves unchanged | Payload refreshed, **runtime not redeployed** — the CLI's "updated" message is payload-only | Run the plugin's own installer step: `agent-worktrees update`; `install.* update` (bridge/codespaces); `init.* --force` (containers/mcp/dispatch); logger installer. |
| `agent-worktrees` / `agent-bridge` **command not found** | Runtime not installed, or `~/.local/bin` not on PATH | `Test-Path ~/.local/bin/agent-worktrees`; add `~/.local/bin` to PATH; re-run the plugin's `init.*`/`install.*` if the runtime is absent. |
| A **skill won't load** in a session | `experimental` off, plugin not enabled, or session not restarted (plugins scan at startup) | Confirm `experimental: true` in `~/.copilot/settings.json`; confirm the plugin in `enabledPlugins`; **restart the session**. |
| **agent-bridge not responding** | Service (systemd user unit / Windows scheduled task) not running | `agent-bridge status`; start via `install.* start` / `systemctl --user status agent-bridge`; on Windows check the "Agent Bridge" scheduled task. |
| Bridge runs but a **remote send fails** | SSH transport, not the bridge | Test the SSH alias directly; check topology with `agent-bridge machines` / `agent-bridge agents`; fix the alias/key before touching the service. |
| **MCP tools unavailable** in a sub-agent | agent-mcp bridge not wired / not ready | Verify the agent's `mcp-servers` entry and the `agent-mcp` bridge config; honor the MCP-readiness pattern (report unavailability, fall back to CLI). |
| Runtime seems **half-upgraded / corrupt** | Interrupted install, drifted venv | Re-run the plugin's installer with `-Force`/`--force`; if still broken, use the baseline reset (below). |

## Diagnostic commands

```bash
copilot plugin list                          # what's installed + enabled
agent-worktrees --version && agent-worktrees status
agent-bridge version && agent-bridge status  # service health
agent-codespaces version                     # if adopted
agent-mcp status                             # if installed
```

Compare a deployed `plugin.json` version against the repo's
`marketplace.json` `plugins[N].version` to confirm whether a machine is actually
behind or the source was never bumped.

## Baseline reset (escape hatch)

When a machine's runtimes are wedged and you want a clean baseline, the repo
ships an idempotent reset that stops services, removes the installer-based
runtimes/binstubs/tasks, and (optionally) uninstalls the plugins — it works even
when the CLIs are broken:

```powershell
pwsh -File tools\reset.ps1                       # prompts; -Yes to skip
pwsh -File tools\reset.ps1 -Yes -RemovePlugins   # also copilot plugin uninstall
```
```bash
bash tools/reset.sh                              # prompts; --yes to skip
bash tools/reset.sh --yes --remove-plugins
```

Your source repos and their `.worktrees` are never touched. `agent-containers`
and `agent-mcp` may need manual removal of `~/.agent-*` until reset covers them.

## Reference

`docs/architecture.md` (runtimes, ports, the payload/runtime split),
`docs/install-contract.md` (the runtime-plugin contract), and each plugin's own
`docs/getting-started.md`. To land a fix once you've found the cause, use
`contributing-to-copilot-extensions`.
