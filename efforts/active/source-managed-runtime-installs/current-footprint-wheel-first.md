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
