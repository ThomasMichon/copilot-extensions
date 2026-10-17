# Venue Plugin Staging and Registration Parity

- **Slug:** `venue-plugin-staging-parity`
- **Repo:** copilot-extensions
- **Branch(es):** independent managed worktrees and PRs targeting `dev`
- **Created:** 2026-10-16
- **Status:** Draft
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
coincidental staging-directory layout.

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
- [ ] Land this proposal and the registration-before-launch vision refinement
  before implementing the new behavior.

### Phase 1 - Trusted Codespace and container registration

- [ ] Trace the actual headed CLI and headless ACP launch owners, including
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

### 2026-10-16 - Continuation and reprioritization

- Verified the vision and local-loopback PRs are merged.
- Claimed #5286 for continuation; prioritized trusted Codespace/container
  registration in both launch modes before remaining static SSH/privileged work.
- Prepared this public canonical sub-effort; environment-specific adoption
  context remains outside the public repository.
- Captured the explicit install/copy -> settings assembly -> launch ordering for
  both modes, mandatory per-plugin ACP arguments, and CLI base/local merge.
