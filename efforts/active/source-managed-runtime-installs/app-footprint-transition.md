# Manager-owned application footprints

[Back to the effort](README.md) · Tracking: #12905 ·
[Verbatim operator snapshot](inception-transcript.md#manager-owned-app-footprints-and-local-promotion)

## Current objective and scope

Move generic install/update management from `agent-worktrees` to
`worktree-manager`, with one fully converted component proving install and update
using only the new flow before broad app refactoring. Extend the existing
promotion and installer machinery; do not build a parallel dependency resolver,
queue or lifecycle engine.

For an independently admitted standalone Manager release, the footprint
producer checks the release manifest against the pristine source payload
before adding deployment metadata. It passes the verified root wheel and
its complete native dependency set to the existing profile and footprint
writers. The resulting slot can be copied and checked by the regular
Manager construction reader without creating an environment or selecting a
version. A missing dependency must fail before a footprint is published.
This preparation step does not replace bootstrap, setup or recovery.

The initial capture cleared review and merged in #12908. The operator
subsequently selected Worktree Manager itself as the reference, through
Manager-owned orchestration, complete Main/pinned-Dev/frozen-WIP footprints,
real install/update qualification and reviewed, CI-passing, merged delivery.
Every owned implementation PR remains owned through actual merge and source
worktree finalization; opening a PR is not completion.

Existing promotion, vendoring, execution, component, native and deployment
owners retain their scope, evidence, reservations and holds. Reconcile concrete
overlaps before editing their source. Host/fleet rollout, credentials/access
changes, unrelated app conversion and bulk repository relocation are excluded.
Vault remains a footprint example; audit Manager's actual dependencies rather
than inferring a Vault/SSH dependency from the example.

## Required model

### Promotion produces the installable unit

- Main carries wholly-copyable `apps/<app>` directories. Its existing
  Dev-to-Main vendoring/materialization flow copies embedded first-party library
  code into every consuming app's `src`; installation does not chase Dev pointers.
- A pinned Dev commit or WIP on Dev runs that same promotion flow locally,
  producing an install-footprint folder before orchestration. Freeze the exact
  WIP inputs first; installed code never follows a mutable worktree.
- Local promotion handles all vendoring and assigns local output versions.
  Published releases retain their existing authority; local output does not
  claim release attestation or require edits to tracked source version files.
  The existing version owner must distinguish different immutable inputs,
  including different WIP snapshots, without a second version service.
- Manager consumes the prepared footprint, copies selected app directories
  wholesale into their version slots and uses their own setup frontdoors.
  Prebuilt wheels use one declared convention known to setup, not an arbitrary
  file whose presence silently overrides the project or dependency lock.

### Manager orchestrates; app setup realizes a version

Manager owns generic installation selection, dependency ordering, copy/stage,
activation and update. Worktrees is a consumer, not an installation prerequisite
or an updater that Manager must load or repair first. The finite bootstrap can
establish Manager through the same component setup contract without a resident
Manager, Copilot session or working legacy installer.

The bootstrap runs the whole update by invoking the finite orchestration tool
delivered with Manager. Only tools from that Manager installation reconcile
the control/knowledge repository pair's plugin configuration, derive the complete
required application/service closure and build the update roster. A successful
Manager repair followed by delegation back to a Worktrees/project-engine
controller is not this flow. Worktrees is one consumer in the roster.
The orchestration tool composes existing discovery, construction, setup and
lifecycle owners; the bootstrap script does not implement their algorithms.
Manager is the carrier for those vendored tools, keeping bootstrap small rather
than introducing a running-Manager-daemon requirement.
Before obtaining that tooling, the bootstrap resolves the target source/ref
from registered projects and their configuration or the machine-side override,
using the canonical selection rules. Main is not an implicit replacement for
an explicitly resolved target.
The resolution determines which Manager version to install first. Only after
installing that selected Manager does bootstrap invoke its delivered finite
orchestration tool for the complete update.

The operator's proposed footprint is:

```text
<repo>/apps/<app>/
  pyproject.toml
  bin/setup.{ps1,sh,cmd}
  src/

<home>/.copilot-extensions/apps/<app>/
  versions/<version>/         # complete promoted app directory
    state/                   # only disposable, exclusively version-owned data
  state/                     # durable application state
  config/                    # configuration that survives version replacement
  <version-marker>
  bin/setup.{ps1,sh,cmd}       # version-neutral

<home>/.local/bin/
  <app-command>.{ps1,sh,cmd}   # version-neutral
```

The new flow specifically uses this application root rather than installing
new versions into `~/.agent-<app>`. Legacy and new footprints can coexist until
explicit full setup updates the global commands. Copying or staging the new
footprint must leave the old command destination unchanged; neither coexistence
nor activation implies deletion of the old tree or competing writers to its
durable state.

Version code, dependencies and setup inputs remain immutable after construction.
Configuration, secrets, sessions, databases and other state needed across updates
live outside version slots. Exclusively version-owned disposable disk caches,
temporary databases and similar execution state may use
`versions/<version>/state/`, not arbitrary files mixed into code. Losing that
directory must not lose user data or prevent update/recovery. It is not shared
between versions or a migration source required by a successor. Existing live
process/retention custody still prevents premature slot deletion.

The long-term repository layout is separate from application installation:

```text
<home>/.copilot-extensions/repos/<repo>/
  worktrees/
  config.yaml
```

This reuses the marketplace-cell hierarchy's organizational shape, not its
plugin identity or mutable-state selection. Capture the target now; moving
existing repositories/configuration is not required by the Manager reference
and needs its own owner-scoped migration.

Slot-local `setup --stage` internally uses PATH `uv` with the project's
declared Python. It prepares the candidate without updating the version marker
or neutral app/global redirects. It also leaves live services, registrations
and mutable active configuration unchanged.

Full slot-local setup performs the app's required setup/readiness and commits
the current version plus matching version-neutral app/global redirects.
Preserve [successful V2 startup as the roll-forward commit point](inception-transcript.md#successful-v2-startup-owns-roll-forward):
a pointer write alone is not service readiness, and an outgoing V1 cannot
reverse a committed incoming generation. Preserve the existing
[authoritative replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
and [foreground/service lifetime](update-service-lifetimes.md) contracts.

### Embedded libraries and shared IPC components are different edges

Promotion embeds code intended to execute in a consumer process. A separately
installed shared runtime component, such as SSH Manager, is instead its own
app/library installation with its own version and setup. Its implementation
is not vendored into or imported by consumers; consumers invoke the provider's
version-neutral binstubs and communicate only through IPC.

Manager resolves the declared install dependency graph and installs/stages each
required provider before a consumer's full setup. If that setup invokes the
provider, a compatible provider must also be selected and ready: staging alone
does not publish a neutral command or start a service. Do not work around this
by importing its code or reaching directly into its private version/venv.
Existing compatible providers can satisfy readiness without unnecessary restart.

## Pinned checkout staging

The checkout is the source of truth for first-party code and payloads. Matching
published wheels are build outputs of that source, not a second version
selector. An adopter-specific distribution archive is not an input. Record the
requested ref and resolved commit independently: repeating `--stage` uses the
pin, even if a branch or tag moved. Advancing is a distinct operation; stage
the new version alongside the old one.

> The Bootstrapper should always accept an override build via env var, just as it can accept an alternate repo or random source folder as an override via env var, too.

Resolve invocation overrides before installing Manager, including on an
already-pinned install. A local folder need not be a registered checkout;
freeze the caller-selected input before using it. A selected build must match
that source. Exercise the effective Manager version and wheel selection for a
non-default build with both repository and directory inputs. Missing or
incompatible overrides fail without selecting a default build, editing the
adopter's config, or activating the staged installation. Preserve the existing
transport-only meaning of `WORKTREE_MANAGER_BOOTSTRAP_REPO` and
`WORKTREE_MANAGER_BOOTSTRAP_REF`.

> If the bootstrapper is run against a commit which happens to be already-installed but no longer current, ideally it can validate those builds are still there, then re-stage and re-setup the existing builds, taking back over from whatever was active.

With B current, explicitly select previously installed A. Check each retained
slot against A's requested build/profile and existing setup readiness checks.
Reuse complete slots and UV environments; do not download or rebuild healthy
ones solely because B is current. A missing or damaged slot goes through normal
construction/repair from A's inputs before it can count as staged. `--stage A`
must leave B current. Full setup of the complete compatible A roster runs its
normal setup/cutover, verifies A's readiness and current commands, and retains
B's slot. Selecting older code does not restore an older copy of user data.

### Acceptance sequence _(agent-recommended)_

1. Select the ref before selecting Manager. Cover Main, a branch, a commit and
   an annotated tag, including a moved ref, unknown ref and machine override.
   Missing/conflicting inputs or unavailable matching wheels must not fall
   back to a floating revision or another installation.
2. From a cold isolated root with no Worktrees install, populate Manager's
   version slot, complete its UV environment from the admitted wheel closure
   and invoke its finite roster tool. Verify the selected commit, distribution
   versions, wheel identities and physical interpreter prefix.
3. Use the same entry to stage the required roster from a repository pair,
   including dependency edges and plugins without apps. Keep staged Dispatch
   tasks and Worktrees status apart from live stores; do not copy, initialize
   or migrate live data. Include failure partway through and a repeat.
4. Rehearse two actual Main promotions with the new bootstrap. Retain both
   slots, show that the first pin stays put until advanced and verify that
   staging publishes no live pointers/launchers or services. Record each
   SHA, artifact selection and outcome; a missing publication is not a pass.
5. Continue through the extracted-app inventory. Qualify the marketplace
   pointer switch only after the remaining checks pass. Runtime-state
   migration is separate from both staging and that switch.

## Receiving sequence _(agent-recommended)_

1. Review and merge this direction before app refactoring. Reconcile current
   promotion/installer/application owners and live overlapping PRs.
2. Move the finite generic installation orchestration under Manager, reusing
   canonical copy, execution and activation mechanics. Remove Worktrees as its
   bootstrap/management dependency; do not merely wrap the old installer.
3. Define the complete promoted app contract: authoritative project/Python/lock
   inputs, neutral command identity, setup and wheel convention, and separately
   declared IPC dependencies. Reuse existing version/source identities.
4. Produce equivalent footprints from Main, pinned Dev and explicit frozen WIP
   with the same promotion implementation. Keep local version assignment in
   generated outputs, not the mutable source tree or a public release.
5. Qualify Worktree Manager's real install and update through this path
   exclusively, reconciling its existing source owners. Include its actual dependencies
   and lifecycle; a CLI-only result does not qualify service/IPC behavior.
6. Only after that complete reference flow passes, convert additional apps
   through bounded owner-scoped slices. Existing historical qualification is
   retained, not relabeled as qualification of this new model.
7. Retire Main's root `worktree-manager/` dev-source copy in stages (#14501).
   `apps/worktree-manager` is the new-flow footprint, not a drop-in payload for
   the legacy entry: legacy `bootstrap.ps1`/`.sh` runs
   `scripts/runtime_install.py` from root `worktree-manager/` and needs its
   `libs/` siblings, `packaged-assets.json`, `bootstrap_inputs.py`/
   `bootstrap_update.py` and `bootstrap-uv.ps1`, which the app footprint does
   not carry. Path swaps are therefore not a conversion.
   - **Stage 0, inventory.** Record every reader of `main:worktree-manager/`:
     the bootstrap scripts, `tools/recover_source.py`, the Update Driver source
     selection, and consuming-harness bootstrap, update and clean-room
     readers. Adopters keep their own reader mapping. Ambiguous readers stay
     listed until an owner confirms them.
   - **Stage 1, opt-in.** Give each reader an explicit app-footprint selection
     that installs from `apps/worktree-manager` through Manager-owned setup,
     qualified by steps 4-5. The legacy root stays the default.
   - **Stage 2, default.** Flip the default only after the acceptance sequence
     and two rehearsed Main promotions pass. The legacy path stays readable.
   - **Stage 3, retirement.** Stop promoting root `worktree-manager/` only
     after the fleet's shipped updaters, recovery and harness bootstraps no
     longer read it. Older updaters that fetch Main must keep a usable root
     until then; removal is its own reviewed release-scope change.

Implementation choices, not extra operator requirements: reuse `uv.lock`,
`.python-version`, a slot-local physical `.venv` and the existing completion
record; prefer a simple shell-readable selection marker; generate thin
setup/application shims rather than hand-maintain copies. Manager invokes the
incoming slot's setup directly, not the neutral setup that still selects V1.
Keep durable configuration/data/cache outside immutable version code; confine
exclusively version-owned disposable state to the slot's `state/`. Preserve
existing installation identity, package/feed policy and process custody.

## Validation and completion _(agent-recommended)_

- [x] Initial request capture and governing visions cleared normal review in
  #12908; #12905 remains open for implementation, not closed by that capture.
- [x] Subsequent Manager reference/layout/state amendments cleared normal review
  before runtime realization in #12923.
- [ ] Main and local pinned-Dev/WIP promotion produce complete copyable app
  directories with correct vendored imports and distinguishable local versions.
  Source files remain unchanged; local outputs do not claim public release.
- [ ] With Worktrees absent or unusable, Manager can install/update the exemplar
  from a footprint without calling or importing Worktrees installation code.
- [ ] A source-concealed copied app stages using PATH uv and declared Python;
  no system-Python discovery, escaped source dependency or copied venv is needed.
- [ ] Staging and repeated staging preserve exact active pointers, neutral
  commands, configuration, registrations and service ownership.
- [ ] Legacy and new-root fixtures coexist before activation; staging preserves
  exact legacy global-command routing and full setup explicitly selects the
  new installation without deleting the old footprint.
- [ ] Runtime writes remain outside version code/setup inputs. Durable state
  and configuration survive replacement; slot-local disposable state is
  exclusively version-owned beneath `state/`, unshared and unnecessary to a
  successor or supported recovery. Retiring a slot respects live ownership.
- [ ] Required IPC providers stage before consumer full setup and are selected/
  ready before neutral invocation. Missing, incompatible or cyclic dependencies
  are explicit outcomes. Consumer code never imports provider implementation
  or uses its private slot as an alternate invocation path.
- [ ] Full V1 install, V2 staging, V2 activation, repeat, failure/recovery and
  supported reverse selection preserve state, exact command routing and actual
  service readiness where applicable. No legacy-installer fallback is exercised.
- [ ] Setup and every declared command preserve arguments/exit codes across
  supported shells. Native platform, OS-policy, caller-loss, service-retention
  and launch-performance controls remain with their existing owners/lanes.
- [ ] Source, release and authorized installed evidence remain separate.
  Completing one exemplar does not mark the portfolio or parent effort Done,
  authorize a fleet rollout or release another owner's obligation.
