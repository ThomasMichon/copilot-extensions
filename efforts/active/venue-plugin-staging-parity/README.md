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

### Fixture-maintenance constraint

> We should ensure that minor updates to the flows don't require heavy rewrites
> to all the fixtures.

Use the existing registry distinction between implementation-only and semantic
changes. Preserve frozen packets and their original capture provenance rather
than recapturing unrelated flows to satisfy a shared commit or source snapshot.

### Same-repository child and relationship-map clarification

Public-safe capture; only the private control-repository name is abstracted:

> Great, keep driving. One edge case I missed: if one [control-harness] worktree
> drives another [control-harness] worktree as a sub-agent, the child worktree
> should draw from its *own* local marketplace, not the source worktree.
>
> It's probably worth making a table/flowchart of the host/child relationships of
> agent-bridge and how plugin projection will work in each. HTML and some
> diagrams would work for this.

This makes the target-native precedence rule explicit for two worktrees of the
same repository. It does not replace an explicitly authored external
target-local source, expand plugin eligibility, or waive the remaining venue,
release or authorized-rollout gates.

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

- [x] Preserve declared source kind through shared preparation in every
  integrated venue: retain Git origin/revision/location declarations, retain
  local references or reflected local copies, and rebind target-owned absolute
  local references relative to the actual execution checkout.
- [x] Gate task delivery on the complete effective plugin set, allowing native
  repository-scoped Git startup acquisition without treating process creation,
  a connection or a session acknowledgment as plugin readiness.
- [x] Publish worked examples mapping controller selection and execution
  location to target-local settings and the complete automatic ACP directory
  set; cover source preservation, target ownership, mixed origins, installed
  fallback and explicit target-local choices.
- [x] Apply target-local registration before static-registry local-loopback ACP
  launches, retaining existing own/related selection, native
  operator precedence and account/platform locality checks. Resolve local
  filesystem references without assuming a remote provider owns this path.
- [x] Apply the same registration barrier through the existing local
  acquisition owner in CLI and ACP modes; settings-only consumer discovery is
  not proof that this adapter prepares its target.
- [x] Prove that a same-repository child worktree draws from its own local
  marketplace revision rather than the controller worktree or anchor, retaining
  child disablement and missing-payload refusal.
- [x] Publish an HTML host/child relationship matrix and diagrams showing
  plugin source authority, projection, settings, launch and readiness, with
  implemented and still-unqualified lanes distinguished.
- [x] Apply the preparation contract to the existing genuine static-registry
  SSH ACP owner: actual target resolution, eligible controller reflection,
  target-native authority and complete directory arguments before productive
  stdio. Native cross-machine skill acceptance remains in the matrix below.
- [x] Close supported headed static-SSH CLI integration through its existing
  owner, without introducing another launcher.
- [x] Resolve the privileged launch's real identity/filesystem context and
  prepare through its supported channel; do not assume it shares the
  unprivileged account's home.
- [x] Preserve loopback account/platform locality checks and existing binding
  behavior. Land tested slices independently.

### Phase 3 - Release and acceptance

Current source through #7856 is confirmed in canonical Main commit
`809fd1114af48d86ed710f56b6f2048053bc86f6`, whose unpaused release state records
the exact `2057089fd9880843d254f3a3650f9535419f1863` Dev snapshot promoted at
`2026-10-22T13:20:39Z`. Ten selected Bridge/shared-preparation production files
match the merged source byte-for-byte. This includes #7841 and supersedes the
earlier publication observation, not the requirement to verify subsequent
slices, perform only authorized rollout or prove the effective running version.

An earlier verified Main is `c02050ae353fee8d36fb37f4531c9037b009e2cd`,
whose unpaused state records Dev `258a4dd71b4f667bb24f6abb921ea276b633d32f`
promoted at `2026-10-22T20:49:07Z`. Promotion PR #7957 merged that snapshot;
the earlier retained promotion #7905 has also merged. Privileged source #7899
and the independent CI repair #7907 are both ancestors of the recorded Dev
snapshot, and all 21 changed source paths outside their effort/changefile
records have identical Git blobs in that Dev snapshot and Main. The released
agent-bridge version is `0.9.111-dev1`. This proves publication, not authorized
rollout or the effective running version; those obligations remain open.

The correction was first verified in Main `1310525ffd5ff03b5442f04523e4f19569b554e6`,
whose unpaused state records Dev `01d45627a7227d655025b3bc238f2542e60ef043`
promoted at `2026-10-23T08:41:16Z`. Both canonical and vendored registration
modules exactly match #8165's merged source. Released versions are
agent-bridge `0.9.120-dev1`, agent-worktrees `1.24.211-dev1`,
agent-codespaces `0.5.108-dev1`, agent-containers `0.2.89-dev1` and
agent-ssh `0.1.99-dev1`. Unified controller rollout completed and the actual
running Bridge reported `0.9.120.dev1`, healthy and ready. All five controller
runtime interpreters imported the exact corrected registration module.
The disposable venue's later unified update selected agent-worktrees
`1.24.212-dev1` and agent-bridge `0.9.122-dev1`, also importing the exact
correction. Main `1899e2e5eed5ccfc4e87cae1b3f43b4d4f71ea70` records
Dev `8086afc7db615beb3ca01f003e8608e90be409af` at
`2026-10-23T09:14:35Z`, which includes #8165. Native Codespace acceptance
below used those effective runtime versions, not a source-only patch.

- [ ] Verify publication from `dev` to `main`; perform only authorized rollout
  through the unified update path and record the effective running version.
  The earlier #8165 publication and running-version evidence above remains
  settled. The #8405 caller/preflight correction is now published and verified
  in the running controller. The subsequent namespace-identity repair #8715
  requires its own containing release and authorized rollout.
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
- [x] Real linked Git worktrees at different committed revisions exercise
  same-repository child marketplace authority through ACP launch composition,
  CLI acquisition preparation and copy-mode preparation. Independent settings
  consumers, repeat preparation, explicit child disablement and missing child
  payloads never substitute the parent revision or mutate global preferences.
- [x] Synthetic fresh-checkout integration uses the real configuration writer
  and plugin resolver, verifies on-disk payloads and local marketplace sources,
  and launches an independent consumer rather than mocking past registration.
- [ ] Trusted container proof: headed and headless agents enumerate the
  expected skills from the reflected payloads.
- [ ] Complete the #1146 acceptance now inherited by #5286: an eligible
  target-user-enabled plugin and a staged repository plugin are both usable in
  the same executing ACP child and supported headed CLI, in a Codespace and the
  trusted-container control. Retain explicit disables and target-local
  precedence, correlate source/cwd/settings/readiness, and leave global
  preferences unchanged; separate single-plugin proofs do not close this row.
  The combined Codespace proof now passes in both modes on the published
  correction; the corresponding trusted-container proof remains open.
- [x] Codespace proof: the same narrow discovery check succeeds for both launch
  modes, with effective paths/settings and launch version recorded.
- [ ] Static local, SSH, and privileged lanes have equivalent acceptance
  evidence or explicit tracked transfers.
  Installed static-local ACP now passes; genuine headed acquisition is blocked
  by #8715. SSH and privileged native acceptance remain open.
- [x] Source-preservation examples agree with executable cases: local
  same-world references retain the original path, remote local sources use
  reflected paths, Git declarations remain Git declarations after installation,
  and target-owned absolute paths become target-relative marketplace paths.
- [x] Git preparation and activation leave user-global preferences unchanged;
  an unrelated repository using the same execution profile does not load the
  target repository's selected plugins.
- [x] Native startup acquisition completes before task delivery; a failed or
  incomplete selection prevents ready status and task submission, including
  cancellation, retry and modes with no skill contribution to observe.
- [x] Implementation-only flow updates require zero golden-fixture rewrites
  after an explicit source classification/fingerprint update. Within an
  unchanged wire generation, a semantic source can receive its affected capture
  without recapturing unrelated peers; stale fingerprints, missing rationale,
  uncovered semantic changes and corrupted packet/provenance hashes still fail.
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

### 2026-10-24 - Verify installed local ACP and preserve execution ownership

