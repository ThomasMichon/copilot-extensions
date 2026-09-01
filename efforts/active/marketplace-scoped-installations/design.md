# Marketplace-Scoped Installations — Architecture

Back to the [effort](README.md).

## Scope boundary

This architecture is private runtime infrastructure for the runtime-bearing
core plugin identities defined by the `copilot-extensions` suite. It is not a
general plugin namespacing mechanism. An independent marketplace may carry a
copy of those identities for source-isolation proof, but payload-only plugins
and unrelated downstream, internal, or third-party plugin families must not
create, validate, expose, or depend on these installation cells.

Namespaced mode is explicit opt-in and default-off. While this effort is active,
all namespaced installation and lifecycle execution occurs only in disposable
clean-room environments. Persistent development and production machines remain
in legacy mode and may inspect the model only through read-only diagnostics.

## Decisions

1. **`copilot-extensions` is the durable host concept.** A marketplace with the
   same name is one installation source, not the owner of the concept.
2. **Marketplace provenance is part of installation identity.** Plugin name and
   version alone never select mutable state or a running service.
3. **Generic plugin shims are payload-local.** Skills, hooks, agents, services,
   and peers invoke the shim belonging to the payload that supplied them.
4. **Global command space is project-owned.** `~/.local/bin` retains project
   entry points only. Each wrapper records and pins its owning installation.
5. **Committed repo policy is source-neutral.** Marketplace cells isolate
   machine-local runtime and adoption state; they do not duplicate ordinary
   committed project configuration.
6. **Cross-cell composition is explicit.** Same-cell sibling discovery is the
   default. A provider or client crossing cells names the exact target identity.
7. **Missing provenance fails closed.** No compatibility path may silently use
   an unqualified runtime root, ambient same-named command, wildcard marketplace
   scan, or fixed endpoint.
8. **Namespaced activation is user-local and default-off.** The shared user
   config expresses desired mode; an activation receipt records the actual
   authoritative root. Existing legacy state never migrates or competes merely
   because the flag changed.

## Approved target model

The implementation uses this directory organization:

```text
~/.copilot-extensions/
  marketplaces/
    <marketplace-id>/
      namespace.json
      plugins/
        <plugin>/
          install.json
          versions/<version>/
          snapshots/<version>/
          current-version
          last-known-good
          deploy-manifest.json
          state/
          run/
          logs/
      repos/
        <stable-repo-id>/
          identity.json
          <plugin>/...
```

This layout belongs to the install contract, not vision-level intent. The
marketplace identifier combines a readable configured marketplace key with a
normalized source fingerprint. It remains stable across plugin updates but
distinguishes independent sources with the same display name.

Source resolution precedence:

1. explicit context supplied by reconciliation, dispatch, or an installer;
2. provenance attached to a staged payload;
3. the installed marketplace payload boundary;
4. the nearest directory-marketplace catalog;
5. a canonical local marketplace path for development;
6. otherwise fail as ambiguous.

The normalized marketplace source is the identity input. The transient Copilot
cache path is evidence of provenance, not by itself the portable identity.

## Invocation chain

```text
loaded skill / hook / injected command catalog
  -> <owning-payload>/bin/<agent-command>
  -> marketplace installation context
  -> <cell>/plugins/<plugin>/install.json
  -> current-version / last-known-good
  -> immutable version-slot interpreter
  -> plugin module
```

Payload shims are checked-in, thin, and generated from canonical
POSIX/PowerShell/CMD templates. They resolve their own payload root, move their
working directory outside the replaceable payload, validate cell identity, and
dispatch directly to the selected interpreter. They never resolve a sibling
through `PATH`.

Skills cannot interpolate `${PLUGIN_ROOT}` directly. Each runtime plugin
therefore emits a session command catalog from a hook that receives
`COPILOT_PLUGIN_ROOT`. Operative skill instructions refer to that exact command
entry rather than assuming a global executable.

The command catalog uses this initial contract:

```json
{
  "schema": "copilot-extensions.session-command-catalog",
  "version": 1,
  "plugin": "<plugin>",
  "payload": {"provenance": "payload-local"},
  "commands": [{
    "id": "<command>",
    "argv": ["<absolute-payload-command>"],
    "shell": "direct|cmd",
    "availability": "ready|unavailable"
  }]
}
```

