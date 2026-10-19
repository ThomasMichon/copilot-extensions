# Venue Plugin Staging and Registration Parity

- **Slug:** `venue-plugin-staging-parity`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed worktrees and PRs targeting `dev`
- **Created:** 2026-10-16
- **Status:** Active
- **Vision:** [venue-parity](../../../visions/venue-parity/README.md), especially
  `venue-agnostic-launch` and `plugin-dir-parity-for-static-targets`
- **Umbrella issue:** #5286
- **Related issues:** #5072; parent program #954

## Guiding Intent

An agent executing inside a target repository receives the eligible plugins
selected for that repository, not merely a successful transport connection.
Reflect local payloads into the execution environment and register the selected
sources in the target checkout. Native repository-scoped Git activation may
complete during startup, but the execution owner delivers no task until the
effective plugin selection is ready. Headed CLI and headless ACP sessions
should discover equivalent skills.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Owns plan, integration, PRs, and completion evidence | Managed source worktree |
| Venue evidence readers | Read-only traces of the existing provider contracts | Bounded delegated tasks |
| Implementation workers | Own disjoint changes assigned after proposal review | Managed source worktrees |

## Coordination

- **Topology:** serial reviewed slices; generic shared preparation before
  provider-specific adapters.
- **Host (owns PRs):** Coordinator.
- **Delegates:** evidence only until the proposal clears review; no overlapping
  edits or delegated Git lifecycle operations in a shared checkout.
- **Handoff:** preserve this README, the active slice, its code location, and
  outstanding validation/rollout obligations.
- This is the plugin-delivery sub-effort of the
  [venue-parity program](../venue-parity/README.md), not a replacement for its
  authentication, session-host, or lifecycle workstreams.

## Context

#5295 established static-target plugin parity in the vision. #5306 implemented
local-loopback related-plugin resolution. Their merge does not prove remote
payload reflection, target-local registration, or headed/headless discovery.

Codespace code already resolves and copies local payloads, and passes plugin
directories to some launch paths; existing remote-marketplace registration
instead writes user settings. Bridge owns remote Session Hosts for trusted
venues. Reconcile those actual paths rather than introducing a second launcher
or assuming legacy provider commands are the current execution owner.

#5072 tracks dependency-closed subset marketplaces. Reuse or extend its existing
mechanisms where present; do not make independent theme packages depend on
coincidental staging-directory layout. Closure preserves required selected
payload assets; manifest companion relationships do not independently authorize
another plugin's propagation or require same-agent execution.

## Request

Public-safe capture of the operator's direction (repository-specific names
abstracted; the requested mechanics and priorities are unchanged):

> Keep driving the effort. When bridging to a repository, its plugins must reach
> the agent for that repository, whether the session is headed or headless,
> local or on a Codespace. The primary goal is that an agent executing in a
> Codespace or container has those plugins registered. Reflect the plugins into
> the container, update the target repository's
> `.github/copilot/settings.local.json` to point at the plugins' local paths,
> and then launch the agent. Agent-bridge uses `--plugin-dir` for ACP; determine
> whether CLI also needs it.

### Operator clarification

> ACP is supposed to pass `--plugin-dir` for each plugin; CLI will use
> `.github/copilot/settings.local.json` merged on top of
> `.github/copilot/settings.json`, but we can also use `--plugin-dir`. For both
> cases, we should still assemble `.github/copilot/settings.local.json` after
> installing all remote-marketplace plugins and copying over all locally-sourced
> in-worktree plugins.

### Source-preservation clarification

> A controller's local-path reference remains a local-path reference for the
> bridged agent: a reflected copy on a remote machine or container, or the same
> original location in the same execution world. A Git reference remains a Git
> reference. The exception is an absolute local reference to a plugin defined
> in the target repository: rebind it as a target-relative local-marketplace
> reference.

Source declarations and launch directories are distinct outputs. Installing a
Git-sourced plugin does not authorize replacing its declared Git source with an
installation-cache directory. ACP still receives the installed payload's local
directory. A path into a Git checkout remains path-sourced when the controller
chose that path. Worked examples must show both outputs, including the
target-owned relocation exception and the authority of target-local overrides.

Repository scoping is the default. Preparing a selected plugin must not enable
it in unrelated repositories or mutate user-global marketplace and enablement
preferences as a readiness workaround. Native repository-declared auto-install
is distinct from global plugin-management commands; shared installation
bookkeeping alone does not grant global activation.