- Natural release run `40085094788` passed exact candidate-source qualification,
  publication/readback and Main merge. Pinned Main
  `e549f040e3e3f7b82cfeade3f1446fb000aed162` records admitted Dev
  `442c9d38c8826bbb59947b225367e85a884f2fa1`; #8405 is an ancestor of that
  admitted snapshot. All six affected production files match Dev and Main
  byte-for-byte. This closes that publication gate, not the parent effort.
- Explicitly authorized controller rollout encountered the existing #8312
  Manager re-entry failure after a healthy Worktrees bootstrap. The documented
  serialized engine fallback completed the full unified update successfully.
  This is not a claim that the default Manager path is repaired.
- The actual running Bridge reports `0.9.142.dev1`, ready, healthy and not
  draining, with HTTP protocol 50. Its imported caller/preflight modules match
  the qualified published source. Selected Worktrees, Codespaces, Containers and
  SSH payloads are `1.24.252-dev1`, `0.5.123-dev1`, `0.2.100-dev1` and
  `0.1.117-dev1`.
- A fresh ordinary installed-client local ACP child completed both target-native
  and controller-projected skill invocations with their hidden proof responses.
  The original same-world controller path, target disablement, caller identity,
  base settings, catalogs and user-global preferences were preserved. Its
  native identity and execution cwd matched the ready receipt before the first
  prompt; the successful one-turn body was ended after evidence capture.
- A real coordinator task targeted at an independently created, sessionless
  worktree then deferred before headed acquisition:
  `Cannot establish hosting policy: managed target has no execution directory`.
  The task and allocation remain preserved. An absent native head and missing
  acquisition journal are not being relabeled as no-launch proof.
- #8715 tracks the missing execution-project propagation through the Dispatch
  worktree reference and Bridge namespace consumer. The bounded repair retains
  active matching owner identity, rejects malformed identity, and keeps fresh
  actual-checkout hosting checks before readiness and staging. It does not use
  queue-lane identity, predicted cwd or a policy bypass. The distinct Worktrees
  intrinsic-enforcement work in #8703 remains with its owner.
- Failing-first producer and consumer controls reproduced the dropped identity
  and original hosting refusal. Corrected selections pass 41 Dispatch namespace
  and 350 coupled Bridge namespace, hosting and acquisition cases, including a
  real native provider-child JSON round trip.
- Validation also exposed Windows Git treating a long object expression as a
  filesystem path under the checker's isolated configuration. Explicit revision
  separation fixes the read without changing Git policy, bytes or deadlines.
  The maintenance fixture includes the current HTTP-50 helper and source
  closure. Its ten controls pass, including exact-object/missing-object reads
  and a real flow-only update leaving every golden byte unchanged. The strict
  checker passes all 814 fixtures without recapture.
- Source lint, installation contract and documentation consistency pass.
- #8730 merged as `1a357949dfe37b22528e8b248caf3c787712195c` after Copilot
  approved exact head `1a1fe830526277a1a3c7e8707093770c24f6111c` with no inline
  findings and required CI passed. Managed completion verified and reconciled
  the merged squash without finalizing this active effort.
- The containing release, authorized rollout and actual installed headed #8715
  acceptance remain required. The preserved task and allocation retain their
  existing recovery fences; source success does not fabricate an earlier
  acquisition receipt or authorize a replacement native conversation.
- Container access remains independently blocked by the vendor CLI loader
  defect. Headed local, SSH, privileged, container and issue reconciliation
  gates remain active; neither this proof nor the earlier Codespace acceptance
  completes the broader program.

### 2026-10-23 - Preserve the explicit caller through ordinary CLI creation

- An actual installed Bridge local ACP launch exposed a client-side gap:
  ordinary `create --caller` selected a delivery cursor but recomputed the
  caller before creation. The target-native skill loaded, while the eligible
  controller-selected plugin was absent. This failed acceptance is not credited.
- Ordinary create and named-agent send now retain one selected caller through
  affinity lookup, creation, conflict retry, and prompt delivery. Qualified
  ownership checks and target-local precedence remain unchanged.
- The corrected source CLI reached the actual installed Bridge HTTP and native
  launch owner. It registered the original same-world controller payload path,
  retained target disablement, and completed both projected and target-native
  skill invocations in one child. Controller catalog, target base settings, and
  user-global preferences remained unchanged. The successful test body ended.
  This is candidate integration evidence, not published or installed-client
  acceptance.
- The directly affected CLI suites pass 315 tests. Related parser and ownership
  fixtures now supply current immutable-invocation and qualified-owner facts.
  Provider replay separately checks the current advertisement while retaining
  the linked predecessor packet; no historical golden was rewritten.
- Reconciliation with the concurrent HTTP-47 change preserves its full source
  closure and strict critical-owner bindings. The ordinary-flow maintenance
  fixture now carries the required tools, source inventory, and read-only local
  object history. Batched local Git observations make the full 583-fixture check
  complete in about 31 seconds without increasing its per-command deadline or
  the fixture's 90-second check limit. Nine focused maintenance/batch tests,
  41 existing checker regressions, and 331 reconciled client/capture tests pass.
- The owned authority capture refreshes only its three current vectors and
  their registry records from a real committed source checkpoint, reproducing
  the output without writes and retaining the registry's existing indentation.
- The final complete guard tier passes 391 tests with nine expected skips.
- Review follow-up separates typed consumption of the unmodified HTTP-45
  response from the current producer advertisement. Forced replacement also
  preflights qualified remote ownership and capability before ending the
  incumbent, retaining the captured caller facts for creation and retries.
  Missing/malformed ownership, unsupported capability, a missing replacement
  agent, and cross-request caller-context reuse all refuse without ending it.
- #8405 merged as `9626b048209b0f3abde92d2b66ff0a89d93c9438` after
  Copilot approved exact head `dfa9f4f5179d6185565f6947c7a0a9fcd44dad6b`,
  both review findings were fixed and resolved, and required CI passed. Managed
  completion verified the merge and reconciled the source worktree past the
  squash without finalizing this active effort.
- The containing release is blocked by #8411. Run `40055232492` passed source
  units, artifact construction/attestation, and candidate composition before
  exact candidate-source reuse rejected `services/chat/src/agent_chat_service/__init__.py`.
  The existing bounded source-only repair claim and #3336 control/adoption
  ownership remain authoritative; no competing repair, forced rerun, Main
  mutation, or installed update was attempted.
- Publication, authorized rollout, deployed local acceptance, the remaining
  headed/SSH/privileged lanes, and the independently blocked trusted-container
  proof remain open. The next slice observes that owned release repair, verifies
  a containing qualified Main snapshot, then performs authorized running-state
  and native acceptance instead of crediting this source merge as deployment.

### 2026-10-23 - Prepare the remote-container controller and isolate bootstrap failure

- The operator selected a remote container host and a client-only controller
  installation, not a local engine, hypervisor, daemon, or elevated setup.
  The consumer-owned configuration and pinned client installer are merged.
  The actual current-user client reports the declared version; its isolated
  first-touch and idempotence checks pass without modifying engine contexts,
  host overrides, or credential configuration.
- Exclusive coordination for the selected existing remote host was verified.
  Its native connection bootstrap fails before writing the host stanza: a
  forced self-import removes a private helper from an active module invocation.
  A no-network reproduction distinguishes this external CLI defect from missing
  authentication or plugin preparation. The temporary host lease was released;
  no remote body, owned tunnel, or container remains running.
- The consumer harness readiness guard and focused setup guide are now merged
  after clean review and successful required CI. The owning bounded setup flow
  deployed the published runtime, whose catalog command now reports the exact
  external module-reload failure with exit 1 while inventory still succeeds.
  Broken/healthy first-touch module fixtures also pass. This detects and
  documents the defect rather than hotpatching the external distribution.
- The trusted-container combined-selection proof remains open pending a working
  connection bootstrap. The completed native Codespace evidence is unchanged;
  static local/SSH/privileged acceptance and issue reconciliation remain open.

### 2026-10-23 - Verify published correction in both native Codespace modes

- The authorized full unified controller update completed, including normal
  managed service cutovers. Actual Bridge health and all five relevant runtime
  imports matched the published correction. The venue's own unified update
  retained its existing forwarded control route; no local broker, account
  switch, new elevation or unmerged runtime patch was introduced.
