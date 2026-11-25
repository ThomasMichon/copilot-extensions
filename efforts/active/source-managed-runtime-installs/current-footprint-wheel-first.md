# Current-footprint wheel-first compatibility track

Parent: [Source-Managed Runtime Installs](README.md), issue #7028.

## Goal

Convert all existing copilot-extensions installation and setup flows to
wheel-first installation on their current footprint. Maintain compatibility
with prior footprints as far as possible so version-over-version installation
does not require users to adopt a different installation layout.

This is independent of the
[new application footprint](app-footprint-transition.md) and its Manager
reference in #12905. It does not take over pinned-version installation,
new-footprint rollback, or runtime-state recovery/export/import/handoff design.
The new-footprint reference is not a prerequisite for this compatibility track.
Actual overlap in shared code still requires coordination with its owner.

## Existing boundaries to preserve

- Keep supported installer, bootstrap, update and self-provisioning entry points.
  Preserve their argument, exit-status and noninteractive calling contracts.
- Retain current installed paths, selectors and command redirects where
  possible. Do not silently move a caller to the new application hierarchy.
- Preserve configuration, databases, sessions and other persistent runtime
  state. Keep service lifecycle and command publication with their current
  owners rather than treating an installed environment as an activated service.
- Reuse admitted wheel/profile construction and existing artifact producers.
  A preparation helper or an explicitly supplied wheel option is not proof
  that an ordinary install/update caller is wheel-first.
- Preserve the documented distinction between unsupported or unavailable
  artifacts and a failed admitted-wheel construction. Do not conceal a failed
  wheel installation with an unreported source build.
- Keep Copilot plugin-content delivery independent. Source conversion and
  fixture validation do not authorize deployment to an existing installation.
- Keep automatic-update inhibition automatic-only. Installation completion
  requires incoming readiness and all current selections, independently of
  outgoing retirement. Delayed cleanup retains the install contract's
  mutation-time current-generation and central-role protections.

The [install contract](../../../docs/install-contract.md) and
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
govern the implementation. Compatibility must preserve useful existing
consumer contracts, not turn stale incumbent metadata into a permanent veto
on an admitted incoming installation.

## Inventory and evidence

Complete this inventory before treating any runtime family as converted.
Derive the roster from current source declarations; do not assume a historical
runtime count covers every present-day setup or self-provisioning path.

| Surface | Known source seam | Remaining evidence |
|---|---|---|
| Standalone Manager bootstrap and update | `worktree-manager/bootstrap.ps1`, `bootstrap.sh`, `scripts/runtime_install.py` | Trace ordinary acquisition and installation, not only prepared-footprint inputs. Prove supported prior-footprint transitions. |
| Plugin-native installation and self-provisioning | Plugin-owned `scripts/install.*`, `init.*`, setup commands and attributable launchers | Enumerate all owners and exceptions. Distinguish an optional closed-wheel branch from the default consumer path on both platforms. |
| Shared construction and artifact admission | Existing source/wheel profile adapters and runtime construction owners | Establish which existing callers supply the complete admitted closure and which still build first-party source at installation time. |
| Release and local-source preparation | Existing wheel build, source/profile and materialization tools | Preserve source/version association and complete dependency closure without changing the installed layout. |

Each final inventory row records its owner, source entry point, current
footprint, artifact selection, compatibility requirements, focused tests and
actual acceptance result. Unknown rows remain open; a representative pass
does not qualify the portfolio.

### Current source roster

The November 25 source inventory derives the roster from
`.github/plugin/marketplace.json`, each package's `pyproject.toml`, installer
entry points and `payload-invocation.json`. There are **14 Python runtime
plugins, but 13 legacy installer owners**. `agent-remote-driver` has an
application declaration and no legacy `install.*` or `init.*` entry point.
It is not a missing fourteenth legacy installer.

The table names the canonical script pair under each plugin's `scripts/`
directory. `init.*` forwards are not another installation owner when the
plugin also has `install.*`. A root below means the existing installation's
root, including a supported override or installation-cell binding, not a
requirement to move state to the legacy default path.