### Startup-readiness decision

The operator selected **startup-readiness**: native repository-scoped Git
acquisition and activation may complete during productive process startup.
Bridge must confirm the selected plugins are ready before delivering a task.
This replaces a strict requirement that native installation bookkeeping exist
before the productive Copilot process starts; it does not relax source
preservation, repository scope, target-local registration before startup, or
failure reporting. Modes requiring explicit directories must still have usable
payload paths when those directories are supplied.

## Plan

### Phase 0 - Reconcile and review

- [x] Verify #5295 and #5306 merged; retain their already-completed scope.
- [x] Claim #5286 and inspect current provider/bridge contracts and overlapping work.
- [x] Land this proposal and the registration-before-launch vision refinement
  before implementing the new behavior.

### Phase 1 - Trusted Codespace and container registration

- [x] Trace the actual headed CLI and headless ACP launch owners, including
  bridge-owned remote Session Hosts and provider process boundaries.
- [x] Implement shared payload reflection/registration preparation for trusted
  Codespace/container launches: copied payloads and a resolvable local
  marketplace declaration plus explicit enablement in
  the target repository's `.github/copilot/settings.local.json`.
- [x] Deferred to `#5286 / Phase 2 - Static local, remote SSH and privileged relay`:
  local launch-adapter preparation and local filesystem references remain part
  of this effort, not completion inferred from the trusted remote providers.
- [x] Complete installation of every selected remote-marketplace plugin and
  copying of every selected locally sourced in-worktree payload before
  assembling target-local settings, in both CLI and ACP modes. Verify installed
  payload locations instead of treating an expected cache path as installation.
- [x] Preserve unrelated configuration and repository-native plugins; reconcile
  only owned entries. Make repeat preparation idempotent.
- [x] Pass `--plugin-dir` for every effective prepared plugin to ACP. CLI uses
  target `settings.local.json` merged over `settings.json`; explicit plugin
  directories may supplement that supported path. Settings registration is
  mandatory in both modes, not replaced by the ACP flags.
- [x] Prepare before starting either child; surface copy/registration failure
  rather than treating an agent missing requested plugins as ready.
- [x] Keep restricted/untrusted containers outside automatic projection.
- [x] Land the smallest coherent change with directly related docs, tests, and
  changefiles.

### Phase 2 - Static local, remote SSH and privileged relay

- [ ] Preserve declared source kind through shared preparation in every
  integrated venue: retain Git origin/revision/location declarations, retain
  local references or reflected local copies, and rebind target-owned absolute
  local references relative to the actual execution checkout.
- [ ] Gate task delivery on the complete effective plugin set, allowing native
  repository-scoped Git startup acquisition without treating process creation,
  a connection or a session acknowledgment as plugin readiness.
- [x] Publish worked examples mapping controller selection and execution
  location to target-local settings and the complete automatic ACP directory
  set; cover source preservation, target ownership, mixed origins, installed
  fallback and explicit target-local choices.
- [ ] Apply target-local registration before static-registry local-loopback ACP
  launches, retaining existing own/related selection, native
  operator precedence and account/platform locality checks. Resolve local
  filesystem references without assuming a remote provider owns this path.
- [ ] Apply the same registration barrier through the existing local
  acquisition owner in CLI and ACP modes; settings-only consumer discovery is
  not proof that this adapter prepares its target.
- [ ] Apply the same contract to genuine remote static-registry SSH targets:
  target-owned resolution plus control-repository payload staging.
- [ ] Resolve the privileged launch's real identity/filesystem context and
  prepare through its supported channel; do not assume it shares the
  unprivileged account's home.
- [ ] Preserve loopback account/platform locality checks and existing binding
  behavior. Land tested slices independently.

### Phase 3 - Release and acceptance

- [ ] Verify publication from `dev` to `main`; perform only authorized rollout
  through the unified update path and record the effective running version.
- [ ] Complete the validation matrix below, or explicitly transfer any
  unavailable lane to a named tracked objective.
- [ ] Reconcile #5286, #5072, and #954 without closing broader unrelated scope.
- [ ] Mark Done only after every Plan/Validation item is resolved or transferred.

## Validation Plan

- [x] Unit contracts cover first preparation, repeat preparation, settings
  preservation, identity mapping, required payload absence, malformed settings,
  and restricted-venue refusal.
