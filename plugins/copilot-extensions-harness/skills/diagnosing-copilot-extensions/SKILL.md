---
name: diagnosing-copilot-extensions
description: >
  Diagnoses installed copilot-extensions payloads, commands and services.
  Identifies artifact ownership, separates canonical installer-equivalent
  repair from prohibited runtime-code patches, and verifies consumers.
  Covers failed/refused update subtasks and stale operational state without
  erasing preserved state or activating optional services.
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
  - 'should I patch this myself'
  - 'hotfix a plugin locally'
  - 'work around a copilot-extensions bug'
---

# Diagnosing copilot-extensions

Something's wrong with a deployed plugin. **Diagnose before remediating** — an
error names a symptom, not a root cause. Read the literal error, form a
hypothesis, gather evidence, and only then act. For an idempotent step a single
retry is a fine first move; never force-deploy or kill a process on a hunch.

For any external-service outage attribution, bring the
[provider bulletin and scoped evidence](../../references/external-service-outages.md)
in the operator-facing report. Without a matching receipt, label the suspected
provider cause unconfirmed rather than presenting it as an outage.

## Identify the owner; repair artifacts, never monkey-patch code

This is the primary purpose of this skill, and it binds **every** consumer of
the suite, whether or not they ever contribute:

1. **Resolve ownership and desired state.** Identify the addressed installation,
   authentic selected release/profile, configuration and exact command binding.
   Use the source-owned
   [footprint reference](https://github.com/ThomasMichon/copilot-extensions/blob/main/docs/installation-footprint.md)
   (`docs/installation-footprint.md` in a resolved source checkout) for variants
   and the failure → subtask → artifact → verification map. A familiar path or
   process name is not ownership; the inventory is not exhaustive.
2. **Use the official owner first, then complete authorized repair.** If update
   fails, refuses, or cannot perform a subtask it normally attempts or is
   expected to complete, **any installer-equivalent subtask within the existing
   repair scope may be restored**: configuration, binstubs/generated launch
   assets, central daemons/tasks, doctorable worktree state, footprint folders/
   junctions and stale operational guards. Prefer authentic payload/shared
   installer subtasks or standalone helpers; when unavailable, reconstruct only
   the canonical artifact proven by the selected authentic template/schema and
   resolved inputs, under the owner's exclusion/conditional-publication boundary.
   Preserve before-state and verify the canonical reader and actual consumer.
   Missing installer capability is not a blanket denial of operational repair.
3. **Never monkey-patch runtime package code or shipped templates.** Exact
   regeneration of generated launcher/template output is artifact repair, not
   code monkey-patching. Do not modify package logic, invent a custom footprint
   or repair a completed immutable environment in place. Real code defects need
   source contribution and authentic replacement. **Operational lock state is
   also different from code:** use
   `agent-conduct-guidance:recovering-operational-locks` for approved,
   evidence-bound quarantine/hand-edits or bypasses with exclusion, recomparison/
   CAS and receipts. Do not restore a categorical official-CLI-only restriction.
4. **Preserve independent gates and state.** This does not grant privileges,
   change selected source/feed policy, waive authentic-input/incoming-health
   checks, capture foreign session/state, stop session hosts/keepers or protected
   forwards, or activate optional services. Keep configuration, credentials,
   repositories/worktrees, sessions and databases. Verify the whole requested
   incoming set and all current pointers/registrations; queued work or a dead-PID
   receipt is not success. Old recovery/ZDD and final central retirement are
   independent, best-effort work under the
   [replacement goal](https://github.com/ThomasMichon/copilot-extensions/blob/main/visions/installer/README.md#authoritative-runtime-replacement),
   not manual-update permission gates. Report actual blockers precisely.

For a genuine code defect:
   - **File at the established tracker destination** — when that destination is
     this upstream suite, open/find its GitHub issue in generic terms: no
     PII, internal paths/hostnames, account details, or proprietary/downstream
     context (see `contributing-to-copilot-extensions`'s *Sanitization* rule
     and its worked examples in
     [`references/sanitization-examples.md`](../../references/sanitization-examples.md)). This is the right move even if
     you never intend to fix it yourself; dependency ownership alone must not
     redirect a consumer's already-established report destination.
   - **Replace from authentic source** if the deployed software is stale or
     corrupted, through authorized update or the canonical repair boundary above.
   - **Contribute a versioned fix** through
     `contributing-to-copilot-extensions`'s source worktree/PR flow, never by
     editing the deployed package.

## Where things live

| What | Path |
|------|------|
| Installed plugin payloads | `~/.copilot/installed-plugins/copilot-extensions/<plugin>/` |
| Runtime roots | `~/.agent-*` (for example `~/.agent-worktrees/`, `~/.agent-bridge/`, `~/.agent-codespaces/`, `~/.agent-containers/`, `~/.agent-mcp/`, `~/.agent-logger/`, `~/.agent-dispatch/`, `~/.agent-index/`, `~/.agent-vault/`) <!-- marketplace-isolation: allow deployed-runtime-diagnostics --> |
| **Writable source checkout** | Resolve it, don't assume it's undocumented -- run `<agent-worktrees catalog argv[0]> related resolve copilot-extensions` (the exact `argv[0]` from the session command catalog, never a bare PATH lookup; or the equivalent for another plugin-suite repo). A runtime-only path above is never the only copy; concluding a source-location gap without running this first is the mistake, not a real gap. <!-- marketplace-isolation: allow deployed-runtime-diagnostics --> |
| Versioned slots | Resolved legacy roots or explicitly activated core installation cells; historical flat environments and modern nested `.venv` applications coexist. See the footprint reference for owners, selectors and preserved state. |
| Binstubs | `~/.local/bin/agent-*` (`.ps1` primary + `.cmd` fallback on Windows) <!-- marketplace-isolation: allow deployed-runtime-diagnostics --> |
| Enablement | Effective user/repo Copilot settings (`enabledPlugins` / `extraKnownMarketplaces`); ordinary payload loading and user-level session-extension activation have distinct gates in `docs/installation.md` |
| Catalog | `.github/plugin/marketplace.json` in the repo |

## Symptom → cause → action

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `copilot plugin update` says **"already at latest"** but the code is stale | Version not bumped before merge (marketplace compares versions) | Check the plugin's `plugins[N].version` in the repo vs the deployed `plugin.json`; the fix is a version bump on the *source* side (see `contributing-to-copilot-extensions`). |
| `plugin update` **succeeded** but the runtime behaves unchanged | Payload refreshed, **runtime not redeployed** — the CLI's "updated" message is payload-only | Use the unified deploy path: `<repo> update` (normally `agent-worktrees update`) on the machine. If the payload/runtime have the same version but content drift is suspected, use `<repo> update --force`. Per-plugin `install.*` / `init.*` is only a local-testing or recovery path. <!-- marketplace-isolation: allow deployment-management --> |
| `agent-worktrees` / `agent-bridge` **command not found** | Runtime not installed, `~/.local/bin` not on PATH, or an earlier PATH entry shadows the binstub | Check `Get-Command agent-worktrees -All` / `which -a agent-worktrees`; ensure `~/.local/bin` wins; run `<repo> update` to reconcile missing runtime/binstubs. <!-- marketplace-isolation: allow deployed-runtime-diagnostics --> |
| A **skill won't load** in a session | Plugin not enabled, repository not trusted, or session not restarted | Confirm effective enablement/trust and **restart the session**. Ordinary skill loading does not require experimental mode; session extensions have a separate activation gate. |
| **agent-bridge not responding** | Service not running, stale routing table, or client assuming an old fixed port | `agent-bridge status` (it resolves the live dynamic port); use `<repo> update` / `agent-bridge status` evidence before restarting. On POSIX check the user service; on Windows current service lifecycle may be user-mode, with legacy scheduled-task artifacts only if installed earlier. <!-- marketplace-isolation: allow deployed-runtime-diagnostics --> |
| Bridge runs but a **remote send fails** | SSH transport, not the bridge | Test the SSH alias directly; check topology with `agent-bridge machines` / `agent-bridge agents`; fix the alias/key before touching the service. <!-- marketplace-isolation: allow deployed-runtime-diagnostics --> |
| Windows: **two `python.exe` processes**, or a base-interpreter path, looks rogue | A normal stdlib-venv launcher/base-worker pair may represent one logical daemon | Correlate native parent/child identities, selected flat/nested environment, `pyvenv.cfg` and owning service record. Neither process count nor base path proves duplication; see the footprint reference and Bridge's split-brain guidance. |
| **MCP tools unavailable** in a sub-agent | agent-mcp bridge not wired / not ready | Verify the agent's `mcp-servers` entry and the `agent-mcp` bridge config; honor the MCP-readiness pattern (report unavailability, fall back to CLI). |
| Runtime seems **half-upgraded / corrupt** | Interrupted install, incomplete versioned slot, or same-version drift | Use authorized `<repo> update --force`; if it fails/refuses, follow the footprint reference's owning subtask and canonical repair boundary. Preserve durable state; never erase the runtime root. |
| Official update/repair **cannot finish an installer subtask** | Missing legacy command, damaged generated artifact, or stale operational guard | Restore that authorized subtask through authentic owner helpers or proof-bound canonical artifact reconstruction. Verify the original requested operation, not merely disappearance of the error. |

## Diagnostic commands

```bash
copilot plugin list                          # what's installed + enabled
agent-worktrees --version && agent-worktrees status # marketplace-isolation: allow deployed-runtime-diagnostics
agent-bridge version && agent-bridge status  # marketplace-isolation: allow deployed-runtime-diagnostics
agent-codespaces version                     # marketplace-isolation: allow deployed-runtime-diagnostics
agent-mcp status                             # marketplace-isolation: allow deployed-runtime-diagnostics
```

These reads still use the exact attributable command binding for the selected
installation. Update/force is a mutation, not a diagnostic read, and requires
the existing update/repair authority.

Compare the admitted selected source, delivered payload, completed/selected
runtime and actual running generation separately. A deployed `plugin.json`
comparison alone cannot establish whole-installation currency.

## Reset is not state-preserving repair

Source-checkout `tools/reset.ps1` / `tools/reset.sh` are limited destructive
recovery tools, not universal repair or clean-install-equivalence helpers.
Inspect their current implementation and removal scope before any separately
authorized reset. Do not use them as the fallback for an ordinary repair that
must preserve configuration, credentials, sessions, databases and connectivity.
Never delete a whole runtime root, traverse a junction for recursive cleanup,
kill by process name, or remove managed worktrees manually.

## Reference

`docs/architecture.md` (runtimes, ports, the payload/runtime split),
`docs/install-contract.md` (the runtime-plugin contract), and each plugin's own
`docs/getting-started.md`; the
[footprint and repair reference](https://github.com/ThomasMichon/copilot-extensions/blob/main/docs/installation-footprint.md#installer-equivalent-repair)
owns the inventory and repair decision map. Source code/template changes
continue through `contributing-to-copilot-extensions`; canonical operational
artifact repair does not require pretending it is a source-code contribution.