| Runtime owner | Canonical native entry | Existing default root | Construction and compatibility coverage still required | Focused existing tests under that plugin |
|---|---|---|---|---|
| `agent-worktrees` | `install.ps1`, `install.sh` | `~/.agent-worktrees` | Ordinary install/update and first use; explicit Source inputs; editable Dev remains distinct. Preserve repository commands and monitor selection. Coordinate update forwarding with #10570. | `test_installer_completion_publication.py`, `test_installer_slot_clean_posix.py`, `test_installer_binstub.py` |
| `agent-pull-requests` | `install.ps1`, `install.sh` | `~/.agent-pull-requests` | Legacy and cell paths, ordinary construction and fixed first use; retain watch/session state and command ownership. | `test_cell_installer_slice.py`, `test_cell_payload_upgrade.py`, `test_bootstrap_driver.py` |
| `agent-bridge` | `install.ps1`, `install.sh` | `~/.agent-bridge` | Install/update/provision and fixed first use; preserve configuration, authentication and database. Closed-wheel `NoService` is a separate caller-owned lifecycle mode, not default service acceptance. | `test_install_ps1_no_service.py`, `test_install_completed_slot_admission.py`, `test_installer_required_cutover.py` |
| `agent-codespaces` | `install.ps1`, `install.sh` | `~/.agent-codespaces` | Ordinary install/update, fixed first use and Dev selection. Preserve caller-owned lifecycle and helper publication; coordinate #12967. | `test_installer_engine_adoption.py`, `test_self_provisioning_binstub.py`, `test_install_ps1_recovery.py` |
| `agent-containers` | `init.ps1`, `init.sh` | `~/.agent-containers` | Initialization, stamped/provisioned runtime and generated commands; retain container-provider state and do not confuse container tool preparation with host runtime installation. | `test_installer_bootstrap_python.py`, `test_runtime_profile.py`, `test_payload_invocation.py` |
| `agent-mcp` | `init.ps1`, `init.sh` | `~/.agent-mcp` | Initialization and fixed first use; preserve nested environment shape, routes and configuration. | `test_bootstrap_driver.py`, `test_installer_python_selection.py`, `test_installed_application_namespace.py` |
| `agent-ssh` | `install.ps1`, `install.sh` | `~/.agent-ssh` | Ordinary installation and stamp-to-selected-snapshot first use; preserve SSH configuration, profiles and source-directory identity. | `test_install_snapshot.py`, `test_source_install_directory.py`, `test_payload_invocation.py` |
| `agent-logger` | `install.ps1`, `install.sh` | `~/.agent-logger` | Install/update and fixed first use; preserve source redirects, log configuration and scheduled synchronization ownership. | `test_source_runtime_install.py`, `test_source_runtime_reselection.py`, `test_windows_scheduling.py` |
| `agent-dispatch` | `install.ps1`, `install.sh` | `~/.agent-dispatch` | Ordinary construction and declared first-use lifecycle; retain queue/database state and required activation. | `test_installer_engine_adoption.py`, `test_source_runtime_install.py`, `test_installer_required_cutover.py` |
| `agent-vault` | `install.ps1`, `install.sh` | `~/.agent-vault` | Ordinary construction and fixed first use; retain opt-in, credentials, vault data, provider selection and service ownership. | `test_install_service_failures.py`, `test_bootstrap_driver.py`, `test_installer_managed_readers.py` |
| `agent-index` | `install.ps1`, `install.sh` | `~/.agent-index` | Client, server/store extras, independent engine environment and cells are separate profiles. Do not qualify all of them from a client-wheel pass. | `test_installer_build_admission.py`, `test_server_venv_provisioning.py`, `test_installer_publication_and_health.py` |
| `agent-machines` | `init.ps1`, `init.sh` | `~/.agent-machines` | Initialization and command provisioning; preserve machine configuration and installed requirement-package state. Executing a requirement package is not another installer for this runtime. | `test_init_powershell.py`, `test_installer_readiness.py`, `test_installation_configuration.py` |
| `budget-guidance` | `install.ps1`, `install.sh` | `~/.budget-guidance` | Ordinary install and fixed first use; preserve configuration/cache and command contracts. | `test_installers.py`, `test_installer_build_attempts.py`, `test_bootstrap_check_reconcile_always_on.py` |

**Acceptance for every plugin row remains open.** The current native
PowerShell entries accept explicit closed-wheel inputs. MCP's POSIX installer
also accepts the complete native wheel argument quartet and routes it through
the existing nested-environment constructor. Neither capability makes an
ordinary invocation wheel-first. The remaining POSIX interfaces and all
ordinary artifact-selection paths still need conversion and validation. Existing
`uv pip install` source branches and install-time first-party wheel caching
remain source installation, not published-wheel acceptance.

### Shared and standalone callers

- **Manager Main self-update:** `runtime_source_self_update.py` selects
  `runtime_source_release.py` before the existing source path. #14466 connects
  independently selected published inputs to the existing Manager
  construction, selection and activation owners. It preserves the current
  root and does not export the separate application footprint. Missing
  qualified repository-scoped release access, a pre-channel source or an
  unavailable artifact retains reported source fallback; verification or
  admitted construction failure does not. Windows fixture evidence does not
  qualify every prior generation or POSIX.
- **Manager native bootstrap:** `bootstrap.ps1` and `bootstrap.sh` acquire
  source and invoke `scripts/runtime_install.py`. Its
  `source_acquisition.manager_payload` is the input handoff. Main reuses the
  released-wheel owner with the bootstrap's selected UV. A retained private
  tooling environment supplies Packaging through the existing dependency
  policy; bootstrap Python stays unchanged. Git-optional
  bootstrap, explicit prepared inputs, repair source selection and custom refs
  retain their existing paths. A fresh machine without a qualified
  repository-scoped release reader still uses the source compatibility path;
  that case is not a wheel-install pass.
  A Windows Python-entry fixture consumed published wheels, activated the
  existing flat root, dispatched the version command and preserved its
  configuration and state sentinel. Test-daemon cleanup settled. It took
  838.25 seconds, so the 300-second timing gate remains unqualified. This
  does not qualify the outer download shell, POSIX or an older-generation
  transition.