Consumers append arguments to the supplied `argv`; they never reconstruct the
path from `payload`, search `PATH`, or substitute another same-named command.
`shell: "direct"` names an executable payload entry point. `shell: "cmd"` names
a payload-local CMD launcher that must be invoked through the Windows shell;
structured input uses stdin or a request file rather than inline CMD JSON.

Project binstubs remain in `~/.local/bin` and forward to an absolute,
payload-local agent-worktrees shim. An ownership receipt binds the project
command to marketplace identity, repository identity, and payload/runtime
identity. A second marketplace must explicitly transfer ownership or choose a
distinct project command; last-writer-wins replacement is forbidden.

## State and configuration

Committed configuration moves toward:

```text
<repo>/.copilot-extensions/
  agent-worktrees/config.yaml
  agent-worktrees/related.yaml
  agent-codespaces/config.yaml
  agent-index/config.yaml
```

These files describe repository policy and remain marketplace-neutral. Optional
marketplace overlays are explicit exceptions, not the default.

Machine-local project state lives under the adopting cell's `repos/` tree. A
normalized remote identity plus a collision-resistant suffix is the canonical
key; repository basename remains display metadata only.

Runtime-root activation is governed by the normative
[`installation-mode install contract`](../../../docs/install-contract.md#installation-mode-governance);
the effort's [governance note](installation-mode-governance.md) retains
rationale and rollout. Missing policy preserves legacy behavior. Namespaced
policy creates a cell automatically only for a new installation with no
unattributed legacy footprint; existing state requires explicit migration, a
legacy ownership tombstone, and a generation-pinned activation receipt.

Host-provided plugin-data directories are useful inputs but not the identity
contract. Hooks and LSP servers receive `COPILOT_PLUGIN_DATA`; Agent Plugins
specification MCP servers also receive `PLUGIN_DATA`; legacy MCP servers and
JavaScript extensions do not receive an equivalent automatic directory. A
surface may use the supplied directory only when the host proves that it is
qualified by globally distinguishing marketplace provenance. All other
surfaces resolve the same installation context explicitly, so isolation does
not depend on a variable that is absent from part of the plugin runtime.

## Runtime isolation requirements

Every process receives immutable installation context containing marketplace,
plugin, and instance identity plus roots for runtime, state, endpoints,
providers, logs, cache, and repositories. Child processes replace conflicting
legacy root variables rather than inheriting them accidentally.

Endpoint records and provider manifests include both producer and intended
consumer identity. Readers reject mismatches before dialing or spawning. Service
names, leases, mutexes, pipes/sockets, scheduled tasks, systemd units, and
coalescing keys are cell-scoped. Local services prefer dynamic or OS-native
endpoints published through the cell's rendezvous state.

Remote execution carries the context explicitly. It never reconstructs the
target from `~/.agent-*` paths on the remote host.

The detailed Phase 3 contract is
[`phase-3-installation-context.md`](phase-3-installation-context.md).

## Affected production surfaces

### Shared canonical libraries and guards

- New vendorable installation-context primitive.
- `libs/versioned-runtime` remains responsible for selecting an interpreter
  within an explicitly supplied runtime root.
- `libs/plugin-resolve`, `endpoint-rendezvous`, `core-delegation`,
  `credential-relay`, `ssh-manager`, `single-instance-lease`, `zdd`, and
  `config-migrate`.
- `tools/sync-versioned-runtime.py`, vendored-library synchronization, install
  contract checks, bootstrap checks, runtime-resolution checks, and new
  marketplace-isolation guards.

### Runtime plugin installer families

The installer/init, bootstrap, payload-shim, service-launch, and vendored
resolver surfaces of:

- agent-worktrees
- agent-bridge
- agent-codespaces
- agent-containers
- agent-dispatch
- agent-index
- agent-logger
- agent-machines
- agent-mcp
- agent-ssh
- agent-vault

### High-risk runtime ownership

| System | Isolation-sensitive ownership |
|--------|-------------------------------|
| agent-worktrees | Global repo/project registries, project overrides, reconciliation, project binstubs, worktree/session state |
| agent-bridge | Sessions database, provider registry, service/ports/endpoints, installed-plugin discovery, sibling launch |
| agent-codespaces | Adopted repos, bridge relay binding, provider registration, generated remote assets, remote context |
| agent-containers | Bridge binding, leases, relay state, SSH transport, remote launch paths |
| agent-dispatch | Coordinator/supervisor identity, fixed endpoints, federation state, SSH remote discovery |
| agent-index | Daemon and engine identity, scheduled tasks, endpoint and durable index state |
| agent-logger | Scheduled service identity, logging configuration and durable chronicle state |
| agent-vault | Service/task/lease identity and durable encrypted state |
| agent-mcp | Bridge discovery, wildcard installed-plugin scans, overrides and token cache |
| agent-ssh | Shared ssh-manager sockets, locks, connection configuration and transport processes |

For service-bearing plugins such as agent-bridge, a later cell-qualified
runtime-root migration must move the service identity and canonical launcher in
the same operative cutover. Moving only the interactive client would let its
self-healing path start a cell-local daemon while legacy management commands
still control the machine-global daemon.
| agent-machines | Runtime root, machine registry and payload command surface |

### Repo configuration and command consumers

- agent-worktrees config, hooks, related-repo, state-root, harness-state, doctor,
  CLI, reconciliation, and update-stage modules.
- worktree-manager harness-state, core-install, engine-client, discovery,
  catalog, model, self-install, and bootstrap surfaces.
- Runtime-plugin skills, sub-agent prompts, hooks, extensions, provider
  manifests, service definitions, and remediation output containing operative
  bare `agent-*` commands.
- `docs/configuration.md`, `docs/architecture.md`, `docs/install-contract.md`,
  patterns, per-plugin architecture/config references, clean-room scenarios,
  tests, fixtures, and examples encoding legacy paths.

## Migration rules

- New cell-scoped paths win; legacy paths are read only through an explicit
  compatibility resolver.
- Existing unqualified state has no trustworthy owner. Migration requires the
  operator to name the destination cell and writes an ownership receipt.
- Migration holds the legacy plugin lock/lease and destination install lock
  across quiescence, state transfer, tombstone publication, activation
  publication, and verification.
- If new and legacy state both exist, diagnostics report both and never merge
  registries silently.
- Legacy services and global shims are retired only after ownership is proven,
  the new cell is healthy, and rollback metadata exists.
- Uninstall removes only artifacts carrying the uninstalling cell's ownership
  receipt.
- Committed repo config uses new-first, legacy-fallback reads for a bounded
  compatibility window; install/update never rewrites it.
- Every legacy installer/bootstrap entrypoint must use the shared activation
  probe and refuse namespaced-active, orphaned-transfer, and maintenance before
  any exemplar becomes operative.

## Cross-platform gate

Every operative phase must prove Windows and POSIX behavior in disposable clean
rooms before its contract becomes mandatory. A persistent workstation, server,
or ordinary plugin installation is not a namespaced test venue during this
effort. Particular attention is required for:

- Windows payload replacement while a payload-local PowerShell/CMD shim is
  running;
- scheduled-task, named-mutex, named-pipe, and process identity scoping;
- systemd user-unit, Unix-socket, executable-bit, and atomic-marker behavior;
- WSL host/guest identity propagation, with separate receipt and root ownership
  unless a future explicit sharing contract is introduced;
- quoting and stdio preservation through payload shims and remote execution.

The acceptance test is two marketplace cells with the same plugin names running
simultaneously through install, first use, service start, project adoption,
update, rollback, repair, and uninstall without observing or modifying one
another. Negative acceptance also proves that payload-only plugins and unrelated
plugin families never enter this resolver or gain cell-owned runtime, state,
command, service, or lifecycle artifacts, even when installed from a
marketplace that also carries a core suite plugin.

## Report-only inventory guard

`tools/check-marketplace-isolation.py` scans operative plugin surfaces:
install/lifecycle scripts, checked-in binstubs, runtime source, hooks,
extensions, skills, and agent prompts. It excludes tests, fixtures, snapshots,
and descriptive plugin docs so the baseline measures behavior-producing or
agent-operative surfaces rather than historical prose.

The stable categories are:

- `unqualified-runtime-root`
- `global-plugin-binstub`
- `path-sibling-launch`
- `fixed-service-identity`
- `bare-agent-command`

Default output is a concise category summary and always exits zero. `--verbose`
adds file/line findings, `--json` emits the complete machine-readable inventory,
and `--strict` is reserved for Phase 6 after all producing phases conform. An
intentional compatibility seam uses
`marketplace-isolation: allow <reason>`; a reasonless suppression remains a
finding.
