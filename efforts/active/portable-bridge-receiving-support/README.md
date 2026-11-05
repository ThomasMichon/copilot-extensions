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
- [ ] Prove the complete ACP consumer flow, publish it through required gates and steward its PR to merge before extending it.

### Phase 3 - Complete the existing muxed flow
- [ ] Stage the same standalone remote-driver plugin at session creation and compose canonical mux/pane launch assets with only their required support closure.
- [ ] Supply required TMux/native-mux setup through its existing owner, scoped to the selected launch. Preserve installed guest state and unrelated sessions; unavailable genuine prerequisites remain explicit, not a request for the full suite.
- [ ] Preserve Worktrees/Manager launch ownership, literal arguments, seed delivery, native process/terminal lifetime, exit handling and driver generation/exclusive mutation semantics.
- [ ] Consolidate packaging only where it removes duplication. Preserve independent remote-driver use, ACP behavior and explicit mode selection.
- [ ] Prove the complete muxed consumer flow, publish it and steward its PR through merge.

### Phase 4 - Compatibility and closeout
- [ ] Document supported caller/receiver combinations, retained-host recovery and rollback using actual selected capabilities rather than package-version equality.
- [ ] Update reality docs, focused tests and changefiles; retain historical protocol evidence and the required unit-only portfolio.
- [ ] Record source, release and installed/native/external results separately; resolve every Plan and Validation Plan item before Done.

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