- [x] Concurrent preparations against one checkout retain both intended
  registration sets and unrelated settings; an interrupted write leaves the
  previous valid file usable.
- [x] Existing user-level registration and target-local registration of the
  same identity resolve to the selected target payload without duplicate skill
  loading. Explicit target-local operator overrides remain authoritative unless
  this operation explicitly owns that entry.
- [x] Pre-start preparation covers headed CLI and ACP with the same selected
  payload identities; ordering tests prove remote installation and local copying
  complete before settings assembly, and settings assembly completes before
  launch. Missing installation or copy failure prevents child startup.
- [x] Companion metadata cannot expand the eligible set: a selected sample
  alone reports its external companion without copying/enabling it; an
  explicitly selected pair is prepared together. Target-native selected remote
  plugins still install, and target-local enable/disable choices remain intact.
- [x] An independent CLI consumer discovers the merged base/local settings, and
  ACP receives one `--plugin-dir` for every effective prepared plugin, including
  both remote-installed and worktree-copied sources.
- [x] Synthetic fresh-checkout integration uses the real configuration writer
  and plugin resolver, verifies on-disk payloads and local marketplace sources,
  and launches an independent consumer rather than mocking past registration.
- [ ] Trusted container proof: headed and headless agents enumerate the
  expected skills from the reflected payloads.
- [x] Codespace proof: the same narrow discovery check succeeds for both launch
  modes, with effective paths/settings and launch version recorded.
- [ ] Static local, SSH, and privileged lanes have equivalent acceptance
  evidence or explicit tracked transfers.
- [ ] Source-preservation examples agree with executable cases: local
  same-world references retain the original path, remote local sources use
  reflected paths, Git declarations remain Git declarations after installation,
  and target-owned absolute paths become target-relative marketplace paths.
- [ ] Git preparation and activation leave user-global preferences unchanged;
  an unrelated repository using the same execution profile does not load the
  target repository's selected plugins.
- [ ] Native startup acquisition completes before task delivery; a failed or
  incomplete selection prevents ready status and task submission, including
  cancellation, retry and modes with no skill contribution to observe.
- [x] Existing own-plugin, related-plugin, Session Host resume, and native
  repository settings behavior remain intact.

## Proposal

Keep selection and launch policy in agent-bridge or its existing shared
dependency; providers expose substrate operations rather than independent
registration policies. Reuse the existing local-marketplace parser, payload
copy exclusions, and supported per-repository settings schema.
Provider/related selections, with their existing harness-only propagation
filter, and the target repository's enabled declarations define the exact
eligible set. Dependency objects describe companion requirements, not venue
eligibility. Report an out-of-selection or disabled companion explicitly so
the caller can supply its execution owner, including an external host. Do not
silently stage/enable it, assume host authority, or fail merely because it is
absent from this agent. A missing explicitly selected payload or failed selected
installation/copy remains a launch-blocking error.
The existing contract uses `extraKnownMarketplaces` for marketplace declarations
and `enabledPlugins` for `<plugin>@<marketplace>` enablement; its parser is
`libs/plugin-resolve/src/plugin_resolve/settings.py`. This operation owns only
the registration entries it creates for its reflected payloads, not those maps
wholesale, repository-native declarations, or any other top-level setting.
Repeated preparation must retain those unrelated values and avoid identity
collisions. CLI may also receive explicit plugin directories when its supported
launch contract requires them; the settings file is not assumed sufficient
without a consumer proof.

Agent-recommended implementation safeguards: use an atomic merge under an
appropriate existing lock for shared settings; use stable owned registration
keys and immutable or safely reconciled staging paths so concurrent launches do
not invalidate one another; retain unrelated operator settings; fail explicitly
on malformed configuration. These are reliability details, not new authority
or a reason to create a daemon.

## Journal

### 2026-10-18 - Detached seed integration and admission ambiguity review

- Detached Codespace/container owners now carry the writer-produced context,
  withhold owned terminal seeds and use the protocol-gated Bridge queue. Existing
  creation-seed receipts retain deferred delivery and settlement; ordinary
  attached/operator input remains distinct.
