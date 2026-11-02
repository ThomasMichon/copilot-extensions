# Fresh bootstrap and selected-source recovery

Parent: [Source-Managed Runtime Installs](README.md), Phase W.
Coordination: #7028, #7743, #7758 and #9688.
Intent: [fresh-default upstream installation](../../../visions/installer/README.md#fresh-default-upstream-installation).

## Bounded composition proposal - agent-recommended

A recovery consumer must reach current upstream dispatch code before it depends
on the installed Manager, Worktrees, a runtime interpreter or an incumbent
dispatcher. The dispatch source and the requested installation source are
independent. Fetching the latest dispatcher must not silently update a selected
older target; selecting an older target must not select an older dispatcher.

Extend the canonical `worktree-manager/bootstrap.ps1` entry with an explicit
source-recovery mode and read-only capability discovery. The fresh bootstrap
acquires its current source-selection tools before inspecting the incumbent.
Its target choices are current Main, an explicit full Main-ancestor commit,
or an existing exact Main pin read through native runtime-source configuration.
An explicit local checkout supplies Git objects, not permission to change that
checkout, follow its working-tree files or replace the upstream dispatcher.
Keep the existing independent Manager, service-runtime and plugin-content
policies; recovery does not rewrite them merely to communicate one selection.

The composition freezes one commit/tree and Main reference, prepares the target
through the existing local promotion/wheel-assembly owner, and defers installation
to that selected source's supported native entry. A local Dev source remains
available only with the native explicit Dev selection/consent and ancestry
rules; it must not become an accidental interpretation of an existing Main pin.
Persisted pin changes, if requested at all, remain a separate explicit operation.

## Existing seams and compatibility

- `runtime_source_config.py` owns configured service-source parsing.
- `runtime_source.py` owns exact acquisition, fetched-branch ancestry, source
  isolation and the acquisition lease.
- `tools/local_install_source.py` owns Git-addressed materialization and
  first-party wheel construction without package-version changes.
- `tools/local_install_profile.py` owns complete native dependency inputs;
  consume its returned snapshot rather than the incomplete first-party output.
- The selected `worktree-manager/bootstrap.ps1 --install-source` and native
  application installers own application, publication and launch.
- The existing footprint provider/history, scoped removal owners and durable
  execution driver retain recognition, repair, exclusive custody and outcomes.

The released Manager/Worktrees prepared-input adapters are not evidence that
every application supports this journey. Negotiate the actual selected entry's
capability before changing runtime state. An unsupported historical source or
portfolio is an explicit unsupported result, never permission to install a
different commit, use a raw-source fallback or claim suite recovery from a
Manager-only construction. Existing application-adapter owners retain their
qualification work; this slice must not add another constructor or supervisor.

## Footprint and authority

The current upstream toolkit, not each external consumer, owns historical
footprint/version recognition and conditional repair selection. Unknown or
incompatible reuse evidence selects fresh construction, not a requirement to
make the incumbent healthy. Preserve incoming-start-first replacement and
independent incumbent retirement from the parent fresh-default contract.

Default preview may acquire disposable source data and report the immutable
selection, supported native handoff and any upstream-recognized repair needs.
It must not publish a runtime, stop a service, change registrations or select a
different current generation. Applying a named operation requires explicit
authority for that scope. Separately requested old-code removal or shutdown
uses the native owner and its finite inventory, never process-name killing,
directory-root deletion or a copied cleanup list.

Preserve configuration, repositories, credentials, sessions, protected
connectivity, durable queue history and unresolved effects. A cleanup receipt
does not settle uncertain installer custody. Acquisition, preparation,
installation, selected running health and displaced-owner retirement remain
separate outcomes. This proposal authorizes no installed-host action.

## Plan - agent-recommended

- [ ] Review this composition contract before implementation.
- [ ] Add fresh bootstrap capability discovery and explicit recovery routing
  without changing ordinary bootstrap/update behavior.
- [ ] Resolve the selected source and configured Main pin through existing
  source owners; retain the immutable selection through native handoff.
- [ ] Compose existing promotion/profile/application entries with capability
  checks, exact exit propagation and no installed-dispatch fallback.
- [ ] Compose current upstream footprint inspection and any explicitly
  authorized repair; preserve native execution and resource custody.
- [ ] Qualify the actual Windows entry and publish its supported argument and
  compatibility contract for thin external consumers.

## Validation Plan - agent-recommended

- [ ] Deliberately broken installed dispatcher/interpreter/Manager entries are
  not invoked before fresh dispatch acquisition.
- [ ] Fresh Main, an older merged commit and configured Main pin retain the
  requested commit/tree while dispatch stays current upstream.
- [ ] Local-source use leaves checkout files, HEAD and configuration unchanged.
- [ ] Invalid pin, ancestry, HTTP, offline and unsupported-capability failures
  perform no runtime mutation and never substitute source or dispatch code.
- [ ] Exercise the selected source's real promotion/profile/native installer
  in a disposable Windows environment, not only mocked argument forwarding.
- [ ] Old, absent, incompatible and corrupt footprints reach the upstream
  recognition/fresh path without an external copy of the version matrix.
- [ ] Preview, repeated attempts and partial failures preserve configuration,
  credentials, sessions, queues, foreign resources and exact native outcomes.
- [ ] State the supported application portfolio and unqualified cases
  explicitly; do not infer whole-suite or live-host acceptance.
