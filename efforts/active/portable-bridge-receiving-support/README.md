# Portable Bridge Receiving Support

- **Slug:** `portable-bridge-receiving-support`
- **Repo:** copilot-extensions
- **Branch(es):** serial, independently reviewed functional slices targeting `dev`
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [agent-bridge](../../../visions/plugins/agent-bridge/README.md#host-selected-receiving-support) - portable receiving support; [cli-default-bridging](../../../visions/cli-default-bridging/README.md#standalone-remote-driver-and-scoped-managed-delivery) - independent driving capability
- **Umbrella issue:** #10636

## Guiding Intent

An otherwise supported Bridge-managed Copilot launch must work when the
receiving venue's full coordination suite is absent, old or independently
updated. Bridge brings the minimal compatible support for its selected mode.
Repository hosting policy, workspace identity, authenticated transport and
exclusive session custody remain authoritative at the receiving boundary.

This is composition of existing owners, not another launcher or a switch from
ACP to CLI mode by default.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Portable-support driver | Canonical plan, receiving-support composition, tests and serial PRs | #10636 and this effort |
| Existing launch owners | Retain canonical Manager mux/pane assets and installation-independent lifecycle | #8669 and #5579 |
| Existing driver and hosting owners | Retain mutation generations, host recovery and session custody | #5930, #3194 and #6467 |

## Coordination

One owner drives the functional sequence in one source worktree. Publish the
first complete ACP flow as soon as its gates pass, then extend the existing
muxed flow; neither partial delivery closes this effort.

Consume canonical launcher assets rather than fork their source. Coordinate
actual overlapping edits with their existing owners before changing those
paths. This effort does not assume their custody or resume their retained
attempts. No parallel daemon, allocator, installer or control protocol.

## Context

- Bridge's provider preflight invokes guest Worktrees for `agent-policy`, and
  for selected managed-worktree resolution. Its portable Session Host receiver
  also delegates policy to installed guest Worktrees. These are separate
  consumers; repairing only preflight would leave launch enforcement broken.
- `libs/repository-policy` already transports the canonical resolver, portable
  PyYAML source and license notices for a fresh read without site packages.
  Its current guest probe is explicitly preflight-only, refuses an explicit
  installation context, and is not fallback permission after an incompatible
  installed receiver. Review an explicit receiving contract amendment first.
- The Session Host already ships the selected host-role closure over existing
  venue transports. The standalone remote-driver already supplies extension
  driving without Bridge or Worktrees installed in the guest.
- Manager owns the canonical mux launch and copied pane-shell assets;
  Worktrees consumes that boundary. #8669 retains installation-independent
  lifecycle and #5579 launch-depth reduction. Packaging should compose these
  assets, not reproduce their terminal, seed, exit and process-ownership logic.
- #10622 and its [completed source effort](../../2026/11/05%20responsive-codespace-admission/README.md)
  provide responsive claim/relay operations and cancellation custody. They do
  not remove guest-suite requirements or qualify installed receiving support.

## Request

Portable source direction:

> We've reduced the complexity around the mux-session launcher to be pretty compact. If necessary we could merge it with agent-remote-driver to be a small "deployable" which registers the plugin-wrapped extension, sets up TMux, and has just the necessary pieces to create mux-wrapped copilot runs which are compatible with launching by agent-worktrees and worktee-manager. But this becomes agent-bridge's "foreward-deployable" tool to drive Copilot in any location, whether our full suite is present, updated, or otherwise.

The bounded acceptance is recorded in #10636. Combining packages is conditional
on useful simplification, not a required rewrite. Preserve standalone use,
existing launch owners and the chosen session mode.

The phased sequence and detailed validation controls below are
**agent-recommended implementation refinements**, not additional deployment
authority.

## Scope Approvals

Source investigation, reviewed implementation and ordinary issue/PR publication,
with caller-contained synthetic process and transport fixtures. A reviewed
plan does not authorize shared rollout, daemon restart, guest full-suite
installation, global plugin-setting rewrites, credential inspection/export,
forced claims or replacement of retained sessions. Native/external execution
requires its own applicable authority and is recorded separately.

## Plan

### Phase 1 - Reviewed contract and composition boundary
- [x] Reserve the public issue, confirm compatible effort adoption and deduplicate existing owners.
- [x] Trace both guest-policy consumers and identify the canonical policy, Session Host, remote-driver and Manager launch assets.
- [x] Review the standing-intent clarification and this plan before implementation.
- [x] Inventory supported local, SSH, Codespace and container receiving paths in ACP and muxed modes; reuse their owners rather than add a venue runtime or transport.
- [x] Specify the portable receiver's selected marketplace/context and compatibility boundary without weakening negative policy or adding a mandatory guest-owner RPC.

### Phase 2 - Complete ACP flow without a guest suite
- [x] Deliver canonical policy support through the existing Bridge transport/bundle, independent of absent, old or current guest Worktrees/Bridge installations.
- [x] Check the actual execution checkout and selected overlays freshly at preflight and launch/resume/successor boundaries; a preflight answer is not a launch grant.
- [x] Preserve managed workspace identity through its existing allocation/resolution owner. Do not reinterpret a worktree ID as a path or replace allocation with raw Git operations; distinguish a genuinely unavailable managed operation from ordinary provider-workspace launch.
- [x] Separate no-wake inspection from an authorized activating launch through the venue owner; preserve claim, relay readiness and original-attempt cancellation.
- [x] Prove the complete ACP consumer flow, publish it through required gates and steward its PR to merge before extending it.

### Phase 3 - Complete the existing muxed flow
- [x] Stage the same standalone remote-driver plugin at session creation and compose canonical mux/pane launch assets with only their required support closure. _(source composition merged in #10748; native scoped loading remains in Validation Plan)_
- [ ] Supply required TMux/native-mux setup through its existing owner, scoped to the selected launch. Preserve installed guest state and unrelated sessions; unavailable genuine prerequisites remain explicit, not a request for the full suite.
- [ ] Preserve Worktrees/Manager launch ownership, literal arguments, seed delivery, native process/terminal lifetime, exit handling and driver generation/exclusive mutation semantics.
- [x] Consolidate packaging only where it removes duplication. Preserve independent remote-driver use, ACP behavior and explicit mode selection. _(source composition and compatibility controls merged in #10748)_
- [ ] Prove the complete muxed consumer flow, publish it and steward its PR through merge. _(joined source flow and publication complete in #10748; native/installed consumer acceptance remains open)_

### Phase 4 - Compatibility and closeout
- [x] Document supported caller/receiver combinations, retained-host recovery and rollback using actual selected capabilities rather than package-version equality. _(source contracts and controlled combinations documented in #10748; native/installed tiers remain unqualified)_
- [x] Update reality docs, focused tests and changefiles; retain historical protocol evidence and the required unit-only portfolio. _(#10748; source tiers only)_
- [ ] Record source, release and installed/native/external results separately; resolve every Plan and Validation Plan item before Done.
- [ ] Qualify the prerequisite host account/owner callbacks through their existing launchers before retrying the external receiving flow. _(newly observed acceptance prerequisite; agent-recommended source refinement)_
- [ ] Bound the Vault prerequisite's outgoing credential-writer transition through its existing lifecycle owner after incoming discovery publication; preserve cooperative connections during grace and use native identity/owner-checked cold retirement when handoff cannot settle. _(#11057; closes the existing authoritative-runtime-replacement intent, not a new watchdog or live recovery grant)_

## Validation Plan

- [ ] Real isolated receiving processes exercise sparse, old-suite and current-suite guests; guest suite commands can be unavailable or incompatible without becoming the selected Bridge receiver.
- [ ] Allowed, denied, malformed and unreadable repository policy, canonical/legacy paths and selected-marketplace overlay denials use the same canonical evaluator.
- [ ] A policy change after successful preflight refuses actual launch; resume/successor checks retain the actual execution checkout and context, with no silent legacy-context downgrade.
- [ ] Ordinary provider workspaces and managed worktree identities remain distinct; unknown/mismatched allocation refuses before creating another workspace or session.
- [ ] No-wake reads leave stopped fixtures untouched; explicitly admitted activation reaches only its selected fixture through the venue owner.
- [ ] ACP and muxed flows each prove launch, observation, one admitted send and reconnection to the same conversation; no effectful probe-and-fallback replay.
- [ ] Slow/cancelled preparation, failed delivery, unconfirmed cleanup and rollback retain original resources and pending effects. Cleanup cannot remove a newer receiver or driver generation.
- [ ] Scoped extension loading, real native mux consumption where available, human takeover and exclusive control retain existing semantics; fake-SDK controls are not native acceptance.
- [ ] New/prior caller and receiver combinations, provider rollback and missing capability produce a supported result or a specific pre-effect refusal, not blanket guest-suite upgrades.
- [ ] Focused unit/real local consumer and fresh-environment controls, required source gates, review and CI pass. Record unavailable native/external tiers honestly and obtain effect authority before any live qualification.
- [ ] For #11057, prove new-endpoint discovery with an existing predecessor connection retained during grace, cooperative and hung retirement, single credential-writer exclusion, native birth/role/installation and reselected-successor refusal, and truthful failure when retirement or incoming readiness cannot be established.

## Proposal

Extend the host-owned support already shipped by Bridge. The portable receiver
is a deliberate execution owner, not fallback after an installed owner's
failure. It carries canonical enforcement code, not cached permission, and
qualifies its selected context against the actual receiving checkout.

Keep the current legacy probe's meaning until the new receiving contract is
reviewed and implemented. Prefer one shared canonical evaluator and existing
bundle/transport lifecycle. For CLI mode, stage the existing plugin and launch
assets rather than requiring a full guest Manager or inventing a second mux
implementation. The exact support closure follows the consumer trace and tests.

## Journal

### 2026-11-05 - Reviewed-intent preparation

- #10622 and its archive #10672 are merged. This proposal is the next finite
  source objective, not installed acceptance of those changes.
- Confirmed both guest Worktrees requirements and the probe's narrower current
  contract. Preserved hosting refusal instead of substituting a weaker reader.
- Compared Request, Guiding Intent and Plan: retained conditional packaging,
  launch-owner compatibility and guest-suite independence without adding a
  default-mode change or deployment grant. Implementation awaits review.

### 2026-11-05 - Reviewed plan accepted

- #10676 received approval with no active findings, passed required CI and
  merged as `f4929d2f2f22d60a6c05431e1c897b3e3973afbb`.
- Re-read the complete effort after pull-forward. Continue Phase 1's supported
  receiving-path inventory and context/compatibility contract before the first
  ACP implementation slice. Source and installed acceptance remain separate.

### 2026-11-05 - Portable policy receiving implementation

- The receiving-path inventory and version-1 selected-context contract are in
  the [hosting reference](../../../plugins/agent-bridge/docs/repository-hosting.md#host-selected-portable-policy-contract).
  Local, remote managed-workspace and SSH ownership remain distinct.
- Implemented the two separate policy consumers using the existing canonical
  archive and host bundle. No guest-suite command selects portable policy;
  unbound prior callers retain their original enforcing adapter.
- Initial controls passed: 87 focused receiver cases, then 223 host/container
  cases with five platform-specific skips. The wider activation packet found
  one expected stopped-refresh call-shape assertion; corrected it and retained
  the real activation/no-wake controls.
- Complete bundled ACP-peer, final source gates and review are still in
  progress. These source-fixture results are not release, installed, native
  Copilot, muxed or external-venue acceptance.

### 2026-11-05 - Actual provider composition

- The real CodeSpace child declaration retained a guest-owner shell guard;
  trusted-container preparation independently checked the guest owner before
  projecting credentials. Addressed both consumers rather than treating the
  unwrapped bundle fixture as a complete provider flow.
- Added provider-first version-1 handoff declarations. Old callers retain the
  guarded commands. New callers select only advertised receiving support;
  they do not strip shell text, probe/replay an effectful command, or shadow
  the installed Worktrees executable.
- The explicit canonical adapter now belongs to `repository_policy.receiving`.
  Container preparation uses its bounded text transport and pinned
  instance/user before credential projection. Bridge archive construction and
  selected-context reads stay off the serving event loop.
- The shared policy packet passed 41 cases, the expanded Bridge packet passed
  242 with one platform skip, and CodeSpace provider compatibility passed 45.
  Pure context units pass seven cases. Final container process/parser controls,
  source gates and review remain open; the source effort and umbrella issue
  stay Active.

### 2026-11-05 - ACP source candidate validated

- Corrected the activation consumer against the real provider contract:
  `ensure_ready` does not wake a CodeSpace. Only a typed stopped-venue result
  and the advertised `activation: start` handoff permit the admitted launch
  to call its existing provider owner. An available repository denial is
  separately tested to refuse without readiness or activation.
- Prior providers keep their guest-owner preflight as well as their guarded
  child command. A missing/incompatible owner refuses before preparation;
  no failed installed-owner invocation selects the portable receiver.
- Final changed-boundary packet: 127 Bridge admission/receiver cases, 45
  CodeSpace provider cases and 41 canonical policy cases pass. The real Host/
  provider packet passed 242 with one platform skip; container preparation
  passed 53 with one platform skip, plus the actual CLI parser control.
- Seven pure context units pass. Ruff, install contract, documentation,
  headless launch and current-source contract evidence pass. Preparing the
  declared checker environments exposed two new library typing issues;
  corrected both and passed the library on Linux/Windows targets. The other
  39 admitted projects passed their unchanged scopes.
- These are isolated source results, including the shipped Host with a mock
  ACP peer, not native Copilot or installed/external acceptance. Publish and
  merge this ACP slice next; mux composition, compatibility closeout and the
  umbrella issue remain open. No runtime rollout or guest upgrade.

### 2026-11-05 - ACP review and admission corrections

- Opened #10692 for the ACP slice, without closing the umbrella issue. Required
  CI rejected three changed source bindings that had not been re-admitted.
  Corrected those bindings and positively admitted the seven pure context
  cases with explicit parameter IDs, their source closure and coverage targets.
  The complete Windows-native unit packet passed 4,146 cases in 255.031 seconds;
  its distinct profile is local qualification, not PR-CI or promotion evidence.
- The first review identified three consumer gaps: the advertised provider
  `start` command was missing, runtime-proxy dropped stdin, and initial resume
  admission refused a stopped CodeSpace before it could reach activation.
  Added provider-owned activation, base64 forwarding through the actual proxy
  and typed initial resume activation under the original stop fence.
- Added real parser/dispatch, proxy/driver/isolated-reader and resume-level
  regressions rather than only testing successful mocked provider commands.
  Validation and renewed review are in progress. This PR is not merged or
  deployed; ACP publication, mux composition and the remaining acceptance
  controls stay open.
- Review-fix validation passes: 91 CodeSpace parser/provider cases, 107
  container cases with nine platform-specific skips, and the 136 unchanged
  Bridge cases plus all six new stopped-resume controls. The latter use the
  real namespace parser and initial resume entry, retaining the same durable
  conversation. Corrected stale test doubles and two Windows-incompatible
  fixtures encountered by these packets instead of skipping their failures.
- Updated only the changed current `session_resume.py` contract binding and
  its implementation-only rationale; historical captures and provenance are
  unchanged. Final committed-source guards, unit admission and renewed
  review/CI remain required.

### 2026-11-05 - Activation custody and account review corrections

- The reconciled ACP head passed required CI. Subsequent reviews identified
  activation cancellation/drain races, discarded current receiving support on
  legacy resume and a merged-list account collision in provider activation.
- Activation now settles its original bounded provider invocation before
  cancellation releases recovery admission. Initial resume takes that admission
  before activation, retaining the original stop fence. A negotiated receiving
  contract updates only retained metadata, not the venue, checkout or conversation.
- Provider activation resolves the authoritative exact-name account binding;
  activation and readiness polling retain that account. Missing/failed bound
  lookups cannot select a different account's same-named venue.
- The focused Bridge packet passed 147 cases, including a real delayed provider
  process, repeated cancellation, drain-before/during activation and retained
  legacy upgrade. CodeSpace parser/lifecycle controls passed 136 cases. Final
  source evidence, review and CI remain open; no installed or external acceptance.

### 2026-11-05 - HTTP admission and retained repository review corrections

- The next exact-head review confirmed all four prior corrections and identified
  the HTTP-start sibling of the drain race, missing retained repository selectors
  during negotiation and an overly strict unmapped-account refusal.
- HTTP start now holds manager-owned admission before provider resolution and
  through original-operation cancellation settlement. Resume passes the retained
  repository/remote and verifies the selected execution checkout before recovery.
  First-use activation resolves the authenticated login and revalidates the exact
  venue with its explicit credentials; failed authoritative bindings never fall
  back.
- The Bridge packet passed 152 cases, including actual HTTP route/process
  cancellation and drain controls plus differing leading-`cd` cross-repository
  cases. CodeSpace parser/lifecycle controls passed 141 cases, including unmapped
  first use and identity, explicit-lookup and token failures. Updated source
  evidence, required CI and a new passing review remain separate gates.

### 2026-11-05 - Retained checkout backstop

- The next review confirmed the three previous corrections and identified an
  explicit-checkout gap before remote-host preparation. Admission now checks
  explicit selections too; the real preparation method independently rejects
  a changed known retained checkout before target/database updates or child
  preparation. It resolves a copy rather than mutating retained metadata on
  refusal.
- All 27 focused controls pass, including explicit/non-explicit cross-repository
  admission, actual preparation under explicit, provider and managed bindings,
  and existing fresh/replacement authenticated-hook sequencing. The ACP connection
  is substituted, not native Copilot acceptance.
- Reconciled independent contract-registry edits with incoming Dev history.
  The prior rebased head passed all 4,146 admitted units and 15 start consumers;
  final source admission and renewed review/CI remain open after this backstop.

### 2026-11-05 - Available legacy negotiation and read-only refresh

- The reconciled head received approval and passed required CI, but two earlier
  reviewed gaps remained valid. Kept it unmerged while addressing them rather
  than treating approval as permission to ignore known activation/compatibility
  defects.
- Legacy receiving metadata now negotiates an available current provider before
  the first policy consumer is selected. Stopped activation reuses that read-only
  selection under the original fence; guest-owner failure never triggers
  negotiation as fallback. Retained portable inspection remains independent of
  an unavailable current namespace owner.
- Later full-target refresh is non-activating and preserves explicit checkout
  intent. A real admitted-resume test pauses resolution, records a durable stop
  behind the held lifecycle lock and makes the venue Shutdown. No second
  activation, new Host or clearing of the newer stop is permitted.
- The focused packet passed 13 cases, including Available/Shutdown upgrade,
  unsupported prior adapter, missing current owner, stop-during-negotiation,
  the actual resume/stop race and existing placement/argument preservation.
  Broader recovery validation and renewed exact-source gates remain open.
- The complete selected recovery packet now passes 195 cases. It caught an
  ordering regression during the refactor: checkout validation now precedes
  policy consumption, including when a venue becomes Available between reads.
  Stopped fixtures retain their typed no-wake refusal. The delayed-provider
  fixture explicitly models this availability read while keeping its real
  subprocess and repeated-cancellation settlement controls.
- Final source qualification passed all 4,146 admitted units in the distinct
  Windows-native profile (220.547 seconds), all 40 admitted strict projects on
  Linux/Windows targets and the current source gates. The separate opt-in
  Windows test passed two cycles/four real console children from a `pythonw`
  parent: no observed visible windows, terminal acquisitions or child focus
  changes, and no remaining fixture descendants. It substitutes `gh --version`
  for backend requests and does not qualify live activation or native Copilot.

### 2026-11-05 - Preserve container policy-before-readiness

- The next exact-head review confirmed both resume corrections and identified a
  container shortcut gap. Restricted activating namespace admission to
  CodeSpaces; containers retain their existing policy check before readiness
  until an independently negotiated activation contract exists.
- Actual HTTP namespace/hosting controls substitute container inspect/exec
  results for stopped and running-but-denied members. They refuse before
  readiness, staging or session creation. All 15 focused container and preserved
  CodeSpace activation controls pass; final unit/source and review gates remain.

### 2026-11-05 - ACP source slice merged; mux composition next

- #10692 received exact-head approval with no active findings, all fourteen
  review threads resolved and required green CI, then merged as
  `1508a124f3f40a9acc747a6ffd60d8298956050c`. The umbrella issue remains open.
- Re-read the complete effort after merge. Closed only Phase 2's source-flow
  publication gate: isolated receiving processes, actual provider parser/
  transport consumers and the shipped Host with a mock ACP peer prove this
  source slice. The 4,146-case unit packet, focused recovery/container controls,
  admitted strict-type scopes and separate Windows launch-mechanics observation
  remain distinct evidence tiers, not installed or native Copilot acceptance.
- Pulled the continuing worktree onto the actual merged Dev commit. Verified
  every owned Python source/test blob matched the squash merge before dropping
  only the already-merged local range; no new work was discarded.
- Continue Phase 3 by tracing the existing Bridge extension and standalone
  remote-driver integration, then composing canonical Manager pane/launcher
  assets through their existing packaging owner. Preserve existing launch and
  lifecycle ownership; no guest full-suite bootstrap, parallel launcher or
  default-mode change. Compatibility, mux, native/external acceptance, release
  and authorized rollout remain open.

### 2026-11-05 - Mux extension custody and canonical support closure

- Traced the existing mux consumer: CodeSpace detached launch still installs
  guest tooling and calls `embody`; Bridge's extension directly invokes native
  mutation separately from the standalone driver's generation fence. Merely
  loading both extensions would not establish exclusive remote control.
- Added an explicit launch-only driver client. It grants only the pristine
  observed startup generation, refuses intervening input or another grant,
  serializes original-claim sequences and holds on failed/unknown effects.
  Bridge's opt-in adapter uses that same standalone driver, pins the native
  session/checkout and cannot downgrade to legacy native delivery or controls.
  Ordinary unselected extension behavior remains unchanged.
- Added manifest-driven composition of the unchanged canonical Manager pane
  shell and standalone remote-driver plugin. Nested packaged assets retain
  their plugin layout without copying unrelated payloads; traversal and linked
  source/destination paths refuse. The launch-only Bridge manifest excludes
  full-suite hooks and skills. No Manager launcher/lifecycle source was changed.
- The complete standalone-driver and changed Bridge extension packet passed
  148 cases, with one optional installed-SDK check skipped. Seven isolated
  source/materialized bundle cases and 21 materializer controls passed.
  The materialized test caught a ZIP manifest timestamp mismatch; corrected
  deterministic metadata and proved byte-identical source/release archives.
  Ruff and changed-file whitespace checks passed.
- This is local implementation progress, not the complete mux flow or a
  published slice. Receiving launch execution, provider handoff, TMux
  prerequisite composition, managed-workspace compatibility, native consumer
  evidence and publication remain open. No runtime rollout or external
  session was started; retain the existing launch owners and all parent gates.

### 2026-11-05 - Portable mux receiver under implementation

- Added a launch-scoped receiver and a provider-facing process declaration.
  The selected archive carries canonical policy sources, the unchanged pane
  shell and standalone driver, without selecting guest-suite commands.
- Each attempt retains its input digest and native pane/incarnation receipt.
  Unknown creation stays pending; retry cannot create or adopt a replacement.
  A request-owned native socket avoids inheriting a different TMux server's
  credential environment or putting credentials on command arguments.
- Policy is checked before creation and again inside the actual execution
  pane. The selected driver adapter observes unique native plugin roots
  before control and permanently holds on missing or duplicate observations.
- Initial receiver/package controls passed 29 cases, including a real isolated
  child denying changed repository policy with no guest suite on PATH.
  Ten focused extension controls and Python lint pass. A Windows ZIP-reader
  normalization gap found by the controls was corrected.
- This is local implementation, not a complete provider flow or native
  acceptance. Connection Owner socket custody, provider-bound registration,
  canonical prerequisite setup and launch-scoped loadout qualification remain
  open. No source publication, runtime rollout or live guest mutation.
- Follow-up controls passed 31 receiver/package cases and 26 extension/metadata
  cases. Socket identity now fences a replaced endpoint separately from pane
  identity. Selected receiving metadata preserves the provider-qualified scope
  without querying guest Worktrees or inventing a registered machine/repository;
  ordinary incomplete-metadata clearing remains unchanged.

### 2026-11-05 - Retained portable endpoint custody

- Added a versioned pending/confirmed native endpoint to the shared venue
  support. Owner persistence, re-hold and rollback retain it; malformed or
  incomplete endpoint records cannot downgrade to default-server addressing,
  and a fresh launch cannot erase an unsettled portable attempt.
- Portable observations enter the existing qualified inventory adapter, with
  its no-wake, credential-scope and cancellation handling. They remain distinct
  from same-named default-server sessions. Both positive renewal and release
  retain the observed Owner generation; a stale positive observation cannot
  renew a replacement tenant.
- Bridge reservation routes preserve the provider-authored endpoint through
  registration. The registering client cannot replace it. The active Owner
  advertises portable mux support independently of its installed package.
  Exact attach, pane-tail and stop commands prove socket device/inode and
  native session/pane identity; an unknown stop is not automatically replayed.
- Corrected the receiver's use of socket ctime: TMux deliberately chmods its
  socket when clients attach/detach, so ctime is not a stable birth identity.
  Device/inode and native incarnation remain the retained addressing proof.
- The final isolated packets passed 296 CodeSpace cases and 99 Bridge cases.
  Corrected an outdated purge-race test wrapper to forward the database's
  membership keyword, preserving the actual race rather than skipping it.
  Ruff and changed-file whitespace checks passed.
- These are source/transport-fixture controls, not actual native TMux or
  installed/external acceptance. The provider does not yet select this
  receiver. Launch handoff, scoped registration credentials, prerequisite
  composition, complete-flow qualification and publication remain open.

### 2026-11-05 - Canonical prerequisite composition

- Coordinated the narrow extraction on #8669 and #5579 without taking their
  retained launch, installation-survival or Windows observation work.
  Worktrees' public setup wrapper now delegates to a standalone standard-library
  owner with the same package-manager order, noninteractive privilege behavior,
  bounded attempts and post-install availability check.
- Included that exact helper through the existing packaged-asset declaration.
  The materialized/source archives remain byte-identical. An actual packaged
  helper control imports no guest Worktrees package and uses the canonical
  setup path with the package-manager effect substituted.
- The receiver advertises optional setup separately. It checks policy before
  setup and again before native creation. A known precreation prerequisite
  failure can continue only the same ready attempt; an exclusive attempt lock
  prevents concurrent ready retries from creating twice. Uncertain native
  creation remains pending and is never replayed.
- Native control and receiver reuse address the retained pane/session identity
  even after a human renames the TMux session. A same-named replacement or
  replaced socket still cannot be adopted.
- All eight unchanged Worktrees prerequisite cases pass. The final receiving/
  package packet passed 35 cases; the final focused endpoint/detached packet
  passed 167. The earlier complete Owner/legacy launch packet passed 296 and
  the Bridge reservation/receiver packet passed 99. These are isolated source
  controls, not actual native TMux or live package-manager execution.
- Provider launch selection, scoped registration credentials, prepared-plugin/
  owned-seed integration, complete-flow qualification and publication remain
  open. No runtime, shared guest or live venue was modified.

### 2026-11-05 - Explicit CodeSpace mux handoff and retained launch recovery

- Added explicit portable selection at Bridge's CLI creation entry and the
  CodeSpace detached provider. Default legacy launch remains unchanged.
  Same-cell provider invocation retains its existing validated peer boundary;
  unavailable providers cannot select a different installation after failure.
- The selected Bridge process and actual running Bridge/Connection Owner
  advertise independent capabilities. The selected Bridge supplies registration
  credentials through receiving stdin, with private attempt-scoped routing
  and configuration. No guest full-suite installation or shared auth rewrite.
- Portable preparation retains claims, target locks, relay/auth verification
  and the compiled plugin barrier, while skipping dotfiles, harness and
  repository provisioning. Canonical policy precedes plugin preparation and
  remains fresh at prerequisite, native-creation and pane-execution boundaries.
- Journal the original prepared request before native launch, including fixed
  native/attempt IDs, reservation, seed and workspace. Before submission retain
  compiled plugin inputs and the exact payload digest. Lost creation replies,
  registration failures and changed inputs retain the original endpoint and
  ownership; recovery cannot create a competing attempt or enter legacy cleanup.
  Confirmed rejoin observes that same native conversation and owned seed
  admission reuses its original key. Existing Owner-assigned forward reporting
  remains the source of current forwarded ports.
- The first combined provider/legacy packet passed 264 cases with five platform
  skips. The final focused provider/legacy-detached and actual Windows-peer
  packet passed 145 cases after the driver-label and forwarded-port refinements.
  The earlier Bridge registration/health packet passed 46 cases. The latest
  Bridge-entry, receiver/package and inert Windows-provider packet passed 95.
  An additional actual Windows peer/stdin packet passed eight cases, with
  source fixtures for portable launch/recovery alongside the inert process.
- The Windows witness caught text-mode LF-to-CRLF conversion; the portable
  provider seam now sends UTF-8 bytes. Its actual process control preserves
  literal plugin metadata and multiline stdin without a daemon or provider.
  The source entry control also caught an incorrect context-adapter assumption,
  corrected to the Bridge owner's existing validated installation boundary.
- Admitted strict checking found two missing endpoint generic annotations;
  corrected them and passed the affected library on Linux and Windows targets.
  Other admitted strict scopes passed; the 28 pending projects remain pending.
  The install contract reports zero violations. A bare interpreter lacked the
  unit runner's coverage dependency; full unit/source/contract gates remain open.
- These are local source and synthetic transport/native-process controls, not
  actual Copilot/TMux, clean-room install, live credentials or external-venue
  acceptance. Complete joined-consumer qualification, compatibility review,
  final source gates and publication remain open. No shared rollout, daemon
  restart, guest upgrade or live worker was performed.

### 2026-11-05 - Joined mux consumer and original admission

- Composed the actual Bridge CLI, CodeSpace detached provider, selected archive
  and receiver, extracted Bridge/standalone-driver extensions, SQLite handoff
  and driver HTTP route. The joined control proves one admitted and consumed
  seed, then the same conversation on rejoin without another creation or send.
  A duplicate selected driver holds without an SDK effect.
- The joined path exposed a fake-client interface gap, missing readonly
  readiness polling, unsupported typed startup composition and the retained
  inbox's ordinary remote-enrollment refusal. Corrected the actual client call
  and added explicit source capabilities rather than bypassing those refusals.
- Readonly control polling requires the actual daemon's named capability and
  filters before claim or expiry. Mutation controls remain unsupported.
  Startup admission binds the original operation, complete input and successful
  observation; native readiness is rechecked around the one-shot final grant.
- First portable enrollment requires the current claimed launch reservation,
  exact native session and attempt. Same-process replacement retains the
  existing predecessor CAS. Ordinary remote registrations, changed launches
  and unqualified recovery remain refused. Portable admission cannot expose a
  seed through the legacy inbox before enrollment.
- The final focused custody/startup/joined packet passed 23 cases. The Node
  compatibility wrapper passes all 133 underlying cases. Earlier broader
  focused execution passed 209 cases and exposed historical schema fixtures
  that mislabeled current guards; their corrected historical construction and
  the remaining affected checks passed targeted validation. Touched Python
  lint and the 13-plugin install contract pass.
- The SDK, native mux, provider process/SSH/forwards and POSIX-to-private-scratch
  carrier are explicit fixture substitutions. The extracted policy readers
  run in real isolated Python processes. This is source composition, not native
  Copilot/TMux, installed or external-venue acceptance. Docker is unavailable
  on this test host; no service startup or repair was attempted.
- Re-admitted only the three changed existing unit source bindings after
  reviewing their diffs; the exact required unit inventory is unchanged.
  Whole-unit execution, current contract evidence, remaining source gates,
  compatibility review and publication are still open. No shared rollout or
  guest-suite upgrade occurred.

### 2026-11-05 - Required-gate corrections

- The first whole-unit run passed 2,516 cases before stopping on the original
  basename-only asset error text. Restored that existing diagnostic while
  retaining safe relative paths for the separately declared packaged assets.
  The complete packet must run again; the partial result is not qualification.
- The module-size gate identified three files above its unchanged cap. Moved
  CLI venue handoff into its own bounded component, session record types and
  validation into the existing record owner, and verified stop behavior into
  its own component. Existing CLI/caller seams remain available; no launcher,
  allocation, cleanup or lease behavior was added.
- The extracted Bridge CLI/Windows-process/joined packet passes 71 cases;
  Owner persistence, generation fences, endpoint control, detached launch and
  stop controls pass 257. Marked the actual standalone standard-library
  receiver's no-window primitive explicitly rather than adding a guest runtime
  dependency for that low-level boundary.
- Added changefiles for the remaining consumers of the changed shared venue
  helper. Whole-unit qualification and the final module/headless/changefile
  checks remain open on the corrected source, along with current contract
  evidence and publication. No baseline cap or authority gate was widened.

### 2026-11-05 - Corrected whole-unit qualification

- The corrected source `1afb472fd63c35a175732e692c1176293b54fceb` passes all
  4,146 admitted units, with 21 deliberately deselected cases, in 207.59
  seconds of pytest execution. The complete Windows-native packet, including
  preparation, coverage and verification, finishes in 234.671 seconds.
  This local native profile is not required PR/promotion CI evidence.
- The unchanged module-size gate passes all 37 changed source files.
  Headless, documentation, changefile and 13-plugin install-contract checks
  pass. All 40 admitted strict projects pass both Linux and Windows targets;
  the 28 pending projects remain unqualified.
- The current contract audit identifies 16 changed source fingerprints and
  the missing current-registry update. The new capability, readonly-control
  and portable-consumer behavior needs matching current evidence, not a bulk
  golden refresh or non-semantic relabeling. Historical capture packets remain
  untouched. Source publication, review/CI and native/external acceptance remain
  open; no rollout or live product worker was performed.

### 2026-11-05 - Current portable contract capture preparation

- Extended only the current HTTP-52 owner with 25 existing focused cases and
  independent expectations for the three actual advertised portable
  capabilities. Earlier 91 cases, HTTP generations, introduction floors and
  historical inputs/packets are retained. The 27 focused cases pass; eight
  declaration-preparation/refusal tests and touched Python lint pass.
- Registered the explicit portable wire producers and the packaged receiving
  closure in the current capture recipe. This includes the standalone
  read-only driver adapter and canonical mux endpoint, not another runtime
  owner. Declaration preparation remains distinct from executed evidence.
- The joined source fixture substitutes SDK, native mux, provider process,
  SSH, Owner, claims, forwards and its POSIX state namespace explicitly.
  Current capture execution, required review/CI, native/installed qualification
  and this effort's remaining Plan/Validation items are still open.

### 2026-11-05 - Published checkpoint and executed portable evidence

- Published draft #10748 through the managed flow after preserving the one
  upstream effort-index conflict. The published source checkpoint is
  `b9d56fdb83825fb44114e38072cc53b81e1356cd`; every changed production source
  byte is retained across the rebase.
- The actual source-bound HTTP-52 capture passes all 116 cases. Its no-write
  verification independently passes the same portfolio, and the contract
  audit passes both contracts and 1,018 fixtures. Retained exact capture
  metadata protects the original source declaration through squash publication.
  Historical evidence and provenance prefixes remain unchanged.
- Hosted eligible-unit, dependency-seasoning, strict-type, install,
  attribution and identifier checks pass on the checkpoint. Its contract gate
  correctly refuses declarations without the subsequently executed fixtures;
  fresh CI on the evidence update remains required.
- The local all-declaration age audit cannot reach four public npm packages
  through validated TLS; the independent native HTTP client confirms the
  handshake failure. Python-only declarations pass. Hosted dependency
  seasoning passes; no TLS policy or global configuration was weakened.
- Review, fresh final-head CI and native/installed/external acceptance remain
  open. The source and parent efforts remain Active; no rollout or live
  product worker was started.

### 2026-11-05 - Generated consumer and canonical-history corrections

- The current-head CI guard identified two stale packaging-helper copies in
  the newly shipped Worktrees admission toolkit. Refreshed them with the
  canonical payload generator; all 13 manifests check clean and both changed
  copies pass Python lint. Generator-only native newline changes were
  formatted back without changing their canonical content.
- A shallow fixture initially removed required historical Git ancestry.
  The corrected canonical-history fixture excludes only the new capture
  commit and exposed one genuine older HTTP-51 README retention gap.
  Preserved its exact recorded Git blob and SHA-256 as an inert source
  artifact; no historical packet, expected value or provenance was rewritten.
  The corrected fixture must pass again on committed source before merge.
- Copilot's comment review on the evidence head reports no actionable
  findings and no discussion threads. It explicitly retains the unqualified
  native/installed tiers. Updated-head review and required CI remain open;
  this is not source-effort or parent completion.

- Completed the historical-closure inventory against canonical history plus
  the candidate tree, with the new capture commit absent. Of 1,986 distinct
  recorded blobs, four additional HTTP-51 documentation/probe blobs were
  missing after retaining the README. Preserved all four exact captured
  byte sequences together rather than iterating on first-error reports.
  Complete canonical-history validation remains the next acceptance check.
- Retained the original HTTP-51 capture registry and typed Git object proof
  as well. The canonical-history check exposed a classification mismatch
  when that older capture was interpreted through today's registry.
  Original capture-time semantic decisions are now retained independently;
  neither the validator nor historical operation packets were changed.
- Complete canonical-history validation now passes both contracts and 1,018
  fixtures on `846569699e6a38bcf9201958eb950fa750470fc6`, with both the new
  HTTP-52 and original HTTP-51 capture commits explicitly absent.
  Fresh published-head CI and review remain required; no native or installed
  acceptance is inferred from this repository evidence.

### 2026-11-05 - Mux source slice merged; native acceptance next

- #10748 is verified merged as `3e859abf8b43ffd303a36ee367f1d93488a8ee0f`
  at 20:37:32 UTC. Its exact final head passed required CI and received a
  current Copilot comment review with no actionable findings or unresolved
  threads. The repository-authorized self-merge exception omitted only the
  additional code-owner vote; no failed check or change request was bypassed.
- #10636 remains open. Re-read the Plan/Validation Plan and resolved only the
  source staging, packaging, documentation and evidence items this merge
  completed. Native mux/setup/lifetime, actual scoped extension loading,
  old-guest coexistence, installed and external acceptance remain open.
- Pulled the source worktree forward after verifying all 223 owned changed
  paths were byte-identical in the squash merge. Unrelated upstream Index
  and writer changes were retained. Existing capture source, metadata and
  recovery refs remain available; no unmerged work was discarded.
- The next slice is release/installed readiness inspection, followed by
  authorized native/external consumer validation. A merge is not rollout.
  Existing source approval does not authorize shared updates, restart,
  guest-suite installation or interference with retained sessions. Preserve
  the Active effort and obtain only missing effect scope before live work.

### 2026-11-05 - Installed readiness and host callback prerequisite

- The promoted mux source reached a selected, healthy Bridge runtime.
  Post-installer HTTP health and the selected CLI independently confirmed the
  incoming version, relay readiness and portable receiving capabilities.
  This is local installed readiness, not a complete receiving flow.
- The first external read-only attempt failed at namespace resolution before
  a worker was created. Independent native provider commands returned valid
  resolver output; a scoped retry exposed an unavailable Worktrees account
  snapshot. No token value, guest code change or model result was inspected.
- The existing account callback returned valid output after 13.5 seconds,
  exceeding its unchanged ten-second caller budget. The legacy global
  launcher selected a fresh bytecode namespace on every invocation despite
  the canonical helper qualifying that completed runtime for installed reuse.
- Applied that existing helper to the PowerShell and POSIX global front doors,
  preserving older-helper fallback, immutable runtime bytes and existing
  account/owner/deadline semantics. Six native front-door controls pass.
  A separate actual read-only callback comparison retained all samples:
  current launcher 7.91 seconds; proposed source launcher 4.30 then 3.97.
  Host load and filesystem caches were not reset; this is measured source-entry
  evidence, not a latency baseline, installed correction or successful retry.
- Source gates, public review/CI, release and authorized normal installation
  of this prerequisite remain open. The receiving effort remains Active.

### 2026-11-06 - Lossless native cache selection

- Expanded the real front-door fixture with a Unicode profile and forced
  CRLF stdout. Its native PowerShell resolver uses UTF-8 with a BOM, and the
  cache-parent assertion compares actual filesystem identity rather than
  unequal ordinary/extended Windows path spellings.
- The qualified old-source control reproduced four PowerShell failures:
  decoded paths no longer addressed the intended cache directory. Both
  Git Bash controls passed; this did not reproduce a Git Bash CRLF defect.
- Selector replies now carry ASCII-safe JSON. PowerShell decodes the string
  directly; POSIX uses a separate read-only binary decoder, retaining
  Unicode, embedded newlines and native path bytes without shell evaluation.
  The corrected packet passes all nine cases, including original
  old-helper fallback, repeated cache reuse and immutable-slot preservation.
- Corrected-source actual account callback samples returned the full existing
  JSON shape in 2.90 and 2.76 seconds without diagnostics. Earlier samples
  remain above; this is still insufficient for tail/baseline qualification.
  Updated-head review, CI, release, installation and live receiving remain open.

### 2026-11-06 - Callback prerequisite source accepted

- #10812 is verified merged at `4e63a7ca09f8c04769e8ff4c4fd2421b44eca5d0`
  after approval with no active findings on the exact corrected head and
  successful required CI, including the PR gate. The repository-authorized
  self-merge exception omitted only the additional code-owner vote.
  #10636 remains open.
- Re-read the complete effort and retained every native/installed/external
  gate. The source-cache prerequisite is delivered, not installed acceptance.
  No complete receiving flow or product operation is claimed.
- Normal source sync aborted on the squash history without changing the
  branch. Verified all six owned paths against the actual merge, retained a
  backup branch, and replayed only the empty post-reviewed range onto current
  Dev. No unmerged work was discarded.
- Next: verify the promoted launcher bytes, use the authorized normal host
  update, then qualify the actual installed account/owner callbacks and one
  existing receiving venue before the broader live checks. Source/runtime
  publication never grants deployment or guest-suite upgrade authority.

### 2026-11-06 - Release blocked before native test collection

- #10826 is verified merged after exact-head approval and required green CI.
  Re-read this complete effort; all installed/native/external gates remain open.
- Promotion run `42139267241` passed candidate construction, unit qualification
  and artifact attestation, then refused the frozen source/base binding after
  Main advanced. The existing #8660 records that occurrence; no stale candidate
  was forced through.
- The new required Windows Resume gate failed during environment preparation
  in runs `42141026158` and `42142292967`, before test collection. The missing
  evidence upload is secondary, not a failed Resume assertion.
- Verified the exact second run's frozen bundle and reproduced preparation
  failure from candidate `a70eb8ce02f9b0b35e6b382f336fd61989366b51`.
  Captured UV diagnostics identify conflicting `agent-update-driver` URLs in
  Manager's and Bridge's separate materialized library copies. Dev's shared
  canonical library paths do not exercise this setup difference.
- The preparation-only reproduction used local Python 3.13, not the hosted
  Python 3.12. It ran no native assertions or daemon. The initial general
  archive extraction was stopped; the selected setup closure was extracted
  separately from the same immutable archive.
- Supplied the exact diagnosis on #10823 and to the already-claimed #10830
  investigation. That owner retains the correction; no competing runner,
  installer-policy or controller change was made. Keep the native gate required.
- #10812 remains merged but unreleased. Next: observe the existing owner's
  correction and successful promotion, verify actual release bytes, then use
  the authorized installation and original single-venue acceptance path.

### 2026-11-06 - Callback release verified; ordinary update prerequisite repair

- The release preparation corrections in #10843 and #10856 passed promotion
  `42155377340`. Verified the actual released launcher bytes before an
  authorized installation. The installed account callback returned valid JSON
  without diagnostics in 4.22 seconds, within its unchanged ten-second budget.
  This is one installed observation, not a latency baseline or receiving proof.
- The ordinary update terminated unsuccessfully at separate Pull Requests CLI
  health and Vault selected-service readiness gates. Neither payload delivery
  nor other successful runtime phases was reported as whole-update success.
- Reserved #10959 for the generic optional-Fleet import defect. Three real CLI
  subprocess controls reproduce help/version failure before the fix. Deferring
  attention implementation imports preserves execution semantics and keeps
  the existing installed-health rejection intact. The focused CLI/attention/
  native-health packet passes 56 cases; Ruff and install contract pass.
- #10961 received approval on its exact published head, passed required CI and
  merged as `fdc4794ecb45b3f861b7e01120ccd7cfa745bbe2`. It still requires actual
  release and installation. This architecture-neutral prerequisite does not
  close the receiving effort or its native/external acceptance controls.
- Release run `42191934746` failed both real Windows Resume variants before
  pane acknowledgement because `work_coalescing_singleton` was unavailable.
  Existing contributor PR #10960 owns that fixture correction. Supplied the
  matching failure evidence there rather than creating a competing repair;
  required native qualification remains intact.
- Re-read the complete effort after the source merge. Next: verify the actual
  corrected release and normal installation result, then resume the same
  authorized first-venue receiving acceptance. No native Copilot, mux lifetime,
  sparse/old guest or complete external flow has been accepted.
- A separate selected-generation readiness discrepancy is reserved in #10971:
  the native 250 ms ping inherited repeated configuration-owner resolution
  from ordinary backend ping. A lightweight optional hint separates lifecycle
  metadata from configuration/backend work without raising the deadline or
  changing public ping. The actual TCP controls reproduce the old failure;
  27 corrected generation controls pass, including legacy response acceptance,
  ordinary ping, real successor retirement and writer exclusion.
  A raw-file fixture reader discovered during the wider packet now uses its
  canonical delete-sharing reader; publication checks were not weakened.
  This source correction still needs review, release and authorized rollout.

### 2026-11-06 - Native health prerequisite source accepted

- #10974 received exact-head approval with no active issues and all required
  green CI, then merged as `f12d59b92eb1c0f94a63e694147e42b0d8fe20ec`.
  The published production/test bytes match the locally tested commit.
  The managed self-merge exception omitted only the additional code-owner vote.
- The existing contributor's Windows Resume fixture correction #10960 is
  separately merged as `375ed2eb53fead5ee8cd3b0daeeb997382d42623`.
  Preserve that ownership and the required native gate; no competing fix or
  independent merge of the contributor's PR was performed here.
- Re-read this effort's Plan and Validation Plan. No receiving/native/external
  item closes from these prerequisite merges. Observe their actual release,
  verify the released bytes and complete authorized normal installation before
  the original single-venue acceptance. The effort and #10636 remain Active.

### 2026-11-06 - Prior-observer compatibility before rollout

- Release `42202812208` completed successfully, including real Windows Resume
  and exact-source reuse. No ordinary installation was started: a final mixed-
  direction analysis identified that a prior observer still sends full ping
  without the new hint, so repeated configuration resolution can prevent it
  from observing and retiring for its successor.
- Reopened #10971 for that remaining direction. Contextless full ping now uses
  the resident default selected at initialization. Local clients already send
  their freshly resolved context; credential requests without a context still
  resolve configuration on every request. Ordinary expiration checks remain.
  Documented the read-only resident-status scope rather than claiming fresh
  configuration selection for an old contextless ping.
- The legacy-default regression failed before this correction. The complete
  generation/settlement/readiness/discovery packet passes 144 cases with one
  platform skip and a CPython closed-pipe destructor warning. The real warm
  successor control now uses an old-style observer without the hint; actual
  retirement, handoff and writer-exclusion assertions remain intact.
  Local client context changes and fresh credential selections are checked.
- Ruff, install contract, documentation and whitespace gates pass. This
  compatibility follow-up still needs review, release and authorized normal
  installation; it does not close any receiving or parent acceptance item.

### 2026-11-06 - Prior-observer correction accepted

- Review of #10984 identified that its legacy fixture wrapped TCP only.
  Corrected both TCP and Unix dialers and asserted the actual native transport
  and hint-free request after original-process retirement. All 28 generation
  controls pass on Windows; Linux-native execution was not performed here.
- The corrected head `225897c057cf8c93c2d74524a9a9bf7dd02dceeb` received
  approval with no active issues, its corrected thread was resolved and every
  required check passed. #10984 merged as
  `99f0643336263a9c85f8a1519facb5dd416846bd`. Its provider source matches the
  locally tested commit. No failed check or change request was bypassed.
- Re-read the remaining Plan and Validation Plan. Wait for a new release that
  includes this provider correction, then verify actual source and authorized
  ordinary installation before resuming the original first-venue acceptance.
  The earlier successful release does not contain this follow-up.

### 2026-11-06 - Corrected release qualified

- Release run `42208938885` passed native Windows Resume, exact-source reuse,
  publication and promotion. Main commit
  `f921808d2292394abc738149891de4efdc933630` contains the expected provider,
  generation-health and optional-Fleet CLI corrections.
- The authorized ordinary installation reached a Windows caller-Job
  breakaway refusal before its finite worker started. Native request
  inspection retained the pending request and its launch error.
- The documented foreground-supervised update mode reused that same request
  and exclusive execution authority. Manager construction and cutover
  reported success; suite-engine preparation is still running. No substitute
  service, scheduler, privilege change or caller-cleanup weakening was used.
- Whole installed and receiving-consumer acceptance remains open. The
  supported supervised mode does not qualify default durable-worker launch
  in an externally restrictive caller Job.

### 2026-11-06 - Bounded Vault writer-transition plan

- The supervised whole update completed with exit 1: all runtime modules
  except Vault passed, and the corrected Pull Requests CLI's actual help
  command succeeds. Vault's selected incoming endpoint responds promptly
  but does not admit credential work; its predecessor reports shutdown
  while remaining alive. These are separate installed outcomes.
- #11057 reserves the bounded follow-up. Incoming-first new-port publication
  already reaches the new generation; the remaining veto is the outgoing
  process/credential-writer gate, not stale discovery routing.
- Reconcile this slice to
  [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
  and the [existing cutover pattern](../../../docs/patterns/graceful-daemon-cutover.md).
  Keep the existing resident/installation owner: publish incoming discovery,
  offer cooperative handoff and connection grace, then make an identity- and
  owner-checked bounded cold transition when the predecessor cannot settle.
  Preserve exclusive credential writes and current-generation selection.
- This plan changes no wire protocol and introduces no watchdog, periodic
  reaper or private process-termination mechanism. The synthetic TCP-reset
  check did not reproduce the old shutdown hang, so its cause remains
  unproved. Existing source tests do not qualify this new transition.
- Review this plan before implementation. Live process recovery is not
  authorized by the source direction; installed rollout and the original
  first-venue acceptance remain separate gates.
- #11059 received exact-head approval with no active findings and green
  required CI, then merged as `10e3ec8c9e28f899d2086f8a3a67420f3a84773c`.
  Re-read the remaining Plan and Validation Plan. Implement #11057 through
  the existing lifecycle and shared native process/owner primitives next;
  no installed or receiving-consumer gate closes from this planning merge.

### 2026-11-06 - Bounded Vault writer transition implemented

- The focused failing control reproduced writer custody surviving settled
  credential work until transport cleanup. The outgoing lifecycle now releases
  that lease before cleanup and privately acknowledges the release. The
  incoming service still acquires the actual shared lease; a public health
  response cannot supply this acknowledgement.
- The existing incoming tick grants thirty seconds after discovery publication,
  then makes one central-only retirement attempt through shared native process
  custody. Selection, discovery, admitted owner, installation slot, runtime
  identity, principal and native birth are checked before mutation. Unknown
  ownership or exit stays an explicit refusal, not a fabricated healthy update.
- Actual Windows fixtures preserve an admitted old connection during grace,
  transfer ready writer custody before hung cleanup, and cold-exclude hung
  credential work before incoming admission. The retained admission supplies
  the exact registry after launch-only environment fields have been cleared.
  Fixed-address aliases are retried after old-process exit.
- The focused lifecycle/discovery/readiness packet passes 212 cases with one
  platform-specific skip and a CPython closed-pipe destructor warning. It
  includes old/new receipt directions, malformed receipt and independent
  owner/selection/birth/exit refusal controls. Linux/macOS, installer clean-room,
  installed rollout and external receiving acceptance remain unqualified.
  These isolated source controls do not close the parent effort.
- Python lint, install contract, documentation and headless checks pass.
  Final committed-source checks, review/CI, release and separately authorized
  installed acceptance remain open. No live predecessor was terminated.
- The final generation/ownership packet passes all 62 cases after latching
  positive predecessor exit, avoiding repeated native probes in steady state.
  The unchanged settlement, lifetime, auth-state, readiness and discovery
  controls retain their earlier result. All 40 admitted strict projects pass
  both declared type targets; pending scopes are not implicitly qualified.

### 2026-11-06 - Writer-chain and first-review corrections

- The first implementation head's required CI passed, but review identified
  live backend-child exclusion, stale Unix aliases and the shared native
  identity's `reused` result. Held #11072 as draft while addressing them;
  that earlier head is not accepted or deployed.
- A new failing native control also reproduced three-generation replacement:
  discovery can name a passive failed newcomer while an older daemon still
  owns credential work. Capture that exact contended lease holder as a second
  bounded candidate, independently validate its central ownership and recheck
  current lease contention at mutation. No historical sweep is introduced.
- Native retirement now pauses the exact parent before census. Credential or
  unknown children refuse retirement and resume the parent, retaining writer
  exclusion. Only the actual system console-host image is exempt on Windows;
  a same-named foreign image remains a worker. No descendants are killed.
  The declared `psutil` dependency supports this finite native census.
- Positive original exit includes a reused PID. Unix alias reclamation checks
  the captured owned socket identity and liveness, preserving changed/live
  paths. These corrections preserve the existing incoming lifecycle owner.
- All 73 focused generation/ownership controls pass, including actual repeated
  replacement and a real held backend process reached through the production
  KeePassXC edit constructor. Three subsequent changed-lease controls and the
  explicit Linux Unix-alias assertion still require execution. Final source,
  dependency-age, review/CI, release and installed acceptance remain open.
- The final affected packet passes 232 cases with one platform skip and
  named per-case/teardown JUnit retained by the test owner. An earlier packet
  exposed a Windows-only portable-fixture omission (`AF_UNIX`); supplying the
  explicitly mocked platform constant passes all five residual probe controls.
  Final whole-packet execution is clean, without the prior pipe warning or
  held-cleanup diagnostic. Linux-native execution is still not claimed.
- Serialized documentation validation passes after preparation's transient
  source inputs are gone. Python dependency seasoning passes all 564
  declarations with zero violations; lint and the install contract pass.
  Fresh committed-source checks and review/CI remain required.
- Restrict console-image identity reads to the native system directory, so an
  unrelated backend image never causes filesystem probing outside that directory.
  All seven affected native/census controls pass again. The two retained reports
  contain 240 named testcases, zero failures/errors and the single platform skip;
  repeated controls are not counted as new portfolio coverage.

### 2026-11-06 - Independent Unix alias custody and native fixture routing

- The second review confirmed the first corrections but found that the fixed
  Unix alias can belong to a suffixed-primary predecessor or an even older
  writer. Track its installation, native birth and socket identity separately
  from discovery, using a private bounded atomic owner record for each fixed
  bind. Legacy fixed-primary owners can be captured before exit. Missing or
  invalid ownership never invents another process-retirement target.
- Alias mutation and identity-checked cleanup share the publication lease.
  Retry until the actual alias owner exits; preserve live, replaced and unknown
  paths. This closes the existing replacement intent rather than extending it.
- Endpoint-derived fixture requests now use the advertised native transport;
  Unix-only discovery no longer requires a TCP alternative. Explicit fixed TCP
  controls remain intact. Added portable ownership, invalid-record, selection
  and cleanup controls, plus Linux-native successive-cold and older-writer
  fixed-alias assertions.
- The affected lifecycle, settlement, handoff, discovery and readiness packet
  passes 251 tests with two platform skips. A CPython closed-pipe destructor
  warning remains visible; it is not silently described as a clean run.
  After the final reader-bound and invalid-record fallback tightening, all
  eighteen alias controls pass separately. Named JUnit remains retained.
- Windows source evidence does not qualify the Linux-native controls, macOS,
  installer clean-room, installed whole-suite update or external receiving
  acceptance. The PR remains unmerged pending final gates and fresh review/CI;
  no live predecessor was terminated by this source correction.
- Final lint, install contract, serialized documentation, headless launch,
  module-size and shared-contract checks pass. All forty admitted strict
  projects pass both declared type targets; Vault's pending type migration
  is not implicitly qualified.

### 2026-11-06 - First-touch and retained-transport review corrections

- The next review confirmed independent Unix ownership and transport-aware
  fixtures, then found first-touch record creation before the run directory
  exists and premature Windows alias completion. Kept #11072 draft rather
  than merge the otherwise-green head.
- An absent-directory regression reproduced the first-touch failure. The
  private owner writer now creates that directory before its temporary file.
- A real Windows three-generation control reproduced the fixed TCP retry
  stopping after the discovery predecessor exits while an older service,
  having released writer custody, still retains its transports. Completion
  now requires each configured transport's actual bind, retaining successful
  binds across retries. A native bounded presence check replaces the generic
  unprobed pipe result; only not-found permits a new fixed-pipe bind.
- Native fixed TCP and named-pipe requests both reconnect after the older
  listener's controlled exit. Portable controls also preserve busy, denied and
  unknown presence results, and exercise independently partial restoration.
- Retained native diagnostics exposed a PID-publication/cleanup race on
  Windows, not an expired backend hold: replacement could collide with the
  donor's PID-file reader. Writer-admission PID publication now shares the
  publication lease and rechecks selection/discovery. Contention keeps
  credential admission closed. Both a deterministic contention control and
  real backend settlement pass.
- The backend fixture preloads successor imports before its unchanged
  eight-second held-work budget. Actual activation and retirement still occur
  while the real backend is held. Failure output now retains bounded owned
  process/log diagnostics and an explicit fixture-expiry marker.
- The final affected packet passes 265 tests with three platform skips and
  named per-case evidence retained. A CPython closed-pipe destructor warning
  remains visible; no held-cleanup diagnostic occurs. Earlier EOF and timeout
  outcomes remain in the retained reports rather than being erased by retries.
  Linux/macOS, installer clean-room, installed whole-update and external
  receiving acceptance remain separate. No live process was changed.
- A final cleanup control failed when a newer fixed-alias owner reused the
  same socket inode. Fixed-path cleanup now also requires the current private
  record's PID and native birth to match this process. Own, changed-PID and
  changed-birth cases pass; all 36 selected alias/cleanup consumers pass with
  one Linux skip. This is a focused final correction, not a repeated full
  265-case packet or additional unrelated coverage.

### 2026-11-06 - Serialize rollback with pinned retirement

- The next #11072 review had no inline findings, but its summary identified
  a real selector race. The daemon-publication lease protects discovery;
  SDK activation and rollback use a different kernel selector lease. Two
  selection checks cannot close the interval before native termination.
- Cold retirement now borrows that existing SDK lease from its own immutable
  slot's retained bootstrap helper, holding it through pinned termination and
  confirmed exit. No second selector protocol or generic SDK change is added.
  Missing, nonordinary, corrupt or busy custody refuses the cold attempt;
  cooperative replacement remains available.
- The native regression pauses immediately after final owner revalidation
  and before real termination. A concurrent real SDK `activate` first proves
  that the selector lease is busy, then acquires it only after the original
  process is positively dead. The generation packet passes 63 cases with
  one Linux-only skip. The subsequent corrupt/busy refusal controls pass all
  five cases; unchanged native cases retain that earlier packet's evidence.
  This does not claim another monolithic run or Linux-native qualification.
- The replacement reference now documents selector custody and its refusal
  outcome. Review, exact-head CI, release and installed/external acceptance
  remain open. No live service was changed by these source tests.