- An independent source check exercised the real HTTP router, SQLite queue,
  public client, native SDK readiness observation and native task delivery
  together. One queued task invoked the selected skill, an identical operation
  retry returned the same message, and changed compiled selection was refused.
  Preferences and target settings stayed byte-identical. The isolated test
  server and native processes were stopped; this is not installed rollout or a
  full remote lifecycle proof.
- Independent review found a blocking ambiguity: a lost response after durable
  admission could be mistaken for non-admission and cause a newly created
  session to be stopped while its task was already running. The correction must
  reconcile the same operation and retain native/session-connection ownership
  while admission is unknown. Review, contract attestation and publication stay
  open until this case is fixed and verified.

### 2026-10-18 - Automatic acquisition candidate and compiled native loadout

- Ordinary local ACP acquisition now invokes preparation automatically. Native
  CLI acquisition uses the existing Worktrees launch owner after its real
  profile-environment merge, before mux creation, and carries the writer's
  compiled selection into task admission. Legacy live CLI without a matching
  native-bound preparation receipt is refused rather than guessed or reseeded.
- The compiled context comes from the effective registration under its existing
  lock: actual projected identities, logical identities, available versions and
  known local marketplace roots. A private Git acquisition root is not reported
  as the productive native marketplace root. Remote transports can opt into the
  versioned context record without changing prepared-directory parsing.
- Independent source-owner verification used the real controller selection
  reader, a separate Worktrees preparation process and the actual native
  installer. A fresh interactive frontend then accepted the compiled set of a
  target-native local plugin, a controller-local alias and a Git-sourced plugin.
  The two local plugins had no skills, so skill discovery was not the readiness
  oracle. All three registered before one SDK-delivered task invoked the
  selected Git skill; preferences and target settings stayed byte-identical.
- This check exercised real owner preparation, compilation, native registration
  and source inbox delivery with a synthetic queue. It did not replace the
  pending full managed-owner/HTTP-daemon or external-venue acceptance lanes.
  Detached provider seed forwarding remains the next source integration step;
  native session registration alone is not its completion condition.

### 2026-10-18 - Candidate native gate and remote ownership integration

- The source readiness observer passed in an actual joined interactive CLI:
  selected registered plugins were ready, a missing plugin remained pending,
  and a mismatched session was refused. Session and cwd remained stable. The
  temporary observer was removed; no plugin installation, activation, reload,
  task send or preference write was used by the observation.
- A fresh interactive native consumer then exercised the source observer and
  inbox-delivery helper together. A synthetic queue's missing-plugin seed was
  held with zero SDK sends; the actual registered loadout was observed before
  exactly one SDK send and successful skill invocation. The productive test
  profile's preferences and target settings stayed byte-identical. This used a
  real frontend and SDK, not a model's readiness statement; the queue adapter
  was synthetic, so it is not full acquisition-owner or HTTP-daemon evidence.
- The candidate typed gate does not by itself cover real startup owners.
  Automatic local acquisition preparation, compiled effective expectations and
  withholding of owned seeds remain a separate integration step before claiming
  end-to-end readiness.
- Trusted Codespace/container producers now derive target-owned annotations
  from explicit repository-registry bindings. Bridge preserves that identity
  when merging provider and related plans. Unrelated checkouts are not inferred
  from a matching basename or remote.
- A new registered-bare-anchor case exposed a missing source-side identity
  path; the candidate now handles that anchor without relaxing the receiver's
  real-checkout requirement or nested-repository refusal. Focused contained
  selections passed 40 Codespace, 30 container, 120 Bridge and 14 shared-parser
  compatibility contracts. Read-only source-peer CLI checks also resolved the
  bindings with sibling Python imports unavailable. These are candidate results,
  not merge, release or rollout evidence.
- Owned mode controls now retain their readiness requirement and recheck it
  immediately before applying autopilot. A successful initial observation cannot
  authorize a later mode change after cwd or plugin state changes; ordinary
  operator mode controls retain their existing shape.

### 2026-10-18 - Native selector and registration grounding

- An isolated native CLI acquisition fetched an explicitly selected non-default
  Git branch and installed the plugin at that revision's nested catalog
  location. The installed version and a revision-specific skill marker matched
  the selected branch, not the default branch's root-level plugin. The source
  declaration was unchanged; no transport rewrite was used.
- A separate fresh-profile run used only the target repository's declaration,
  with no global plugin-management commands. The actual CLI loaded and invoked
  that same non-default, nested plugin. An unrelated repository using the same
  profile and cache did not load it. User settings remained byte-identical after
  both runs, and the target's Git source declaration was preserved.
