# Phase 2 Launcher Contract Inventory

Back to the [Marketplace-Scoped Installations effort](README.md).

## Purpose

Phase 2 made agent-facing calls payload-local and made global project commands
attributable. The remaining `global-plugin-binstub` findings are not one
mechanical cleanup: they mix payload-owned calls that can move now, durable
external launch records that need installation context, legacy wrapper
publication that can retire only after migration, and descriptive text that is
not itself a launcher.

This inventory preserves the literal Phase 2 completion criterion: generic
global plugin wrappers do not count as retired while any real external caller
still depends on them. The 86 findings are the guard-visible baseline, not proof
that the guard currently sees every caller.

## Baseline

On 2026-08-26, `python tools/check-marketplace-isolation.py --json` reported 86
`global-plugin-binstub` findings across 14 plugins.

| Contract family | Findings | Phase | Reason |
|-----------------|---------:|-------|--------|
| Payload-owned self-wrappers | 6 | Phase 2 | The checked-in `agent-ssh` `emit-profile` and `verify` wrappers start inside their own payload and can invoke that payload's generated command directly. |
| Generic wrapper publication and plugin-specific PATH guidance | 36 | Phase 6 retirement | Installer declarations and compatibility guidance are the legacy generic plugin surface itself. They cannot disappear until cell-local runtimes and canonical launchers are healthy and ownership-checked. |
| Mixed project-command directory and PATH use | 10 | Permanent project surface plus Phase 6 generic cleanup | agent-worktrees uses the same global directory for attributable project commands and the legacy generic wrapper. Project publication and the PATH needed to reach it remain; only the generic plugin use retires. |
| Durable provider manifests | 4 | Phase 3 | `agent-codespaces` and `agent-containers` persist commands for a sibling service. The record must name a same-cell provider through explicit installation context, not pin a replaceable payload or select a global command. |
| Readiness legacy fallback | 3 | Phase 6 retirement | `agent-codespaces` already prefers its payload-local command. The remaining global path is only a legacy-ready fallback and retires with the wrapper it probes. |
| Remote transport selection | 3 | Phase 3 | `agent-index` constructs a remote command and `dtssh` exports a remote PATH. The destination cell and its canonical launcher must be serialized explicitly across the transport boundary. |
| Operator, bootstrap, nudge, and generated binding launchers | 7 | Phase 3 contract, Phase 6 fallback retirement | These calls originate outside a stable payload-local session catalog or persist beyond the payload that generated them. They need an explicit management context or attributable canonical launcher; their legacy fallback is removed only after migration. |
| Credential and askpass integration | 2 | Phase 3 contract, Phase 6 fallback retirement | The generated askpass helper persists outside the originating payload and invokes the vault runtime later. It needs a durable attributable launcher before its global fallback can retire. |
| Descriptive skills, help, and generated package metadata | 15 | Cleanup with the owning slice | These lines describe the legacy contract or duplicate package prose; they are not installed launchers. Correcting them prevents new consumers from depending on the old surface but does not by itself retire a wrapper. |
| **Total** | **86** | | |

## Detailed accounting

### Payload-owned self-wrappers - 6

- `plugins/agent-ssh/scripts/emit-profile.{sh,ps1}` - 3 findings.
- `plugins/agent-ssh/scripts/verify.{sh,ps1}` - 3 findings.

Both script families are invoked from an attributable plugin payload. They can
self-locate `../bin/agent-ssh` and preserve the raw-checkout Python fallback.
They do not need a machine-global command or Phase 3 cell registry to select
their own payload.

### Generic wrapper publication and plugin-specific PATH guidance - 36

- agent-bridge installers - 2.
- agent-codespaces installers - 4.
- agent-containers initializers - 4.
- agent-dispatch installers - 5.
- agent-index installers - 4.
- agent-logger installers - 3.
- agent-machines initializers - 3.
- agent-mcp initializers - 3.
- agent-ssh installers - 4.
- agent-vault generic wrapper publication and PATH guidance - 4.

The generic wrappers retire only after:

1. Phase 3 provides explicit installation context and an installation-local
   canonical launcher.
2. Phase 4 moves each runtime and service lifecycle into its cell.
3. Phase 6 attributes legacy state, proves the new cell healthy, preserves
   rollback, and removes only ownership-matched compatibility artifacts.

### Mixed project-command directory and PATH use - 10

- agent-worktrees installers and WSL project-command publication - 10.

The guard is syntactic and reports any use of the shared command directory.
These findings mix the generic `agent-worktrees` compatibility wrapper with
globally reachable, ownership-receipted project commands. Phase 6 removes only
the generic plugin use. Project-command publication and the PATH configuration
needed to reach attributable project commands remain permanent and require an
ownership-scoped guard allowance rather than deletion.

### Durable provider manifests - 4

