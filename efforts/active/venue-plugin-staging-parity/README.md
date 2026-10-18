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
Reflect payloads into the execution environment, register their local paths in
the target checkout, and launch only after that preparation succeeds. Headed
CLI and headless ACP sessions should discover equivalent skills.

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

## Plan

### Phase 0 - Reconcile and review

- [x] Verify #5295 and #5306 merged; retain their already-completed scope.
- [x] Claim #5286 and inspect current provider/bridge contracts and overlapping work.
- [x] Land this proposal and the registration-before-launch vision refinement
  before implementing the new behavior.

### Phase 1 - Trusted Codespace and container registration

- [x] Trace the actual headed CLI and headless ACP launch owners, including
  bridge-owned remote Session Hosts and provider process boundaries.
- [ ] Implement shared payload reflection/registration preparation: local
  filesystem references for local launches, copied payloads for remote launches,
  and a resolvable local marketplace declaration plus explicit enablement in
  the target repository's `.github/copilot/settings.local.json`.
- [ ] Complete installation of every selected remote-marketplace plugin and
  copying of every selected locally sourced in-worktree payload before
  assembling target-local settings, in both CLI and ACP modes. Verify installed
  payload locations instead of treating an expected cache path as installation.
- [ ] Preserve unrelated configuration and repository-native plugins; reconcile
  only owned entries. Make repeat preparation idempotent.
- [ ] Pass `--plugin-dir` for every effective prepared plugin to ACP. CLI uses
  target `settings.local.json` merged over `settings.json`; explicit plugin
  directories may supplement that supported path. Settings registration is
  mandatory in both modes, not replaced by the ACP flags.
- [ ] Prepare before starting either child; surface copy/registration failure
  rather than treating an agent missing requested plugins as ready.
- [ ] Keep restricted/untrusted containers outside automatic projection.
- [ ] Land the smallest coherent change with directly related docs, tests, and
  changefiles.

### Phase 2 - Static remote SSH and privileged relay

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

- [ ] Unit contracts cover first preparation, repeat preparation, settings
  preservation, identity mapping, required payload absence, malformed settings,
  and restricted-venue refusal.
- [ ] Concurrent preparations against one checkout retain both intended
  registration sets and unrelated settings; an interrupted write leaves the
  previous valid file usable.
- [ ] Existing user-level registration and target-local registration of the
  same identity resolve to the selected target payload without duplicate skill
  loading. Explicit target-local operator overrides remain authoritative unless
  this operation explicitly owns that entry.
- [ ] Shared preparation covers headed CLI and ACP with the same selected
  payload identities; ordering tests prove remote installation and local copying
  complete before settings assembly, and settings assembly completes before
  launch. Missing installation or copy failure prevents child startup.
- [ ] Companion metadata cannot expand the eligible set: a selected sample
  alone reports its external companion without copying/enabling it; an
  explicitly selected pair is prepared together. Target-native selected remote
  plugins still install, and target-local enable/disable choices remain intact.
- [ ] An independent CLI consumer discovers the merged base/local settings, and
  ACP receives one `--plugin-dir` for every effective prepared plugin, including
  both remote-installed and worktree-copied sources.
- [ ] Synthetic fresh-checkout integration uses the real configuration writer
  and plugin resolver, verifies on-disk payloads and local marketplace sources,
  and launches an independent consumer rather than mocking past registration.
- [ ] Trusted container proof: headed and headless agents enumerate the
  expected skills from the reflected payloads.
- [ ] Codespace proof: the same narrow discovery check succeeds for both launch
  modes, with effective paths/settings and launch version recorded.
- [ ] Static local, SSH, and privileged lanes have equivalent acceptance
  evidence or explicit tracked transfers.
- [ ] Existing own-plugin, related-plugin, Session Host resume, and native
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