- The candidate shared preparation engine also acquired that revision through
  the actual native installer, retained the original Git declaration, returned
  the verified payload directory, and repeated without changing productive
  preferences or installing into the productive profile.
- A separate candidate-engine check rebound an absolute anchor reference to
  `./.ai` in the actual execution worktree. The settings-only native CLI loaded
  and invoked that worktree's distinct skill, not the anchor's version, without
  snapshots or source-catalog changes. This does not establish remote-producer
  ownership annotation.
- The first synthetic run hit a Windows Git cache-path limit. A shorter isolated
  native cache path succeeded; an operation-scoped long-path setting alone did
  not. This is acquisition evidence, not an external-network acceptance result
  or authorization for user-global plugin management.
- A read-only, temporary extension in an actual interactive native CLI session
  observed its registered plugins through the public session RPC, with stable
  session and checkout metadata. Optional installation-path fields were absent
  on the selected entries. The observer was removed afterward. This grounds the
  readiness API but does not close the fresh-start task-delivery integration
  gate.

### 2026-10-18 - Startup-readiness ordering accepted

- The operator selected native repository-scoped startup activation, with task
  delivery gated on confirmed plugin readiness. This removes a strict
  install-before-process bookkeeping requirement, not the requirement to
  deliver the complete selected plugin set before accepting work.
- Source-kind preservation, target-owned relative relocation and repository-only
  activation remain unchanged. There is no authorization for user-global
  installation/enablement changes, a profile switch, dummy inference or private
  native-state manipulation.
- The seven worked settings/argument cases are published in this draft's
  acceptance reference. Updated their ordering language and retained the
  separate implementation/validation gates for startup readiness, Git source
  handling and deferred adapters.

### 2026-10-18 - Source preservation and repository-scoped activation

- Clarified the standing contract: local references stay local, Git references
  stay Git-based, and an absolute local reference to target-owned content is
  rebound relative to the actual execution checkout. Source declarations and
  concrete ACP directories are distinct outputs.
- Repository scope is the default. Native repository-declared Git auto-install
  was exercised without global marketplace/install commands: the declaring
  repository loaded the selected skill, user-global settings remained unchanged,
  and a separate repository using the same profile did not load it.
- The existing management CLI, cache-only reuse, and no-prompt ACP
  initialization/session creation are not established scoped preparation
  barriers. Normal startup activation is not by itself proof that acquisition
  completed before the productive child. That readiness seam remains open;
  no user-global activation or silent profile replacement is an acceptable
  workaround.
- Preserved the mixed-local-origin and installed-inventory corrections, with
  242 shared contracts and focused Bridge coverage. An independent actual CLI
  consumer loaded both original physical payloads from settings generated by
  preparation, including the operation-owned local alias. Neither source catalog
  was changed.
- Added worked acceptance examples for ordinary local references, explicit
  target choices, remote local copies, preserved Git sources, target-owned
  relocation, mixed physical origins and legacy ACP inventory. These examples
  do not close the source-preserving Git or deferred-adapter gates.

### 2026-10-18 - Static local ACP candidate and consumer proof

- The next bounded slice implements ordinary static local ACP preparation
  after actual workspace allocation and environment resolution, before either
  raw-local or local Session Host child startup. Structured argument provenance
  replaces only automatic plugin directories; explicit flags remain intact.
- Shared preparation has opt-in same-filesystem references. Target-local
  settings point directly at the original selected marketplace; preparation
  neither snapshots those payloads nor writes source catalogs. Remote-copy
  defaults, native operator authority, atomic settings publication and verified
  remote installation remain unchanged.
- Direct launches honor their explicit Copilot executable. Managed opaque
  wrappers use a separate packaging CLI selected from the resolved environment,
  matching the existing shared preparation contract; this is not a claim that
  the installer executable equals a backend selected later by a setup script.
  No Worktrees launch-plan API or configuration surface is added.
- Contained coverage includes fresh/repeated preparation, real related-repo
  selection, allocation ordering, source-worktree precedence, operator
  overrides, explicit arguments, installer environment, missing installation,
  malformed settings, cancellation quiescence and deferred-lane exclusions.
  Independent actual Copilot CLI and ACP consumers loaded and invoked a
  synthetic skill from the original filesystem reference. The CLI consumer
  used only target settings, with no plugin-directory flags; ACP used the
  launch adapter's effective directory.