- Resumed the previously failed headed conversation without discarding its
  history. Its native readiness control reported the complete 34-plugin
  selection ready, with no missing entries, before task delivery or either
  skill invocation. Remote cache-version expectations were absent; 29 local
  version expectations remained. Both requested skills loaded successfully,
  with real native tool starts/completions and context-delivery records.
- A separate fresh ACP session on the updated venue loaded both the
  target-user-enabled skill and the repository-staged skill in one turn.
  This proves the combined selection in each mode, not two disconnected
  single-plugin checks.
- Full user-global settings and config stayed byte-for-byte unchanged across
  the successful headed and ACP checks. Native listing outside the repository
  still exposed only the user's global enabled selection, not the staged
  repository plugins.
- Captured the results, then stopped only the completed headed body through
  its provider and ended only the idle ACP session through Bridge.
  Trusted-container combined acceptance and the static/SSH/privileged lanes
  remain open; this closes the publication/authorized-rollout row, not the
  effort or the cross-venue acceptance row.

### 2026-10-23 - Land Git cache-version readiness correction

- #8165 merged to Dev as `01d45627a7227d655025b3bc238f2542e60ef043`
  after current-head Copilot approval, no open review findings and every
  required check passing. The merge retains both provenance trailers, and
  #5286 remains open.
- Shared resolver coverage passed 304 tests with one platform skip; the
  native observer passed 28 tests, including the unchanged strict local-version
  negative case. The Bridge contract registry passed 83 tests without fixture
  rewrites. Vendoring, source lint, module-size and changefile checks passed.
- The compiled Git requirement no longer pins an isolated cache's manifest
  version. Git declarations and local expectations remain unchanged.
  Publication, authorized unified rollout and the failed headed native
  acceptance's controlled revalidation are the next slice, not completion.

### 2026-10-23 - Diagnose native Git cache-version readiness mismatch

- Confirmed publication of the headed SSH slice and its follow-up attachment
  cleanup in Main `9489a0fe8e4284538c3e00d5aaad2ac0cf474f75`, recording
  Dev `b2ed7bf3b24e47e82197f36db2ccd7a2292902fb`. Compared the production
  closure, allowing only generated installer include expansion.
- The operator authorized unified updates of the controller and disposable
  parity venues, including managed service restarts, followed by bounded
  current-user native checks. The controller update completed, and actual
  running versions were checked. This does not authorize elevation or a
  second-account launch.
- A current native Codespace ACP check loaded the staged repository skill.
  The combined target-user/staged-repository proof remains open. The subsequent
  headed startup registered but withheld its seed. A registry comparison exposed
  incompatible retained Git manifest-version expectations and newer native
  versions; confirming that correction through the session's readiness RPC
  still requires native revalidation. Global settings remained byte-for-byte
  unchanged; no task or model turn was admitted. This is failed acceptance,
  not a passing headed result.
- Correct the compiled requirement rather than refresh productive globals,
  bypass readiness or pin a floating Git declaration to an isolated cache.
  A real local-Git regression advances the same origin/ref/location from v1
  to v2 while reusing v1 preparation state; the native observer accepts the
  versionless remote requirement while existing local root/version mismatches
  still fail. Publication and controlled native revalidation of this correction
  remain open, as do all unresolved Phase 3 acceptance rows.

### 2026-10-22 - Land headed SSH source and continue release acceptance

- #7920 merged to Dev as `854229f1b7b9bffb226857361b7d73b5e3ae9973`
  after exact-head Copilot approval and green required CI, including the
  hosted Linux unit packet. All 15 concrete review threads are resolved.
  The merge retains both authorship/session provenance trailers.
- This closes the last Phase 2 source-integration row, not the parent effort
  or its live acceptance matrix. Same-repository child authority and the HTML
  relationship atlas remain already completed; they were not replaced by
  controller-owned payloads during the SSH work.
- Next: verify publication of this new source, then perform only authorized
  rollout with effective-running evidence. Trusted-container headed/headless
  proof, the combined target-user/staged-repository acceptance in both remote
  venues and modes, and static/SSH/privileged live acceptance remain open.
  No native session, second account, elevation, updater or productive rollout
  was authorized or performed by this source milestone.

### 2026-10-22 - Retire the original pending attachment hold on verified stop

- Current-head full review found a retained-hold leak in the stop path:
  attached unknown admission used a unique hold, but stop released the scope
  hold and retired the journal. Startup grace could retain the original hold
  until an immediate fresh launch reused the same mux, keeping it alive after
  the fresh attachment exited.
- Stop now validates and captures the pending operation's original mux-backed
  hold and timestamp before termination. Only after verified stop does it
  conditionally release that captured hold, then archive and retire the
  active journal. Unrelated or refreshed holds are not removed.
- Real hold bookkeeping covers unknown attached admission, verified stop,
  immediate fresh attachment and exit, with no peer, an unrelated peer, and
  an interleaved refreshed owner. SSH discovery/recovery coverage passes
  115 cases with three POSIX-only skips; lint and module caps pass. The prior
  exact-head hosted Linux unit packet is green after the narrow import
  readmission, but the new source head still owes review and required CI.

### 2026-10-22 - Coordinate discovery deadlines and canonical network keys

- Full review's remaining summary concerns were reproducible: discovery
  repeated checkout/anchor resolution beyond its caller deadline, and userless
  SCP remotes were parsed as URI schemes. Discovery now uses one receiver
  with 60 seconds for its work and 20 seconds for transport/cleanup, below
  Bridge's unchanged 90-second limit. Native anchor validation remains at
  launch; no caller, preparation, test or runtime cap was widened.
- SSH and Bridge activation now share the existing network identity parser,
  moved to the lower-level plugin-resolution library with the activation
  import retained as a compatibility export. SCP parsing precedes URI scheme
  interpretation except for file URIs. URL path decoding and path case agree
  across both consumers, and typed filesystem keys cannot become network
  hostnames. No new dependency or version override was introduced.
- Validation: SSH discovery/recovery 112 passed with three explicit POSIX
  skips; Bridge dispatch/namespace/own-plugin consumers 128 passed; existing
  activation library contracts 82 passed. The isolated first-touch Windows
  snapshot test also passes in 89 seconds after removing the original source
  and dependency trees; it explicitly imports the installed SSH module and
  executes the installed shared parser, not a cached source substitute.
  Vendoring, install closure, module caps, lint and the unchanged protocol
  fixtures pass. Live SSH/native acceptance and authorized rollout remain
  unperformed, and the parent remains Active.
- Dev advanced during review and enrolled activation-preservation's import
  closure in the exact unit packet. The managed branch sync retained a recovery
  checkpoint; the manifest now binds only the changed compatibility export
  and its new canonical parser, without changing nodes, targets, effects or
  budgets. A bounded Windows packet attempt reached 839 passes before the
  already documented Linux-only activation cohort attempted native Windows
  loading and was refused by the unchanged observer. That is not a passing
  packet or native qualification; the required hosted Linux packet must pass.

### 2026-10-22 - Correct configured remote identity and attached recovery

- Full review identified two distinct repository identity defects. The SSH
  probe now asks the existing native Worktrees owner for `get git-remote`
  before reading that remote's URL; upstream-only checkouts no longer report
  an empty repository. Discovery, preparation and launch checks share the same
  probe. Filesystem origins have case-sensitive, type-qualified keys so a
  relative path cannot alias a GitHub coordinate.
- Recovery retains and reacquires the original keeper hold identity from the
  pending operation. Repeated unknown admission does not accumulate attached
  holds, and a later successful attachment releases that original hold on
  exit without removing another session's forward. Missing or unrelated hold
  metadata is refused rather than guessed.
- Targeted source validation: SSH 103 passed with three explicit POSIX-only
  skips; native Worktrees context/query and routing coverage 227 passed; Bridge dispatch
  and namespace coverage 98 passed. Lint and both contracts with all 456
  frozen fixtures pass. The configured-remote tests execute the generated
  probe against actual Git and exercise the public repository-discovery entry
  point, but substitute the native owner response; they are not live SSH or
  genuine POSIX acceptance evidence.
- CI caught the context CLI crossing its module-size cap after the new scalar.
  Its existing read-only key descriptions/dependencies now live in a separate
  metadata module, without changing the cap or baseline. Wider routing tests
  also exposed a stale updater fixture; it now intercepts the current request
  boundary instead of entering the isolated test updater.