- **Manager Dev and prepared Source:** `runtime_source_self_update.prepared`,
  `runtime_source_prepare.py`, `runtime_install_selection.py` and
  `install_source.py` retain complete Source/profile association and held
  input custody. Prepared-application setup is not evidence that an ordinary
  legacy native caller consumes those inputs.
- **Unified repository update:** Worktrees' `installer.py`, `update_stage.py`
  and `reconcile.py`, together with Manager update dispatch, remain real
  consumers to qualify. They can select another installer, profile or
  platform branch; a direct installer pass does not qualify these callers.
- **First use, hooks and commands:** `payload-invocation.json` selects
  generated launchers, runtime gates and native installer lifecycle.
  `libs/update-driver/src/update_driver/first_use.py` declares fixed recipes
  for Vault, Pull Requests, SSH, Codespaces, Logger, Bridge, MCP and Budget.
  Its provision/stamp/install operations call existing owners; they are not
  permission to substitute a second environment builder. Other declared
  launcher and hook paths still need their own consumer results.
- **Shared construction:** `libs/installer-engine/installer_engine.py`,
  `installer_source_build.py`, `install_source.py` and
  `libs/versioned-runtime/versioned_runtime_construction.py` distinguish
  retained source builds from admitted wheel construction. Installer-engine,
  versioned-runtime and payload-invocation copies are generated/vendored
  providers, not separately enumerated runtimes.
  The attached Bash adapter preserves its legacy positional source contract
  and accepts a complete wheel selection from the same payload. MCP is its
  first POSIX wheel consumer. Native transport and real wheel-byte selection
  controls are separate: a substituted constructor is not a full install pass.
- **Artifact preparation:** `tools/build_python_artifacts.py`,
  `local_install_source.py`, `local_install_profile.py`,
  `artifact_release_discovery.py` and `bootstrap_manager_release.py` prepare
  or discover inputs. Their success does not prove installation.
  `application_footprint.py`, application setup and the Remote Driver
  application belong to the separate footprint track; this compatibility
  inventory does not declare them legacy conversions.

The cross-platform transition matrix remains pending for each ordinary
consumer: clean construction, repeat/reuse, a supported earlier footprint,
missing/unsupported inputs, refused admitted inputs, failure/retry, command
selection and applicable incoming service readiness. Preserve durable state
and report deferred outgoing cleanup separately, as required by
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement).

## Plan

### 1. Establish complete current-flow coverage

- [ ] Enumerate installer, bootstrap, ordinary update, setup and first-use
  provisioning callers, including platform-specific and source/dev exceptions.
- [ ] Record existing wheel-first behavior and remaining source-build paths
  separately from the new-footprint implementation.
- [ ] Reconcile active claims for the specific shared helpers and entry points
  selected for each change. Retain the existing public owners and evidence.

### 2. Convert through the existing owners

- [ ] Select the first coherent caller/helper slice from the inventory.
- [ ] Route its ordinary installation through the matching admitted wheels
  using the existing construction and activation owners.
- [ ] Preserve supported previous-footprint recognition, current paths and
  state ownership. Document unavoidable compatibility limits explicitly.
- [ ] Apply the same contract to the remaining caller families without adding
  a second installer, a replacement layout or new state-transfer machinery.

### 3. Qualify actual transitions

- [ ] Exercise a clean installation through each changed public entry point.
- [ ] Exercise repeated installation and transitions from supported prior
  footprints, including mixed caller/provider versions where applicable.
- [ ] Exercise missing/incompatible artifacts, failed construction and retry.
  Report the actual selected input and preserve the documented usable
  installation/state behavior.
- [ ] Verify ordinary commands and relevant service readiness after selection;
  preparation-only evidence is not end-to-end installation acceptance.

### 4. Publish bounded, working increments

- [ ] Run changed-code unit tests, applicable lint and contract checks.
- [ ] Run focused real-entry-point and clean-environment Windows/POSIX
  validation where available; record exact limitations for unavailable lanes.
- [ ] Land independently useful changes through normal review and required CI.
  Track release promotion separately from installation on an existing host.
- [ ] Resolve every inventory row and report remaining separately owned
  new-footprint or deployment work without marking it as completed here.

## Validation contract

Wheel-first evidence must show that matching wheels, rather than first-party
source builds, produced the installed environment. Transition evidence must
show that the actual installer recognized the prior footprint and preserved
the required commands, configuration and persistent state. Neither a wheel
build, a mock of the final install call nor an isolated prepared-project pass
is a substitute for that consumer evidence.

Use the repository's existing test runners and fixture containment. Never
update the operator's installed runtime, restart a shared service or retire a
foreign process to obtain a test pass. Keep failed observations distinct from
later passing retries and keep artifact-preparation timing separate from
installation, activation and warm reuse.

## Completion

This track is complete only when all current-flow inventory rows have a
verified result, required source changes are merged, and compatibility limits
and remaining release/deployment ownership are explicit. Source completion
does not assert adoption of the separate new footprint or a live fleet update.