- `plugins/agent-codespaces/scripts/register-bridge-provider.{sh,ps1}` - 2.
- `plugins/agent-containers/scripts/register-bridge-provider.{sh,ps1}` - 2.

The provider manifest outlives the session-start hook that writes it and is
consumed by agent-bridge over a later process boundary. A payload-cache path is
not a durable launcher because payload replacement may invalidate it. Phase 3
must let the hook resolve the same installation cell, persist producer and
consumer identity, and publish the provider's canonical launcher.

### Readiness legacy fallback - 3

- `plugins/agent-codespaces/scripts/readiness-context.{sh,ps1}` - 3.

Readiness already reports the payload-local command as ready before checking the
legacy binstub. The remaining path is intentionally a compatibility probe and
should disappear with the legacy wrapper in Phase 6, not be replaced by another
ambient alias.

### Remote transport selection - 3

- `plugins/agent-index/src/agent_index/transport.py` - 1.
- `plugins/agent-ssh/transports/dtssh/scripts/install-{client,host}.sh` - 2.

These calls cross a machine or shell boundary where the originating payload path
is not meaningful. Phase 3 must serialize marketplace and plugin identity plus
the target-side canonical launcher. Ambient remote PATH remains invalid even if
it happens to contain the expected command.

### Operator, bootstrap, nudge, and generated binding launchers - 7

- agent-machines bootstrap checks - 2.
- agent-worktrees session launcher fallback - 1.
- agent-worktrees nudge registration - 2.
- harness-knowledge binding emission - 2.

These surfaces either reconcile legacy runtime installation, persist a callback,
or need to reach a sibling plugin from a hook that cannot consume another
plugin's session catalog. Phase 3 supplies explicit management and same-cell
selection. Where an explicit payload-local command can be accepted earlier, the
legacy fallback still remains until Phase 6 migration proves it unused.

### Credential and askpass integration - 2

- agent-vault askpass generation and setup guidance in `scripts/install.sh` - 2.

The generated `vault-askpass` helper and `SUDO_ASKPASS` configuration persist
outside the payload and may run in a later, non-interactive process. Phase 3
must give that helper a durable attributable launcher. Phase 6 removes the
legacy global fallback after ownership and health are proven.

### Descriptive skills, help, and generated package metadata - 15

- agent-dispatch generated package metadata - 2.
- agent-mcp generated package metadata - 1.
- agent-machines setup guidance - 1.
- agent-vault setup guidance - 2.
- agent-worktrees help and setup guidance - 5.
- copilot-extensions-harness contribution and diagnosis guidance - 3.
- customizing-copilot installation guidance - 1.

These findings should be revised with the implementation slices they describe,
not treated as a separate Phase 2 completion gate.
Generated `*.egg-info/PKG-INFO` copies should remain synchronized with their
source metadata. The isolation guard should continue reporting operative
instructions, but documentation-only findings must not be mistaken for proof
that a wrapper is installed.

## Known guard-invisible callers

The current guard recognizes literal `.local/bin` text. It does not yet catch
equivalent paths assembled from components or strings whose source escaping
does not match its regular expression. The caller inventory therefore also
includes:

- `agent-codespaces/src/agent_codespaces/_invoke.py`, whose persisted spawn
  command selects the version-stable global binstub.
- `agent-containers/src/agent_containers/_invoke.py`, which carries the same
  persisted-spawn requirement.
- `agent-bridge/src/agent_bridge/agent_registry.py`, where the service resolves
  the global `agent-worktrees` management command.
- the PowerShell remote branch in `agent-index/src/agent_index/transport.py`,
  alongside the one Bash branch already counted.

These callers reinforce the Phase 3 dependency: each needs a durable canonical
launcher or explicit management context, not a replaceable payload path.
Broadening the guard to recognize component-built and escaped path forms is a
prerequisite for making it blocking in Phase 6.

## Serial execution

1. Move the six payload-owned agent-ssh self-wrappers to their own generated
   payload command and add cross-platform wrapper tests.
2. Clean stale descriptive and generated metadata references as the owning
   plugin slices land.
3. Complete Phase 3 installation context and canonical launcher contracts.
4. Convert provider manifests, remote transport, nudge, bootstrap, generated
   binding, service, scheduler, credential, and askpass callers to those
   contracts in their owning runtime phases.
5. In Phase 6, attribute legacy state, verify health and rollback, remove
   generic global wrappers, preserve attributable project-command publication
   and its PATH contract, add ownership-scoped guard allowances, broaden the
   guard to cover known invisible forms, and then make it blocking.

## Completion rule

Phase 2 issue #1103 remains open and the effort checkbox remains unchecked until
the generic global wrappers can be removed without stranding any service,
provider, MCP, remote, credential, askpass, deployment, generated, or operator
caller. Finishing the six immediate findings is progress, not closure.