- Main publication is independently confirmed for privileged source #7899
  and CI repair #7907: the recorded promoted Dev contains both commits and
  its 21 affected source blobs exactly match Main. No updater, native launch,
  second account, elevation or productive deployment was invoked. The headed
  SSH source row remains open until its PR actually merges, and the parent
  acceptance and effective-running obligations remain open.

### 2026-10-22 - Qualify the existing headed SSH CLI owner

- Bridge's supported `ssh:<alias> --cli` dispatch now forwards the current
  controller's qualified selections and roots through `agent-ssh copilot`.
  Its read-only repository query uses the actual remote checkout, and launch
  refuses changed repository selection or an unsupported explicit worktree
  selector instead of silently choosing an anchor.
- The existing Worktrees anchor owner's dry-run resolves the actual native
  identity, mux and checkout before reservation or preparation. Its root must
  match the configured workspace's Git root; a managed child is not silently
  redirected to a parent anchor. A nested workspace retains the existing
  anchor-root execution behavior.
- Attached and detached modes share the existing native launch and owned-seed
  runner. Shared preparation registers effective repository settings before
  startup; child-native sources and disables win, Git origins remain Git,
  unrelated absolute local sources reflect and registered target-owned
  absolute sources rebind. No duplicate automatic CLI directory flags are
  added. Tasks wait for the compiled loadout, not merely registration or a
  visible pane; attached mode then joins the same mux.
- Unknown seed admission retains the exact operation and native session in a
  separate delivery journal that survives disposable keeper replacement.
  Recovery does not prepare, reload or recreate the session, and changed native
  identity or checkout refuses a fresh fallback. Both SSH consumers reuse the
  same existing bounded stdlib receiver supervisor, now library-owned.
- Source validation: the complete SSH suite passes 652 cases with 123 explicit
  skips in smaller file groups under the unchanged item/group limits. Bridge
  guards pass 351 with eight skips; dispatch/shared-receiver coverage passes
  69 with two skips. Final streamed preparation coverage passes 23 with one
  POSIX-only skip. Both contracts and all 456 frozen HTTP fixtures pass without
  packet or capture-provenance rewrites.
- First-touch validation explicitly opts into the existing isolated Windows
  snapshot test: stamp, remove the original payload and dependency trees,
  provision through the public binstub, and import the installed SSH package
  with its new shared helper. It passes in 93 seconds without native Copilot,
  authentication, elevation or productive rollout. The opt-in first-use
  tests previously allowed less than 25 seconds despite the installer's
  documented 30-120-second contract; their own bounded provisioning allowance
  now matches that contract on both platforms. Default unit/group budgets
  remain unchanged. Two extracted installer fixtures also now supply their
  actual directory and logging prerequisites.
- Documentation impact: updated both plugin READMEs, the worked settings
  table, the HTML host/child atlas and the shared-library contract. This closes
  the existing venue-agnostic-launch intent through established owners; no new
  launcher, broker or standing policy is introduced. Docker is unavailable on
  this host, so the isolated first-touch test is not a Docker clean-room claim.
  Native cross-machine skill acceptance, the simultaneous target-user/staged
  union, privileged/container lifecycle and authorized running-version proof
  remain open. Review, required CI and merge still gate this source candidate.
- Published #7920 through managed source reconciliation onto current `dev`.
  The source candidate is open, not merged or deployed; current-head review and
  every required check remain its completion gate. The all-ecosystem local
  dependency audit could not read four unchanged npm declarations because
  secure public-registry TLS failed on this host; the changed Python ecosystem
  passes all 329 declarations. No certificate-verification bypass or global
  package-manager configuration change was made.
- Required CI correctly identified the shared-library publication closure:
  every consumer shipping `plugin-resolve` needs a changefile even though
  the extracted supervisor is only invoked by Bridge and SSH. Extended the
  same changefile to those consumers instead of bypassing the gate or
  hand-editing versions; their runtime behavior remains unchanged.
  Synchronized the sole remaining checked-in `plugin-resolve` consumer copy
  from the verified canonical extraction; the library-copy, headless and
  publication-closure guards now pass locally.
- Addressing #7920's five blocking review findings before another review:
  harden even existing POSIX delivery directories before access; archive the
  exact unknown operation only after verified stop; match machine-only
  related entries through full registered remotes; retain the attached PID
  handoff's new timestamp; and keep the real launcher hold alive beyond the
  mux startup grace during preparation. The post-launch transfer runs after
  native creation is recorded so transfer failure still cleans up that
  session. Original-operation evidence, concurrent ownership guards and
  unrelated holds remain intact.
- Focused SSH validation passes 77 with three explicit POSIX-only skips on
  Windows, including real refresh/release, clock-controlled preparation past
  300 seconds, stop failure retention and unknown-stop-fresh-launch.
  CodeSpace and container detached consumers pass 123 and 62 respectively;
  the optional shared handoff does not change their existing lifecycle.
  POSIX mode assertions must run on Linux CI; no Windows permission result is
  presented as POSIX proof. No native inference, elevation or installed
  rollout was used. Current-head review and required CI still gate merge.
- A further current-head review added concurrent recovery and pre-recovery
  repository fencing findings (plus a duplicate of the long-preparation hold).
  Recovery now conditionally writes and clears the exact original journal
  entry under lock, preserving a newer operation or an already-cleared entry;
  deterministic interleavings cover both update and clear. The checkout probe
  checks the full expected coordinate before native resolution or recovery,
  retaining the later preparation fence. The complete SSH suite passed 658
  with 125 explicit skips before these final two fixes; their changed files
  pass the 77-case focused rerun. Bridge guards pass 351 with eight skips,
  dispatch/real-selector tests pass 57, and both contracts with all 456 frozen
  fixtures still pass without packet, provenance or protocol-floor changes.
- Review of exact remediation head `084b619656` confirmed those eight findings
  resolved, but found two further hold-transition defects. Rejoin now
  preserves a probe-confirmed live mux backing rather than replacing it with
  the caller's PID; reservation/preparation failure and pruning after the
  caller exits therefore leave the original session's forward intact. Stop
  explicitly refuses a still-preparing scope before a PID liveness token can
  replace its native mux target. Real hold refresh/pruning and stop-during-
  preparation regressions cover both paths. Current-head review and required
  CI remain prerequisites; no source slice is treated as parent completion.
- The next exact-head review marked both inline findings resolved but retained
  a launch/stop concurrency warning in its summary. Three deterministic
  public-CLI regressions reproduced the warning before any merge: stop could
  release an actively preparing scope (including attached mode), and a second
  launch could mutate that scope before the first completed. Launch/recovery
  and stop now hold one controller-local OS lock for the resolved native scope
  through cleanup or verified retirement, separate from keeper monitoring.
  Contenders fail promptly before changing ownership; unrelated scopes remain
  independent, and interactive attachment is outside the critical section.
  All 84 focused SSH cases pass with three explicit POSIX-only skips; the new
  real cross-process test proves same-scope exclusion, other-scope admission
  and lock release/reacquisition. This uses the existing lock primitive, not
  a new launcher, broker, global service or distributed-lock claim.
- A further exact-head review identified the indeterminate-probe variant:
  an existing mux hold retained under the normal unknown-liveness grace was
  not a positive live probe, so failed acquisition could still leave it
  PID-backed. Before releasing the lifecycle lock, a pre-handoff failure now
  restores the captured original mux hold and its original confirmation
  history, conditionally on the exact launcher PID and update stamp still
  owning the transition. It never overwrites a newer owner, and successful
  handoff does not roll back. Both reservation and preparation failures now
  exercise unknown probes, caller exit and 400-second pruning; the focused
  suite passes 87 with the same three explicit POSIX-only skips.
- The next exact-head review's graceful-interruption warning reproduced with
  a real SIGINT delivered immediately after worker startup: the old wait-only
  handler returned before the receiver finished. SIGINT is now deferred across
  startup and completion, the prior handler is restored, and POSIX streamed
  transports use a separate terminal session. The real signal/handler-restore
  regression and transport-isolation contract pass with all 89 focused cases
  (three POSIX-only skips). This closes graceful Ctrl+C ownership release, not
  forced termination, a dead host or unknown remote transport failure; those
  native lifecycle acceptance boundaries remain explicit.
