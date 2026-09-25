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

## Baseline (historical snapshot, superseded by the 2026-09-25 re-audit below)

On 2026-08-26, `python tools/check-marketplace-isolation.py --json` reported 86
`global-plugin-binstub` findings across 14 plugins.

After [#1187](https://github.com/ThomasMichon/copilot-extensions/pull/1187),
the guard-visible count was 80 as of that PR. The six payload-owned
self-wrapper findings were complete at that point; every durable boundary and
the generic wrapper retirement were still open as of 2026-08-26 -- the
2026-09-25 re-audit below found the durable provider manifests have since
converted too, so this paragraph no longer describes current state.

### 2026-09-25 re-audit

`python tools/check-marketplace-isolation.py --json` now reports **83**
`global-plugin-binstub` findings across 14 plugins — a different 14 than the
baseline's, and the per-family table below no longer sums cleanly against a
single fixed snapshot. A raw line-by-line re-read of all 83 current findings
produced an accurate current *count* plus a **partial** triage — enough to
confirm which families converted, which findings moved category, and which
plugins are newly unaccounted for, but not a complete re-derivation of every
finding's family (see the explicit gaps called out below and the "needs its
own fresh family pass" note further down):

- **Payload-owned self-wrappers and durable provider manifests are both
  fully converted and no longer appear at all.** The `agent-ssh`
  `emit-profile`/`verify` wrappers (Phase 2, done) and the
  `agent-codespaces`/`agent-containers` `register-bridge-provider` durable
  manifests (Phase 3, done — Phase 3 is fully checked off in the main
  README) both dropped out of the guard-visible set entirely, not merely
  shrunk.
- **`harness-knowledge`'s 2 "operator/bootstrap/nudge" findings moved
  category**, not disappeared: `assemble_plugins.py`'s
  `shutil.which("agent-worktrees")` is now `path-sibling-launch` and
  `bind_knowledge.py`'s `.agent-worktrees` path is now
  `unqualified-runtime-root` (see the 2026-09-24 marketplace-scoped-
  installations journal entry — these were sampled and left as genuine,
  unconverted backlog needing a design pass, not annotated).
- **`customizing-copilot`'s 1 descriptive finding is fixed** (the same
  2026-09-24 journal entry annotated it as a documentation false positive).
- **Two plugins are new since 2026-08-26** and were never part of the
  original 14: `budget-guidance` (added 2026-09-05, 4 generic-installer
  findings, unconverted) and `agent-pull-requests` (added 2026-09-22, 4
  generic-installer findings, unconverted). Neither has been triaged beyond
  confirming they're genuine, not annotated.
- **`agent-machines` gained 2 new findings** (baseline was 6: 3 generic
  installer + 2 operator/bootstrap + 1 descriptive "setup guidance", which is
  the same `SKILL.md:54` finding present today, unchanged) tied to its
  post-rewrite `cell_lifecycle.py` engine (the cross-platform lifecycle
  engine that replaced the plugin's earlier
  `rollback-pin.py`/`runtime-gate.{sh,ps1}` exemplar implementation): a
  `payload-invocation.json` legacy-binstub-path declaration (line 20, mirrors
  `agent-index/payload-invocation.json:22`'s equivalent declaration — both
  look like intentional migration metadata analogous to `agent-bridge`'s
  `legacyRuntimeRoot`, but neither has been confirmed against the install
  contract or annotated) and `cell_lifecycle.py:294`'s
  `item["identity"].startswith(".local/bin/agent-machines")` (runtime code
  that recognizes the legacy binstub identity as part of its own migration
  logic — plausibly intentional, not yet reviewed or annotated). A fresh
  slice should resolve both rather than guessing their disposition here.
- **`agent-bridge`, `agent-index`, and `agent-logger` each gained 1 finding**
  and **`agent-mcp` lost 1** since the baseline;
  these look like incidental drift from unrelated feature work in each
  plugin rather than a pattern, but were not individually traced.

**Current per-plugin `global-plugin-binstub` counts (2026-09-25, 83 total):**

| Plugin | Findings | Delta vs. 2026-08-26 |
|--------|---------:|-----------------------|
| `agent-worktrees` | 18 | unchanged |
| `agent-machines` | 8 | +2 (new, see above) |
| `agent-vault` | 8 | unchanged |
| `agent-codespaces` | 7 | -2 (durable provider manifests converted) |
| `agent-index` | 6 | +1 (untraced) |
| `agent-ssh` | 6 | -6 (baseline was 12: 6 self-wrapper + 4 generic + 2 remote-transport; the 6 self-wrapper findings converted, leaving 6) |
| `agent-dispatch` | 5 | unchanged |
| `agent-containers` | 4 | -2 (durable provider manifests converted) |
| `agent-logger` | 4 | +1 (untraced) |
| `agent-pull-requests` | 4 | new plugin, not in 2026-08-26 baseline |
| `budget-guidance` | 4 | new plugin, not in 2026-08-26 baseline |
| `agent-bridge` | 3 | +1 (untraced) |
| `agent-mcp` | 3 | -1 (untraced) |
| `copilot-extensions-harness` | 3 | unchanged |
| `harness-knowledge` | 0 | -2 (moved to `path-sibling-launch`/`unqualified-runtime-root`) |
| `customizing-copilot` | 0 | -1 (annotated as a documentation false positive) |

This table is the current count per plugin; it is not yet split by contract
family the way the 2026-08-26 table below is — that per-finding family
re-derivation (which of the 83 belongs to which retirement-dependency
bucket) is the fresh pass still needed.

The table below is retained as the historical 2026-08-26 record (do not
edit it to match the current count) — the accurate, current per-plugin
breakdown is the table immediately above, and a family re-derivation for it
is still a fresh pass needed.

| Contract family (2026-08-26 snapshot) | Findings | Phase | Reason |
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

## Detailed accounting (historical: 2026-08-26 snapshot, not current counts)

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

1. Completed in
   [#1187](https://github.com/ThomasMichon/copilot-extensions/pull/1187):
   moved the six payload-owned agent-ssh self-wrappers to their own generated
   payload command and added cross-platform wrapper tests.
1a. Completed: registered `agent-bridge` as a `peer-launch` OWNERS member (it
   was previously only a PEERS target) and converted its
   `handoff-check` -> `agent-worktrees handoffs-check` call to the validated
   same-cell boundary, with the ambient-`PATH` lookup preserved as an
   explicitly marked legacy-compatibility fallback. `agent_registry.py`'s
   separate `_agent_worktrees_bin()` resolution (a different call site in the
   same plugin, listed under "Known guard-invisible callers" below) is a
   distinct, not-yet-converted caller.
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
