# Manager-owned application footprints

[Back to the effort](README.md) · Tracking: #12905 ·
[Verbatim operator snapshot](inception-transcript.md#manager-owned-app-footprints-and-local-promotion)

## Current objective and scope

Move generic install/update management from `agent-worktrees` to
`worktree-manager`, with one fully converted component proving install and update
using only the new flow before broad app refactoring. Extend the existing
promotion and installer machinery; do not build a parallel dependency resolver,
queue or lifecycle engine.

The current contribution records the visions, exact request and receiving plan
before refactoring. Existing promotion, vendoring, execution, component, native
and deployment owners retain their scope, evidence and holds. No component is
assigned, no app code is changed, and no host install/update is authorized here.
Vault is the footprint example, not a selected exemplar.

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

The operator's proposed footprint is:

```text
<repo>/apps/<app>/
  pyproject.toml
  bin/setup.{ps1,sh,cmd}
  src/

<home>/.copilot-extensions/apps/<app>/
  versions/<version>/         # complete promoted app directory
  <version-marker>
  bin/setup.{ps1,sh,cmd}       # version-neutral

<home>/.local/bin/
  <app-command>.{ps1,sh,cmd}   # version-neutral
```

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
5. Select one component with its existing owner and qualify its real install
   and update through this path exclusively. Include its actual dependencies
   and lifecycle; a CLI-only result does not qualify service/IPC behavior.
6. Only after that complete reference flow passes, convert additional apps
   through bounded owner-scoped slices. Existing historical qualification is
   retained, not relabeled as qualification of this new model.

Implementation choices, not extra operator requirements: reuse `uv.lock`,
`.python-version`, a slot-local physical `.venv` and the existing completion
record; prefer a simple shell-readable selection marker; generate thin
setup/application shims rather than hand-maintain copies. Manager invokes the
incoming slot's setup directly, not the neutral setup that still selects V1.
Keep configuration/data/cache outside immutable version code and preserve
existing installation identity, package/feed policy and process custody.

## Validation and completion _(agent-recommended)_

- [ ] Request capture and governing visions clear normal review before app
  refactoring; #12905 remains open for implementation, not closed by this plan.
- [ ] Main and local pinned-Dev/WIP promotion produce complete copyable app
  directories with correct vendored imports and distinguishable local versions.
  Source files remain unchanged; local outputs do not claim public release.
- [ ] With Worktrees absent or unusable, Manager can install/update the exemplar
  from a footprint without calling or importing Worktrees installation code.
- [ ] A source-concealed copied app stages using PATH uv and declared Python;
  no system-Python discovery, escaped source dependency or copied venv is needed.
- [ ] Staging and repeated staging preserve exact active pointers, neutral
  commands, configuration, registrations and service ownership.
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