- Requested an explicit full-PR re-review when a subsequent summary named
  budget/discovery defects without locations. That review supplied two
  actionable findings: stop now passes its explicit expected repository into
  the resolving adapter, and differently spelled native anchor paths are
  canonicalized on the target before checkout comparison. Canonical execution
  roots are retained; a genuinely different anchor still refuses. The separate
  reservation-budget observation is also addressed: its original lease now
  includes the existing 90-second checkout probe, 540-second preparation and
  both 60-second transport grace periods, rather than silently consuming the
  native launch's allowance. No execution timeout or default test cap changed.
  A clock-controlled regression spends the full preparation and supported
  native launch budgets before claiming the original reservation. All 93
  focused SSH cases pass with three explicit POSIX-only skips; unchanged
  CodeSpace/container detached consumers still pass 123/62.

### 2026-10-22 - Land privileged receiver source and continue the parent

- #7899 merged at `f850d0be2540c35046d2ea80d8066e370141cdb1` after Copilot
  approved exact head `8129431579a37c2dc791fb5757fc342448f6a149`, every required
  check passed and all six inline findings plus the surviving-host body
  finding were addressed. Credited the privileged source integration only.
- Final targeted receiver/batch/host validation passes 170 cases with four
  existing optional skips, including 51 privileged cases. Earlier expanded
  preference/rehydration and host/preference sets passed 396 and 234 cases;
  both wire contracts and all 456 frozen fixtures remain unchanged.
- The required attribution gate passed on the actual final parity head.
  The narrow infrastructure repair #7907 remains a separate merged source
  change until trusted Main publishes it and its actual failure-path execution
  is qualified; a passing old-policy check does not prove that new fallback.
- Re-read the parent Plan and Validation Plan after merge. Supported headed
  static SSH CLI is the next source-owner slice. Privileged native elevation,
  different-account access, combined target-user/repository capabilities,
  trusted-container/cross-machine lifecycle and authorized installed-version
  proof remain open. No updater, service restart or live elevation was run.

### 2026-10-22 - Integrate the actual privileged receiver in source

- Addressed #7899's first review at the actual consumer boundaries: target-settings
  new/load now return verified receiver options and selection for standard
  primary readback; model/effort inputs survive full frontend rehydration;
  static privileged metadata cannot trigger namespace-provider refresh; and
  execution subdirectories remain distinct from repository registration roots.
  The expanded managed regression set passes 396 cases, including 38 privileged
  cases with real SDK metadata and preference application plus actual
  SessionManager cold resume. Contract validation still passes both contracts
  and all 456 unchanged fixtures. Live elevation and rollout remain unqualified.
- The next review identified receiver-side prompt auto-resume's independent
  recreation fallback. Resume now disables recreation at admission for both
  the primary relay and a persisted privileged receiver binding. Actual
  BridgeAgent prompt admission through SessionManager exhausts native load
  retries without creating a conversation or changing its native ID, and a
  missing native ID refuses before spawn. The focused receiver/session suite
  passes 247 cases, including 40 privileged cases; ordinary opt-in fallback
  remains covered by existing session tests.
- The third review's body also identified surviving-host recovery, despite
  reporting no open inline threads. The receiver now retains launch evidence
  paired to the actual host/child, nonce fingerprint and native conversation,
  refreshes verified preference changes, and restores it only after matching
  authenticated reattachment. Startup and stopped-load recovery do not
  re-prepare, reload or replace the child. Focused privileged controls pass 49
  cases; the combined host/preference/privileged set passes 234 with four
  existing optional skips. Mismatched host, conversation, digest or missing
  evidence refuses explicitly. Review and publication remain pending.
- A subsequent review isolated terminal refusal to each startup/post-cutover
  batch record rather than aborting recovery of later healthy hosts. Individual
  resume remains terminal. Two-host controls cover both claim modes, report
  the refused record and prove that its host is neither pruned nor replaced;
  the focused privileged suite passes 51 cases.
- Required CI exposed an independent commit-association API failure, tracked
  in #7906. The isolated repair #7907 merged at
  `56b973ddfeb8f5754f11086cd4fa2ae11994f0fb` after current-head approval and
  green required checks. Its narrow, reported complete-census fallback retains
  metadata, membership, duplicate-head and budget/fencing safeguards (37 Node
  contracts pass). Trusted-Main publication and an actual passing parity gate
  remain separate obligations; no CI bypass or installed rollout was used.
- Published source candidate #7899 for review. Its managed rebase includes no
  intervening Bridge or shared-library changes; the qualified implementation
  is unchanged. Required CI, review, merge and subsequent publication remain
  distinct from authorized installed and native privileged acceptance.
- Completed the existing owner trace: `LocalSpawner` always starts a fresh
  Session Host under the receiver's inherited token and resolved environment.
  Preparation belongs after its own allocation/profile merge, not inside a
  replacement launcher or the primary's unprivileged filesystem assumptions.
- The source candidate uses optional version-1 ACP metadata on the existing
  authenticated elevated relay. The primary verifies controller source authority
  separately from caller affinity and qualified allocation ownership; the
  receiver retains its named-target registry and actual productive profile.
  It prepares repository settings and the complete directory set before spawn,
  binding ACP cwd to the checkout that was actually prepared.
- Required capability and matching preparation receipts fail explicitly.
  Legacy/mismatched incumbents cannot trigger ordinary resume's fresh-session
  fallback. No global protocol minimum changed. The HTTP source classification
  is implementation-only; all 456 frozen contract fixtures remain unchanged.
- The final targeted managed suite passed 544 cases across privileged context, actual
  SDK metadata/registration, local preparation, registry, serialization and
  existing ACP/session lifecycle behavior. Elevation is simulated in source
  tests, not asserted as live evidence. Review, merge and publication are still
  pending for this candidate; the Phase 2 checkbox remains open until it lands.
- Refined privileged controls pass 30 cases, including the actual `AcpClient`
  lifecycle through both SDK endpoints, preserved productive HOME/global
  settings, stopped receiver re-preparation after serialization, refusal of an
  unprepared live incumbent, and transport-error retries that never create a
  replacement conversation. Compatibility guards pass 351 cases with eight
  explicitly skipped existing live/optional lanes.
- No installed updater, shared-service restart, task registration or live
  elevation was performed. Native privileged, different-account and combined
  capability acceptance remain open alongside headed static SSH CLI and the
  other parent validation lanes.

### 2026-10-22 - Verify actual Main publication without installing it

- Pinned canonical Main `809fd1114af48d86ed710f56b6f2048053bc86f6`.
  Its release state is unpaused and records the exact #7856 merge
  `2057089fd9880843d254f3a3650f9535419f1863` as the Dev snapshot promoted at
  `2026-10-22T13:20:39Z`.
- Compared ten actual Main production blobs, covering Bridge's static SSH
  preparation/receiver/transport/selection and the shared Git preparation,
  ownership and execution helpers, against that merged source. All match
  byte-for-byte. This is published source, not an installed or running-runtime
  result. No release-control action or updater was invoked.
- The privileged lane trace confirms the current Windows relay is transparent
  ACP-over-WebSocket and resolves its default agent inside the actual receiver.
  Its `session/new` currently drops caller provenance and request overrides;
  `requires_admin` targets also remain excluded from local preparation.
  Continue tracing actual Session Host ownership before selecting the bounded
  receiver-side integration. No privileged daemon was started or modified.

### 2026-10-22 - Land static SSH ACP and continue the active parent

- #7856 merged at `2057089fd9880843d254f3a3650f9535419f1863` after Copilot
  approved the exact `114ac566713be161099d5024a6153216f645c820` head,
  required checks succeeded and all four review threads were resolved.
  The actual landed commit retains both required attribution trailers.
- Credited only the static SSH ACP source integration. The native Windows SSH
  preparation/cancellation controls and actual POSIX receiver cleanup controls
  do not establish headed SSH CLI or cross-machine native skill acceptance.
  The validation matrix, inherited simultaneous-user union, trusted-container,
  privileged and lifecycle gates remain open.
- A keep-alive completion initially aborted an attempted rebase safely while
  local PR tracking still said open. Ordinary merged-state reconciliation and
  the supported retry then advanced past the verified squash, retaining a
  backup ref. No manual rebase/reset, worktree finalization, installed update,
  shared-service restart or new worker campaign was performed.