- These are source-candidate and fresh-checkout consumer checks, not installed
  rollout or native CLI acquisition acceptance. Native local CLI, SSH,
  privileged execution, live trusted-container proof and release remain open.
  This candidate still requires review, required CI and merge; no implementation
  checkbox is closed by this journal entry.

### 2026-10-18 - Trusted provider registration merged; static targets next

- #7157 merged into `dev` as
  `677623a2dcaf1fadb69f9b5dfbabb5e691f8ac09`. Its exact final head had 50
  successful or legitimately skipped checks and a clean owner-authored Comment review with no remaining
  findings. This records a merge, not release promotion or installed rollout.
- Actual Codespace ACP and native CLI discovery exercised 34 effective
  controller/native registrations and payloads. ACP received every effective
  directory; native CLI loaded the reflected skill through target settings with
  no plugin-directory flags. Actual creation, genuine child reuse and controlled
  stale-ledger replacement all completed their mandatory preparation barrier.
  A separate actual attached-shell consumer verified the prepared checkout and
  34 local registrations from a neutral directory; only its terminal consumer
  was substituted, so it is not claimed as a full native-TTY/model interaction.
- Shared and provider regressions cover operator authority, late selection/
  origin edits, consecutive and overlapping preparations, source-worktree
  precedence, exact claim teardown, cancellation and hook-created checkouts.
  Real archives exclude mixed-case environment secrets while retaining the
  existing example/sample/template exceptions and required assets.
- #7176's Windows CI repair retains separate constant-identity readiness and
  publication jobs, each bounded to five minutes and required by both aggregate
  gates. Hosted runs passed in 65 and 169 seconds without dropping selectors or
  weakening qualification inventory.
- The merged commit's Dev CI passed. Trusted Main remains under the
  operator-approved release containment tracked by #6237; its promotion gate
  deliberately skips publication. Do not treat a successful no-op workflow as a
  release, unpause that separately owned control work, or install unpromoted
  source to declare rollout complete.
- The local launch-adapter portion is explicitly carried into Phase 2 alongside
  static remote SSH and privileged execution. The next stretch traces those
  actual launch owners and identity/filesystem boundaries before extending the
  existing preparation engine. Live trusted-container discovery and the release/
  authorized-rollout gate remain unresolved; no parent completion is claimed.

### 2026-10-18 - Complete catalog authority and authenticated hook sequencing

- Remote installations retain the native marketplace catalog and selected
  entry, including catalog ownership independent of optional plugin authorship.
  The isolated native cache supplies first-install metadata; verified receipts
  retain it when the native cache is later evicted.
- Session Hosts deploy helpers before establishing the relay, then execute
  authenticated repository hooks with the launch environment before preparing
  the child. Default combined provider provisioning remains compatible.
- Interrupting attached preparation before readiness cancels its in-flight
  work and awaits owned relay, connection and worker cleanup; normal interactive
  child exit still releases the prepared resources normally.
- ACP selection reads controller-first related declarations, not merely
  controller-first payloads. Retained decisions outrank discovered fallbacks,
  while explicit provider enablement and target-native operator authority keep
  their distinct precedence.
- Shared validation passes 112 tests. Post-materialization provider checks
  exercise controller archive bytes, retained booleans, authenticated
  hook-created checkouts and pre-readiness cancellation. Required CI and
  external source-candidate revalidation remain separate gates.

### 2026-10-18 - Review-driven authority and launch-order corrections

- Authored native settings retain their layer precedence behind unchanged
  operation-owned projections. Repeated preparation reloads original local
  catalogs and remote origins, including changed payloads and newly selected
  entries; operator-changed local declarations remain authoritative.
- Disabled selections survive provider metadata, namespace and native CLI
  handoffs. Provider enablement wins overlapping related fallback declarations;
  target-native operator settings are reconciled afterward.
- Fresh and recovered remote Session Hosts share the authenticated
  checkout-creation barrier before Git-dependent preparation. Attached launches
  retain a foreground relay fallback through the interactive child's lifetime.
- Shared regressions pass 108 tests; provider and shipped-copy consumer checks
  cover the changed contracts. Controller-worktree precedence and live consumer
  revalidation remain part of this slice, not inferred from earlier proof.