- Re-read the canonical Plan and Validation Plan after merge. Continue with
  the privileged receiver's actual identity/filesystem and existing relay
  channel, retaining the separate supported headed-SSH CLI obligation. Source
  publication and authorized effective-running-version proof are not inferred
  from this merge. This effort remains **Active**.

### 2026-10-22 - Keep probe and engine Git authority consistent

- The next #7856 review identified inherited Git selectors redirecting the
  checkout probe, while the shared engine correctly removed them. The probe
  now removes that same selector set before querying the verified root; the
  regression supplies another checkout's Git directory and work tree and
  confirms only the actual child receives registration.
- Self-review also reproduced a coupled executable-lookup failure: quoted
  absolute and relative Windows Git PATH entries failed in the probe, then
  exposed bare-Git lookup in the shared preparation and ownership readers.
  Those readers now use the existing canonical execution lookup. Producer
  plans bind relative entries once to the actual execution checkout before
  inspecting a different, deeper source checkout.
- Native controls select the declared Git with an unavailable daemon PATH and
  refuse a missing execution Git rather than borrowing the daemon binary.
  The sole physical vendored copy remains byte-identical; the existing
  nine-consumer changefile covers this shared correction. Two synthetic
  owner/inventory fixtures now supply complete child environments, matching
  the existing contract instead of relying on accidental Windows lookup.
- Validation passes 302 shared-engine cases with one platform skip, 335
  contained ACP/CLI/sibling-worktree consumer cases with two live opt-in skips,
  351 full Bridge guards with eight explicit skips, and both actual SSH
  controls through the bounded host-state runner. The changed adapter now has
  35 cases. Packaging, vendored-source, headless and touched-Python gates pass;
  no frozen fixtures were rewritten.
- No new API, launcher, environment overlay, credential flow or installed
  runtime change was introduced. The parent remains **Active**, and this
  correction still awaits the current source PR's review and merge.

### 2026-10-22 - Keep Windows deadlines independent and distinguish execution cwd

- #7856's next review found that a Windows worker surviving its supervisor
  retained its own Job handle but lost the supervisor's deadline enforcement.
  The worker now starts an independent deadline before executing preparation;
  expiry terminates its entire Job, even if the supervisor is gone.
- The exact regression keeps the worker and its mutating child active past a
  one-second deadline, rather than making the worker voluntarily exit. The
  published prior source fails the heartbeat-stability control; corrected
  source passes, before the child's six-second natural completion. Probe
  writers and temporary files were cleaned up.
- The same review identified valid nested execution directories being compared
  against a repository-root registration context. The receiver now verifies
  the Git root separately, validates the raw compiled context against that
  root, and retains the productive execution cwd with explicit repository-root
  metadata. A native child-marketplace and launch-order regression exercises a
  nested directory; an absent root proof fails explicitly.
- The adapter suite passes all 32 cases, including the real SSH controls.
  These corrections remain in the current source PR; no running-release,
  privileged or complete parent-effort result is implied.

### 2026-10-22 - Correct session-qualified controller and early-exit cleanup

- #7856's first review identified two real gaps. The shared venue planner now
  forwards the controller session identity recorded by the existing launch
  owner, preserving matching session-qualified owner references while still
  refusing a mismatched session.
- A real POSIX control reproduced the published candidate's descendant leak:
  success (exit 0) and error (exit 3) returned while a redirected child still
  wrote; only the deadline control quiesced. The corrected receiver retains an
  owned POSIX group keeper until complete-group cleanup on every outcome.
  A broken report pipe or lost supervisor also reaches the keeper's cleanup.
- Current native POSIX controls confirm quiescence for success, error, deadline
  and supervisor loss. Windows/selection coverage passes 149 cases, including
  actual SSH controls and the matching/mismatched controller regressions.
  The existing retained test venue was used only for bounded stdlib probes,
  with no agent, provisioning, credential relay or runtime update, then stopped
  and preserved. Temporary remote probes were removed.
- These are corrections to the current source PR, not parent completion or
  running-release proof. Privileged, headed CLI, native combined-capability and
  authorized-rollout gates remain open.

### 2026-10-22 - Integrate the existing static SSH ACP preparation boundary

- Exposed the shared stdlib Python bootstrap separately from its unchanged
  POSIX wrapper and reused the existing controller/provider selection planner.
  Static SSH ACP now resolves the actual remote worktree before registration,
  even for a stored id/cwd, and replaces only its automatic plugin argument
  span. The supported project/direct launch owner, profile/account, auth hooks,
  explicit arguments and productive preferences stay unchanged.
- Receiver subprocess controls exercise real fresh Git checkouts, current
  controller payload reflection, child-native enable/disable/missing authority,
  large stdin payloads, Unicode/spaced paths, Gitless legacy behavior and
  failure-before-stdio ordering. No persisted constructor field or remote
  launcher was introduced; existing old-reader metadata remains readable.
- An actual Windows SSH control exposed a real cancellation leak: terminating
  the local client did not stop a mutating remote descendant. The corrected
  adapter retains the bounded channel until it settles. Its streamed stdlib
  supervisor contains worker descendants in a kill-on-close Windows Job or
  POSIX process group and enforces a receiver deadline before the outer
  transport timeout. Real SSH cancellation and receiver-deadline controls
  confirm the synthetic descendant stops; the failed probe was bounded and
  exited before this continuation.
- Validation: 296 shared-library tests; 260 focused transport/profile/rollback
  contracts; 295 managed, contained preparation/consumer tests; and 24 adapter
  tests including the two opt-in real SSH controls. Full managed Bridge guards
  pass 351 cases with eight explicit skips and no fixture rewrites. One
  shared-library platform skip and two ordinary opt-in skips are explicit.
  Install-contract (13 plugins), headless, asyncio-timeout, vendored-library
  and touched-Python checks pass. Publication remains to be completed.
- The live SSH control shares the test runner's filesystem; it is not a
  cross-machine native-skill or headed CLI result. Two declared POSIX profiles
  were unavailable at hostname resolution, with no profile/runtime repair or
  alternate auth introduced. Privileged, headed SSH CLI, trusted-container,
  combined target-user/staged-repository and authorized-rollout gates remain
  open. The effort stays **Active**.

### 2026-10-22 - Land the sibling-worktree atlas and refresh publication facts

- #7841 merged as `124aae4fe46f0859b5de60ad2fe9b134f4431e3a` after the
  current-head Copilot approval, successful checks and zero review threads.
  The HTML matrix and diagrams were also rendered in a separate headless
  browser profile; no productive browser profile or shared service changed.
- Ordinary managed status reconciliation followed by sync advances the source
  worktree after a deleted-PR-ref refusal. No manual rebase/reset, raw merge,
  installed update or worktree finalization was used.
- A fresh read of canonical Main state reports `paused: false`, with the
  `9c70ad31b355de63ea76e42c367bb0cbd3670974` snapshot promoted at
  `2026-10-22T09:25:02Z`. Earlier parity source slices through #7534 are in
  that Dev snapshot, and selected production/fixture-maintenance bytes compare
  equal on actual Main. Publication is no longer described as paused; no
  runtime rollout or effective running-version proof is inferred.
- Continue the existing genuine static-SSH lane. Its actual preparation seam
  is after the shared SSH connection and concrete remote-worktree resolution,
  before the current project-binstub/direct ACP command opens. Static SSH is
  not a namespace-provider CLI, and it does not yet use a survivable SSH
  Session Host. Keep its execution identity, native owner and transport; do
  not add a competing launcher or assume the privileged lane shares its home.
- The complete simultaneous target-user/staged-repository acceptance inherited
  from #1146 is now explicitly pending in the Validation Plan. #5286 and #5072
  remain open, and this effort remains Active.

### 2026-10-22 - Prove sibling-worktree authority and map projection relationships

- Captured the same-repository child clarification and HTML relationship-map
  request without changing plugin eligibility or source-kind policy. The
  existing target-native precedence path already resolves the child's own
  declared marketplace; no runtime rewrite or wire/fixture rebaseline is needed.
- Added nine regressions using real linked Git worktrees at different committed
  revisions. ACP launch composition, CLI acquisition preparation and copy-mode
  preparation all retain child-local payloads, observe child edits on repeat,
  preserve global preferences, honor child disablement and refuse an enabled
  missing child payload instead of substituting the parent's revision.
- The focused contained projection/acquisition/venue selection passes 193
  cases. An independent subprocess reads the actual child settings and marker
  bytes; it does not merely assert constructed argument strings.
- Added a self-contained HTML atlas with a twelve-row relationship matrix,
  role boundaries, sibling-worktree diagram and authority-to-readiness flow.
  Both accessible SVGs, local links and unique IDs validate without a renderer,
  external asset or script. README and worked examples link the atlas.
- This is source-consumer regression and documentation, not a new live
  Copilot/SSH/privileged/container acceptance claim. Remaining venue,
  simultaneous-user-union, release and authorized-rollout gates stay open.
- The full guard pass also found a real current authority-digest change after
  #7254 modified `agent-procutil`. Reconstructing all authority inputs from the
  preceding capture and current committed sources isolates that one module.
  The existing capture tool refreshed only three current vectors and their
  registry provenance against public source
  `2d42fad24d9724d677bc3eaea3497bf6b4ab6497`; the other 453 registered fixtures
  remain untouched. Strict attestation assertions are unchanged. All 456
  registry fixtures validate; the full Bridge guard suite passes 351 cases
  with eight explicit platform/tier skips.

### 2026-10-21 - Bound fixture maintenance to the changed flow

- The bounded follow-up to #1164 aligns HTTP evidence tests with the existing
  canonical registry policy. Removed duplicate assertions that required every
  current packet to match today's source bytes and every re-attestation to
  share one capture commit. The checker and its schema remain unchanged.
- Added a permanent isolated-copy regression using the real corpus and actual
  checker/HTTP consumer tests. An implementation-only source comment plus one
  explicit registry update passes with all 406 fixture files byte-identical.
  The previous extra source-equality assertion fails on that same case.
- Added checker cases for explicit non-semantic changes, missing rationale,
  stale fingerprints, a selectively refreshed semantic capture with untouched
  peers, and corruption despite a non-semantic classification. The maintenance
  correction changes no production wire version, capability floor or runtime.
- Full guards also detected the real authority-component digest change from
  already-merged #7455. The capture-time digest was reproduced from its exact
  old sources and the new value traced to `agent-procutil`. The existing checked
  capture path refreshes only three current authority vectors and registry
  provenance; unrelated packets and historical authority captures stay frozen.
- This closes the requested maintenance constraint, not the remaining venue,
  release or authorized-rollout gates.

### 2026-10-21 - Merge the source-preserving local and startup-readiness slice

- #7185 merged into `dev` as
  `edf04012ec694f8bac76b130b52d2b65c62b1c61`. The final full review reported no
  open issues and confirmed the rollback finding resolved; all required CI
  passed and all 16 review threads were resolved before merge.
- Closed the implemented source-preservation, static-local ACP, local CLI/ACP
  acquisition, owned startup-admission and locality-preservation Plan items.
  Closed their source-example, repository-isolation and startup-failure/retry
  validation items using the previously recorded contained and native evidence.
  This does not close the combined static-local/SSH/privileged acceptance item:
  local evidence exists, but the other lanes remain pending.
- The effort remains **Active**. Genuine remote static-registry SSH, privileged
  and wrapped/command/sub-daemon contexts, receiver-local integration, live
  trusted-container and ambiguous external-lifecycle acceptance remain open.
  Publication to `main` and authorized running-version rollout remain gated
  by #6237; no release promotion, shared update or deployment was performed.
- The canonical tracking remains #5286, #5072 and parent #954. Source merge
  closes this implementation slice, not those broader issues or the effort.

### 2026-10-20 - Preserve rollback hydration and quoted Windows executable lookup

- A newer full review on the same head invalidated the earlier clean verdict
  before any merge was attempted. Its rollback finding reproduced: two new
  constructor keys in persisted targets made the preceding strict reader fail.
  Preparation provenance now lives inside existing extensible request-override
  metadata. New readers recover it after an old reader hydrates and re-saves
  the target; ephemeral prepared loadouts and requests remain unpersisted.
- A frozen, hash-checked copy of the preceding target reader exercises real
  SQLite session hydration and rollback/forward round trips. Ordinary targets
  retain the preceding serialized shape, provider classification stays unchanged,
  and preparation metadata survives without overwriting unrelated overrides.
  Session/acquisition/snapshot coverage passes 256 cases; local launch,
  transport and compatibility coverage passes 172.
- Both executable helpers now honor paired quotes around Windows PATH entries
  before anchoring relative entries to the real execution checkout. Tests run
  an actual selected batch command from quoted relative and absolute directories
  containing spaces, preserve the productive environment, and retain literal
  quote characters on POSIX. The full contained shared suite passes 284 cases
  with one explicit platform skip; the shipped vendored helper remains identical.
  The focused lookup selection also passes 14 cases with one platform skip,
  including real installation through both direct and emitted consumers with
  quoted Windows PATH entries.
- Authenticated native Codespace revalidation passes on committed source
  `9d4a450898081e9b0f78cb3b716955d55cfe9212`: one selected-skill task, identical
  retry message 3, unchanged productive preferences and verified owned cleanup.
  The venue was recovered and stopped without deletion or shared runtime updates.
- The supported HTTP range and capability floors are unchanged. Current-head
  native acceptance, review, CI and merge remain required; no previously pinned
  merge permission transfers to the amended head.

### 2026-10-20 - Preserve published remote ACP ownership and advance readiness to 44

- A further upstream merge published managed remote ACP ownership at HTTP 43
  during review. Replayed the owned slice onto the new base, preserving that
  capability at 43, attention boundaries at 42, direct targets at 41 and the
  supported minimum at 1. Only readiness-bound task/mode delivery requires 44;
  ordinary older operations retain their sliding-range compatibility.
- Preserved all 344 published HTTP fixtures byte-for-byte and all upstream
  ownership assertions. Re-attested 45 independent generation-43 expectations
  and eight readiness vectors at 44. All 406 registered fixtures and the three
  authority vectors reproduced from committed source pass.
- Full Bridge guards pass 337 cases with eight explicit platform/tier skips.
  The protocol, registry, target, ownership, provider and reconciliation
  selection passes 224 cases, including old-daemon capability refusal before
  submission and current-reader tolerance of older ordinary packets.
- Authenticated native Codespace acceptance passes against committed source
  `25a5685be6ab272691609bde32c4209eb4b1649d` and the real HTTP-44 boundary.
  Exactly one task invoked the selected skill; retry retained message 3.
  Productive preferences stayed unchanged and the owned mux, authentication
  scope, forward and HTTP fixture were cleaned up. This remains source-only
  acceptance, not release promotion or shared runtime rollout.
- The integrated head still owes fresh review, required CI and authorized
  role-scoped merge. Earlier one-head merge consent does not transfer to it.

### 2026-10-20 - Retain newly published attention boundaries and advance readiness to 43

- While the reconciled head was in review, #7272 published attention boundaries
  at HTTP 42. Replayed this owned branch again, retaining direct remote ACP
  targets at 41, attention boundaries at 42 and the supported minimum at 1.
  Readiness alone now requires 43; ordinary receipt delivery through 42 remains
  supported. Preserved all 299 upstream HTTP fixtures and deferred-reference
  metadata, and re-attested 50 current vectors without rewriting history.
- The integrated protocol, target, attention and admission selection passes 281
  tests. Full Bridge guards pass 299 with eight explicit platform/tier skips;
  all 358 immutable fixtures and the reproduced authority vectors pass.
- Authenticated native Codespace acceptance passes against committed runtime
  `a2b7b5c976e2bfd474150c50243678916b7b8517` and the real HTTP-43 boundary.
  Exactly one SDK task invoked the selected skill and retry retained message 3.
  Existing auth, mode-0700 scope, unchanged productive settings and owned
  process/forward cleanup retain their verified behavior. The final publication
  still owes current-head review, CI and role-scoped merge authority.

### 2026-10-20 - Reconcile the upstream protocol generation without narrowing compatibility

- New `dev` changes assigned HTTP generation 41 to direct remote ACP targets
  while this open slice independently used 41 for plugin readiness. Replayed
  the owned commits onto the new base and assigned readiness generation 42,
  retaining remote ACP targeting at 41 and the minimum-supported generation
  at 1. The supported range and per-capability floors remain distinct.