- Review resolution, required CI, merge, release and authorized rollout remain
  pending. No Phase 1 or parent-effort completion is claimed.

### 2026-10-17 - Preserve marketplace authority across multi-plugin preparation

- Registration snapshots operator marketplace declarations at lock entry and
  reconciles each marketplace once. Generated values from an earlier plugin in
  the same transaction cannot deactivate its native origin.
- Native enabled selections, not only host requests, consult active original
  origins rather than treating a generated subset as the complete catalog.
  Unchanged base remote declarations remain available behind owned projections;
  genuine operator-changed local payload paths retain precedence.
- Legacy recovery covers the provider's actual nested workspace fallback chain.
  It resolves named environment values as data, without arbitrary shell
  evaluation, and persists the concrete execution directory before preparation.

### 2026-10-16 - Cold-auth and target-native integration corrections

- Remote Session Host preparation moved to an authenticated pre-child callback:
  existing helpers and verified relay forwards precede native installation,
  and the same supervisors remain owned through publication and launch.
- Headed providers now retain their normal relay/launch environment while
  preparing; attached SSH executes preparation after its reverse-forward is up,
  and detached containers prepare after their existing keeper is established.
- A target-native remote origin declared in local settings is installed and
  localized without losing its original declaration. Git-local ownership
  metadata retains the exact origin and its active authority.
- Legacy environment-expanded workspace commands resolve in the venue without
  launching Copilot; concrete execution paths are persisted before preparation.
- Explicit related/provider sources whose marketplace exists only in the target
  checkout are deferred there for authoritative lookup. Host planning no longer
  rejects them before target resolution, and missing target authority/payload
  still fails before publication or launch.
- Pure shared-library regressions pass. New provider ordering/recovery
  regressions are written; execution awaits the independently owned provider
  test environments. No live acceptance claim follows from these source edits.

### 2026-10-16 - Separate companion requirements from propagation authority

- Removed automatic manifest-dependency expansion from both planning and
  target-side preparation. Only explicit eligible selections and target-native
  enabled declarations enter the install/copy barrier.
- Retained dependency-object parsing and selected sibling asset topology.
  Out-of-selection/disabled companions now produce qualified diagnostics, not
  implicit enablement or a same-agent execution assumption.
- Preserved the existing harness-only propagation filters at provider/related
  boundaries. Generic upstream code does not select organization-specific
  browser or authentication composition.
- This supersedes the earlier same-agent companion-conflict behavior; selected
  payload failures still block launch. Consumer/live acceptance remains
  coordinator-owned.

### 2026-10-16 - Native consumer review and source corrections

- Shared-resolver success did not establish native CLI acceptance: the initial
  subset catalogs omitted native-required owner/version metadata. Publication
  now retains marketplace metadata and emits each payload's manifest version.
- Dependency closure now accepts the native `{name, marketplace}` objects,
  including sibling and external-marketplace companions.
- Target-owned enabled declarations join host selections, including remote
  marketplace installations. Explicit disables and target-local operator
  overrides are preserved; disabled or missing required companions are errors.
- Caches, snapshots, receipts and locks moved to checkout-specific Git metadata.
  Generated local settings use a Git-local exclude, not a tracked ignore change.
- Explicit worktree resolution moved to the providers' native dry-run execution
  contract; the shared engine no longer discovers a Worktrees command.
- Focused synthetic contracts cover these corrections. Native consumer
  revalidation, bounded provider regressions and live acceptance remain open;
  this journal is not a Phase 1 completion claim.

### 2026-10-16 - Reviewed implementation authorized

- Proposal #7064 merged at `d6e952ec1d4288669109963b3342e17d5414b966`.
- Phase 1 source implementation is authorized; publication and live acceptance
  remain coordinator-owned. Static SSH and privileged relay remain Phase 2.
- Source validation covers trusted containers; live container acceptance remains
  blocked on an available Docker venue.

### 2026-10-16 - Continuation and reprioritization

- Verified the vision and local-loopback PRs are merged.
- Claimed #5286 for continuation; prioritized trusted Codespace/container
  registration in both launch modes before remaining static SSH/privileged work.
- Prepared this public canonical sub-effort; environment-specific adoption
  context remains outside the public repository.
- Captured the explicit install/copy -> settings assembly -> launch ordering for
  both modes, mandatory per-plugin ACP arguments, and CLI base/local merge.