- Preserved every upstream immutable HTTP fixture, including the generation-41
  targets and exact-workspace-owner vectors. Integrated generation-42 evidence
  retains the independently frozen expectations and older capability floors;
  the new readiness vectors continue to exercise real HTTP/SQLite admission.
- Focused contained integration passes 244 cases, including ordinary receipt
  delivery to generation-39/40/41 peers, older ordinary packets accepted without
  readiness gating, readiness refusal before dispatch to generation-40/41
  peers, and remote ACP target negotiation through generation 42.
- Broad guard collection exposed this slice's standard-library `TypedDict`
  inside the newly integrated Pydantic session-snapshot graph on Python 3.11.
  Used the explicitly declared `typing-extensions` form without changing the
  dictionary shape and added a strict typed-target JSON round-trip regression.
  The full Bridge guards pass 295 cases (eight explicit platform/tier skips);
  focused launch, transport, typed-target and readiness coverage passes 174.
- Repeated the authenticated Codespace consumer against committed source
  `4d432a57f939efb9c49a5ed1969f6f5c98a13120`, including the real HTTP-42
  client/server boundary and source extension. Exactly one admitted task invoked
  the selected skill; retry returned message 3. Mode-0700 auth scope, one-time
  credential consumption, mux/forward/server cleanup and unchanged productive
  settings all passed. Recovered the session and stopped the preserved venue.
  Current-head review and required CI still gate merge; the earlier one-time
  provider merge consent was pinned to the old head, not this replacement.

### 2026-10-19 - Validate detached source startup with existing inference authentication

- A source-bound Codespace consumer exercised canonical emitted preparation,
  the detached owned-command builder, real Worktrees `embody`, the full Bridge
  extension, and HTTP-41/SQLite/client/CLI admission. A no-skill local selection
  and a non-default Git revision with a nested plugin location registered at
  their expected versions before one SDK-delivered task successfully invoked
  the selected skill. A missing selection was refused before admission;
  identical retries returned the same message without another task.
- Reused the existing clean-room host-token borrowing strategy for the native
  selected identity and the existing process-only Copilot launcher. The
  credential travelled over stdin into guarded tmpfs and was consumed once by
  native Copilot with the documented `--secret-env-vars` isolation enabled. No interactive
  login, global activation, productive-profile switch or shared runtime
  deployment was needed. Productive configuration and settings stayed
  byte-identical; the native mux, authentication scope, forward and source
  HTTP fixture were cleaned up.
- The live lane exposed an existing launch-directory defect: inherited default
  ACLs can override umask and widen a plain `mkdir`. Executable scopes now
  specify mode 0700 at creation. A real POSIX default-ACL regression covers the
  source shell command, and the live consumer confirmed mode 0700 and guarded
  cleanup. The contained Windows process-auth file passes 35 tests with its
  POSIX-only ACL case skipped; final Linux CI still gates merge.
- This is composed source-consumer evidence with the existing completed native
  bootstrap runtime as a read-only dependency, not fresh-machine provisioning,
  a production Connection Owner rollout or a live ambiguous-admission recovery
  proof. Those remaining validation and release obligations stay open in
  Phase 2/3; this journal is not an effort-completion claim.

### 2026-10-19 - Bind executable lookup to the real execution context

- Full review found eager native executable lookup using the daemon's cwd for
  relative `PATH` entries, and an outdated README exclusion for local CLI
  acquisition. The same lookup context also matters when preparation moves a
  relative installer command into its intentionally neutral acquisition cwd.
- Bound lookup to the final execution directory and reused a stdlib-only shared
  helper for the direct owner and preparation, including emitted receivers.
  Preparation anchors a copy of `PATH` before Git/installer work, retains the
  original execution directory across repository-root discovery, and resolves a
  required installer lazily. Productive environment, process cwd, explicit
  command precedence and opaque runtime wrappers remain unchanged.
- Real contained subprocess regressions cover relative, empty and dot entries,
  direct/environment/managed relative installers, and the emitted receiver from
  a subdirectory. The local/transport selection passes 141 tests and the entire
  shared preparation suite passes 282, including unchanged private preferences.
  The dependent customization suite passes 527 tests with the existing private
  Git validation policy; no machine-global Git configuration is changed.
  The README now distinguishes supported ordinary local CLI acquisition from
  remaining excluded contexts. New-head review and CI still gate merge.

### 2026-10-19 - Fix the actual merged-hook inventory CI gate

- Current-head full review returned no open issues, but runtime CI exposed a
  customization-inventory regression when the PR's merge tree included #7160's
  native bootstrap-admission wrapper. The scanner did not recognize that
  output-free wrapper and misclassified the suite's session-start stack.
- Extended only the static scanner's standard-wrapper proof: capture selector
  output, require the bounded empty-output wrapper structure and recursively
  verify its legacy delegate. No adoption hook, installation policy, runtime,
  test expectation or CI requirement was changed or bypassed.
- Both wrapper regressions failed before the fix. The full customization suite
  then passed 527 tests in isolated Python 3.12 state, using test-private Git LF
  settings with safe-CRLF checks retained. A separate static replay of the actual
  committed hook/writer sources classified them output-free without executing
  admission, installers or any service operation. New-head review and CI remain
  required before merge.

### 2026-10-19 - Replay frozen readiness packets at the public boundary

- Full review confirmed the three recovery corrections resolved, then found
  the same public-request error in eight readiness fixtures: they supplied the
  reserved internal startup kind instead of a public queued prompt.
- Corrected every affected packet and its numeric control binding, deterministic
  selection digest and admission receipt. Added one parameterized real
  HTTP-router/SQLite/control-ack regression over all eight fixtures; it proves
  reserved incoming kinds still fail, readiness precedes task admission, the
  generated inbox packet matches exactly, identical retries keep one message,
  and changed selection is refused.
- The regression reproduced HTTP 400 before the packet correction. The combined
  wire/readiness/native-golden selection now passes all 89 tests, and immutable
  source/fixture evidence checks pass. This replaces the insufficient
  model-parsing-only check for public-request replayability. Fresh current-head
  review and required CI still gate merge.

### 2026-10-19 - Current-head full-review recovery corrections

- The first full review of the combined startup candidate found three further
  recovery defects: confirmed admission could be lost on settlement retry,
  unknown container delivery could replay before restoring a dead forward,
  and CodeSpace tenant expiry could erase its pending journal. These findings
  block merge; the earlier named local-review corrections were not a substitute
  for the full-PR review.
- The follow-up retains confirmed admission and retries only receipt settlement,
  persists CodeSpace workflow journals independently of connection leases, and
  restores/probes the retained native forward before unknown delivery replay.
  A bounded, per-attempt CodeSpace recovery connection does not renew the
  original session lease or launch another mux.
- Focused contained suites pass 189 CodeSpace, 62 container and 115 shared venue
  contracts, including expired-tenant migration, failed keeper replacement,
  failed probes and settlement after native-session exit. Current-head review
  and required CI remain pending; no installed rollout is claimed.
- Actual CI also exposed strict unknown-field rejection in the three new wire
  models and a container test that patched the internal release alias but still
  invoked the host CLI through the public alias. The follow-up preserves typed
  ownership validation while tolerating additive peer fields, and makes the
  test's release boundary hermetic. The CI-equivalent wire/readiness selection
  passes all 80 tests; the corrected container selection passes all 62.

### 2026-10-19 - Admission ambiguity correction and contract evidence

- Admission now distinguishes admitted, refused and unknown outcomes. An
  identical operation retries against its original native session and input;
  unknown admission retains the session, connection ownership and recovery
  journal instead of stopping a session that may already be executing.
- Pending container delivery journals survive replacement of disposable keeper
  state, including replacement spawn failure and another worktree's release.
  Creation receipts settle only after confirmed admission. Independent review
  verified both named safety findings resolved; the coordinator independently
  reran the 58 detached-container tests.
- Protocol-41 evidence retains historical packets and capability floors, adds
  typed readiness/task/mode and legacy-omission vectors, and records actual
  committed source blobs. Focused contract/readiness consumers and the registry
  checker's full regression suite passed. Extracted cohesive startup helpers
  without widening module limits; the integrated Bridge guard suite passed
  under the same Python generation used by CI. Publication, current-head
  review, required CI and external-venue acceptance remain open.

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
